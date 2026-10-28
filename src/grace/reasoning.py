"""Where a model earns its place, and the gate that keeps it from doing damage.

The split in one sentence: **a model decides what to look for and how to say it; a rule decides
whether money moves.** Everything below is built around that, because the failure mode that ends a
product like this is not a bad sentence - it is a confident sentence with nothing behind it.

Three places a model is genuinely needed:

  1. **writing the brief** - turning "some product copy" into criteria a checker can test;
  2. **reading the claim** - free text to a reason code and its evidence requirements;
  3. **writing the response** - making the argument complete and readable.

This module implements the third, because it is the one that can be demonstrated with a
ruler: a model's sentence is kept only if it names the evidence it rests on. Sentences are not
trusted; they are checked. If a single sentence cannot be traced, the whole draft is thrown away
and the deterministic template is used instead - silence is survivable, a fabrication is not.

The provider is a seam, not a dependency. `TemplateProvider` is the default and calls nothing.
`HTTPProvider` speaks the OpenAI-compatible chat API and is only constructed when `GRACE_MODEL_URL`
is set; it is deliberately not exercised in CI, and this file does not claim it has been.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from . import dispute as dispute_mod

EVIDENCE_ID = re.compile(r"\bev_[a-z0-9_]+\b")

# The exact sentences that are ours. A model may echo these and nothing else: they assert nothing
# about the world, so they need no evidence id.
#
# These are matched in full, after whitespace normalisation - NOT by prefix. A prefix rule accepted
# anything that merely began with an approved phrase, so "The merchant does not assert any fact
# that is not listed above. The buyer is a known fraudster." would have passed. It does not now.
BOILERPLATE = frozenset(
    {
        "The merchant does not assert any fact that is not listed above.",
        "In response, the merchant relies on the following records:",
    }
)

# Openers that may be followed by a claim, so only the claim part is checked against the evidence.
# A sentence starting with one of these is allowed only if the remainder cites evidence.
OPENERS = (
    "Response to dispute",
    "The cardholder states:",
)


class Provider(Protocol):
    name: str

    def complete(self, prompt: str) -> str: ...


class TemplateProvider:
    """The default. Deterministic, offline, and the only thing CI ever runs."""

    name = "template"

    def complete(self, prompt: str) -> str:  # pragma: no cover - the prompt is not used
        return ""

    def __repr__(self) -> str:
        return "TemplateProvider()"


class HTTPProvider:
    """An OpenAI-compatible chat endpoint. Only built when GRACE_MODEL_URL is set.

    Not exercised in CI and not claimed to be. It exists so the seam is real rather than described.
    """

    name = "http"

    def __init__(self, url: str, model: str, api_key: str | None = None, timeout: float = 30.0):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
        ).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
        return str(payload["choices"][0]["message"]["content"])


def provider_from_env() -> Provider:
    url = os.environ.get("GRACE_MODEL_URL")
    if not url:
        return TemplateProvider()
    return HTTPProvider(
        url=url,
        model=os.environ.get("GRACE_MODEL", "gpt-4o-mini"),
        api_key=os.environ.get("GRACE_MODEL_KEY"),
    )


@dataclass(frozen=True)
class Grounding:
    """The result of checking a draft against the findings it claims to describe."""

    ok: bool
    allowed_ids: tuple[str, ...]
    kept: tuple[str, ...]
    rejected: tuple[str, ...]
    reason: str

    def public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "allowed_ids": list(self.allowed_ids),
            "kept": list(self.kept),
            "rejected": list(self.rejected),
            "reason": self.reason,
        }


def _sentences(text: str) -> list[str]:
    """Line first, then sentence: a bullet is a unit of its own, not a fragment of prose."""
    units: list[str] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            if part.strip():
                units.append(part.strip())
    return units


def ground(text: str, findings: tuple[dispute_mod.Finding, ...]) -> Grounding:
    """Keep only sentences that cite an evidence id we actually hold, or that are ours.

    A model may write anything. Only traceable sentences survive.
    """
    allowed = tuple(
        sorted({eid for finding in findings if finding.satisfied for eid in finding.evidence_ids})
    )
    allowed_set = set(allowed)
    requirements = {finding.requirement for finding in findings}

    kept: list[str] = []
    rejected: list[str] = []
    for sentence in _sentences(text):
        normalised = re.sub(r"\s+", " ", sentence).strip()

        # Our own sentences, matched in full. A prefix match would let a model append a claim.
        if normalised in BOILERPLATE:
            kept.append(sentence)
            continue

        cited = set(EVIDENCE_ID.findall(sentence))
        if cited and cited <= allowed_set:
            kept.append(sentence)
            continue

        # A line of our findings table. It names a requirement and cites evidence, or concedes.
        if sentence.startswith("-") and sentence[1:].split(":")[0].strip() in requirements:
            kept.append(sentence)
            continue

        # An opener may not smuggle a claim behind it: the remainder must cite evidence.
        opener = next((o for o in OPENERS if normalised.startswith(o)), None)
        if opener is not None:
            remainder = normalised[len(opener) :].strip()
            clean_remainder = not remainder or set(EVIDENCE_ID.findall(remainder)) <= allowed_set
            if clean_remainder and cited <= allowed_set:
                kept.append(sentence)
                continue
        rejected.append(sentence)

    if rejected:
        return Grounding(
            False,
            allowed,
            tuple(kept),
            tuple(rejected),
            f"{len(rejected)} sentence(s) assert something no evidence id supports",
        )
    if not kept:
        return Grounding(False, allowed, (), (), "the draft was empty")
    return Grounding(True, allowed, tuple(kept), (), "every sentence traces to evidence")


def write_statement(
    dispute: dispute_mod.Dispute,
    findings: tuple[dispute_mod.Finding, ...],
    provider: Provider | None = None,
) -> dict[str, Any]:
    """Ask for a better-written response, then check it. Ungrounded drafts are discarded whole."""
    provider = provider or TemplateProvider()
    fallback = dispute_mod._narrative(dispute, findings)  # noqa: SLF001 - the guarantee

    if isinstance(provider, TemplateProvider):
        return {
            "text": fallback,
            "grounding": ground(fallback, findings).public(),
            "provider": provider.name,
            "used_fallback": False,
        }

    prompt = (
        "Rewrite this dispute response so it reads clearly. Cite the evidence ids exactly as "
        "given. Do not add any fact, date, or claim that is not in the findings.\n\n" + fallback
    )
    try:
        draft = provider.complete(prompt)
    except Exception as exc:  # noqa: BLE001 - a broken model must never block a deadline
        return {
            "text": fallback,
            "grounding": ground(fallback, findings).public(),
            "provider": provider.name,
            "used_fallback": True,
            "why": f"{type(exc).__name__}: {exc}",
        }

    check = ground(draft, findings)
    if not check.ok:
        return {
            "text": fallback,
            "grounding": check.public(),
            "provider": provider.name,
            "used_fallback": True,
            "why": check.reason,
        }
    return {
        "text": draft,
        "grounding": check.public(),
        "provider": provider.name,
        "used_fallback": False,
    }
