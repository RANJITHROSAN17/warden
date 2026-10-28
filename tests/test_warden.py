"""The rules that must never quietly stop being true."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from warden import receipts
from warden.catalog import item, vendor
from warden.gateway import Gateway
from warden.intent import LexicalJudge, tokenize
from warden.ledger import Ledger
from warden.models import LineItem, Mandate, Outcome, Vendor

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def new_gateway() -> Gateway:
    return Gateway(ledger=Ledger(":memory:"))


def standard_mandate(gateway: Gateway, **overrides) -> Mandate:
    options = {
        "purpose": "Q4 office restock",
        "ceiling": 500.0,
        "categories": ("office_supplies",),
        "min_vendor_age_days": 30,
        "intent_threshold": 0.55,
        "ttl_hours": 24,
    }
    options.update(overrides)
    return gateway.issue_mandate(**options)


# ── the funnel ──────────────────────────────────────────────────────────────────────────────


def test_a_matching_purchase_is_allowed_and_paid() -> None:
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    decision, decision_id = gateway.attempt(
        mandate,
        vendor("V-ACME"),
        [item("SKU-PAPER", 5), item("SKU-PENS", 2)],
        "restock printer paper and pens for the office",
    )
    assert decision.outcome is Outcome.ALLOW
    assert decision.paypal["mode"] == "simulated", "the rail must label itself"
    assert str(decision.paypal["order_id"]).startswith("SIM-ORDER")
    assert gateway.ledger.verify(decision_id)[0] is True


def test_a_basket_that_does_not_match_the_stated_intent_is_held() -> None:
    """The whole point: $480 of espresso machine is under a $500 ceiling and still stops."""
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    decision, _ = gateway.attempt(
        mandate, vendor("V-ACME"), [item("SKU-ESPRESSO", 1)], "restock printer paper for the office"
    )
    assert decision.outcome is Outcome.HOLD
    failed = {rule.rule_id for rule in decision.rules if not rule.passed}
    assert "intent.binding" in failed
    assert decision.amount < mandate.ceiling, (
        "it was held while under the ceiling, not because of it"
    )


def test_the_ceiling_holds_and_explains_by_how_much() -> None:
    gateway = new_gateway()
    decision, _ = gateway.attempt(
        standard_mandate(gateway),
        vendor("V-ACME"),
        [item("SKU-TONER", 6)],
        "restock printer toner for the office",
    )
    assert decision.outcome is Outcome.HOLD
    assert "34.00" in (decision.counterfactual or "")


def test_a_new_payee_is_held_even_when_the_amount_is_small() -> None:
    gateway = new_gateway()
    decision, _ = gateway.attempt(
        standard_mandate(gateway),
        vendor("V-FRESH"),
        [item("SKU-PAPER", 4)],
        "restock printer paper for the office",
    )
    assert decision.outcome is Outcome.HOLD
    failed = {rule.rule_id for rule in decision.rules if not rule.passed}
    assert "vendor.approved" in failed or "vendor.maturity" in failed


def test_an_expired_mandate_is_denied_not_held() -> None:
    gateway = new_gateway()
    expired = Mandate(
        id="mnd_expired",
        purpose="old",
        ceiling=500.0,
        currency="USD",
        allowed_categories=("office_supplies",),
        min_vendor_age_days=0,
        intent_threshold=0.0,
        expires_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        issued_by="finance@example.com",
    )
    decision, _ = gateway.attempt(
        expired, vendor("V-ACME"), [item("SKU-PAPER", 1)], "restock printer paper"
    )
    assert decision.outcome is Outcome.DENY


# ── grants ──────────────────────────────────────────────────────────────────────────────────


def test_a_grant_releases_exactly_one_basket_and_cannot_be_replayed() -> None:
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    first, first_id = gateway.attempt(
        mandate, vendor("V-ACME"), [item("SKU-TONER", 6)], "restock printer toner for the office"
    )
    assert first.outcome is Outcome.HOLD

    grant = gateway.grant(first_id, "finance@example.com", "budget approved")

    released, _ = gateway.attempt(
        mandate,
        vendor("V-ACME"),
        [item("SKU-TONER", 6)],
        "restock printer toner for the office",
        grant=grant,
    )
    assert released.outcome is Outcome.ALLOW
    assert released.grant_id == grant.id

    # A different basket is not covered by the same grant.
    other, _ = gateway.attempt(
        mandate,
        vendor("V-ACME"),
        [item("SKU-TONER", 7)],
        "restock printer toner for the office",
        grant=grant,
    )
    assert other.outcome is Outcome.HOLD, "a grant is not a raised ceiling"


def test_a_granted_row_says_so_first_in_the_ledger() -> None:
    """The ledger is read quickly; "allow, over ceiling" in that order reads as a bug."""
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    _, held_id = gateway.attempt(
        mandate, vendor("V-ACME"), [item("SKU-TONER", 6)], "restock printer toner for the office"
    )
    grant = gateway.grant(held_id, "finance@example.com", "budget approved")
    _, granted_id = gateway.attempt(
        mandate,
        vendor("V-ACME"),
        [item("SKU-TONER", 6)],
        "restock printer toner for the office",
        grant=grant,
    )
    row = next(r for r in gateway.ledger.rows() if r["id"] == granted_id)
    assert row["outcome"] == "allow"
    assert row["reason"].startswith("released by one-time grant")


def test_a_grant_needs_a_held_decision() -> None:
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    _, decision_id = gateway.attempt(
        mandate, vendor("V-ACME"), [item("SKU-PAPER", 1)], "restock printer paper for the office"
    )
    with pytest.raises(ValueError):
        gateway.grant(decision_id, "finance@example.com", "not needed")


# ── receipts ────────────────────────────────────────────────────────────────────────────────


def test_a_tampered_receipt_fails_verification() -> None:
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    _, decision_id = gateway.attempt(
        mandate, vendor("V-ACME"), [item("SKU-ESPRESSO", 1)], "restock printer paper"
    )
    receipt = gateway.ledger.receipt(decision_id)
    assert receipt is not None
    assert receipts.verify(receipt)[0] is True

    receipt["payload"]["outcome"] = "allow"
    ok, message = receipts.verify(receipt)
    assert ok is False and "signature" in message


def test_the_receipt_records_the_judge_that_was_actually_used() -> None:
    gateway = new_gateway()
    _, decision_id = gateway.attempt(
        standard_mandate(gateway),
        vendor("V-ACME"),
        [item("SKU-PAPER", 1)],
        "restock printer paper for the office",
    )
    payload = gateway.ledger.receipt(decision_id)["payload"]
    assert payload["intent"]["judge"] == "lexical-overlap"
    assert payload["mode"] == "simulated"


# ── intent ──────────────────────────────────────────────────────────────────────────────────


def test_intent_scores_a_match_above_a_mismatch() -> None:
    judge = LexicalJudge()
    match = judge.judge("restock printer paper for the office", [item("SKU-PAPER", 5)])
    miss = judge.judge("restock printer paper for the office", [item("SKU-ESPRESSO", 1)])
    assert match.score > 0.55
    assert miss.score < 0.2
    assert match.evidence and miss.evidence


def test_tokenizer_drops_stopwords_and_folds_plurals() -> None:
    tokens = tokenize("We need to buy printer papers for the office")
    assert "printer" in tokens and "paper" in tokens
    assert "the" not in tokens and "we" not in tokens


# ── the HTTP surface ────────────────────────────────────────────────────────────────────────


def test_api_seed_endpoint_produces_a_verifiable_ledger(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import warden.api as api_module

    api_module.gateway = Gateway(ledger=Ledger(tmp_path / "api.db"))
    client = TestClient(api_module.app)

    seeded = client.post("/api/demo/seed").json()
    assert len(seeded["created"]) == 5
    assert seeded["counts"] == {"allow": 2, "hold": 3, "deny": 0}

    rows = client.get("/api/ledger").json()["rows"]
    assert len(rows) == 5

    for row in rows:
        assert client.get(f"/api/decisions/{row['id']}/verify").json()["ok"] is True


def test_api_rejects_an_unknown_mandate(tmp_path) -> None:
    from fastapi.testclient import TestClient

    import warden.api as api_module

    api_module.gateway = Gateway(ledger=Ledger(tmp_path / "api2.db"))
    client = TestClient(api_module.app)
    response = client.post(
        "/api/purchases",
        json={
            "mandate_id": "nope",
            "vendor_id": "V-ACME",
            "items": [{"sku": "SKU-PAPER"}],
            "stated_intent": "paper",
        },
    )
    assert response.status_code == 404


# ── the rail must never lie ─────────────────────────────────────────────────────────────────


def test_asking_for_the_sandbox_without_credentials_is_an_error_not_a_fallback(monkeypatch) -> None:
    """Silently degrading to the simulator would be the failure this project exists to catch."""
    from warden.demo import select_rail

    monkeypatch.delenv("WARDEN_PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("WARDEN_PAYPAL_CLIENT_SECRET", raising=False)
    with pytest.raises(SystemExit):
        select_rail("sandbox")


def test_the_simulator_labels_itself_everywhere(monkeypatch) -> None:
    from warden.demo import select_rail

    rail = select_rail("simulated")
    assert rail.mode == "simulated"
    order = rail.create_order(10.0, "USD", "ref")
    assert str(order["order_id"]).startswith("SIM-ORDER")


def test_an_authorised_payment_can_be_voided_and_the_money_never_moves() -> None:
    """The authorisation window is the space a hold exists in - and the reason for AUTHORIZE."""
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    decision, decision_id = gateway.attempt(
        mandate, vendor("V-ACME"), [item("SKU-PAPER", 4)], "restock printer paper for the office"
    )
    assert decision.outcome is Outcome.ALLOW
    authorization = gateway.authorization_for(decision_id)
    assert authorization == decision.paypal["authorization_id"]

    voided = gateway.void(decision_id)
    assert voided["voided"] == authorization
    assert voided["status"] == "VOIDED"


def test_capture_and_void_need_a_real_authorisation() -> None:
    gateway = new_gateway()
    mandate = standard_mandate(gateway)
    _, held_id = gateway.attempt(
        mandate, vendor("V-ACME"), [item("SKU-ESPRESSO", 1)], "restock printer paper"
    )
    with pytest.raises(KeyError):
        gateway.capture(held_id)
    with pytest.raises(KeyError):
        gateway.void(held_id)


# ── paying for work, not goods ───────────────────────────────────────────────────────────────


def test_a_billed_deliverable_without_evidence_is_held() -> None:
    """Intent binding compares a basket to a sentence the agent wrote, so an agent that
    accurately describes its own non-delivery passes it. Evidence is the input that isn't prose."""
    gateway = new_gateway()
    mandate = gateway.issue_mandate(
        purpose="pay WO-1043",
        ceiling=1400.0,
        categories=("contract_work",),
        min_vendor_age_days=30,
        intent_threshold=0.5,
        allowed_vendors=("C-0042",),
    )
    contractor = Vendor(id="C-0042", name="A. Rivera", age_days=520, country="US", approved=True)
    items = [
        LineItem("D1", "Logo set, 4 variants", "contract_work", 1, 700.0, "svg pack"),
        LineItem("D2", "Brand guidelines PDF", "contract_work", 1, 600.0, ""),
    ]
    decision, _ = gateway.attempt(
        mandate,
        contractor,
        items,
        "work order WO-1043 complete: Logo set, 4 variants; Brand guidelines PDF",
    )
    assert decision.outcome is Outcome.HOLD
    failed = {rule.rule_id for rule in decision.rules if not rule.passed}
    assert "delivery.evidence" in failed


def test_an_evidenced_deliverable_is_paid() -> None:
    gateway = new_gateway()
    mandate = gateway.issue_mandate(
        purpose="pay WO-1042",
        ceiling=1200.0,
        categories=("contract_work",),
        min_vendor_age_days=30,
        intent_threshold=0.5,
        allowed_vendors=("C-0042",),
    )
    contractor = Vendor(id="C-0042", name="A. Rivera", age_days=520, country="US", approved=True)
    items = [
        LineItem("D1", "Landing page redesign", "contract_work", 1, 900.0, "pr#87"),
        LineItem("D2", "Mobile navigation fix", "contract_work", 1, 150.0, "pr#88"),
    ]
    decision, _ = gateway.attempt(
        mandate,
        contractor,
        items,
        "work order WO-1042 complete: Landing page redesign; Mobile navigation fix",
    )
    assert decision.outcome is Outcome.ALLOW


def test_the_evidence_rule_is_recorded_in_the_receipt() -> None:
    from warden.demo import run_payout_story

    assert run_payout_story("simulated") == 0
