"""The gateway: one funnel through which every agent payment must pass.

Order matters and is deliberate.

    mandate -> attempt -> intent verdict -> rules -> (maybe) PayPal -> signed receipt

Nothing reaches the payment rail until the rules have passed, and nothing leaves without a
receipt. A refused purchase is recorded with the same care as a paid one, because "what did the
agent try to buy and why was it stopped" is the question a finance team actually asks.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from warden import receipts
from warden.intent import IntentJudge, default_judge
from warden.ledger import Ledger
from warden.models import (
    Decision,
    LineItem,
    Mandate,
    Outcome,
    PurchaseAttempt,
    Vendor,
)
from warden.paypal import PaymentRail, make_rail
from warden.policy import Grant, evaluate

RECEIPT_VERSION = "warden.receipt/1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class Gateway:
    def __init__(
        self,
        ledger: Ledger | None = None,
        rail: PaymentRail | None = None,
        judge: IntentJudge | None = None,
    ) -> None:
        self.ledger = ledger or Ledger()
        self.rail = rail or make_rail()
        self.judge = judge or default_judge()
        self._grants: dict[str, Grant] = {}
        self._authorizations: dict[str, str] = {}

    # ── authority ───────────────────────────────────────────────────────────────────────────
    def issue_mandate(
        self,
        *,
        purpose: str,
        ceiling: float,
        currency: str = "USD",
        categories: Sequence[str] = ("office_supplies",),
        min_vendor_age_days: int = 30,
        intent_threshold: float = 0.55,
        ttl_hours: int = 24,
        issued_by: str = "finance@example.com",
        allowed_vendors: Sequence[str] = (),
    ) -> Mandate:
        mandate = Mandate(
            id=f"mnd_{uuid.uuid4().hex[:10]}",
            purpose=purpose,
            ceiling=round(ceiling, 2),
            currency=currency,
            allowed_categories=tuple(categories),
            min_vendor_age_days=min_vendor_age_days,
            allowed_vendors=tuple(allowed_vendors),
            intent_threshold=intent_threshold,
            expires_at=_iso(_now() + timedelta(hours=ttl_hours)),
            issued_by=issued_by,
        )
        self.ledger.save_mandate(mandate, _iso(_now()))
        return mandate

    def grant(self, decision_id: str, issued_by: str, reason: str, ttl_minutes: int = 30) -> Grant:
        """Release exactly one held basket. Bound to its fingerprint; cannot be replayed."""
        receipt = self.ledger.receipt(decision_id)
        if receipt is None:
            raise KeyError(f"no decision {decision_id}")
        payload = receipt["payload"]
        failed = [rule["rule_id"] for rule in payload["rules"] if not rule["passed"]]
        if not failed:
            raise ValueError("nothing failed on this decision; no grant is needed")
        grant = Grant(
            id=f"grt_{uuid.uuid4().hex[:8]}",
            attempt_fingerprint=payload["attempt"]["fingerprint"],
            rule_id=failed[0],
            issued_by=issued_by,
            reason=reason,
            expires_at=_iso(_now() + timedelta(minutes=ttl_minutes)),
        )
        self._grants[grant.id] = grant
        return grant

    def grant_for_attempt(self, fingerprint: str) -> Grant | None:
        for grant in self._grants.values():
            if grant.attempt_fingerprint == fingerprint and _now() < datetime.fromisoformat(
                grant.expires_at.replace("Z", "+00:00")
            ):
                return grant
        return None

    # ── the funnel ──────────────────────────────────────────────────────────────────────────
    def attempt(
        self,
        mandate: Mandate,
        vendor: Vendor,
        items: Sequence[LineItem],
        stated_intent: str,
        *,
        grant: Grant | None = None,
    ) -> tuple[Decision, str]:
        attempt = PurchaseAttempt(
            id=f"att_{uuid.uuid4().hex[:10]}",
            mandate_id=mandate.id,
            vendor=vendor,
            items=tuple(items),
            stated_intent=stated_intent,
            at=_iso(_now()),
        )
        verdict = self.judge.judge(stated_intent, items)
        decision = evaluate(
            mandate,
            attempt,
            verdict,
            grant=grant if grant is not None else self.grant_for_attempt(attempt.fingerprint()),
            mode=self.rail.mode,
        )

        paypal: dict = {"vendor_id": vendor.id, "vendor_name": vendor.name, "mode": self.rail.mode}
        if decision.outcome is Outcome.ALLOW:
            order = self.rail.create_order(attempt.amount, mandate.currency, attempt.fingerprint())
            authorization = (
                self.rail.authorize(order["order_id"])
                if order.get("order_id")
                else {"authorization_id": None, "status": "NOT_AUTHORIZED"}
            )
            paypal = {**paypal, **order, **authorization}
            if authorization.get("authorization_id"):
                self._authorizations[attempt.id] = str(authorization["authorization_id"])

        receipt = self._sign(decision, mandate, attempt, paypal)
        decision = Decision(**{**decision.__dict__, "paypal": paypal, "receipt": receipt})
        decision_id = self.ledger.add_decision(decision, receipt)
        self._receipt_ids = getattr(self, "_receipt_ids", {})
        self._receipt_ids[decision_id] = attempt.id
        return decision, decision_id

    def _sign(
        self,
        decision: Decision,
        mandate: Mandate,
        attempt: PurchaseAttempt,
        paypal: dict,
    ) -> dict:
        payload = {
            "receipt_version": RECEIPT_VERSION,
            "at": decision.at,
            "mode": decision.mode,
            "outcome": decision.outcome.value,
            "mandate": mandate.public(),
            "attempt": attempt.public(),
            "rules": [rule.public() for rule in decision.rules],
            "intent": {
                "score": decision.intent.score,
                "evidence": decision.intent.evidence,
                "judge": decision.intent.judge,
            },
            "reasons": list(decision.reasons),
            "counterfactual": decision.counterfactual,
            "grant_id": decision.grant_id,
            "paypal": paypal,
        }
        return receipts.sign(payload)

    # ── after the fact ──────────────────────────────────────────────────────────────────────
    def capture(self, decision_id: str) -> dict:
        attempt_id = getattr(self, "_receipt_ids", {}).get(decision_id)
        authorization_id = self._authorizations.get(attempt_id or "")
        if not authorization_id:
            raise KeyError("this decision has no authorization to capture")
        return self.rail.capture(authorization_id)

    def void(self, decision_id: str) -> dict:
        attempt_id = getattr(self, "_receipt_ids", {}).get(decision_id)
        authorization_id = self._authorizations.get(attempt_id or "")
        if not authorization_id:
            raise KeyError("this decision has no authorization to void")
        return self.rail.void(authorization_id)
