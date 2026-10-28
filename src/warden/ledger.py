"""The ledger: SQLite, because a payments audit trail should survive the process that wrote it."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from warden import receipts
from warden.models import Decision, Mandate

SCHEMA = """
CREATE TABLE IF NOT EXISTS mandates (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    vendor_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    mandate_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    intent_score REAL NOT NULL,
    mode TEXT NOT NULL,
    paypal_id TEXT,
    receipt TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── mandates ────────────────────────────────────────────────────────────────────────────
    def save_mandate(self, mandate: Mandate, at: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO mandates (id, at, body) VALUES (?, ?, ?)",
            (mandate.id, at, json.dumps(mandate.public(), sort_keys=True)),
        )
        self._conn.commit()

    def get_mandate(self, mandate_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT body FROM mandates WHERE id = ?", (mandate_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def list_mandates(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT at, body FROM mandates ORDER BY at DESC").fetchall()
        return [{**json.loads(row["body"]), "issued_at": row["at"]} for row in rows]

    # ── decisions ───────────────────────────────────────────────────────────────────────────
    def add_decision(self, decision: Decision, receipt: dict[str, Any]) -> str:
        decision_id = f"dec_{uuid.uuid4().hex[:12]}"
        paypal_id = str(
            decision.paypal.get("authorization_id") or decision.paypal.get("order_id") or ""
        )
        self._conn.execute(
            """INSERT INTO decisions (id, at, outcome, amount, currency, vendor_id, vendor_name,
               mandate_id, attempt_id, intent_score, mode, paypal_id, receipt)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                decision.at,
                decision.outcome.value,
                decision.amount,
                decision.currency,
                decision.paypal.get("vendor_id", ""),
                decision.paypal.get("vendor_name", ""),
                decision.mandate_id,
                decision.attempt_id,
                decision.intent.score,
                decision.mode,
                paypal_id,
                json.dumps(receipt, sort_keys=True),
            ),
        )
        self._conn.commit()
        return decision_id

    def rows(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            body = json.loads(row["receipt"])
            payload = body.get("payload", {})
            out.append(
                {
                    "id": row["id"],
                    "at": row["at"],
                    "outcome": row["outcome"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "vendor": row["vendor_name"]
                    or payload.get("attempt", {}).get("vendor", {}).get("name", ""),
                    "mandate_id": row["mandate_id"],
                    "mode": row["mode"],
                    "intent_score": row["intent_score"],
                    "reason": (payload.get("reasons") or [""])[0],
                    "counterfactual": payload.get("counterfactual"),
                    "paypal_id": row["paypal_id"],
                }
            )
        return out

    def receipt(self, decision_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT receipt FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        return json.loads(row["receipt"]) if row else None

    def verify(self, decision_id: str) -> tuple[bool, str]:
        receipt = self.receipt(decision_id)
        if receipt is None:
            return False, "no such decision"
        return receipts.verify(receipt)

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM decisions GROUP BY outcome"
        ).fetchall()
        counts = {"allow": 0, "hold": 0, "deny": 0}
        for row in rows:
            counts[row["outcome"]] = int(row["n"])
        return counts

    def replay(self, mandate_id: str) -> Iterable[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT receipt FROM decisions WHERE mandate_id = ? ORDER BY at", (mandate_id,)
        ).fetchall()
        return [json.loads(row["receipt"])["payload"] for row in rows]
