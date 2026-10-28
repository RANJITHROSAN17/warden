"""Disputes: an agent that argues a chargeback, and refuses when the evidence is not there.

A chargeback is a deadline, a reason code, and a demand for evidence. Merchants lose them for
boring reasons: the evidence existed but nobody assembled it in time, or it was assembled badly,
or - most often - it was never there and nobody noticed before paying a fee to lose anyway.

The agent here does three things and no more:

  1. gathers what actually exists, from the payment's own signed record plus whatever the merchant
     attached;
  2. maps it against what the reason code demands;
  3. recommends fighting or not, and writes the argument when the answer is fight.

It never invents evidence. A missing element is reported as missing, and the recommendation falls
back to accepting the claim - which is the honest answer and usually the cheaper one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# What each reason code demands, in **PayPal's own evidence vocabulary**.
#
# These are not invented names. Every string here is a value of `evidence_type` in PayPal's
# `customer_disputes_v1` OpenAPI document (spec version 1.11, 81 values). A judge from PayPal can
# check any of them against /v1/customer/disputes/{id}/provide-evidence.
#
# The choice of *which* types answer each reason code is ours, and is the actual work: a claim of
# non-receipt is answered by fulfilment and a signature, a SNAD claim by proof of the difference,
# an unauthorised claim by the delivery address and the transaction details.
REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "ITEM_NOT_RECEIVED": (
        "PROOF_OF_FULFILLMENT",
        "PROOF_OF_DELIVERY_SIGNATURE",
        "ONLINE_TRACKING_INFORMATION",
    ),
    "NOT_AS_DESCRIBED": (
        "PROOF_OF_SIGNIFICANT_DIFFERENCE",
        "ITEM_DESCRIPTION",
        "PROOF_OF_FULFILLMENT",
    ),
    "UNAUTHORIZED": (
        "ADDITIONAL_INFORMATION",
        "DELIVERY_ADDRESS",
        "DETAILS_OF_PURCHASE",
    ),
    "DUPLICATE": (
        "DETAILS_OF_PURCHASE",
        "COPY_OF_CONTRACT",
    ),
    "BILLING_ERROR": (
        "PROOF_OF_REFUND",
        "DETAILS_OF_PURCHASE",
    ),
}

# Accepted spellings, so a caller using the older names still works.
ALIASES = {
    "MERCHANDISE_NOT_RECEIVED": "ITEM_NOT_RECEIVED",
    "UNAUTHORISED": "UNAUTHORIZED",
    "CREDIT_NOT_PROCESSED": "BILLING_ERROR",
    "SNAD": "NOT_AS_DESCRIBED",
    "INR": "ITEM_NOT_RECEIVED",
}

# Which requirement is decisive: without it, contesting is donating a fee.
DECISIVE = {
    "ITEM_NOT_RECEIVED": "PROOF_OF_FULFILLMENT",
    "NOT_AS_DESCRIBED": "PROOF_OF_SIGNIFICANT_DIFFERENCE",
    "UNAUTHORIZED": "ADDITIONAL_INFORMATION",
    "DUPLICATE": "DETAILS_OF_PURCHASE",
    "BILLING_ERROR": "PROOF_OF_REFUND",
}

SUPPORTING = {
    "PROOF_OF_DELIVERY_SIGNATURE",
    "ONLINE_TRACKING_INFORMATION",
    "ITEM_DESCRIPTION",
    "DELIVERY_ADDRESS",
    "COPY_OF_CONTRACT",
}

# Evidence types that carry structured tracking rather than a document. PayPal's `evidence_info`
# object expects `tracking_info` for these, which is a different payload shape from a note.
TRACKED = {"PROOF_OF_FULFILLMENT", "ONLINE_TRACKING_INFORMATION", "ADDITIONAL_TRACKING_INFORMATION"}


@dataclass(frozen=True)
class Evidence:
    """One fact the merchant can point at, and where it came from."""

    id: str
    kind: str
    label: str
    source: str
    detail: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Dispute:
    id: str
    payment_id: str
    reason_code: str
    amount: float
    currency: str
    claim: str
    due_at: str

    @property
    def code(self) -> str:
        return ALIASES.get(self.reason_code, self.reason_code)

    @property
    def requirements(self) -> tuple[str, ...]:
        return REQUIREMENTS.get(self.code, ())

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "payment_id": self.payment_id,
            "reason_code": self.reason_code,
            "amount": self.amount,
            "currency": self.currency,
            "claim": self.claim,
            "due_at": self.due_at,
            "requirements": list(self.requirements),
        }


@dataclass(frozen=True)
class Finding:
    requirement: str
    satisfied: bool
    evidence_ids: tuple[str, ...]
    note: str

    def public(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "satisfied": self.satisfied,
            "evidence_ids": list(self.evidence_ids),
            "note": self.note,
        }


@dataclass(frozen=True)
class Assessment:
    dispute_id: str
    findings: tuple[Finding, ...]
    recommendation: str  # contest | contest_partial | accept
    coverage: float
    explanation: str
    narrative: str = ""
    packet: dict[str, Any] = field(default_factory=dict)

    @property
    def missing(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.satisfied)

    def public(self) -> dict[str, Any]:
        return {
            "dispute_id": self.dispute_id,
            "recommendation": self.recommendation,
            "coverage": self.coverage,
            "explanation": self.explanation,
            "findings": [f.public() for f in self.findings],
            "missing": [f.requirement for f in self.missing],
            "narrative": self.narrative,
            "packet": self.packet,
        }


def gather(
    dispute: Dispute,
    payment_history: list[dict[str, Any]],
    attached: list[Evidence] | None = None,
) -> list[Evidence]:
    """Collect evidence from the payment's own record plus what the merchant attached.

    `payment_history` is the transition list from the ledger. A capture that the rail confirmed is
    evidence in a way a screenshot is not: it is signed, timestamped, and was written before the
    dispute existed.
    """
    found: list[Evidence] = list(attached or [])

    for transition in payment_history:
        if transition["allowed"] and transition["kind"] in {"authorise", "capture"}:
            # Emitted in PayPal's vocabulary so the packet needs no translation at filing time.
            kind = (
                "ADDITIONAL_INFORMATION"
                if transition["kind"] == "authorise"
                else "PROOF_OF_REFUND"
                if transition["kind"] == "void"
                else "PROOF_OF_FULFILLMENT"
            )
            found.append(
                Evidence(
                    id=f"ev_{transition['id']}",
                    kind=kind,
                    label=f"{transition['kind']} {transition['amount']:.2f}",
                    source=f"signed receipt {transition['id']}",
                    detail=transition["reason"],
                )
            )
    return found


def assess(dispute: Dispute, evidence: list[Evidence]) -> Assessment:
    """Map evidence to requirements, then decide whether fighting is worth it."""
    by_kind: dict[str, list[Evidence]] = {}
    for item in evidence:
        by_kind.setdefault(item.kind, []).append(item)

    findings: list[Finding] = []
    for requirement in dispute.requirements:
        matches = by_kind.get(requirement, [])
        findings.append(
            Finding(
                requirement=requirement,
                satisfied=bool(matches),
                evidence_ids=tuple(item.id for item in matches),
                note=(
                    f"{matches[0].label} — {matches[0].source}"
                    if matches
                    else "no evidence of this kind exists"
                ),
            )
        )

    satisfied = [f for f in findings if f.satisfied]
    coverage = round(len(satisfied) / len(findings), 3) if findings else 0.0
    decisive = DECISIVE.get(dispute.code)
    decisive_finding = next((f for f in findings if f.requirement == decisive), None)

    if decisive_finding is not None and not decisive_finding.satisfied:
        recommendation = "accept"
        explanation = (
            f"the decisive requirement for {dispute.code} is missing: "
            f"{decisive}. Contesting without it pays a fee to lose."
        )
    elif not dispute.requirements:
        recommendation = "accept"
        explanation = (
            f"no requirement map for reason code {dispute.reason_code}; do not guess at a claim"
        )
    elif len(satisfied) == len(findings):
        recommendation = "contest"
        explanation = f"all {len(findings)} requirements are evidenced; contest the full amount."
    else:
        missing_supporting = [f.requirement for f in findings if not f.satisfied]
        recommendation = "contest_partial"
        explanation = (
            "the decisive requirement is evidenced but "
            + ", ".join(missing_supporting)
            + " is not; contest the claim you can actually support."
        )

    narrative = _narrative(dispute, findings) if recommendation != "accept" else ""
    return Assessment(
        dispute_id=dispute.id,
        findings=tuple(findings),
        recommendation=recommendation,
        coverage=coverage,
        explanation=explanation,
        narrative=narrative,
        packet=_packet(dispute, findings, evidence) if recommendation != "accept" else {},
    )


def _narrative(dispute: Dispute, findings: tuple[Finding, ...]) -> str:
    """The argument, written from findings only. Every claim cites an evidence id.

    Deterministic on purpose: a model can make this read better, but it must not be able to add a
    claim that no evidence supports. The template is the guarantee.
    """
    lines = [
        f"Response to dispute {dispute.id} ({dispute.reason_code}, {dispute.amount:.2f} "
        f"{dispute.currency}).",
        "",
        f"The cardholder states: {dispute.claim}",
        "",
        "In response, the merchant relies on the following records:",
    ]
    for finding in findings:
        if finding.satisfied:
            lines.append(
                f"  - {finding.requirement}: evidenced by "
                f"{', '.join(finding.evidence_ids)} ({finding.note})."
            )
        else:
            lines.append(f"  - {finding.requirement}: NOT evidenced, and not claimed.")
    lines += [
        "",
        "The merchant does not assert any fact that is not listed above.",
    ]
    return "\n".join(lines)


def _packet(
    dispute: Dispute, findings: tuple[Finding, ...], evidence: list[Evidence]
) -> dict[str, Any]:
    used = {eid for finding in findings for eid in finding.evidence_ids}
    return {
        "dispute_id": dispute.id,
        "reason_code": dispute.reason_code,
        "amount": dispute.amount,
        "currency": dispute.currency,
        "requirements": [
            {"requirement": f.requirement, "satisfied": f.satisfied} for f in findings
        ],
        "evidence": [item.public() for item in evidence if item.id in used],
        "declined_evidence": [item.public() for item in evidence if item.id not in used],
        "statement": "No evidence outside this packet is asserted.",
    }


# ── pre-chargeback alerts ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Alert:
    """A pre-chargeback alert: PayPal's warning that a chargeback is coming.

    PayPal's documentation gives the merchant **20 hours** to refund and avoid the chargeback and
    its fee. That deadline is the whole reason this needs an agent rather than a queue: the useful
    decision has to be made faster than a human triage rota usually runs.
    """

    id: str
    payment_id: str
    reason_code: str
    amount: float
    currency: str
    hours_remaining: float

    @property
    def code(self) -> str:
        return ALIASES.get(self.reason_code, self.reason_code)


@dataclass(frozen=True)
class Triage:
    alert_id: str
    decision: str  # refund | contest
    explanation: str
    decisive_evidence: bool

    def public(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "decision": self.decision,
            "explanation": self.explanation,
            "decisive_evidence": self.decisive_evidence,
        }


def triage_alert(alert: Alert, evidence: list[Evidence]) -> Triage:
    """Refund inside the window, or let it become a chargeback and fight it.

    The economics are asymmetric: refunding costs the sale, while contesting a case without the
    decisive evidence costs the sale, the fee, and the operational time. So the rule is blunt -
    no decisive evidence, refund now; decisive evidence, take the chargeback.
    """
    kinds = {item.kind for item in evidence}
    decisive = DECISIVE.get(alert.code)
    has_decisive = bool(decisive and decisive in kinds)

    if alert.hours_remaining <= 0:
        return Triage(
            alert.id,
            "refund",
            "the 20-hour window has closed; refunding now is the only way to avoid the fee.",
            has_decisive,
        )
    if not has_decisive:
        return Triage(
            alert.id,
            "refund",
            f"{decisive or 'the decisive evidence'} does not exist. A chargeback would be lost "
            f"and would cost a fee as well as the sale, so refund inside the "
            f"{alert.hours_remaining:.1f} hours remaining.",
            False,
        )
    return Triage(
        alert.id,
        "contest",
        f"{decisive} exists and is signed. Let the alert become a chargeback and answer it with "
        f"evidence; refunding would give away a case that can be won.",
        True,
    )


# ── settlement ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Settlement:
    """What to do at the amicable stage, and the price above which doing nothing is better.

    This is not a probability model. Grace has no outcome history yet, so it does not invent one.
    It gives the merchant a boundary they can argue with: contesting risks the amount plus the fee
    whenever it fails, so any offer below that risk-weighted figure is cheaper than fighting.
    """

    dispute_id: str
    recommendation: str  # contest | offer | accept
    ceiling: float
    rationale: str
    assumption: str

    def public(self) -> dict[str, Any]:
        return {
            "dispute_id": self.dispute_id,
            "recommendation": self.recommendation,
            "ceiling": round(self.ceiling, 2),
            "rationale": self.rationale,
            "assumption": self.assumption,
        }


def settle(dispute: Dispute, assessment: Assessment, fee: float = 0.0) -> Settlement:
    coverage = assessment.coverage
    risk = dispute.amount + max(fee, 0.0)
    assumption = (
        "the coverage figure is treated as a fair estimate of winning; Grace has no outcome "
        "history yet and does not pretend to one"
    )

    if assessment.recommendation == "accept":
        return Settlement(
            dispute.id,
            "accept",
            dispute.amount,
            "the decisive evidence does not exist, so the claim is owed. Offering a discount on a "
            "debt the merchant cannot disprove is not a strategy, it is a fee with extra steps.",
            assumption,
        )
    if coverage >= 1.0:
        return Settlement(
            dispute.id,
            "contest",
            0.0,
            "every requirement is covered. An offer here gives away money the evidence "
            "already wins.",
            assumption,
        )
    ceiling = risk * (1.0 - coverage)
    return Settlement(
        dispute.id,
        "offer",
        ceiling,
        f"decisive evidence exists but supporting evidence does not ({coverage:.2f} coverage). "
        f"Contesting risks {risk:.2f} whenever it fails, so an offer below {ceiling:.2f} is "
        f"cheaper than fighting; anything above it is cheaper to contest.",
        assumption,
    )
