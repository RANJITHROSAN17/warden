"""Check a deliverable against a brief, so payment rests on a fact instead of a claim.

The split that makes this honest:

  * **A model writes the brief.** Turning "get me some product copy" into acceptance criteria is the
    judgment call, and it is where the ambiguity lives.
  * **A checker executes the brief.** Once the criteria are explicit, most of them are mechanical -
    a count, a word range, a required phrase. A rule is the correct tool for that, because a rule
    can be shown to a buyer and re-run by anyone.

So the agent is not asked "was this good?" - a question with no answer. It is asked "does this meet
the criteria we both agreed to?" - a question with evidence.

Two refusals are built in:

  * a criterion that cannot be machine-checked makes the whole verdict **escalate**; the agent does
    not guess on a criterion it cannot test;
  * partial payment requires a criterion that can measure how much of the work was done. Without
    one, a partial delivery escalates rather than inventing a percentage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Criterion:
    id: str
    label: str
    kind: str  # count | min_words | max_words | mentions | manual
    target: str | float
    pro_rata: bool = False  # can this criterion measure how much of the work was done?


@dataclass(frozen=True)
class Brief:
    id: str
    title: str
    criteria: tuple[Criterion, ...]
    allow_partial: bool = True


@dataclass(frozen=True)
class Deliverable:
    text: str
    items: tuple[str, ...] = ()
    author: str = "seller-agent"

    @property
    def words(self) -> list[int]:
        return [len(item.split()) for item in self.items]


@dataclass(frozen=True)
class Finding:
    criterion: str
    label: str
    satisfied: bool
    note: str
    coverage: float = 1.0

    def public(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "label": self.label,
            "satisfied": self.satisfied,
            "note": self.note,
            "coverage": round(self.coverage, 3),
        }


@dataclass(frozen=True)
class Verdict:
    brief_id: str
    findings: tuple[Finding, ...]
    decision: str  # capture_full | capture_partial | release | escalate
    fraction: float
    explanation: str
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def met(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.satisfied)

    @property
    def unmet(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.satisfied)

    def public(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id,
            "decision": self.decision,
            "fraction": round(self.fraction, 3),
            "explanation": self.explanation,
            "findings": [f.public() for f in self.findings],
            "met": [f.criterion for f in self.met],
            "unmet": [f.criterion for f in self.unmet],
            **self.extra,
        }


def _check(criterion: Criterion, deliverable: Deliverable) -> Finding:
    count = len(deliverable.items)
    words = deliverable.words

    if criterion.kind == "manual":
        return Finding(
            criterion.id,
            criterion.label,
            False,
            "cannot be checked mechanically - a human has to look at this",
            0.0,
        )

    if criterion.kind == "count":
        target = int(float(criterion.target))
        ok = count >= target
        coverage = min(1.0, count / target) if target else 1.0
        return Finding(
            criterion.id,
            criterion.label,
            ok,
            f"{count} of {target} delivered",
            coverage,
        )

    if criterion.kind == "min_words":
        target = int(float(criterion.target))
        short = [i + 1 for i, w in enumerate(words) if w < target]
        return Finding(
            criterion.id,
            criterion.label,
            not short,
            f"all {count} items at least {target} words"
            if not short
            else f"items {short} are short",
            1.0 if not short else (count - len(short)) / count if count else 0.0,
        )

    if criterion.kind == "max_words":
        target = int(float(criterion.target))
        long_ = [i + 1 for i, w in enumerate(words) if w > target]
        return Finding(
            criterion.id,
            criterion.label,
            not long_,
            f"all {count} items at most {target} words" if not long_ else f"items {long_} overrun",
            1.0 if not long_ else (count - len(long_)) / count if count else 0.0,
        )

    if criterion.kind == "mentions":
        needle = str(criterion.target).lower()
        missing = [i + 1 for i, item in enumerate(deliverable.items) if needle not in item.lower()]
        return Finding(
            criterion.id,
            criterion.label,
            not missing,
            f"every item names {criterion.target}"
            if not missing
            else f"items {missing} never name {criterion.target}",
            1.0 if not missing else (count - len(missing)) / count if count else 0.0,
        )

    return Finding(criterion.id, criterion.label, False, f"unknown check {criterion.kind!r}", 0.0)


def verify(brief: Brief, deliverable: Deliverable) -> Verdict:
    findings = tuple(_check(c, deliverable) for c in brief.criteria)

    if any(c.kind == "manual" for c in brief.criteria):
        return Verdict(
            brief.id,
            findings,
            "escalate",
            0.0,
            "the brief contains a criterion that cannot be checked mechanically. Grace does "
            "not pay or refuse on a guess - this one goes to a human.",
        )

    if all(f.satisfied for f in findings):
        return Verdict(
            brief.id,
            findings,
            "capture_full",
            1.0,
            f"all {len(findings)} criteria met against the brief.",
        )

    basis = next(
        (
            f
            for f, c in zip(findings, brief.criteria, strict=True)
            if c.pro_rata and not f.satisfied and f.coverage > 0
        ),
        None,
    )
    if not brief.allow_partial or basis is None:
        return Verdict(
            brief.id,
            findings,
            "release",
            0.0,
            "the brief was not met and no criterion measures how much of it was delivered, so "
            "nothing is captured. Money goes back to the buyer.",
        )

    return Verdict(
        brief.id,
        findings,
        "capture_partial",
        basis.coverage,
        f"{basis.note}. Paying for the work that was done, releasing the rest.",
    )
