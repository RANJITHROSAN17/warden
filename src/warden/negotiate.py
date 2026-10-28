"""Two agents, one mandate: buyer and seller negotiating a price the payer can afford.

PayPal's brief names this case explicitly - "two agents negotiating and settling a payment between
themselves (agent-to-agent commerce)". It is also where a model is unmistakably doing work: not
classifying a string, but arguing a price across rounds and deciding when to walk away.

The mandate is the fence and the model works inside it. That ordering matters: the buyer agent is
given its ceiling as a hard fact, so a persuasive seller cannot talk it past the limit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from warden.llm import LLMError, chat
from warden.models import Mandate

BUYER_PROMPT = """You are purchasing on behalf of a company. You have a hard budget.

Item: {item}
Seller's price: {price:.2f} {currency}
Your maximum: {ceiling:.2f} {currency} total (hard limit, set by finance)
Round: {round}

Write one short message to the seller: a counter-offer or an acceptance. Maximum 30 words.
Do not offer above your maximum under any circumstances."""


@dataclass(frozen=True)
class Round:
    number: int
    speaker: str
    price: float
    message: str


@dataclass(frozen=True)
class Negotiation:
    item: str
    opening_price: float
    agreed_price: float | None
    rounds: tuple[Round, ...]
    walk_away_reason: str | None
    planner: str
    transcript: tuple[str, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return self.agreed_price is not None


class DeterministicBuyer:
    """A buyer that always behaves the same way. Speaks in honest arithmetic, not prose."""

    name = "deterministic-negotiator"

    def __init__(self, ceiling: float, currency: str) -> None:
        self.ceiling = ceiling
        self.currency = currency
        self._last = 0.0

    def counter(self, seller_price: float, rnd: int, item: str) -> tuple[float, str]:
        if seller_price <= self.ceiling:
            return seller_price, f"Accepted at {seller_price:.2f} {self.currency}."
        # Opens below budget and climbs toward it, which is what a buyer with leverage does. The
        # ceiling is still the hard stop, enforced here and again by the rules if it lies.
        first = round(self.ceiling * 0.88, 2)
        offer = first if rnd == 1 else round(min(self.ceiling, (self._last + self.ceiling) / 2), 2)
        self._last = offer
        return offer, f"I can commit {offer:.2f} {self.currency}. That is my budget for {item}."


class ModelBuyer(DeterministicBuyer):
    """The same agent, with a model writing its messages. Falls back on any failure."""

    name = "model-negotiator"

    def counter(self, seller_price: float, rnd: int, item: str) -> tuple[float, str]:
        try:
            text = chat(
                [
                    {"role": "system", "content": "You are a terse corporate buyer agent."},
                    {
                        "role": "user",
                        "content": BUYER_PROMPT.format(
                            item=item,
                            price=seller_price,
                            currency=self.currency,
                            ceiling=self.ceiling,
                            round=rnd,
                        ),
                    },
                ]
            )
        except LLMError:
            return super().counter(seller_price, rnd, item)
        # The model writes the message; the mandate still decides the number. Prose from the
        # model, arithmetic from the authority - that is the architecture in one line.
        if seller_price <= self.ceiling:
            return seller_price, text.strip()[:200]
        first = round(self.ceiling * 0.88, 2)
        offer = first if rnd == 1 else round(min(self.ceiling, (self._last + self.ceiling) / 2), 2)
        self._last = offer
        return offer, text.strip()[:200]


class Seller:
    """A seller with a list price and a floor it will not cross."""

    def __init__(
        self, list_price: float, floor: float, currency: str = "USD", firmness: float = 0.08
    ) -> None:
        self.list_price = list_price
        self.floor = floor
        self.currency = currency
        # How much of the remaining gap the seller gives up per round. A firm seller moves
        # slowly, which is how a negotiation ends in a walk-away rather than a discount.
        self.firmness = firmness

    def respond(self, offer: float, rnd: int, item: str) -> tuple[float, str]:
        if offer >= self.list_price:
            return self.list_price, f"Agreed at list: {self.list_price:.2f} {self.currency}."
        if offer >= self.floor:
            return offer, f"We can do {offer:.2f} {self.currency}. That is the bottom."
        step = round(max(self.floor, offer + (self.list_price - offer) * self.firmness), 2)
        if step <= offer:
            return self.floor, f"{self.floor:.2f} {self.currency} is our floor. Final."
        return step, f"Best we can offer is {step:.2f} {self.currency}; costs have moved."


def negotiate(
    mandate: Mandate,
    item: str,
    seller: Seller,
    *,
    max_rounds: int = 5,
    use_model: bool | None = None,
) -> Negotiation:
    """Negotiate one item to a price the mandate can cover, or walk away.

    The walk-away is the interesting outcome: an agent that buys anyway and lets the payer
    discover the overrun is the failure this project exists to prevent.
    """
    if use_model is None:
        use_model = bool(os.environ.get("WARDEN_LLM_API_KEY"))
    buyer: DeterministicBuyer = (
        ModelBuyer(mandate.ceiling, mandate.currency)
        if use_model
        else DeterministicBuyer(mandate.ceiling, mandate.currency)
    )

    rounds: list[Round] = []
    lines: list[str] = [f"seller  {seller.list_price:>8.2f} {mandate.currency}  listed price"]
    rounds.append(Round(1, "seller", seller.list_price, "listed price"))
    price = seller.list_price

    for rnd in range(1, max_rounds + 1):
        offer, message = buyer.counter(price, rnd, item)
        lines.append(f"buyer   {offer:>8.2f} {mandate.currency}  {message}")
        rounds.append(Round(rnd, "buyer", offer, message))
        if price <= mandate.ceiling and offer >= price:
            return Negotiation(
                item, seller.list_price, price, tuple(rounds), None, buyer.name, tuple(lines)
            )

        counter, reply = seller.respond(offer, rnd, item)
        lines.append(f"seller  {counter:>8.2f} {mandate.currency}  {reply}")
        rounds.append(Round(rnd, "seller", counter, reply))
        price = counter
        if price <= mandate.ceiling:
            return Negotiation(
                item, seller.list_price, price, tuple(rounds), None, buyer.name, tuple(lines)
            )

    reason = (
        f"seller's floor {seller.floor:.2f} {mandate.currency} is above the mandate ceiling "
        f"{mandate.ceiling:.2f} {mandate.currency}; no agreement is possible inside this authority"
    )
    lines.append(f"buyer   walk away                {reason}")
    return Negotiation(
        item, seller.list_price, None, tuple(rounds), reason, buyer.name, tuple(lines)
    )
