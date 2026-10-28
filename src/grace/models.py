"""The domain: an authority, a payment, and the window between authorise and capture.

A payment is not one event. It is at least three: a commitment, a gap, and a settlement. Most of
the money that goes wrong goes wrong in the gap.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

# States a payment can be in. `authorised` and `part_captured` are the open window.
# The order exists but nobody has approved it, so nothing is authorised yet. This state exists
# because PayPal requires buyer approval between create_order and authorize; skipping it is how a
# sandbox integration returns ORDER_NOT_APPROVED the first time it meets the real API.
PENDING_APPROVAL = "pending_approval"
AUTHORISED = "authorised"
PART_CAPTURED = "part_captured"
CAPTURED = "captured"
VOIDED = "voided"
EXPIRED = "expired"
# Part taken, part handed back. The most common honest ending, and the one dashboards miss.
PART_RELEASED = "part_released"

OPEN_STATES = (PENDING_APPROVAL, AUTHORISED, PART_CAPTURED)


@dataclass(frozen=True)
class Authority:
    """What one agent is allowed to commit, and what it may do once money is committed.

    Deliberately small: a purpose, a ceiling, and the compensation powers. The interesting half is
    the second one - whether the agent may take a partial amount, retry, or must hand back to a
    human. That is what bounds the decisions made in the gap.
    """

    id: str
    purpose: str
    ceiling: float
    currency: str = "USD"
    allow_partial_capture: bool = True
    allow_retry: bool = True
    max_capture_attempts: int = 3
    ttl_minutes: int = 30
    # Character assessment. Off by default so existing authorities behave exactly as before; turn
    # it on to make a judgement about the business part of authorisation rather than decoration.
    require_character: bool = False
    min_conformance: float = 0.55

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Payee:
    id: str
    name: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Payment:
    """One commitment of money to one payee."""

    id: str
    authority_id: str
    payee: Payee
    amount: float
    currency: str
    purpose: str
    created_at: str
    expires_at: str
    state: str = AUTHORISED
    captured: float = 0.0
    attempts: int = 0
    order_id: str | None = None
    approval_url: str | None = None
    authorization_id: str | None = None
    # Every declaration the agent made about what it was doing, in order.
    notes: list[str] = field(default_factory=list)

    @property
    def open(self) -> bool:
        return self.state in OPEN_STATES

    @property
    def remaining(self) -> float:
        """Authorised money not yet taken. This is the money at risk in the gap."""
        return round(self.amount - self.captured, 2)

    def public(self) -> dict[str, Any]:
        body = asdict(self)
        body["remaining"] = self.remaining
        body["open"] = self.open
        return body


@dataclass(frozen=True)
class Check:
    """One rule, evaluated. `passed` plus the reason a reader can act on."""

    rule_id: str
    passed: bool
    reason: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Action:
    """An attempt to move a payment forward, and the answer.

    Refusals are first-class: they carry the same checks and the same signed receipt as a
    success, because "why didn't this capture" is the question that ends up in a support ticket.
    """

    id: str
    at: str
    payment_id: str
    kind: str
    amount: float | None
    allowed: bool
    actor: str
    reason: str
    counterfactual: str | None
    checks: tuple[Check, ...]
    rail: dict[str, Any] = field(default_factory=dict)
    receipt: dict[str, Any] = field(default_factory=dict)

    @property
    def failed_rules(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "payment_id": self.payment_id,
            "kind": self.kind,
            "amount": self.amount,
            "allowed": self.allowed,
            "actor": self.actor,
            "reason": self.reason,
            "counterfactual": self.counterfactual,
            "checks": [check.public() for check in self.checks],
            "rail": self.rail,
        }

    def fingerprint(self) -> str:
        body = json.dumps(self.public(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()[:32]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
