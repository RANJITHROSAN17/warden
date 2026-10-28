"""The second story: paying for work, not for goods.

PayPal's own inspiration list names this case directly - "a scheduling assistant that pays
contractors automatically when a job is marked complete". It matters because the failure is
sharper than in procurement: the money is the *last* step of a long relationship, and paying for
work that was not done is the expensive mistake.

The engine does not change. A work order becomes a mandate; the delivered artefacts become the
basket; the completion claim becomes the stated intent. If the deliverable does not match the
order, the payment is held - and the hold says which line of the order is unmet.
"""

from __future__ import annotations

from dataclasses import dataclass

from warden.models import LineItem, Vendor


@dataclass(frozen=True)
class Deliverable:
    """One line of a work order, and what was actually handed over."""

    id: str
    description: str
    amount: float
    delivered: str
    satisfied: bool

    def as_line_item(self) -> LineItem:
        """Treat an accepted deliverable as a priced line, so the rules engine sees one shape."""
        return LineItem(
            sku=self.id,
            description=self.description,
            category="contract_work",
            qty=1,
            unit_price=self.amount,
            evidence=self.delivered,
        )


@dataclass(frozen=True)
class WorkOrder:
    id: str
    title: str
    contractor: Vendor
    deliverables: tuple[Deliverable, ...]

    @property
    def total(self) -> float:
        return round(sum(d.amount for d in self.deliverables), 2)

    @property
    def satisfied(self) -> tuple[Deliverable, ...]:
        return tuple(d for d in self.deliverables if d.satisfied)

    @property
    def unmet(self) -> tuple[Deliverable, ...]:
        return tuple(d for d in self.deliverables if not d.satisfied)

    def completion_claim(self) -> str:
        """The sentence the agent gives when it asks to pay. Judged like any other intent."""
        return f"work order {self.id} complete: " + "; ".join(d.description for d in self.satisfied)
