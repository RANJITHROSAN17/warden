"""The five-minute proof, runnable offline.

Five scenarios, each decided live by the real rule engine and the real intent judge. Only the
*inputs* are scripted - nothing about the outcomes is. At the end the receipts are verified, and
then one is tampered with to show the check fails.

    python -m warden.demo
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from warden.catalog import item, vendor
from warden.gateway import Gateway
from warden.ledger import Ledger
from warden.models import Decision, Outcome
from warden.paypal import PayPalError, SandboxPayPal, SimulatedPayPal

RULE = "-" * 78


def _line(decision: Decision, label: str) -> str:
    mark = {Outcome.ALLOW: "ALLOW", Outcome.HOLD: "HOLD ", Outcome.DENY: "DENY "}[decision.outcome]
    return (
        f"{mark}  {label:<26} {decision.amount:>8.2f} {decision.currency}  "
        f"intent {decision.intent.score:.2f}"
    )


def select_rail(mode: str):
    """Choose the payment rail, and never fall back silently.

    Silently degrading from a real sandbox call to a simulated one would be the exact failure
    this project exists to catch, so asking for `sandbox` without credentials is a hard error
    rather than a quiet substitution.
    """
    if mode == "sandbox":
        try:
            return SandboxPayPal()
        except PayPalError as exc:
            raise SystemExit(f"cannot run the sandbox rail: {exc}") from exc
    if mode == "simulated":
        return SimulatedPayPal()
    from warden.paypal import make_rail

    return make_rail()


def run(mode: str = "auto") -> int:
    gateway = Gateway(ledger=Ledger(":memory:"), rail=select_rail(mode))
    mandate = gateway.issue_mandate(
        purpose="Q4 office restock",
        ceiling=500.0,
        categories=("office_supplies",),
        min_vendor_age_days=30,
        intent_threshold=0.55,
        ttl_hours=24,
    )

    print()
    print("WARDEN  -  spend authority for AI agents")
    print(RULE)
    rail_note = (
        "real PayPal sandbox calls"
        if gateway.rail.mode == "sandbox"
        else "labelled stand-in; set PayPal sandbox credentials for real calls"
    )
    print(f"mode      {gateway.rail.mode}   ({rail_note})")
    print(f"mandate   {mandate.id}  {mandate.purpose}")
    print(
        f"ceiling   {mandate.ceiling:.2f} {mandate.currency}   "
        f"categories {list(mandate.allowed_categories)}   "
        f"vendor age ≥ {mandate.min_vendor_age_days}d"
    )
    print(f"judge     {gateway.judge.name}")
    print(RULE)
    print()

    decisions: list[tuple[str, Decision, str]] = []

    def attempt(label: str, vendor_id: str, items, intent: str, grant=None):
        decision, decision_id = gateway.attempt(
            mandate, vendor(vendor_id), items, intent, grant=grant
        )
        decisions.append((label, decision, decision_id))
        print(_line(decision, label))
        if decision.outcome is not Outcome.ALLOW:
            # Every failed rule is shown, not just the first: in a drift the category and the
            # intent both fire, and hiding one would make the engine look simpler than it is.
            for index, reason in enumerate(decision.reasons):
                prefix = "because     " if index == 0 else "            "
                print(f"       {prefix}{reason}")
            print(f"       would need  {decision.counterfactual}")
            print(f"       decision    {decision_id}")
        else:
            print(
                f"       paypal    {decision.paypal.get('order_id')} → "
                f"{decision.paypal.get('authorization_id')}"
            )
        print()
        return decision, decision_id

    # 1. The intended purchase.
    attempt(
        "1 restock paper",
        "V-ACME",
        [item("SKU-PAPER", 5), item("SKU-PENS", 2)],
        "restock printer paper and pens for the office",
    )

    # 2. Goal drift: agent says supplies, basket is an espresso machine.
    attempt(
        "2 drift, same ceiling",
        "V-ACME",
        [item("SKU-ESPRESSO", 1)],
        "restock printer paper for the office",
    )

    # 3. Over the ceiling.
    _, over_id = attempt(
        "3 over ceiling",
        "V-ACME",
        [item("SKU-TONER", 6)],
        "restock printer toner for the office",
    )

    # 4. A brand-new payee.
    attempt(
        "4 new vendor",
        "V-FRESH",
        [item("SKU-PAPER", 4)],
        "restock printer paper for the office",
    )

    # 5. Same basket as #3, released by a one-time grant.
    grant = gateway.grant(
        over_id, issued_by="finance@example.com", reason="Q4 toner budget approved"
    )
    attempt(
        "5 same basket, granted",
        "V-ACME",
        [item("SKU-TONER", 6)],
        "restock printer toner for the office",
        grant=grant,
    )

    # 6. Authorised, then the authority is withdrawn before the money is taken.
    live_decision, live_id = gateway.attempt(
        mandate,
        vendor("V-ACME"),
        [item("SKU-PAPER", 4)],
        "restock printer paper for the office",
    )
    print(_line(live_decision, "6 authorised, then voided"))
    auth_id = live_decision.paypal.get("authorization_id")
    print(f"       authorised  {auth_id}   money not taken yet")
    voided = gateway.void(live_id)
    print(f"       voided      {voided.get('voided')}   ({voided.get('mode')})")
    print("       because     the mandate was revoked after authorisation; the authorisation")
    print("                   window is what makes that possible without a refund")
    print()

    print(RULE)
    counts = gateway.ledger.counts()
    print(f"allowed {counts['allow']}   held {counts['hold']}   denied {counts['deny']}")
    print(RULE)

    # Receipts: verify them, then break one.
    first_id = decisions[0][2]
    ok, message = gateway.ledger.verify(first_id)
    print(f"verify {first_id}  →  {ok}  ({message})")

    receipt = gateway.ledger.receipt(over_id)
    assert receipt is not None
    tampered = json.loads(json.dumps(receipt))
    tampered["payload"]["outcome"] = "allow"
    from warden import receipts as receipt_store

    ok, message = receipt_store.verify(tampered)
    print(f"verify tampered receipt  →  {ok}  ({message})")
    print()
    print("Every decision above, paid or refused, is a signed receipt:")
    print("  python -m warden.cli serve   →   http://127.0.0.1:8000/")
    print()
    return 0


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Five scenarios, decided by the real engine.")
    parser.add_argument(
        "--rail",
        choices=["auto", "sandbox", "simulated"],
        default=os.environ.get("WARDEN_RAIL", "auto"),
        help="auto uses sandbox credentials when present, otherwise the labelled stand-in",
    )
    args = parser.parse_args(argv)
    return run(args.rail)


if __name__ == "__main__":
    sys.exit(_main())
