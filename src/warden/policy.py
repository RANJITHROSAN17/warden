"""The rule engine.

Rules are evaluated in a fixed order and every one of them reports, pass or fail, with a sentence
a human can read. That matters more than it sounds: a hold that says "policy violation" trains
people to click approve without reading, which is the failure this whole project exists to avoid.

Two design decisions worth defending:

* `HOLD` is the default for anything a human could plausibly grant, and `DENY` is reserved for
  structural impossibilities - an expired mandate, a vendor that does not exist. Anything else
  gets a human, not a wall.
* Every failure carries a **counterfactual**: the smallest change that would make it pass. A
  refusal that cannot be acted on is indistinguishable from a bug.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from warden.models import (
    Decision,
    IntentVerdict,
    Mandate,
    Outcome,
    PurchaseAttempt,
    RuleResult,
)

# Failures here mean "this mandate cannot authorise anything like this", not "ask someone".
HARD_RULES = frozenset({"mandate.expiry"})


@dataclass(frozen=True)
class Grant:
    """A one-time permission for exactly one purchase.

    Not a raised limit. A grant is bound to the fingerprint of the basket that was held, so it
    cannot be reused for the next thing the agent wants to buy. Raising a ceiling is a permanent
    widening; approving one purchase is not.
    """

    id: str
    attempt_fingerprint: str
    rule_id: str
    issued_by: str
    reason: str
    expires_at: str

    def is_valid(self, attempt: PurchaseAttempt, now: datetime) -> bool:
        if self.attempt_fingerprint != attempt.fingerprint():
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return now < expiry


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def evaluate(
    mandate: Mandate,
    attempt: PurchaseAttempt,
    intent: IntentVerdict,
    *,
    now: datetime | None = None,
    grant: Grant | None = None,
    mode: str = "simulated",
) -> Decision:
    """Evaluate one purchase attempt against one mandate."""
    now = now or datetime.now(timezone.utc)
    rules: list[RuleResult] = []

    # 1. Is the mandate still alive?
    expired = now >= _parse(mandate.expires_at) if mandate.expires_at else False
    rules.append(
        RuleResult(
            rule_id="mandate.expiry",
            passed=not expired,
            detail=(
                f"mandate expired at {mandate.expires_at}"
                if expired
                else f"mandate is live until {mandate.expires_at}"
            ),
            counterfactual="issue a new mandate for this purpose" if expired else None,
        )
    )

    # 2. Ceiling.
    over = round(attempt.amount - mandate.ceiling, 2)
    rules.append(
        RuleResult(
            rule_id="mandate.ceiling",
            passed=over <= 0,
            detail=(
                f"{attempt.amount:.2f} {mandate.currency} is {over:.2f} over the "
                f"{mandate.ceiling:.2f} ceiling"
                if over > 0
                else f"{attempt.amount:.2f} of {mandate.ceiling:.2f} {mandate.currency} used"
            ),
            counterfactual=(
                f"remove {over:.2f} {mandate.currency} of goods, or approve this one purchase"
                if over > 0
                else None
            ),
        )
    )

    # 3. Is this vendor known and approved?
    approved = attempt.vendor.approved or attempt.vendor.id in mandate.allowed_vendors
    rules.append(
        RuleResult(
            rule_id="vendor.approved",
            passed=approved,
            detail=(
                f"vendor {attempt.vendor.id} is not an approved payee"
                if not approved
                else f"vendor {attempt.vendor.id} is approved"
            ),
            counterfactual=(
                f"add {attempt.vendor.id} to this mandate's allowed vendors"
                if not approved
                else None
            ),
        )
    )

    # 4. How old is the vendor? New payees are where agent fraud actually lands.
    young = attempt.vendor.age_days < mandate.min_vendor_age_days
    rules.append(
        RuleResult(
            rule_id="vendor.maturity",
            passed=not young or approved,
            detail=(
                f"vendor is {attempt.vendor.age_days} days old; mandate requires "
                f"{mandate.min_vendor_age_days}"
                if young and not approved
                else f"vendor age {attempt.vendor.age_days}d clears the maturity floor"
            ),
            counterfactual=(
                f"wait {mandate.min_vendor_age_days - attempt.vendor.age_days} more days, or "
                f"approve {attempt.vendor.id} explicitly"
                if young and not approved
                else None
            ),
        )
    )

    # 5. Category scope - the cheapest way an agent wanders off-purpose.
    allowed = set(mandate.allowed_categories)
    out_of_scope = sorted({item.category for item in attempt.items if item.category not in allowed})
    rules.append(
        RuleResult(
            rule_id="category.scope",
            passed=not out_of_scope,
            detail=(
                f"categories outside scope: {', '.join(out_of_scope)}"
                if out_of_scope
                else f"all categories within {sorted(allowed)}"
            ),
            counterfactual=(
                f"remove {', '.join(out_of_scope)} items or widen the mandate's scope"
                if out_of_scope
                else None
            ),
        )
    )

    # 6. For work rather than goods: is there an artefact behind the claim?
    #
    # Intent binding compares the basket to a sentence the agent wrote, so it cannot catch an
    # agent that describes its own non-delivery accurately. Evidence is the one input that does
    # not come from the agent's prose, which is why it is a separate, deterministic rule.
    unevidenced = sorted(
        item.sku
        for item in attempt.items
        if item.category == "contract_work" and not item.evidence.strip()
    )
    rules.append(
        RuleResult(
            rule_id="delivery.evidence",
            passed=not unevidenced,
            detail=(
                f"billed without a delivered artefact: {', '.join(unevidenced)}"
                if unevidenced
                else "every billed deliverable points at an artefact"
            ),
            counterfactual=(
                f"attach evidence to {', '.join(unevidenced)} or remove it from the invoice"
                if unevidenced
                else None
            ),
        )
    )

    # 7. Does the basket match the reason the agent gave for buying it?
    matches = intent.score >= mandate.intent_threshold
    rules.append(
        RuleResult(
            rule_id="intent.binding",
            passed=matches,
            detail=(
                f"intent match {intent.score:.2f} below threshold {mandate.intent_threshold:.2f}"
                f" ({intent.evidence})"
                if not matches
                else f"intent match {intent.score:.2f} ({intent.evidence})"
            ),
            counterfactual=(
                "restate the intent to describe what is actually being bought, or change the basket"
                if not matches
                else None
            ),
        )
    )

    failed = [rule for rule in rules if not rule.passed]
    granted = False

    if any(rule.rule_id in HARD_RULES for rule in failed):
        outcome = Outcome.DENY
    elif failed:
        # A grant can release exactly one held basket, for exactly one rule.
        if (
            grant is not None
            and grant.is_valid(attempt, now)
            and any(rule.rule_id == grant.rule_id for rule in failed)
        ):
            outcome = Outcome.ALLOW
            granted = True
        else:
            outcome = Outcome.HOLD
    else:
        outcome = Outcome.ALLOW

    reasons = tuple(f"{rule.rule_id}: {rule.detail}" for rule in failed) or ("all rules passed",)

    counterfactual = None
    for rule in failed:
        if rule.counterfactual:
            counterfactual = rule.counterfactual
            break

    if granted:
        # The grant leads. A row that reads "allow / over the ceiling" in that order looks like a
        # bug until you see who released it, and the ledger is read by people in a hurry.
        reasons = (f"released by one-time grant {grant.id if grant else ''}",) + reasons

    return Decision(
        attempt_id=attempt.id,
        mandate_id=mandate.id,
        outcome=outcome,
        amount=attempt.amount,
        currency=mandate.currency,
        rules=tuple(rules),
        intent=intent,
        reasons=reasons,
        counterfactual=counterfactual,
        at=now.isoformat().replace("+00:00", "Z"),
        mode=mode,
        grant_id=grant.id if granted and grant else None,
    )


def summarise(decisions: Iterable[Decision]) -> dict[str, int]:
    """Counts by outcome, for the header strip of the ledger."""
    counts = {"allow": 0, "hold": 0, "deny": 0}
    for decision in decisions:
        counts[decision.outcome.value] += 1
    return counts
