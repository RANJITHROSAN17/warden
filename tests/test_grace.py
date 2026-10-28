"""Tests for the only thing this product claims: bounded decisions inside the gap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from grace import compensate
from grace.engine import Engine
from grace.ledger import Ledger
from grace.models import (
    CAPTURED,
    EXPIRED,
    PART_RELEASED,
    VOIDED,
    Authority,
    Payee,
)
from grace.rail import SimulatedRail


def build(**authority_overrides):
    rail = SimulatedRail()
    ledger = Ledger()
    engine = Engine(ledger, rail)
    values = {"id": "auth_test", "purpose": "Q4 restock", "ceiling": 500.0}
    values.update(authority_overrides)
    return engine, ledger, rail, Authority(**values)


def payee() -> Payee:
    return Payee(id="V-ACME", name="Acme")


# ── the happy path, so the rest has a baseline ────────────────────────────────────────────


def test_a_full_capture_closes_the_payment() -> None:
    engine, _, _, authority = build()
    payment = engine.authorise(authority, payee(), 480.0)
    assert payment.state == "authorised"
    assert payment.remaining == 480.0

    action = engine.capture(payment)
    assert action.allowed and action.rail["status"] == "COMPLETED"
    assert payment.state == CAPTURED
    assert payment.captured == 480.0
    assert payment.remaining == 0.0


def test_authorising_above_the_ceiling_is_refused_outright() -> None:
    engine, _, _, authority = build(ceiling=100.0)
    try:
        engine.authorise(authority, payee(), 480.0)
    except ValueError as error:
        assert "ceiling" in str(error)
    else:  # pragma: no cover
        raise AssertionError("the ceiling must be enforced")


# ── the bound that matters: an agent cannot take more than it was given ───────────────────


def test_the_agent_cannot_capture_more_than_was_authorised() -> None:
    engine, _, _, authority = build()
    payment = engine.authorise(authority, payee(), 480.0)

    action = engine.capture(payment, 600.0)
    assert action.allowed is False
    assert "capture.within_authorised" in {check.rule_id for check in action.failed_rules}
    assert action.counterfactual
    assert payment.captured == 0.0, "a refused capture must not move money"


def test_a_refusal_is_receipted_like_a_success() -> None:
    """The question after an incident is 'why did this not capture', so refusals must be signed."""
    engine, ledger, _, authority = build()
    payment = engine.authorise(authority, payee(), 480.0)
    engine.capture(payment, 900.0)

    refusals = [t for t in ledger.transitions(payment.id) if not t["allowed"]]
    assert refusals, "the refusal must be in the ledger"
    ok, message = ledger.verify(refusals[0]["id"])
    assert ok, message


def test_capturing_twice_cannot_double_charge() -> None:
    engine, _, _, authority = build()
    payment = engine.authorise(authority, payee(), 480.0)
    engine.capture(payment)

    second = engine.capture(payment)
    assert second.allowed is False
    assert "payment.open" in {check.rule_id for check in second.failed_rules}
    assert payment.captured == 480.0


# ── compensation: the product ─────────────────────────────────────────────────────────────


def test_a_declined_capture_leaves_the_authorisation_open() -> None:
    engine, _, rail, authority = build()
    rail.instrument_limit = 300.0
    payment = engine.authorise(authority, payee(), 480.0)

    declined = engine.capture(payment)
    assert declined.rail["status"] == "DECLINED"
    assert declined.rail["reason"] == "INSTRUMENT_DECLINED"
    assert payment.open, "the money is still committed and must stay usable"
    assert payment.captured == 0.0


def test_the_agent_takes_what_is_available_and_releases_the_rest() -> None:
    engine, _, rail, authority = build()
    rail.instrument_limit = 300.0
    payment = engine.authorise(authority, payee(), 480.0)
    declined = engine.capture(payment)

    chosen = compensate.plan(payment, authority, declined.rail)
    assert chosen.action == compensate.PARTIAL_CAPTURE
    assert chosen.amount == 300.0

    compensate.apply(engine, payment, chosen)
    assert payment.captured == 300.0
    assert payment.state == PART_RELEASED
    assert payment.remaining == 180.0, "the released remainder is visible, not erased"


def test_a_timeout_is_retried_but_a_decline_is_not() -> None:
    """Treating these the same is how retry storms start and good payments get abandoned."""
    engine, _, rail, authority = build()
    rail.transient_failures = 1
    payment = engine.authorise(authority, payee(), 480.0)

    timeout = engine.capture(payment)
    assert timeout.rail["reason"] == "RAIL_TIMEOUT"
    assert compensate.plan(payment, authority, timeout.rail).action == compensate.RETRY

    decline = {"status": "DECLINED", "reason": "INSTRUMENT_DECLINED", "instrument_limit": 0}
    assert compensate.plan(payment, authority, decline).action == compensate.VOID


def test_compensation_respects_the_attempt_budget() -> None:
    engine, _, rail, authority = build(max_capture_attempts=1)
    rail.transient_failures = 5
    payment = engine.authorise(authority, payee(), 480.0)

    first = engine.capture(payment)
    chosen = compensate.plan(payment, authority, first.rail)
    assert chosen.action == compensate.ESCALATE, "the budget is spent; a human should own this"


def test_an_authority_can_forbid_partial_capture() -> None:
    engine, _, rail, authority = build(allow_partial_capture=False)
    rail.instrument_limit = 300.0
    payment = engine.authorise(authority, payee(), 480.0)
    declined = engine.capture(payment)

    chosen = compensate.plan(payment, authority, declined.rail)
    assert chosen.action == compensate.ESCALATE
    refused = engine.capture(payment, 300.0)
    assert refused.allowed is False
    assert "authority.partial_permitted" in {check.rule_id for check in refused.failed_rules}


def test_an_unrecognised_failure_is_never_guessed_at() -> None:
    engine, _, _, authority = build()
    payment = engine.authorise(authority, payee(), 480.0)
    chosen = compensate.plan(payment, authority, {"status": "WHAT", "reason": "UNKNOWN_CODE"})
    assert chosen.action == compensate.ESCALATE


# ── time ──────────────────────────────────────────────────────────────────────────────────


def test_a_lapsed_authorisation_cannot_be_captured() -> None:
    engine, ledger, rail, authority = build(ttl_minutes=30)
    payment = engine.authorise(authority, payee(), 480.0)

    late = Engine(ledger, rail, clock=lambda: datetime.now(timezone.utc) + timedelta(minutes=45))
    refused = late.capture(payment)
    assert refused.allowed is False
    assert "authorisation.not_expired" in {check.rule_id for check in refused.failed_rules}
    assert payment.captured == 0.0

    closed = late.expire(payment)
    assert closed.allowed
    assert payment.state == EXPIRED


def test_expiring_a_live_authorisation_is_refused() -> None:
    engine, _, _, authority = build()
    payment = engine.authorise(authority, payee(), 480.0)
    action = engine.expire(payment)
    assert action.allowed is False
    assert payment.state == "authorised"


# ── the ledger tells the truth about where the money is ──────────────────────────────────


def test_the_summary_separates_open_captured_and_released() -> None:
    engine, ledger, rail, authority = build()

    paid = engine.authorise(authority, payee(), 120.0)
    engine.capture(paid)

    rail.instrument_limit = 300.0
    partial = engine.authorise(authority, payee(), 480.0)
    declined = engine.capture(partial)
    compensate.apply(engine, partial, compensate.plan(partial, authority, declined.rail))
    rail.instrument_limit = None

    waiting = engine.authorise(authority, payee(), 310.0)
    released = engine.authorise(authority, payee(), 240.0)
    engine.void(released)

    summary = ledger.summary()
    assert summary["captured"] == 420.0, "120 captured plus 300 partial"
    assert summary["open"] == 310.0, "only the payment still in the gap"
    assert summary["released"] == 180.0 + 240.0, "the partial remainder and the full void"
    assert waiting.open


def test_a_void_with_money_taken_says_so() -> None:
    engine, _, rail, authority = build()
    rail.instrument_limit = 300.0
    payment = engine.authorise(authority, payee(), 480.0)
    engine.capture(payment, 300.0)
    engine.void(payment)
    assert payment.state == PART_RELEASED, "calling this 'voided' would erase the 300 that settled"


def test_a_plain_void_reads_as_voided() -> None:
    engine, _, _, authority = build()
    payment = engine.authorise(authority, payee(), 240.0)
    engine.void(payment)
    assert payment.state == VOIDED
    assert payment.captured == 0.0


# ── the receipt chain ────────────────────────────────────────────────────────────────────


def test_every_transition_verifies_and_a_single_edit_does_not() -> None:
    engine, ledger, _, authority = build()
    payment = engine.authorise(authority, payee(), 480.0)
    engine.capture(payment)

    for transition in ledger.transitions(payment.id):
        ok, message = ledger.verify(transition["id"])
        assert ok, message

    transition_id = ledger.transitions(payment.id)[0]["id"]
    receipt = ledger.receipt(transition_id)
    receipt["payload"]["allowed"] = not receipt["payload"]["allowed"]
    from grace import receipts as receipts_module

    ok, _ = receipts_module.verify(receipt)
    assert ok is False, "one flipped field must invalidate the signature"


# ── the HTTP surface ─────────────────────────────────────────────────────────────────────


def test_the_api_serves_the_whole_story(tmp_path) -> None:
    from fastapi.testclient import TestClient

    import grace.api as api_module

    api_module.ledger = Ledger(tmp_path / "grace.db")
    api_module.engine = Engine(api_module.ledger, SimulatedRail())
    client = TestClient(api_module.app)

    seeded = client.post("/api/demo/seed").json()
    assert len(seeded["payments"]) == 5
    states = {p["state"] for p in seeded["payments"]}
    assert {"captured", "part_released", "voided", "authorised", "expired"} == states

    payment_id = seeded["payments"][0]["id"]
    detail = client.get(f"/api/payments/{payment_id}").json()
    assert detail["transitions"]

    transition_id = detail["transitions"][0]["id"]
    assert client.get(f"/api/transitions/{transition_id}/verify").json()["ok"] is True

    tampered = client.post(f"/api/transitions/{transition_id}/tamper-demo").json()
    assert tampered["genuine"]["ok"] is True
    assert tampered["tampered"]["ok"] is False
    # the stored receipt is untouched
    assert client.get(f"/api/transitions/{transition_id}/verify").json()["ok"] is True


# ── disputes: argue only what the evidence supports ──────────────────────────────────────


def test_an_authorisation_is_recorded_once(tmp_path) -> None:
    """A duplicated record in an evidence packet looks like a second charge."""
    engine, ledger, _, authority = build()
    payment = engine.authorise(authority, payee(), 180.0)
    kinds = [t["kind"] for t in ledger.transitions(payment.id)]
    assert kinds.count("authorise") == 1


def test_a_fully_evidenced_claim_is_contested() -> None:
    from grace import dispute as d

    engine, ledger, _, authority = build()
    payment = engine.authorise(authority, payee(), 180.0)
    engine.capture(payment)

    claim = d.Dispute(
        "dsp_1", payment.id, "ITEM_NOT_RECEIVED", 180.0, "USD", "never arrived", "2026-11-04"
    )
    attached = [
        d.Evidence("e1", "delivery_proof", "carrier scan", "logistics"),
        d.Evidence("e2", "delivery_date", "delivered 14 Oct", "logistics"),
        d.Evidence("e3", "customer_contact", "customer confirmed", "inbox"),
    ]
    result = d.assess(claim, d.gather(claim, ledger.transitions(payment.id), attached))
    assert result.recommendation == "contest"
    assert result.coverage == 1.0
    assert "not assert any fact" in result.narrative


def test_a_claim_without_the_decisive_evidence_is_not_fought() -> None:
    from grace import dispute as d

    engine, ledger, _, authority = build()
    payment = engine.authorise(authority, payee(), 180.0)

    claim = d.Dispute(
        "dsp_2", payment.id, "ITEM_NOT_RECEIVED", 180.0, "USD", "never arrived", "2026-11-04"
    )
    result = d.assess(claim, d.gather(claim, ledger.transitions(payment.id), []))
    assert result.recommendation == "accept"
    assert result.narrative == "", "an accepted claim must not file an argument"
    assert "delivery_proof" in result.explanation


def test_the_packet_cites_only_evidence_it_used() -> None:
    from grace import dispute as d

    engine, ledger, _, authority = build()
    payment = engine.authorise(authority, payee(), 180.0)
    claim = d.Dispute(
        "dsp_3", payment.id, "ITEM_NOT_RECEIVED", 180.0, "USD", "never arrived", "2026-11-04"
    )
    attached = [
        d.Evidence("e1", "delivery_proof", "carrier scan", "logistics"),
        d.Evidence("e9", "unrelated_photo", "a photo of a cat", "somewhere"),
    ]
    result = d.assess(claim, d.gather(claim, ledger.transitions(payment.id), attached))
    used = {item["id"] for item in result.packet["evidence"]}
    assert "e1" in used
    assert "e9" not in used, "evidence that answers nothing must not pad the packet"
    assert result.packet["declined_evidence"]


def test_an_unknown_reason_code_is_not_guessed_at() -> None:
    from grace import dispute as d

    engine, ledger, _, authority = build()
    payment = engine.authorise(authority, payee(), 100.0)
    claim = d.Dispute("dsp_4", payment.id, "SOMETHING_NEW", 100.0, "USD", "?", "2026-11-04")
    result = d.assess(claim, d.gather(claim, ledger.transitions(payment.id), []))
    assert result.recommendation == "accept"
    assert "no requirement map" in result.explanation


def test_an_alert_without_the_decisive_evidence_is_refunded_inside_the_window() -> None:
    """20 hours is the whole point: refund faster than a triage rota runs."""
    from grace import dispute as d

    alert = d.Alert("alr_1", "pay_1", "ITEM_NOT_RECEIVED", 180.0, "USD", hours_remaining=17.4)
    triage = d.triage_alert(alert, [d.Evidence("e1", "customer_contact", "chat", "inbox")])
    assert triage.decision == "refund"
    assert triage.decisive_evidence is False
    assert "fee" in triage.explanation


def test_an_alert_with_signed_evidence_becomes_a_chargeback_worth_fighting() -> None:
    from grace import dispute as d

    alert = d.Alert("alr_2", "pay_1", "ITEM_NOT_RECEIVED", 180.0, "USD", hours_remaining=17.4)
    triage = d.triage_alert(alert, [d.Evidence("e1", "delivery_proof", "scan 4471", "logistics")])
    assert triage.decision == "contest"


def test_paypal_reason_code_aliases_resolve() -> None:
    from grace import dispute as d

    for alias, code in (("INR", "ITEM_NOT_RECEIVED"), ("SNAD", "NOT_AS_DESCRIBED")):
        found = d.Dispute("d", "p", alias, 1.0, "USD", "x", "2026-11-04")
        assert found.code == code
        assert found.requirements


# ── verifying a deliverable against a brief ─────────────────────────────────────────────────


def _market_brief():
    from grace import verify as v

    return v.Brief(
        id="brf",
        title="12 descriptions",
        criteria=(
            v.Criterion("count", "twelve descriptions", "count", 12, pro_rata=True),
            v.Criterion("length", "at least 40 words", "min_words", 40),
            v.Criterion("material", "names the material", "mentions", "recycled"),
        ),
    )


def _copy(n: int) -> tuple[str, ...]:
    body = "recycled aluminium bottle dishwasher safe lid sold separately warranty included " * 4
    return tuple(f"{body}item {i}" for i in range(n))


def test_a_short_delivery_is_paid_for_the_work_that_was_done() -> None:
    from grace import verify as v

    verdict = v.verify(_market_brief(), v.Deliverable(text="", items=_copy(9)))
    assert verdict.decision == "capture_partial"
    assert round(verdict.fraction, 3) == 0.75
    assert [f.criterion for f in verdict.unmet] == ["count"]


def test_a_complete_delivery_is_paid_in_full() -> None:
    from grace import verify as v

    verdict = v.verify(_market_brief(), v.Deliverable(text="", items=_copy(12)))
    assert verdict.decision == "capture_full"


def test_a_criterion_that_cannot_be_checked_escalates_instead_of_guessing() -> None:
    from grace import verify as v

    brief = v.Brief(
        id="b",
        title="logo",
        criteria=(v.Criterion("taste", "good taste", "manual", ""),),
    )
    verdict = v.verify(brief, v.Deliverable(text="", items=_copy(1)))
    assert verdict.decision == "escalate"
    assert "human" in verdict.explanation


def test_no_measurable_basis_means_nothing_is_captured() -> None:
    from grace import verify as v

    brief = v.Brief(
        id="b",
        title="one page",
        criteria=(v.Criterion("length", "at least 40 words", "min_words", 40),),
        allow_partial=True,
    )
    verdict = v.verify(brief, v.Deliverable(text="", items=("too short",)))
    assert verdict.decision == "release"
    assert verdict.fraction == 0.0


def test_the_market_story_holds_then_releases_the_remainder() -> None:
    """End to end: money committed, work short, pro-rata capture, remainder released."""
    from grace.demo import story_market

    assert story_market(mode="simulated") == 0


# ── the grounding gate: a model may write, but a rule decides what survives ─────────────────


def _findings():
    from grace import dispute as d

    found = d.Dispute(
        "dsp_1", "pay_1", "ITEM_NOT_RECEIVED", 180.0, "USD", "never arrived", "2026-11-04"
    )
    evidence = [
        d.Evidence("ev_ship", "delivery_proof", "carrier scan 4471", "logistics"),
        d.Evidence("ev_date", "delivery_date", "delivered 14 Oct", "logistics"),
    ]
    return found, d.assess(found, evidence).findings


def test_a_sentence_that_cites_real_evidence_is_kept() -> None:
    from grace import reasoning

    _, findings = _findings()
    check = reasoning.ground("The parcel was delivered, see ev_ship.", findings)
    assert check.ok is True
    assert check.kept == ("The parcel was delivered, see ev_ship.",)


def test_a_sentence_that_cites_nothing_is_rejected() -> None:
    from grace import reasoning

    _, findings = _findings()
    check = reasoning.ground("The customer is a known fraudster.", findings)
    assert check.ok is False
    assert check.rejected == ("The customer is a known fraudster.",)


def test_a_sentence_citing_an_evidence_id_we_do_not_hold_is_rejected() -> None:
    """Inventing an id is the exact move the gate exists to stop."""
    from grace import reasoning

    _, findings = _findings()
    check = reasoning.ground("Delivery confirmed by ev_ghost.", findings)
    assert check.ok is False
    assert "ev_ghost" not in check.allowed_ids


def test_a_model_that_invents_a_claim_is_discarded_entirely() -> None:
    from grace import reasoning

    found, findings = _findings()

    class Liar:
        name = "liar"

        def complete(self, prompt: str) -> str:
            return "The item was delivered on time (ev_ship). The buyer has a history of fraud."

    result = reasoning.write_statement(found, findings, Liar())
    assert result["used_fallback"] is True
    assert "fraud" not in result["text"]
    assert result["grounding"]["ok"] is False


def test_a_model_that_stays_grounded_is_allowed_to_improve_the_prose() -> None:
    from grace import reasoning

    found, findings = _findings()

    class Honest:
        name = "honest"

        def complete(self, prompt: str) -> str:
            return (
                "Delivery is evidenced by the carrier scan (ev_ship). "
                "It was signed on 14 October (ev_date)."
            )

    result = reasoning.write_statement(found, findings, Honest())
    assert result["used_fallback"] is False
    assert result["text"].startswith("Delivery is evidenced")


def test_the_default_provider_calls_nothing_and_is_still_grounded() -> None:
    from grace import reasoning

    found, findings = _findings()
    result = reasoning.write_statement(found, findings)
    assert result["provider"] == "template"
    assert result["grounding"]["ok"] is True
    assert "does not assert any fact" in result["text"]
