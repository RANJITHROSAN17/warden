"""A model judge and a planner, over any OpenAI-compatible endpoint.

The product does not depend on a hosted model to be correct: when no endpoint is configured the
deterministic lexical judge runs and says so in every receipt. That is not a hedge, it is the
honest arrangement - a demo that quietly degrades to a model with no key would be the same class
of lie this tool exists to catch.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from warden.intent import IntentJudge
from warden.models import IntentVerdict, LineItem

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

JUDGE_PROMPT = """You audit whether a purchase matches the reason an AI agent gave for buying it.

Stated intent: {intent}
Basket:
{basket}

Score how well the basket matches the stated intent, 0.0 to 1.0.
A basket that contains items the intent does not mention scores low, even if the items are cheap.
Reply with JSON only: {{"score": <number>, "evidence": "<one sentence citing specific items>"}}"""


class LLMError(RuntimeError):
    pass


def _post(path: str, payload: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
    base = os.environ.get("WARDEN_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    key = os.environ.get("WARDEN_LLM_API_KEY", "")
    if not key:
        raise LLMError("WARDEN_LLM_API_KEY is not set")
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return dict(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        raise LLMError(f"{exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - network path
        raise LLMError(str(exc)) from exc


def chat(messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
    body = {
        "model": os.environ.get("WARDEN_LLM_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 400,
    }
    data = _post("/chat/completions", body)
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError) as exc:  # pragma: no cover - network path
        raise LLMError(f"unexpected completion shape: {data}") from exc


def parse_json_object(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply, tolerating prose around it."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError(f"no JSON object in reply: {text[:200]}")
    return dict(json.loads(text[start : end + 1]))


class ModelJudge:
    """The real judge. Returns the same `IntentVerdict` shape as the lexical one."""

    name = "model"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("WARDEN_LLM_MODEL", DEFAULT_MODEL)

    def judge(self, stated_intent: str, items: Sequence[LineItem]) -> IntentVerdict:
        basket = "\n".join(
            f"- {item.qty} x {item.description} ({item.category}) at {item.unit_price:.2f}"
            for item in items
        )
        raw = chat(
            [
                {"role": "system", "content": "You output JSON only."},
                {
                    "role": "user",
                    "content": JUDGE_PROMPT.format(intent=stated_intent, basket=basket),
                },
            ]
        )
        data = parse_json_object(raw)
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        return IntentVerdict(
            score=round(score, 3),
            evidence=str(data.get("evidence", "no evidence supplied"))[:300],
            judge=f"{self.name}:{self.model}",
        )


def make_judge() -> IntentJudge:
    """A model judge when an endpoint is configured, the lexical judge otherwise."""
    if os.environ.get("WARDEN_LLM_API_KEY"):
        return ModelJudge()
    from warden.intent import LexicalJudge

    return LexicalJudge()
