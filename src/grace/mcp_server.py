"""Grace as an MCP server: the dispute verbs PayPal's agent toolkit does not expose.

PayPal's agent toolkit (github.com/paypal/agent-toolkit) gives an agent three dispute tools:
`list_disputes`, `get_dispute`, and `accept_dispute_claim`. The first two read. The third
surrenders. There is no way for an agent to contest a dispute, submit evidence, or make an offer,
so an agent with the official toolkit can only lose, politely and at scale.

This server adds the missing verbs. It speaks the MCP stdio transport with no third-party
dependencies: newline-delimited JSON-RPC on stdin/stdout, `initialize`, `tools/list`, `tools/call`.

Tool names are prefixed `grace_` so they cannot collide with `paypal-sandbox` when both servers are
installed in the same client - which is the intended deployment.

The calls map 1:1 onto PayPal's Disputes API:

    grace_assess_dispute   -> GET  /v1/customer/disputes/{id}          (read the claim)
    grace_contest_dispute  -> POST /v1/customer/disputes/{id}/provide-evidence
    grace_offer_settlement -> POST /v1/customer/disputes/{id}/make-offer
    grace_triage_alerts    -> the 20-hour refund-or-fight call on a pre-chargeback alert

Today they read Grace's own ledger. They do not call the live API, and this file does not claim to.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any

from . import dispute as dispute_mod
from .ledger import Ledger

SERVER = {"name": "grace", "version": "0.1.0"}
PROTOCOL = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "grace_list_disputes",
        "description": (
            "List the merchant's open disputes with the recommendation Grace has already made for "
            "each: contest, contest partly, or accept. Read-only."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "grace_assess_dispute",
        "description": (
            "Read one dispute's claim, map it to the evidence its reason code requires, gather "
            "what exists (including evidence from the payment's own signed record), and report "
            "which requirements are met. Returns a recommendation of contest / contest_partial / "
            "accept with coverage and the reasoning. Does not file anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"dispute_id": {"type": "string"}},
            "required": ["dispute_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "grace_contest_dispute",
        "description": (
            "Build the evidence response for a dispute and return exactly what would be "
            "submitted: the packet, the per-requirement evidence ids, and the written statement. "
            "Refuses when the evidence does not support a contest - it will not invent a claim."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"dispute_id": {"type": "string"}},
            "required": ["dispute_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "grace_offer_settlement",
        "description": (
            "Decide whether to contest or to settle inside PayPal's amicable window, and return "
            "the highest offer that is cheaper than fighting. The ceiling is derived from evidence "
            "coverage and the merchant's chargeback fee; it is a boundary, not a prediction."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dispute_id": {"type": "string"},
                "fee_amount": {"type": "number", "default": 0.0},
            },
            "required": ["dispute_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "grace_triage_alerts",
        "description": (
            "Triage pre-chargeback alerts. PayPal gives 20 hours to refund and avoid the "
            "chargeback and its fee; this decides refund-or-fight for each alert from whether its "
            "decisive evidence exists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "alerts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "payment_id": {"type": "string"},
                            "reason_code": {"type": "string"},
                            "amount": {"type": "number"},
                            "currency": {"type": "string"},
                            "hours_remaining": {"type": "number"},
                            "attached": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": [
                            "id",
                            "payment_id",
                            "reason_code",
                            "amount",
                            "hours_remaining",
                        ],
                    },
                }
            },
            "required": ["alerts"],
            "additionalProperties": False,
        },
    },
]


class Grace:
    """The tool implementations, kept separate from the transport so they are testable."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def _row(self, dispute_id: str) -> dict[str, Any]:
        row = self.ledger.get_dispute(dispute_id)
        if row is None:
            raise ValueError(f"no dispute {dispute_id} in the ledger")
        return row

    def _assess(self, row: dict[str, Any]) -> dispute_mod.Assessment:
        found = dispute_mod.Dispute(
            id=row["id"],
            payment_id=row["payment_id"],
            reason_code=row["reason_code"],
            amount=row["amount"],
            currency=row["currency"],
            claim=row["claim"],
            due_at=row["due_at"],
        )
        evidence = dispute_mod.gather(
            found,
            self.ledger.transitions(found.payment_id),
            [dispute_mod.Evidence(**a) for a in row["attached"]],
        )
        return dispute_mod.assess(found, evidence)

    def list_disputes(self, **_: Any) -> dict[str, Any]:
        out = []
        for row in self.ledger.disputes():
            assessment = row.get("assessment") or self._assess(row).public()
            out.append(
                {
                    "dispute_id": row["id"],
                    "reason_code": row["reason_code"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "due_at": row["due_at"],
                    "recommendation": assessment["recommendation"],
                    "coverage": assessment["coverage"],
                    "filed": row.get("filed", False),
                }
            )
        return {"disputes": out, "count": len(out)}

    def assess_dispute(self, dispute_id: str, **_: Any) -> dict[str, Any]:
        row = self._row(dispute_id)
        assessment = self._assess(row)
        self.ledger.save_assessment(dispute_id, assessment.public())
        return assessment.public()

    def contest_dispute(self, dispute_id: str, **_: Any) -> dict[str, Any]:
        row = self._row(dispute_id)
        assessment = self._assess(row)
        if assessment.recommendation == "accept":
            return {
                "filed": False,
                "dispute_id": dispute_id,
                "would_post_to": f"/v1/customer/disputes/{dispute_id}/accept-claim",
                "reason": assessment.explanation,
                "note": (
                    "Grace will not submit an evidence response it cannot support. The decisive "
                    "requirement is missing, so the honest action is to accept the claim."
                ),
            }
        self.ledger.mark_filed(dispute_id)
        return {
            "filed": True,
            "dispute_id": dispute_id,
            "would_post_to": f"/v1/customer/disputes/{dispute_id}/provide-evidence",
            "recommendation": assessment.recommendation,
            "coverage": assessment.coverage,
            "packet": assessment.packet,
            "statement": assessment.narrative,
        }

    def offer_settlement(
        self, dispute_id: str, fee_amount: float = 0.0, **_: Any
    ) -> dict[str, Any]:
        row = self._row(dispute_id)
        assessment = self._assess(row)
        found = dispute_mod.Dispute(
            id=row["id"],
            payment_id=row["payment_id"],
            reason_code=row["reason_code"],
            amount=row["amount"],
            currency=row["currency"],
            claim=row["claim"],
            due_at=row["due_at"],
        )
        settlement = dispute_mod.settle(found, assessment, fee=float(fee_amount))
        body = settlement.public()
        body["would_post_to"] = f"/v1/customer/disputes/{dispute_id}/make-offer"
        return body

    def triage_alerts(self, alerts: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        out = []
        for row in alerts:
            alert = dispute_mod.Alert(
                id=row["id"],
                payment_id=row["payment_id"],
                reason_code=row["reason_code"],
                amount=float(row["amount"]),
                currency=row.get("currency", "USD"),
                hours_remaining=float(row["hours_remaining"]),
            )
            evidence = dispute_mod.gather(
                alert,  # type: ignore[arg-type]
                self.ledger.transitions(alert.payment_id),
                [dispute_mod.Evidence(**a) for a in row.get("attached", [])],
            )
            out.append(dispute_mod.triage_alert(alert, evidence).public())
        return {"alerts": out, "count": len(out)}


def _handle(
    grace: Grace, method: str, params: dict[str, Any], msg_id: Any
) -> dict[str, Any] | None:
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER,
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        handler: Callable[..., dict[str, Any]] | None = {
            "grace_list_disputes": grace.list_disputes,
            "grace_assess_dispute": grace.assess_dispute,
            "grace_contest_dispute": grace.contest_dispute,
            "grace_offer_settlement": grace.offer_settlement,
            "grace_triage_alerts": grace.triage_alerts,
        }.get(name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"unknown tool: {name}"},
            }
        try:
            payload = handler(**arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
                    "isError": False,
                },
            }
        except Exception as exc:  # noqa: BLE001 - a tool error must reach the client, not crash
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            }
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(ledger_path: str | None = None) -> int:
    ledger = Ledger(ledger_path or os.environ.get("GRACE_DB", "grace.db"))
    grace = Grace(ledger)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _handle(
            grace, message.get("method", ""), message.get("params") or {}, message.get("id")
        )
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
