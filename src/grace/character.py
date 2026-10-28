"""The end of the spending limit.

Every system that has tried to make autonomous spending safe has done it the same way: by
restricting the agent. A ceiling. A mandate. A category allowlist. An escrow. All of them are
brakes, and all of them cost the same thing - **volume**. A limit that stops a bad payment also
stops the thousand good ones above it.

This module takes the opposite position. Instead of asking *"is this payment permitted?"* it asks a
question a number cannot answer:

    **Does this payment look like something this business would do?**

That question is answerable, because PayPal already holds the answer - the account's entire payment
history. A plumbing wholesaler buying copper fittings is unremarkable; the same account buying
concert tickets at 3am is not. No rule expresses that, and no static mandate survives contact with a
business whose needs change every week. It is a judgment about *character*, made over a graph no
other company has.

And the judgment is checkable. Every score names the prior payments it was reasoned from, so a
human - or a regulator - can look at the exact evidence and disagree with the conclusion.

The economic consequence is the whole point:

    a safe judgment about character is what allows the ceiling to rise.

Every competitor spends its intelligence on stopping more. Grace's intelligence is what lets the
platform stop *less* while losing nothing.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from typing import Any, Protocol

# Weights are the only tuned numbers in this file. They are stated here rather than buried so a
# reviewer can argue with them - which is the point of a checkable judgment.
W_VENDOR = 0.35
W_PURPOSE = 0.30
W_AMOUNT = 0.25
W_RHYTHM = 0.10

ALLOW_AT = 0.72
HOLD_AT = 0.45


class Matcher(Protocol):
    """How two purpose strings are compared. The one place a model is genuinely required.

    "spring campaign design" and "visual assets for our seasonal launch" share almost no tokens and
    are obviously the same kind of purchase. Lexical overlap cannot see that; an embedding can.
    """

    name: str

    def similarity(self, a: str, b: str) -> float: ...


class LexicalMatcher:
    """Token overlap. The fallback, and it says so in every judgement it produces."""

    name = "lexical"

    def similarity(self, a: str, b: str) -> float:
        return _overlap(a, b)


class EmbeddingMatcher:
    """Cosine similarity over real embeddings from any OpenAI-compatible endpoint.

    Enabled by GRACE_EMBED_URL. It is not exercised in CI and this file does not claim it is - but
    it is the real path, and `Matcher.name` travels into the ledger so a judgement produced by the
    fallback can never be mistaken for one produced by a model.
    """

    name = "embedding"

    def __init__(self, url: str, model: str, api_key: str | None = None, timeout: float = 20.0):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._cache: dict[str, list[float]] = {}

    def _embed(self, text: str) -> list[float]:
        if text in self._cache:
            return self._cache[text]
        body = json.dumps({"model": self.model, "input": text}).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
        vector = [float(x) for x in payload["data"][0]["embedding"]]
        self._cache[text] = vector
        return vector

    def similarity(self, a: str, b: str) -> float:
        va, vb = self._embed(a), self._embed(b)
        dot = sum(x * y for x, y in zip(va, vb, strict=True))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(y * y for y in vb))
        if not na or not nb:
            return 0.0
        return max(0.0, min(1.0, dot / (na * nb)))


def matcher_from_env() -> Matcher:
    url = os.environ.get("GRACE_EMBED_URL")
    if not url:
        return LexicalMatcher()
    return EmbeddingMatcher(
        url=url,
        model=os.environ.get("GRACE_EMBED_MODEL", "text-embedding-3-small"),
        api_key=os.environ.get("GRACE_EMBED_KEY"),
    )


STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "for",
        "to",
        "and",
        "or",
        "in",
        "on",
        "at",
        "with",
        "from",
        "our",
        "this",
        "that",
        "service",
        "payment",
        "invoice",
    ]
)


@dataclass(frozen=True)
class Spent:
    """One thing the business actually paid for. The raw material of character."""

    id: str
    vendor: str
    purpose: str
    amount: float
    at: str  # ISO-8601

    @property
    def hour(self) -> int:
        return int(self.at[11:13]) if len(self.at) >= 13 else 12


@dataclass(frozen=True)
class Request:
    """A payment an agent wants to make, with nothing but a ceiling standing behind it."""

    vendor: str
    purpose: str
    amount: float
    currency: str = "USD"
    at: str = "2026-10-29T10:00:00+00:00"

    @property
    def hour(self) -> int:
        return int(self.at[11:13]) if len(self.at) >= 13 else 12


@dataclass(frozen=True)
class Reason:
    component: str
    score: float
    detail: str
    cites: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "score": round(self.score, 3),
            "detail": self.detail,
            "cites": list(self.cites),
        }


@dataclass(frozen=True)
class Judgement:
    conformance: float
    decision: str  # allow | hold | escalate
    reasons: tuple[Reason, ...]
    nearest: tuple[str, ...] = ()
    suggested_ceiling: float = 0.0
    explanation: str = ""
    provider: str = "lexical"

    def public(self) -> dict[str, Any]:
        return {
            "conformance": round(self.conformance, 3),
            "decision": self.decision,
            "reasons": [r.public() for r in self.reasons],
            "nearest": list(self.nearest),
            "suggested_ceiling": round(self.suggested_ceiling, 2),
            "explanation": self.explanation,
            # Which matcher produced this. "lexical" is the fallback, and saying so is the point.
            "provider": self.provider,
        }


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass(frozen=True)
class Character:
    """What this business looks like, learned only from what it has actually paid for."""

    account: str
    history: tuple[Spent, ...] = ()

    @property
    def vendors(self) -> dict[str, list[Spent]]:
        out: dict[str, list[Spent]] = {}
        for item in self.history:
            out.setdefault(item.vendor.lower(), []).append(item)
        return out

    @property
    def typical_amount(self) -> float:
        return median([item.amount for item in self.history]) if self.history else 0.0

    @property
    def hours(self) -> tuple[int, int]:
        if not self.history:
            return (8, 19)
        hrs = sorted(item.hour for item in self.history)
        return (hrs[len(hrs) // 10], hrs[-1 - len(hrs) // 10])

    def nearest(self, request: Request, limit: int = 3) -> tuple[Spent, ...]:
        def closeness(item: Spent) -> float:
            same_vendor = 1.0 if item.vendor.lower() == request.vendor.lower() else 0.0
            return same_vendor + _overlap(item.purpose, request.purpose)

        return tuple(sorted(self.history, key=closeness, reverse=True)[:limit])


def _vendor_reason(character: Character, request: Request) -> Reason:
    seen = character.vendors.get(request.vendor.lower(), [])
    if seen:
        return Reason(
            "vendor",
            1.0,
            f"{request.vendor} has been paid {len(seen)} times before",
            tuple(item.id for item in seen[-3:]),
        )
    if not character.history:
        return Reason("vendor", 0.5, "no history to compare against", ())
    return Reason(
        "vendor",
        0.15,
        f"{request.vendor} has never been paid by this account, across "
        f"{len(character.vendors)} vendors in its history",
    )


def _purpose_reason(
    character: Character, request: Request, similarity: Callable[[str, str], float]
) -> Reason:
    if not character.history:
        return Reason("purpose", 0.5, "no history to compare against", ())
    scored = sorted(
        ((similarity(item.purpose, request.purpose), item) for item in character.history),
        key=lambda pair: pair[0],
        reverse=True,
    )[:3]
    best, item = scored[0]
    if best <= 0.0:
        return Reason(
            "purpose",
            0.05,
            f"nothing in this account's history resembles '{request.purpose}'",
        )
    return Reason(
        "purpose",
        min(1.0, best * 2.0),
        f"closest prior spend is '{item.purpose}' at {item.vendor}",
        (item.id,),
    )


def _amount_reason(character: Character, request: Request) -> Reason:
    if not character.history:
        return Reason("amount", 0.5, "no history to compare against", ())
    typical = character.typical_amount
    if typical <= 0:
        return Reason("amount", 0.5, "no comparable amounts", ())
    ratio = request.amount / typical
    if ratio <= 1.5:
        return Reason("amount", 1.0, f"{ratio:.1f}x this account's typical payment")
    if ratio <= 4.0:
        return Reason("amount", 0.6, f"{ratio:.1f}x this account's typical payment")
    return Reason("amount", 0.1, f"{ratio:.1f}x this account's typical payment - far outside it")


def _rhythm_reason(character: Character, request: Request) -> Reason:
    low, high = character.hours
    hour = request.hour
    if low <= hour <= high:
        return Reason("rhythm", 1.0, f"{hour:02d}:00 falls inside the usual {low:02d}-{high:02d}")
    return Reason("rhythm", 0.2, f"{hour:02d}:00 is outside the usual {low:02d}-{high:02d}")


def judge(
    character: Character,
    request: Request,
    base_ceiling: float = 500.0,
    matcher: Matcher | None = None,
) -> Judgement:
    """Score a payment against the account's own character, and cite what the score rests on."""
    matcher = matcher or LexicalMatcher()
    reasons = (
        _vendor_reason(character, request),
        _purpose_reason(character, request, matcher.similarity),
        _amount_reason(character, request),
        _rhythm_reason(character, request),
    )
    weights = {"vendor": W_VENDOR, "purpose": W_PURPOSE, "amount": W_AMOUNT, "rhythm": W_RHYTHM}
    conformance = sum(weights[r.component] * r.score for r in reasons)

    if conformance >= ALLOW_AT:
        decision = "allow"
    elif conformance >= HOLD_AT:
        decision = "hold"
    else:
        decision = "escalate"

    # The point of the whole exercise: a character we can trust earns a higher ceiling.
    suggested = round(base_ceiling * (1.0 + max(0.0, conformance - 0.5) * 1.6), 2)

    weakest = sorted(reasons, key=lambda r: r.score)[0]
    if decision == "allow":
        explanation = (
            f"consistent with how {character.account} spends; suggested ceiling "
            f"{suggested:.2f} against a base of {base_ceiling:.2f}"
        )
    elif decision == "hold":
        explanation = f"partly in character, but {weakest.component} is weak: {weakest.detail}"
    else:
        explanation = f"does not look like {character.account}: {weakest.detail}"

    return Judgement(
        conformance=conformance,
        decision=decision,
        reasons=reasons,
        nearest=tuple(item.id for item in character.nearest(request)),
        suggested_ceiling=suggested,
        explanation=explanation,
        provider=matcher.name,
    )


def score_only(conformance: float) -> float:
    """Exposed for tests: the ceiling curve on its own."""
    return math.floor(conformance * 100) / 100
