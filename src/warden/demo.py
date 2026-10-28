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
from warden.models import Decision, LineItem, Outcome, Vendor
from warden.negotiate import Seller, negotiate
from warden.paypal import PayPalError, SandboxPayPal, SimulatedPayPal
from warden.workorder import Deliverable, WorkOrder

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


def run_payout_story(mode: str = "auto") -> int:
    """Paying a contractor when the job is marked complete - including when it isn't.

    Same engine as the purchasing story: a mandate, a basket, a stated intent, signed receipts.
    Only the domain changes, which is the point - the guard is not specific to shopping.
    """
    gateway = Gateway(ledger=Ledger(":memory:"), rail=select_rail(mode))
    contractor = Vendor(
        id="C-0042", name="A. Rivera (design)", age_days=520, country="US", approved=True
    )

    print()
    print("WARDEN  -  paying for work when the job is marked complete")
    print(RULE)
    print(f"mode      {gateway.rail.mode}")
    print("contractor C-0042  A. Rivera (design)   520 days, approved")
    print(RULE)
    print()

    # A complete job: every deliverable accepted.
    finished = WorkOrder(
        id="WO-1042",
        title="Landing page redesign",
        contractor=contractor,
        deliverables=(
            Deliverable("D1", "Landing page redesign, 3 breakpoints", 900.0, "figma+html", True),
            Deliverable("D2", "Mobile navigation fix", 150.0, "pr#88", True),
        ),
    )
    mandate = gateway.issue_mandate(
        purpose=f"pay {finished.id} {finished.title}",
        ceiling=finished.total + 50.0,
        categories=("contract_work",),
        min_vendor_age_days=30,
        intent_threshold=0.5,
        allowed_vendors=(contractor.id,),
    )

    decision, decision_id = gateway.attempt(
        mandate,
        contractor,
        [d.as_line_item() for d in finished.satisfied],
        finished.completion_claim(),
    )
    print(_line(decision, "1 job complete"))
    print(
        f"       paypal      {decision.paypal.get('order_id')} → "
        f"{decision.paypal.get('authorization_id')}"
    )
    print(f"       receipt     {decision_id}")
    print()

    # The same contractor, a job where one deliverable was never handed over.
    partial = WorkOrder(
        id="WO-1043",
        title="Brand refresh",
        contractor=contractor,
        deliverables=(
            Deliverable("D1", "Logo set, 4 variants", 700.0, "svg pack", True),
            Deliverable("D2", "Brand guidelines PDF", 600.0, "", False),
        ),
    )
    mandate2 = gateway.issue_mandate(
        purpose=f"pay {partial.id} {partial.title}",
        ceiling=partial.total + 50.0,
        categories=("contract_work",),
        min_vendor_age_days=30,
        intent_threshold=0.5,
        allowed_vendors=(contractor.id,),
    )
    # The agent bills the full order and says it delivered everything. The claim is text the
    # agent wrote, so it agrees with itself; the evidence field is what disagrees.
    claimed = f"work order {partial.id} complete: " + "; ".join(
        d.description for d in partial.deliverables
    )
    billed = [
        LineItem(
            sku=d.id,
            description=d.description,
            category="contract_work",
            qty=1,
            unit_price=d.amount,
            evidence=d.delivered,
        )
        for d in partial.deliverables
    ]
    decision, decision_id = gateway.attempt(mandate2, contractor, billed, claimed)
    print(_line(decision, "2 missing deliverable"))
    print("       because     the completion claim is text the agent wrote, so it agrees with")
    print("                   itself; the evidence field is what disagrees - D2 has no artefact")
    print("       would need  attach evidence to D2, or bill only the accepted deliverable")
    print(f"       receipt     {decision_id}")
    print()

    print(RULE)
    ok, message = gateway.ledger.verify(decision_id)
    print(f"verify {decision_id}  →  {ok}  ({message})")
    print()
    print("Both decisions are signed. The second one is the interesting receipt: it records")
    print("that the payout was stopped, by which rule, and what would have released it.")
    print()
    return 0


def run_negotiation_story(mode: str = "auto") -> int:
    """Two agents haggle; the mandate decides whether the deal may settle.

    The interesting part is the failure. A buyer agent that concedes past its ceiling and lets
    the payer discover the overrun is the whole problem - so here the agent walks away, and then
    fixes its own problem by buying less rather than by spending more.
    """
    gateway = Gateway(ledger=Ledger(":memory:"), rail=select_rail(mode))
    mandate = gateway.issue_mandate(
        purpose="Q4 office restock - toner",
        ceiling=500.0,
        categories=("office_supplies",),
        min_vendor_age_days=30,
        intent_threshold=0.55,
    )

    print()
    print("WARDEN  -  two agents negotiate, the mandate decides")
    print(RULE)
    print(f"mode      {gateway.rail.mode}")
    print(f"mandate   {mandate.id}   ceiling {mandate.ceiling:.2f} {mandate.currency}")
    print(f"purpose   {mandate.purpose}")
    print(RULE)
    print()

    # ── Round one: six cartridges, a seller whose floor is above the ceiling ──────────────
    seller = Seller(list_price=534.0, floor=505.0, firmness=0.08)
    deal = negotiate(mandate, "6 x toner cartridges", seller)
    print(f"negotiation 1  ({deal.planner})")
    for line in deal.transcript:
        print("   " + line)
    print()

    if deal.succeeded:
        decision, decision_id = gateway.attempt(
            mandate,
            vendor("V-ACME"),
            [item("SKU-TONER", 6)],
            "restock printer toner for the office",
        )
        print(_line(decision, "1 as negotiated"))
        return 0

    # The dealer could not be moved under the ceiling. The agent does NOT settle anyway - and
    # that refusal is the product. What it does instead is ask for less.
    print("   → no deal inside the ceiling. The agent buys less rather than spending more.")
    print()

    # ── Round two: the same negotiation for a smaller basket ─────────────────────────────
    seller2 = Seller(list_price=445.0, floor=430.0, firmness=0.25)
    deal2 = negotiate(mandate, "5 x toner cartridges", seller2)
    print(f"negotiation 2  ({deal2.planner})")
    for line in deal2.transcript:
        print("   " + line)
    print()

    decision, decision_id = gateway.attempt(
        mandate,
        vendor("V-ACME"),
        [item("SKU-TONER", 5)],
        "restock printer toner for the office",
    )
    print(_line(decision, "2 settled and paid"))
    print(
        f"       paypal      {decision.paypal.get('order_id')} → "
        f"{decision.paypal.get('authorization_id')}"
    )
    print(f"       receipt     {decision_id}")
    print()

    # ── The guard still wins when the agent is wrong ─────────────────────────────────────
    print("   ...and if the agent had settled at 534 anyway:")
    over, over_id = gateway.attempt(
        mandate,
        vendor("V-ACME"),
        [item("SKU-TONER", 6)],
        "restock printer toner for the office",
    )
    print(_line(over, "3 over ceiling"))
    print(f"       because     {over.reasons[0]}")
    print(f"       would need  {over.counterfactual}")
    print(f"       receipt     {over_id}")
    print()

    print(RULE)
    ok, message = gateway.ledger.verify(decision_id)
    print(f"verify {decision_id}  →  {ok}  ({message})")
    print()
    print("A model that can talk its way past a limit is not a control. The negotiation is the")
    print("model's; the ceiling is not.")
    print()
    return 0


def run_heal_story(mode: str = "auto") -> int:
    """The agent reads its own refusal and fixes the basket - inside the same fence.

    This is the loop that separates an agent from a script: a hold is not a dead end, it is an
    instruction. Every revision goes back through the same rules, so "self-healing" can never
    mean "gate-eroding" - and the two refusals it is not allowed to fix stay fixed.
    """
    from warden import remediate

    gateway = Gateway(ledger=Ledger(":memory:"), rail=select_rail(mode))
    mandate = gateway.issue_mandate(
        purpose="Q4 office restock",
        ceiling=200.0,
        categories=("office_supplies",),
        min_vendor_age_days=30,
        intent_threshold=0.55,
    )

    print()
    print("WARDEN  -  the agent reads its refusal and acts on it")
    print(RULE)
    print(f"mode      {gateway.rail.mode}")
    print(f"ceiling   {mandate.ceiling:.2f} {mandate.currency}")
    print(f"scope     {list(mandate.allowed_categories)}")
    print(RULE)
    print()

    # The agent's first idea is bad: an espresso machine plus a toner, over budget and half of
    # it out of scope. This is the plan, not a strawman - agents do this.
    plan = (item("SKU-ESPRESSO", 1), item("SKU-TONER", 2))
    result = remediate.run(
        gateway, mandate, vendor("V-ACME"), plan, "restock office supplies for the quarter"
    )

    for index, (decision, decision_id) in enumerate(result.decisions, start=1):
        print(_line(decision, f"{index} attempt"))
        if decision.outcome is not Outcome.ALLOW:
            for reason in decision.reasons:
                print(f"       because     {reason}")
            print(f"       receipt     {decision_id}")
        else:
            print(
                f"       paypal      {decision.paypal.get('order_id')} → "
                f"{decision.paypal.get('authorization_id')}"
            )
        print()

    for revision in result.revisions:
        print(f"   revision {revision.attempt}: {revision.action}")
        print(f"   {revision.explanation}")
        print()

    print(RULE)
    print(f"stopped because: {result.stopped_because}")
    ok, message = gateway.ledger.verify(result.decisions[-1][1])
    print(f"verify final receipt  →  {ok}  ({message})")
    print()

    # And the case it must not fix: a new payee. An agent that talks its way around this is
    # not a control, so the loop stops and asks a human.
    blocked = remediate.run(
        gateway, mandate, vendor("V-FRESH"), (item("SKU-PAPER", 2),), "restock printer paper"
    )
    print(_line(blocked.final, "unfixable case"))
    print(f"       because     {blocked.final.reasons[0]}")
    print(f"       loop        {blocked.stopped_because}")
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
    parser.add_argument(
        "--story",
        choices=["purchase", "payout", "negotiate", "heal"],
        default="purchase",
        help=(
            "purchase: agent buying goods · payout: paying a contractor · "
            "negotiate: two agents haggling · heal: agent repairs its own refusal"
        ),
    )
    args = parser.parse_args(argv)
    if args.story == "payout":
        return run_payout_story(args.rail)
    if args.story == "negotiate":
        return run_negotiation_story(args.rail)
    if args.story == "heal":
        return run_heal_story(args.rail)
    return run(args.rail)


if __name__ == "__main__":
    sys.exit(_main())
