"""Escrow for agent work: hold the money, verify the artefact, then release or void.

This is the module that turns the guard into a market. Two agents agree a price; the buyer's money
is *authorised* but not captured; the seller hands over an artefact; a verifier reads that artefact
against the acceptance criteria written into the work order; only then does money move.

PayPal's AUTHORIZE / CAPTURE / VOID sequence is an escrow primitive that predates agents, and it
is the right one for agent-to-agent work: authorise at agreement, capture on proof, void on
failure. The agent cannot release its own money, and the seller cannot take money without an
artefact. That is a market with a spine.

Honest note on what the verifier is: these are deterministic checks against a written acceptance
list, not a model opinion. Kept that way on purpose - "did the work arrive" is the one question
you do not want a persuasive model answering about itself.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Artefact:
    """What the seller actually handed over."""

    name: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def chars(self) -> int:
        return len(self.text.strip())

    def public(self) -> dict:
        return {"name": self.name, "chars": self.chars, "sha256": self.sha256[:16]}


@dataclass(frozen=True)
class Acceptance:
    """The acceptance criteria written into the work order *before* work starts.

    Deliberately concrete and checkable. A criterion you cannot test is a promise, not a term.
    """

    covers: tuple[str, ...] = ()
    min_chars: int = 0
    requires_source: bool = True

    def describe(self) -> str:
        parts = []
        if self.covers:
            parts.append("covers " + ", ".join(self.covers))
        if self.min_chars:
            parts.append(f"at least {self.min_chars} characters")
        if self.requires_source:
            parts.append("cites a source")
        return "  ·  ".join(parts)


@dataclass(frozen=True)
class Check:
    criterion: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Judgement:
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)


def check(acceptance: Acceptance, artefact: Artefact) -> Judgement:
    """Read the artefact against the order. Every criterion is checked, all are reported."""
    results: list[Check] = []

    for topic in acceptance.covers:
        # Word boundaries, and case-sensitive for short codes. A naive substring match passed a
        # stub because "BR" appears inside "brief" - exactly the kind of check that looks fine
        # until something is trying to get past it.
        flags = 0 if topic.isupper() else re.IGNORECASE
        found = re.search(
            rf"(?<![A-Za-z0-9]){re.escape(topic)}(?![A-Za-z0-9])", artefact.text, flags
        )
        results.append(
            Check(
                criterion=f"covers {topic}",
                passed=found is not None,
                detail="present" if found is not None else "not mentioned anywhere in the artefact",
            )
        )

    if acceptance.min_chars:
        long_enough = artefact.chars >= acceptance.min_chars
        results.append(
            Check(
                criterion=f"at least {acceptance.min_chars} characters",
                passed=long_enough,
                detail=f"{artefact.chars} characters",
            )
        )

    if acceptance.requires_source:
        cited = "http" in artefact.text.lower() or "source" in artefact.text.lower()
        results.append(
            Check(
                criterion="cites a source",
                passed=cited,
                detail="citation present" if cited else "no citation found",
            )
        )

    return Judgement(tuple(results))
