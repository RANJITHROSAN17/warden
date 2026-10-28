"""Core types.

Everything a decision is made from is frozen. A receipt has to be reconstructable months later
from data that cannot have been quietly edited in between.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    """Three outcomes, not two.

    `HOLD` exists because the honest answer to most questionable spending is not "no" - it is
    "not without a human". Collapsing that into `DENY` teaches people to raise ceilings until
    nothing is ever held, which is how card limits already fail.
    """

    ALLOW = "allow"
    HOLD = "hold"
    DENY = "deny"


def canonical_json(value: Any) -> str:
    """The one serialisation used for hashing and signing. Sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Vendor:
    """What the payer knows about who it is paying."""

    id: str
    name: str
    age_days: int
    country: str
    approved: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "age_days": self.age_days,
            "country": self.country,
            "approved": self.approved,
        }


@dataclass(frozen=True)
class LineItem:
    sku: str
    description: str
    category: str
    qty: int
    unit_price: float

    @property
    def total(self) -> float:
        return round(self.qty * self.unit_price, 2)

    def public(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "description": self.description,
            "category": self.category,
            "qty": self.qty,
            "unit_price": self.unit_price,
            "total": self.total,
        }


@dataclass(frozen=True)
class Mandate:
    """A bounded, expiring, signed permission to spend.

    The mandate is the unit of authority. It is deliberately narrow: one purpose, one ceiling,
    one category scope, one expiry. An agent cannot pay without one, so "what was this agent
    allowed to do on Tuesday" always has an answer that is not a Slack message.
    """

    id: str
    purpose: str
    ceiling: float
    currency: str
    allowed_categories: tuple[str, ...]
    min_vendor_age_days: int = 0
    allowed_vendors: tuple[str, ...] = ()
    intent_threshold: float = 0.55
    expires_at: str = ""
    issued_by: str = ""

    def fingerprint(self) -> str:
        return sha256_of(
            {
                "id": self.id,
                "purpose": self.purpose,
                "ceiling": self.ceiling,
                "currency": self.currency,
                "allowed_categories": list(self.allowed_categories),
                "min_vendor_age_days": self.min_vendor_age_days,
                "allowed_vendors": list(self.allowed_vendors),
                "intent_threshold": self.intent_threshold,
                "expires_at": self.expires_at,
                "issued_by": self.issued_by,
            }
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "purpose": self.purpose,
            "ceiling": self.ceiling,
            "currency": self.currency,
            "allowed_categories": list(self.allowed_categories),
            "min_vendor_age_days": self.min_vendor_age_days,
            "allowed_vendors": list(self.allowed_vendors),
            "intent_threshold": self.intent_threshold,
            "expires_at": self.expires_at,
            "issued_by": self.issued_by,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class PurchaseAttempt:
    """What the agent wants to do, in its own words and in structured form."""

    id: str
    mandate_id: str
    vendor: Vendor
    items: tuple[LineItem, ...]
    stated_intent: str
    at: str

    @property
    def amount(self) -> float:
        return round(sum(item.total for item in self.items), 2)

    def fingerprint(self) -> str:
        return sha256_of(
            {
                "mandate_id": self.mandate_id,
                "vendor": self.vendor.id,
                "items": [item.public() for item in self.items],
                "stated_intent": self.stated_intent,
            }
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mandate_id": self.mandate_id,
            "vendor": self.vendor.public(),
            "items": [item.public() for item in self.items],
            "stated_intent": self.stated_intent,
            "amount": self.amount,
            "at": self.at,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class IntentVerdict:
    """Does the basket match the reason the agent gave?

    `judge` names the thing that produced the score. A lexical mock and a model produce the same
    number in the same field, so the receipt never implies more rigour than was applied.
    """

    score: float
    evidence: str
    judge: str


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    detail: str
    counterfactual: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "passed": self.passed,
            "detail": self.detail,
            "counterfactual": self.counterfactual,
        }


@dataclass(frozen=True)
class Decision:
    attempt_id: str
    mandate_id: str
    outcome: Outcome
    amount: float
    currency: str
    rules: tuple[RuleResult, ...]
    intent: IntentVerdict
    reasons: tuple[str, ...]
    counterfactual: str | None
    at: str
    mode: str
    grant_id: str | None = None
    paypal: dict[str, Any] = field(default_factory=dict)
    receipt: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "mandate_id": self.mandate_id,
            "outcome": self.outcome.value,
            "amount": self.amount,
            "currency": self.currency,
            "rules": [rule.public() for rule in self.rules],
            "intent": {
                "score": self.intent.score,
                "evidence": self.intent.evidence,
                "judge": self.intent.judge,
            },
            "reasons": list(self.reasons),
            "counterfactual": self.counterfactual,
            "at": self.at,
            "mode": self.mode,
            "grant_id": self.grant_id,
            "paypal": self.paypal,
            "receipt": self.receipt,
        }
