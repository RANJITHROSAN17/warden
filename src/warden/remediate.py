"""An agent that reads its own refusal and acts on it.

This is what makes the AI an agent rather than a classifier. When Warden holds a payment the
decision comes back with the rule that failed and the smallest change that would release it - a
*counterfactual*, which exists precisely so a program can read it.

So the agent reads it. It drops what is out of scope, trims units until the basket fits the
ceiling, or restates an intent that did not match. Then it re-submits. Nothing is bypassed: the
second attempt goes through the same rules, and a hold that cannot be remediated ends the loop
honestly instead of wearing the gate down.
"""

from __future__ import annotations

from dataclasses import dataclass

from warden.models import Decision, LineItem, Mandate, Outcome

MAX_ATTEMPTS = 3

# Refusals the agent must never "fix" by itself. Each needs a human, a new mandate, or an
# artefact that does not exist - and an agent that rephrases its way past them is the failure.
UNFIXABLE = frozenset({"vendor.approved", "vendor.maturity", "mandate.expiry", "delivery.evidence"})


@dataclass(frozen=True)
class Revision:
    """One attempt at fixing a refusal, and why it was chosen.

    `stated_intent` is only set when the original statement was inaccurate and the agent is
    correcting it. That is a claim the agent can abuse, which is why every attempt keeps its own
    signed receipt: the chain shows the intent change, so a reviewer sees the rewrite rather than
    a settled fact. The receipt chain is the evidence, not any single receipt.
    """

    attempt: int
    action: str
    items: tuple[LineItem, ...]
    explanation: str
    stated_intent: str | None = None


def _failed_rules(decision: Decision) -> set[str]:
    return {rule.rule_id for rule in decision.rules if not rule.passed}


def revise(
    decision: Decision,
    mandate: Mandate,
    items: tuple[LineItem, ...],
    attempt: int = 1,
) -> Revision | None:
    """Return a revised basket, or None when the agent should stop and ask a human."""
    failed = _failed_rules(decision)
    if failed & UNFIXABLE:
        return None

    # Fix in the order a careful human would: drop what was never allowed, then fit the budget.
    # Doing it the other way round trims a compliant basket while the reason it was too big is
    # still sitting in it.
    revised = list(items)
    actions: list[str] = []
    notes: list[str] = []

    if "category.scope" in failed:
        allowed = set(mandate.allowed_categories)
        dropped = sorted({i.category for i in revised if i.category not in allowed})
        revised = [i for i in revised if i.category in allowed]
        if dropped:
            actions.append("drop-out-of-scope")
            notes.append(f"dropped {', '.join(dropped)}")

    if round(sum(i.total for i in revised), 2) > mandate.ceiling:
        while revised and round(sum(i.total for i in revised), 2) > mandate.ceiling:
            cheapest = min(range(len(revised)), key=lambda index: revised[index].unit_price)
            entry = revised[cheapest]
            if entry.qty > 1:
                revised[cheapest] = LineItem(
                    entry.sku,
                    entry.description,
                    entry.category,
                    entry.qty - 1,
                    entry.unit_price,
                    entry.evidence,
                )
            else:
                revised.pop(cheapest)
        if revised:
            actions.append("trim-to-ceiling")
            notes.append(
                f"trimmed to {round(sum(i.total for i in revised), 2):.2f} {mandate.currency}"
                f" inside the {mandate.ceiling:.2f} ceiling"
            )

    if revised and actions:
        return Revision(attempt, "+".join(actions), tuple(revised), "; ".join(notes))

    if not revised:
        return None

    if "intent.binding" in failed:
        described = "restock " + "; ".join(item.description.lower() for item in items)
        return Revision(
            attempt,
            "restate-intent",
            items,
            f"restated the reason as what is actually being bought: {described}",
            stated_intent=described,
        )

    return None


@dataclass(frozen=True)
class LoopResult:
    decisions: tuple[tuple[Decision, str], ...]
    revisions: tuple[Revision, ...]
    stopped_because: str

    @property
    def final(self) -> Decision:
        return self.decisions[-1][0]


def run(gateway, mandate: Mandate, vendor_obj, items, stated_intent: str) -> LoopResult:
    """Attempt, read the refusal, revise, retry - at most `MAX_ATTEMPTS` times."""
    decisions: list[tuple[Decision, str]] = []
    revisions: list[Revision] = []

    decision, decision_id = gateway.attempt(mandate, vendor_obj, items, stated_intent)
    decisions.append((decision, decision_id))
    current: tuple[LineItem, ...] = tuple(items)

    attempt = 0
    while decision.outcome is Outcome.HOLD and attempt < MAX_ATTEMPTS:
        attempt += 1
        revision = revise(decision, mandate, current, attempt)
        if revision is None:
            return LoopResult(
                tuple(decisions),
                tuple(revisions),
                "cannot be fixed by the agent: needs a human, an artefact, or a new mandate",
            )
        revisions.append(revision)
        current = revision.items
        if revision.stated_intent:
            stated_intent = revision.stated_intent
        decision, decision_id = gateway.attempt(mandate, vendor_obj, current, stated_intent)
        decisions.append((decision, decision_id))

    stopped = (
        "remediated inside the mandate"
        if decision.outcome is Outcome.ALLOW
        else "attempt budget exhausted"
    )
    return LoopResult(tuple(decisions), tuple(revisions), stopped)
