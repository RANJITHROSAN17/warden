"""The state machine, and the rules that bound what may happen in the gap.

Every operation here is an `Action`: attempted, checked, allowed or refused, and signed. The rules
are not about whether the *purchase* is a good idea - that decision was made before the money was
committed. They are about whether this particular move is inside the authority that committed it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from grace import receipts
from grace.models import (
    AUTHORISED,
    CAPTURED,
    EXPIRED,
    PART_CAPTURED,
    PART_RELEASED,
    VOIDED,
    Action,
    Authority,
    Check,
    Payee,
    Payment,
)
from grace.rail import PaymentRail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(moment: str) -> datetime:
    return datetime.fromisoformat(moment.replace("Z", "+00:00"))


class Engine:
    def __init__(
        self,
        ledger: Any,
        rail: PaymentRail,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.ledger = ledger
        self.rail = rail
        self.clock = clock

    # ── opening a payment ──────────────────────────────────────────────────────────────────
    def authorise(
        self,
        authority: Authority,
        payee: Payee,
        amount: float,
        purpose: str | None = None,
    ) -> Payment:
        """Commit money to a payee. Nothing is taken yet, and that is the point."""
        if amount > authority.ceiling:
            raise ValueError(f"{amount:.2f} exceeds the authority ceiling {authority.ceiling:.2f}")
        moment = self.clock()
        reference = f"{authority.id}:{payee.id}:{amount:.2f}"
        order = self.rail.create_order(amount, authority.currency, reference)
        authorization = self.rail.authorize(order["order_id"])

        payment = Payment(
            id=f"pay_{uuid.uuid4().hex[:10]}",
            authority_id=authority.id,
            payee=payee,
            amount=round(amount, 2),
            currency=authority.currency,
            purpose=purpose or authority.purpose,
            created_at=_iso(moment),
            expires_at=_iso(moment + timedelta(minutes=authority.ttl_minutes)),
            order_id=order["order_id"],
            authorization_id=authorization.get("authorization_id"),
        )
        self.ledger.save_authority(authority, payment.created_at)
        self.ledger.save_payment(payment)

        action = self._action(
            payment=payment,
            kind="authorise",
            amount=payment.amount,
            allowed=True,
            actor="agent",
            reason=f"authorised {payment.amount:.2f} {payment.currency} to {payee.name}",
            counterfactual=None,
            checks=(Check("authority.ceiling", True, f"inside {authority.ceiling:.2f}"),),
            rail={**order, **authorization},
        )
        self.ledger.add_transition(action)
        return payment

    # ── the operations inside the gap ─────────────────────────────────────────────────────
    def capture(
        self, payment: Payment, amount: float | None = None, actor: str = "agent"
    ) -> Action:
        """Take all or part of the money. Every refusal carries the reason and a fix."""
        authority = self._authority(payment)
        take = round(amount if amount is not None else payment.remaining, 2)
        checks = self._capture_checks(payment, authority, take)
        failed = [check for check in checks if not check.passed]
        if failed:
            return self._refuse(payment, "capture", take, actor, checks, failed)

        payment.attempts += 1
        rail = self.rail.capture(payment.authorization_id, take, payment.currency)
        status = str(rail.get("status") or "")
        if status != "COMPLETED":
            # Policy permitted it; the issuer did not. Both facts matter, so both are recorded.
            self.ledger.save_payment(payment)
            return self._action(
                payment=payment,
                kind="capture",
                amount=take,
                allowed=True,
                actor=actor,
                reason=(
                    f"issuer declined {take:.2f}: {rail.get('reason', status)}"
                    if status == "DECLINED"
                    else f"the rail failed on {take:.2f}: {rail.get('reason', status)}"
                ),
                counterfactual=None,
                checks=tuple(checks),
                rail=rail,
            )

        payment.captured = round(payment.captured + take, 2)
        payment.state = CAPTURED if payment.remaining <= 0.005 else PART_CAPTURED
        self.ledger.save_payment(payment)
        return self._action(
            payment=payment,
            kind="capture",
            amount=take,
            allowed=True,
            actor=actor,
            reason=f"captured {take:.2f} of {payment.amount:.2f}",
            counterfactual=None,
            checks=tuple(checks),
            rail=rail,
        )

    def void(self, payment: Payment, actor: str = "agent") -> Action:
        """Give the unspent commitment back. Nothing moved, so there is nothing to refund."""
        checks = self._open_checks(payment)
        failed = [check for check in checks if not check.passed]
        if failed:
            return self._refuse(payment, "void", payment.remaining, actor, checks, failed)

        rail = self.rail.void(payment.authorization_id)
        released = payment.remaining
        # If part of the money was already taken, saying "voided" would erase that fact.
        payment.state = PART_RELEASED if payment.captured > 0 else VOIDED
        self.ledger.save_payment(payment)
        return self._action(
            payment=payment,
            kind="void",
            amount=released,
            allowed=True,
            actor=actor,
            reason=f"released {released:.2f} {payment.currency}; it was never taken",
            counterfactual=None,
            checks=tuple(checks),
            rail=rail,
        )

    def retry(self, payment: Payment, actor: str = "agent") -> Action:
        """Try the remaining amount again, if the authority and the budget allow it."""
        authority = self._authority(payment)
        checks = (
            *self._open_checks(payment),
            Check(
                "authority.retry_permitted",
                authority.allow_retry,
                "this authority permits retries"
                if authority.allow_retry
                else "this authority does not permit retries",
            ),
            Check(
                "authority.attempt_budget",
                payment.attempts < authority.max_capture_attempts,
                f"{payment.attempts} of {authority.max_capture_attempts} attempts used",
            ),
            self._expiry_check(payment),
        )
        failed = [check for check in checks if not check.passed]
        if failed:
            return self._refuse(payment, "retry", payment.remaining, actor, checks, failed)
        return self.capture(payment, payment.remaining, actor=actor)

    def expire(self, payment: Payment, actor: str = "system") -> Action:
        """Close a payment whose authorisation has lapsed. Late is late; the money goes back."""
        late = _parse(payment.expires_at) <= self.clock()
        checks = (
            Check(
                "authorisation.expired",
                late,
                f"expired at {payment.expires_at}"
                if late
                else f"still valid until {payment.expires_at}",
            ),
        )
        if not late:
            return self._refuse(payment, "expire", None, actor, checks, list(checks))
        payment.state = EXPIRED
        self.ledger.save_payment(payment)
        return self._action(
            payment=payment,
            kind="expire",
            amount=payment.remaining,
            allowed=True,
            actor=actor,
            reason="authorisation lapsed; the commitment is released",
            counterfactual=None,
            checks=checks,
            rail={},
        )

    # ── rules ─────────────────────────────────────────────────────────────────────────────
    def _authority(self, payment: Payment) -> Authority:
        authority = self.ledger.get_authority(payment.authority_id)
        if authority is None:
            raise KeyError(f"no authority {payment.authority_id} for payment {payment.id}")
        return authority

    def _open_checks(self, payment: Payment) -> tuple[Check, ...]:
        return (
            Check(
                "payment.open",
                payment.open,
                f"payment is {payment.state}"
                if payment.open
                else f"payment is {payment.state}; there is nothing left to move",
            ),
            self._expiry_check(payment),
        )

    def _expiry_check(self, payment: Payment) -> Check:
        live = _parse(payment.expires_at) > self.clock()
        return Check(
            "authorisation.not_expired",
            live,
            f"valid until {payment.expires_at}" if live else f"expired at {payment.expires_at}",
        )

    def _capture_checks(
        self, payment: Payment, authority: Authority, amount: float
    ) -> tuple[Check, ...]:
        within = amount <= payment.remaining + 0.005
        partial = amount < payment.remaining - 0.005
        return (
            *self._open_checks(payment),
            Check(
                "capture.amount_valid",
                amount > 0,
                f"{amount:.2f} requested" if amount > 0 else "amount must be positive",
            ),
            Check(
                "capture.within_authorised",
                within,
                f"{amount:.2f} is inside the {payment.remaining:.2f} still authorised"
                if within
                else f"{amount:.2f} is more than the {payment.remaining:.2f} still authorised",
            ),
            Check(
                "authority.partial_permitted",
                (not partial) or authority.allow_partial_capture,
                "partial capture permitted"
                if authority.allow_partial_capture
                else "this authority requires a full capture",
            ),
            Check(
                "authority.attempt_budget",
                payment.attempts < authority.max_capture_attempts,
                f"{payment.attempts} of {authority.max_capture_attempts} attempts used",
            ),
        )

    # ── plumbing ──────────────────────────────────────────────────────────────────────────
    def _refuse(
        self,
        payment: Payment,
        kind: str,
        amount: float | None,
        actor: str,
        checks: tuple[Check, ...],
        failed: list[Check],
    ) -> Action:
        primary = failed[0]
        return self._action(
            payment=payment,
            kind=kind,
            amount=amount,
            allowed=False,
            actor=actor,
            reason=f"{primary.rule_id}: {primary.reason}",
            counterfactual=self._counterfactual(primary.rule_id, payment),
            checks=checks,
            rail={},
        )

    @staticmethod
    def _counterfactual(rule_id: str, payment: Payment) -> str:
        return {
            "payment.open": "this payment is already closed - open a new authorisation",
            "authorisation.not_expired": "a new authorisation is required; this one has lapsed",
            "capture.within_authorised": (
                f"capture at most {payment.remaining:.2f}, or authorise a larger amount"
            ),
            "authority.partial_permitted": "capture the full remaining amount",
            "authority.attempt_budget": "escalate to a human; the attempt budget is spent",
            "authority.retry_permitted": "void and ask the payer for a new authority",
            "authorisation.expired": "wait for the authorisation to lapse",
            "capture.amount_valid": "capture a positive amount",
        }.get(rule_id, "review by hand")

    def _action(
        self,
        payment: Payment,
        kind: str,
        amount: float | None,
        allowed: bool,
        actor: str,
        reason: str,
        counterfactual: str | None,
        checks: tuple[Check, ...],
        rail: dict[str, Any],
    ) -> Action:
        at = _iso(self.clock())
        payload = {
            "receipt_version": 1,
            "at": at,
            "kind": kind,
            "amount": amount,
            "allowed": allowed,
            "actor": actor,
            "reason": reason,
            "counterfactual": counterfactual,
            "checks": [check.public() for check in checks],
            "payment": payment.public(),
            "rail": rail,
        }
        action = Action(
            id=f"act_{uuid.uuid4().hex[:10]}",
            at=at,
            payment_id=payment.id,
            kind=kind,
            amount=amount,
            allowed=allowed,
            actor=actor,
            reason=reason,
            counterfactual=counterfactual,
            checks=checks,
            rail=rail,
            receipt=receipts.sign(payload),
        )
        self.ledger.add_transition(action)
        return action


__all__ = ["Engine", "AUTHORISED", "PART_CAPTURED", "CAPTURED", "VOIDED", "EXPIRED"]
