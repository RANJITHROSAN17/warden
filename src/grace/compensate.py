"""The compensation agent: read a failure, choose the smallest correct next move.

This is the whole product in one function. A capture failed; the money is still committed; the
customer is waiting. What happens next is a decision, not a retry policy.

The agent reasons over three things and nothing else:

  * what the failure actually says  - a decline is not a timeout, and neither is an expiry
  * what the authority permits      - partial capture, retries, attempt budget
  * how much money is still at risk - the remainder, not the original amount

It returns a plan, never an execution. The engine still checks every rule, so a persuasive failure
message cannot widen the agent's powers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grace.models import Authority, Payment

PARTIAL_CAPTURE = "partial_capture"
RETRY = "retry"
VOID = "void"
ESCALATE = "escalate"


@dataclass(frozen=True)
class Plan:
    action: str
    amount: float | None
    explanation: str
    signals: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "amount": self.amount,
            "explanation": self.explanation,
            "signals": list(self.signals),
        }


def plan(payment: Payment, authority: Authority, failure: dict[str, Any]) -> Plan:
    """Choose what to do about a failed capture, or say that a human should decide."""
    status = str(failure.get("status") or "")
    reason = str(failure.get("reason") or "")
    signals = [f"status={status or 'unknown'}", f"reason={reason or 'unknown'}"]

    if status == "FAILED" or reason in {"RAIL_TIMEOUT", "NETWORK", "SERVICE_UNAVAILABLE"}:
        if authority.allow_retry and payment.attempts < authority.max_capture_attempts:
            return Plan(
                RETRY,
                payment.remaining,
                "the rail failed without answering; the amount is not the problem, so retry it",
                (*signals, "transient"),
            )
        return Plan(
            ESCALATE,
            None,
            "the rail keeps failing and this authority cannot retry again; a human should own it",
            (*signals, "budget-spent"),
        )

    if reason == "INSTRUMENT_DECLINED":
        limit = failure.get("instrument_limit")
        if limit is not None and float(limit) > 0 and float(limit) < payment.remaining:
            if authority.allow_partial_capture:
                return Plan(
                    PARTIAL_CAPTURE,
                    round(float(limit), 2),
                    f"the issuer will clear {float(limit):.2f} but not "
                    f"{payment.remaining:.2f}; take what is available and release the rest",
                    (*signals, "partial-available"),
                )
            return Plan(
                ESCALATE,
                None,
                "only part of the money is available and this authority requires a full capture",
                (*signals, "partial-not-permitted"),
            )
        return Plan(
            VOID,
            None,
            "the instrument will not clear any of it; release the commitment and tell the payer",
            (*signals, "nothing-available"),
        )

    if reason in {"AUTHORIZATION_EXPIRED", "EXPIRED"}:
        return Plan(
            VOID,
            None,
            "the authorisation is gone; release the commitment and ask for a new one",
            (*signals, "expired"),
        )

    return Plan(
        ESCALATE,
        None,
        f"unrecognised failure ({reason or status or 'no reason given'}); do not guess with money",
        (*signals, "unknown"),
    )


def apply(engine: Any, payment: Payment, chosen: Plan) -> list[Any]:
    """Execute a plan through the engine. Every step is checked and receipted there."""
    actions: list[Any] = []

    if chosen.action in {PARTIAL_CAPTURE, RETRY}:
        if chosen.action == PARTIAL_CAPTURE:
            action = engine.capture(payment, chosen.amount, actor="compensation-agent")
        else:
            action = engine.retry(payment, actor="compensation-agent")
        actions.append(action)
        if action.allowed and str(action.rail.get("status")) == "COMPLETED":
            if payment.remaining > 0.005:
                actions.append(engine.void(payment, actor="compensation-agent"))
            return actions
        # The plan did not work. Fall through to releasing the money rather than looping.
        if payment.open:
            actions.append(engine.void(payment, actor="compensation-agent"))
        return actions

    if chosen.action == VOID and payment.open:
        actions.append(engine.void(payment, actor="compensation-agent"))
    return actions
