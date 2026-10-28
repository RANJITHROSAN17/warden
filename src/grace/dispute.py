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

# What each reason code actually requires. Written from how disputes are argued: a claim of
# non-receipt is answered with delivery, an unauthorised claim with consent, and so on.
REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "MERCHANDISE_NOT_RECEIVED": ("delivery_proof", "delivery_date", "customer_contact"),
    "UNAUTHORISED": ("authorisation_record", "customer_contact", "customer_history"),
    "DUPLICATE": ("capture_record", "distinct_charges"),
    "NOT_AS_DESCRIBED": ("delivery_proof", "description_match", "customer_contact"),
    "CREDIT_NOT_PROCESSED": ("refund_record",),
}

# Which requirement is decisive: without it, contesting is donating a fee.
DECISIVE = {
    "MERCHANDISE_NOT_RECEIVED": "delivery_proof",
    "UNAUTHORISED": "authorisation_record",
    "DUPLICATE": "distinct_charges",
    "NOT_AS_DESCRIBED": "description_match",
    "CREDIT_NOT_PROCESSED": "refund_record",
}

SUPPORTING = {
    "delivery_date",
    "customer_contact",
    "customer_history",
    "capture_record",
    "description_match",
}


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
    def requirements(self) -> tuple[str, ...]:
        return REQUIREMENTS.get(self.reason_code, ())

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
            kind = "authorisation_record" if transition["kind"] == "authorise" else "capture_record"
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
    decisive = DECISIVE.get(dispute.reason_code)
    decisive_finding = next((f for f in findings if f.requirement == decisive), None)

    if decisive_finding is not None and not decisive_finding.satisfied:
        recommendation = "accept"
        explanation = (
            f"the decisive requirement for {dispute.reason_code} is missing: "
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
