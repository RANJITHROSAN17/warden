"""The purchasing agent that Warden is guarding.

Deliberately small: the point of the project is the guard, not the shopper. Any agent - LangGraph,
a tool-calling loop, a cron job with an HTTP client - is supposed to be able to call the gateway,
so this one is written as the least impressive possible caller.

With no model endpoint configured it plans deterministically from the catalogue, which is what the
offline demo uses. With one configured, the plan comes from the model and is still checked by the
same rules - the guard does not trust its own agent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from warden.catalog import PRODUCTS
from warden.llm import LLMError, chat, parse_json_object
from warden.models import LineItem

PLAN_PROMPT = """You are a purchasing agent with a goal and a catalogue.

Goal: {goal}

Catalogue:
{catalog}

Reply with JSON only:
{{"vendor_id": "<id>", "items": [{{"sku": "<sku>", "qty": <int>}}],
  "stated_intent": "<one sentence saying what you are buying and why>"}}"""


@dataclass(frozen=True)
class Plan:
    vendor_id: str
    items: tuple[LineItem, ...]
    stated_intent: str
    planner: str


def _fallback_plan(goal: str) -> Plan:
    """A deterministic plan, used when no model is configured. Clearly labelled as such."""
    from warden.catalog import item

    return Plan(
        vendor_id="V-ACME",
        items=(item("SKU-PAPER", 5),),
        stated_intent="restock printer paper for the office",
        planner="deterministic-fallback",
    )


def plan(goal: str) -> Plan:
    if not os.environ.get("WARDEN_LLM_API_KEY"):
        return _fallback_plan(goal)

    catalogue = json.dumps(
        {
            sku: {"description": p["description"], "category": p["category"]}
            for sku, p in PRODUCTS.items()
        },
        indent=1,
    )
    from warden.catalog import item

    try:
        raw = chat(
            [
                {"role": "system", "content": "You output JSON only."},
                {"role": "user", "content": PLAN_PROMPT.format(goal=goal, catalog=catalogue)},
            ]
        )
        data = parse_json_object(raw)
        items = tuple(item(entry["sku"], int(entry.get("qty", 1))) for entry in data["items"])
        return Plan(
            vendor_id=str(data["vendor_id"]),
            items=items,
            stated_intent=str(data["stated_intent"])[:280],
            planner=f"model:{os.environ.get('WARDEN_LLM_MODEL', 'default')}",
        )
    except (LLMError, KeyError, ValueError, TypeError):
        return _fallback_plan(goal)
