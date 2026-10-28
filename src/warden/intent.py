"""Intent binding: does the basket match the reason the agent gave?

This is the part of Warden that does not exist as a card control. A spending limit answers "how
much". A merchant blocklist answers "who". Neither answers *"why is this agent buying a coffee
machine when it told me it was restocking paper"* - and that is the failure mode that actually
costs money, because an agent that has drifted off-purpose is still under its ceiling.

Two judges share one interface:

* `LexicalJudge` - deterministic, offline, explainable. Token overlap between the stated intent
  and the basket. It is deliberately simple, and it is what CI and the offline demo run, so a
  model outage cannot make the evidence unreproducible.
* `ModelJudge` (in `warden.llm`) - the real judge in production, returning the same shape.

Neither is allowed to be vague: both must cite the evidence that produced the score, because the
receipt records it verbatim.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from warden.models import IntentVerdict, LineItem

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "of",
        "to",
        "in",
        "on",
        "at",
        "with",
        "from",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "we",
        "our",
        "i",
        "my",
        "need",
        "needs",
        "needed",
        "buy",
        "buying",
        "purchase",
        "purchasing",
        "order",
        "ordering",
        "please",
        "some",
        "get",
        "getting",
    ]
)


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens, stopwords removed, naive plural folding."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    tokens = set()
    for word in words:
        if word in STOPWORDS or len(word) < 3:
            continue
        tokens.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return tokens


def _basket_tokens(items: Sequence[LineItem]) -> set[str]:
    tokens: set[str] = set()
    for item in items:
        tokens |= tokenize(item.description)
        tokens |= tokenize(item.category.replace("_", " "))
        tokens |= tokenize(item.sku)
    return tokens


class IntentJudge(Protocol):
    name: str

    def judge(self, stated_intent: str, items: Sequence[LineItem]) -> IntentVerdict: ...


class LexicalJudge:
    """Deterministic overlap between the stated intent and the basket. No model, no network."""

    name = "lexical-overlap"

    def judge(self, stated_intent: str, items: Sequence[LineItem]) -> IntentVerdict:
        intent = tokenize(stated_intent)
        basket = _basket_tokens(items)
        if not intent:
            return IntentVerdict(
                score=0.0,
                evidence="stated intent contained no usable words",
                judge=self.name,
            )
        matched = sorted(intent & basket)
        missing = sorted(intent - basket)
        extra = sorted(basket - intent)

        # Recall of the intent, with a small penalty when the basket introduces things the intent
        # never mentioned. Both halves matter: an agent can under-describe a basket as easily as
        # it can misdescribe one.
        recall = len(matched) / len(intent)
        drift = len(extra) / max(len(basket), 1)
        score = round(max(0.0, recall - 0.25 * drift), 3)

        evidence = (
            f"matched {', '.join(matched) or 'nothing'}"
            if not missing
            else f"matched {', '.join(matched) or 'nothing'}; intent mentions "
            f"{', '.join(missing)} which the basket does not contain"
        )
        return IntentVerdict(score=score, evidence=evidence, judge=self.name)


def default_judge() -> IntentJudge:
    return LexicalJudge()
