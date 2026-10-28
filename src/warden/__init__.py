"""Warden - spend authority and proof receipts for AI agents that pay.

The claim this project makes is narrow and testable: an agent should not be trusted because it
has a spending limit. It should be trusted because every payment it makes matches the reason it
said it was making one - and because that match is provable after the fact.
"""

from warden.models import (
    Decision,
    IntentVerdict,
    LineItem,
    Mandate,
    Outcome,
    PurchaseAttempt,
    RuleResult,
    Vendor,
)

__all__ = [
    "Decision",
    "IntentVerdict",
    "LineItem",
    "Mandate",
    "Outcome",
    "PurchaseAttempt",
    "RuleResult",
    "Vendor",
]
__version__ = "0.1.0"
