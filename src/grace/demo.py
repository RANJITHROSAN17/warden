"""Five minutes of proof, runnable offline.

Five things that happen to a payment after it is authorised, and what the agent does about each.
The inputs are scripted; the outcomes are decided by the real engine and the real rules.

    python -m grace.demo --story partial
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from grace import character as character_mod
from grace import compensate
from grace import dispute as dispute_mod
from grace import verify as verify_mod
from grace.engine import Engine
from grace.ledger import Ledger
from grace.models import Action, Authority, Payee, Payment
from grace.rail import RailError, SimulatedRail, make_rail

RULE = "-" * 78


def _authority(purpose: str = "Q4 office restock", **overrides: object) -> Authority:
    values: dict[str, object] = {
        "id": "auth_q4_office",
        "purpose": purpose,
        "ceiling": 500.0,
        "ttl_minutes": 30,
    }
    values.update(overrides)
    return Authority(**values)  # type: ignore[arg-type]


def _payee() -> Payee:
    return Payee(id="V-ACME", name="Acme Office Supply")


def _line(action: Action) -> str:
    status = str(action.rail.get("status") or "")
    if not action.allowed:
        mark = "STOP"
    elif status and status not in {"COMPLETED", "VOIDED"}:
        mark = "RAIL"  # policy permitted it; the payment rail did not
    else:
        mark = "OK  "
    amount = f"{action.amount:>8.2f}" if action.amount is not None else "        "
    return f"{mark}  {action.kind:<16} {amount} {action.actor:<18} {action.reason}"


def _show(payment: Payment, actions: list[Action] | Action) -> None:
    for action in actions if isinstance(actions, list) else [actions]:
        print(_line(action))
        if action.failed_rules:
            for check in action.failed_rules:
                print(f"        rule        {check.rule_id}: {check.reason}")
            if action.counterfactual:
                print(f"        would need  {action.counterfactual}")
    tail = f"open {payment.remaining:.2f}" if payment.open else "closed"
    print(
        f"        state       {payment.state}   captured {payment.captured:.2f} "
        f"of {payment.amount:.2f}   {tail}"
    )


def _header(title: str, rail: object) -> None:
    print()
    print(f"GRACE  -  {title}")
    print(RULE)
    print(f"mode      {getattr(rail, 'mode', 'unknown')}")
    print(RULE)


def story_full(mode: str = "auto") -> int:
    """The happy path, so the rest has a baseline: authorised, then captured in full."""
    rail = make_rail(mode)
    engine = Engine(Ledger(), rail)
    authority = _authority()
    _header("authorised, captured, done", rail)

    payment = engine.authorise(authority, _payee(), 480.0)
    print(f"authorised  {payment.id}  {payment.amount:.2f} {payment.currency}")
    print(f"expires     {payment.expires_at}")
    print()
    _show(payment, engine.capture(payment))
    print()
    print("Nothing went wrong, and the receipt says so. Keep this in mind: everything after this")
    print("is the same four operations with something going wrong in between.")
    print()
    return 0


def story_partial(mode: str = "auto") -> int:
    """The issuer will not clear the full amount. The payment still half-happens."""
    rail = make_rail(mode)
    if isinstance(rail, SimulatedRail):
        rail.instrument_limit = 300.0
    engine = Engine(Ledger(), rail)
    authority = _authority()
    _header("the issuer declines the full amount", rail)

    payment = engine.authorise(authority, _payee(), 480.0)
    print(f"authorised  {payment.id}  {payment.amount:.2f} {payment.currency}")
    print()

    print("1  capture the authorised amount")
    failed = engine.capture(payment)
    _show(payment, failed)
    print()

    print("2  the agent reads the failure")
    chosen = compensate.plan(payment, authority, failed.rail)
    print(f"        plan        {chosen.action} {chosen.amount or ''}")
    print(f"        because     {chosen.explanation}")
    print()

    print("3  the plan goes back through the same rules")
    _show(payment, compensate.apply(engine, payment, chosen))
    print()
    print("The buyer gets goods today instead of a support ticket. The rest was never taken -")
    print("there is nothing to refund, because the money never moved.")
    print()
    return 0


def story_retry(mode: str = "auto") -> int:
    """A timeout is not a decline. Retrying is right, and the budget bounds how often."""
    rail = make_rail(mode)
    if isinstance(rail, SimulatedRail):
        rail.transient_failures = 1
    engine = Engine(Ledger(), rail)
    authority = _authority()
    _header("the rail times out, then answers", rail)

    payment = engine.authorise(authority, _payee(), 480.0)
    failed = engine.capture(payment)
    _show(payment, failed)
    print()

    chosen = compensate.plan(payment, authority, failed.rail)
    print(f"        plan        {chosen.action}")
    print(f"        because     {chosen.explanation}")
    print()
    _show(payment, compensate.apply(engine, payment, chosen))
    print()
    print("A decline and a timeout are different facts. Treating them the same is how retry storms")
    print("start, and how a payment that would have worked gets abandoned.")
    print()
    return 0


def story_overreach(mode: str = "auto") -> int:
    """The agent tries to take more than was authorised. The bound holds, and it is receipted."""
    rail = make_rail(mode)
    engine = Engine(Ledger(), rail)
    authority = _authority()
    _header("the agent tries to take more than it was given", rail)

    payment = engine.authorise(authority, _payee(), 480.0)
    print(f"authorised  {payment.id}  {payment.amount:.2f} {payment.currency}   ceiling 500.00")
    print()

    print("1  capture 600.00")
    _show(payment, engine.capture(payment, 600.0))
    print()

    print("2  capture what was actually authorised")
    _show(payment, engine.capture(payment))
    print()
    print("The compensation agent has real power over this payment: it can take part of it, retry")
    print("it, or give it back. It cannot take more than the authority allowed, and every attempt")
    print("to do so is signed, so the attempt itself is on the record.")
    print()
    return 0


def story_expired(mode: str = "auto") -> int:
    """The authorisation lapsed before the capture. Late is late."""
    rail = make_rail(mode)
    ledger = Ledger()
    authority = _authority(ttl_minutes=30)
    engine = Engine(ledger, rail)
    _header("the authorisation expires while everyone waits", rail)

    payment = engine.authorise(authority, _payee(), 480.0)
    print(f"authorised  {payment.id}  {payment.amount:.2f} {payment.currency}")
    print(f"expires     {payment.expires_at}")
    print()

    late_engine = Engine(
        ledger, rail, clock=lambda: datetime.now(timezone.utc) + timedelta(minutes=45)
    )

    print("45 minutes later")
    _show(payment, late_engine.capture(payment))
    print()
    print("the clock catches up with it")
    _show(payment, late_engine.expire(payment))
    print()
    print("A lapsed authorisation is not a failed payment - it is a commitment that ended. The")
    print("difference matters to the customer, who needs a new instruction rather than a retry.")
    print()
    return 0


def story_defend(mode: str = "auto") -> int:
    """A chargeback arrives. What is it worth fighting, and what does the argument look like?

    The interesting case is the one that should not be fought. An agent that contests everything
    loses money politely; the value here is knowing which claim the evidence actually supports.
    """
    rail = make_rail(mode)
    ledger = Ledger()
    engine = Engine(ledger, rail)
    authority = _authority("Q4 office restock")
    _header("answering a chargeback", rail)

    paid = engine.authorise(authority, _payee(), 180.0)
    engine.capture(paid)
    print(f"payment   {paid.id}  180.00 USD  captured, receipted")
    print()

    # Three claims on the same kind of payment, with different evidence behind them.
    cases = [
        (
            "ITEM_NOT_RECEIVED",
            "The item never arrived.",
            [
                dispute_mod.Evidence(
                    "ev_ship", "delivery_proof", "carrier scan 4471 9920", "merchant: logistics"
                ),
                dispute_mod.Evidence(
                    "ev_date", "delivery_date", "delivered 2026-10-14 09:12", "merchant: logistics"
                ),
                dispute_mod.Evidence(
                    "ev_msg",
                    "customer_contact",
                    "customer confirmed receipt by email",
                    "merchant: inbox",
                ),
            ],
        ),
        (
            "ITEM_NOT_RECEIVED",
            "The item never arrived.",
            [
                dispute_mod.Evidence(
                    "ev_ship", "delivery_proof", "carrier scan 4471 9920", "merchant: logistics"
                )
            ],
        ),
        (
            "ITEM_NOT_RECEIVED",
            "The item never arrived.",
            [],
        ),
    ]

    for index, (reason_code, claim, attached) in enumerate(cases, start=1):
        dispute = dispute_mod.Dispute(
            id=f"dsp_{index:03d}",
            payment_id=paid.id,
            reason_code=reason_code,
            amount=180.0,
            currency="USD",
            claim=claim,
            due_at="2026-11-04",
        )
        evidence = dispute_mod.gather(dispute, ledger.transitions(paid.id), attached)
        result = dispute_mod.assess(dispute, evidence)

        print(f"dispute {index}   {reason_code}   {dispute.amount:.2f} {dispute.currency}")
        print(f'        claim       "{claim}"')
        print(
            f"        evidence    {len(attached)} attached, "
            f"{len(evidence) - len(attached)} from the payment's own receipts"
        )
        for finding in result.findings:
            mark = "ok  " if finding.satisfied else "MISS"
            print(
                f"        {mark}  {finding.requirement:<22} "
                f"{', '.join(finding.evidence_ids) or finding.note}"
            )
        print(
            f"        verdict     {result.recommendation.upper()}  coverage {result.coverage:.2f}"
        )
        print(f"        because     {result.explanation}")
        print()

    dispute = dispute_mod.Dispute(
        id="dsp_001",
        payment_id=paid.id,
        reason_code="ITEM_NOT_RECEIVED",
        amount=180.0,
        currency="USD",
        claim="The item never arrived.",
        due_at="2026-11-04",
    )
    best = dispute_mod.assess(
        dispute,
        dispute_mod.gather(
            dispute,
            ledger.transitions(paid.id),
            [
                dispute_mod.Evidence(
                    "ev_ship", "delivery_proof", "carrier scan 4471 9920", "merchant: logistics"
                ),
                dispute_mod.Evidence(
                    "ev_date", "delivery_date", "delivered 2026-10-14 09:12", "merchant: logistics"
                ),
                dispute_mod.Evidence(
                    "ev_msg", "customer_contact", "customer confirmed receipt", "merchant: inbox"
                ),
            ],
        ),
    )
    print("the response that gets filed")
    print(RULE)
    for line in best.narrative.splitlines():
        print(f"   {line}")
    print()
    print("Every requirement maps to an evidence id, and the template cannot add a claim that no")
    print(
        "evidence supports. Half of this packet already existed because the payment was receipted."
    )
    print()
    return 0


def story_alert(mode: str = "auto") -> int:
    """A pre-chargeback alert, and the 20 hours PayPal gives a merchant to decide.

    This is the sharpest case in the product, because the deadline is short enough that a human
    triage rota usually misses it, and the two ways to be wrong cost differently: refunding gives
    away a sale, contesting without evidence gives away the sale and a fee.
    """
    rail = make_rail(mode)
    ledger = Ledger()
    engine = Engine(ledger, rail)
    authority = _authority("Q4 office restock")
    _header("a pre-chargeback alert, 20 hours on the clock", rail)

    paid = engine.authorise(authority, _payee(), 180.0)
    engine.capture(paid)
    print(f"payment   {paid.id}  180.00 USD  captured and receipted")
    print("alert     PayPal warns a chargeback is coming; 20 hours to refund and avoid the fee")
    print()

    shipped = [
        dispute_mod.Evidence("ev_ship", "delivery_proof", "carrier scan 4471 9920", "logistics"),
        dispute_mod.Evidence("ev_date", "delivery_date", "delivered 14 Oct 09:12", "logistics"),
        dispute_mod.Evidence("ev_msg", "customer_contact", "customer confirmed receipt", "inbox"),
    ]

    for label, attached in (("no delivery evidence", []), ("delivery evidence on file", shipped)):
        alert = dispute_mod.Alert(
            id=f"alr_{label.split()[0]}",
            payment_id=paid.id,
            reason_code="ITEM_NOT_RECEIVED",
            amount=180.0,
            currency="USD",
            hours_remaining=17.4,
        )
        evidence = dispute_mod.gather(alert, ledger.transitions(paid.id), attached)  # type: ignore[arg-type]
        triage = dispute_mod.triage_alert(alert, evidence)
        print(f"alert     {label}")
        print(f"        evidence    {len(attached)} attached, 17.4 hours left")
        print(f"        decision    {triage.decision.upper()}")
        print(f"        because     {triage.explanation}")
        print()

    print("Refunding gives away a sale. Contesting without evidence gives away the sale and a fee.")
    print("The agent's job is to know which one it is looking at, in under an hour, every time.")
    print()
    return 0


def story_market(mode: str = "auto") -> int:
    """One agent hires another. The money is held until the work meets the brief.

    This is the product in its natural shape: authorise, deliver, check, then capture or release.
    A purchase with a card moves through those stages in one step; contract work cannot.
    """
    rail = make_rail(mode)
    ledger = Ledger()
    engine = Engine(ledger, rail)
    _header("an agent hires an agent, and the money waits for the work", rail)

    authority = Authority(
        id="auth_catalogue",
        purpose="catalogue copy, 12 SKUs",
        ceiling=200.0,
        ttl_minutes=60,
    )
    seller = Payee(id="AG-COPY", name="Copy Agent")
    print("buyer     procurement agent wants 12 product descriptions")
    print("seller    a copywriting agent quotes 120.00 USD")
    print()

    brief = verify_mod.Brief(
        id="brf_catalogue",
        title="12 product descriptions",
        criteria=(
            verify_mod.Criterion("count", "twelve descriptions", "count", 12, pro_rata=True),
            verify_mod.Criterion("length", "40-60 words each", "min_words", 40),
            verify_mod.Criterion("material", "names the material", "mentions", "recycled"),
        ),
    )
    print("brief     agreed before any money moved")
    for criterion in brief.criteria:
        print(f"          - {criterion.label}")
    print()

    paid = engine.authorise(authority, seller, 120.0)
    print(f"held      {paid.id}  {paid.amount:.2f} USD authorised")
    print("          the money is committed and not taken; the seller cannot touch it yet")
    print()

    nine = tuple(
        f"Recycled aluminium bottle, {i}00ml, brushed finish, dishwasher safe, lid sold "
        f"separately, five year warranty included, ships in recycled packaging, made in a "
        f"solar powered facility, BPA free and phthalate free, leak tested, certified food "
        f"safe, and fully recyclable at end of life."
        for i in range(1, 10)
    )
    delivered = verify_mod.Deliverable(text="", items=nine, author="AG-COPY")
    verdict = verify_mod.verify(brief, delivered)

    print(
        f"delivery  {len(delivered.items)} descriptions, {delivered.words[0]} words each, "
        f"all naming the material"
    )
    for finding in verdict.findings:
        mark = "ok  " if finding.satisfied else "MISS"
        print(f"        {mark}  {finding.label:<28} {finding.note}")
    print()
    print(f"verdict   {verdict.decision.upper()}  {verdict.explanation}")

    if verdict.decision == "capture_full":
        engine.capture(paid)
    elif verdict.decision == "capture_partial":
        engine.capture(paid, round(paid.amount * verdict.fraction, 2))
        engine.void(paid)
    else:
        engine.void(paid)

    takens = (
        round(paid.amount * verdict.fraction, 2) if verdict.decision == "capture_partial" else 0.0
    )
    print(
        f"money     captured {takens:.2f}, released {round(paid.amount - takens, 2):.2f} "
        f"of {paid.amount:.2f}"
    )
    print("          both movements are receipted and both can be re-verified")
    print()

    print("The seller is not robbed and the buyer is not trusting a claim. The brief is the")
    print("contract, the check is the proof, and the money moves on the result.")
    print()
    return 0


def _character_histories() -> dict[str, character_mod.Character]:
    def spent(
        prefix: str, rows: list[tuple[str, str, float, str]]
    ) -> tuple[character_mod.Spent, ...]:
        return tuple(
            character_mod.Spent(f"{prefix}_{i}", vendor, purpose, amount, at)
            for i, (vendor, purpose, amount, at) in enumerate(rows, start=1)
        )

    studio = spent(
        "st",
        [
            (
                "PixelForge Design",
                "campaign artwork for spring launch",
                460.0,
                "2026-07-14T11:20:00",
            ),
            ("PixelForge Design", "campaign artwork revisions", 210.0, "2026-08-02T15:05:00"),
            ("Adobe Stock", "artwork licence renewal", 340.0, "2026-08-21T09:40:00"),
            ("PixelForge Design", "campaign artwork autumn", 520.0, "2026-09-11T14:10:00"),
            ("Figma", "design seats", 288.0, "2026-09-30T10:15:00"),
            ("PixelForge Design", "campaign artwork winter", 480.0, "2026-10-12T13:45:00"),
        ],
    )
    wholesaler = spent(
        "wh",
        [
            ("Copperline Supply", "copper fittings restock", 1820.0, "2026-07-21T08:10:00"),
            ("Northfield Freight", "pallet freight", 640.0, "2026-08-05T07:55:00"),
            ("Copperline Supply", "brass valves", 1240.0, "2026-08-29T09:20:00"),
            ("Northfield Freight", "pallet freight", 715.0, "2026-09-18T08:05:00"),
            ("Copperline Supply", "copper fittings restock", 1990.0, "2026-10-07T08:35:00"),
        ],
    )
    legal = spent(
        "lg",
        [
            ("Court Filing Services", "filing fees", 420.0, "2026-07-09T16:30:00"),
            ("Westlaw", "research subscription", 980.0, "2026-08-01T11:00:00"),
            ("Court Filing Services", "filing fees", 380.0, "2026-09-14T15:20:00"),
            ("Hollywood Storage", "case file storage", 300.0, "2026-09-28T10:40:00"),
            (
                "Marble Creative",
                "brand identity artwork for brochure",
                510.0,
                "2026-10-16T12:05:00",
            ),
        ],
    )
    return {
        "a design studio": character_mod.Character("a design studio", studio),
        "a plumbing wholesaler": character_mod.Character("a plumbing wholesaler", wholesaler),
        "a law firm": character_mod.Character("a law firm", legal),
    }


def story_character(mode: str = "auto") -> int:
    """The same payment, judged against three different businesses.

    A rule cannot tell these apart, because the payment is identical. Only the account's own history
    differs - and that history is the thing PayPal holds and nobody else does.
    """
    _header("the same payment, three businesses, three answers", "no rail - decided before money")

    request = character_mod.Request(
        vendor="PixelForge Design",
        purpose="campaign artwork for spring launch",
        amount=480.0,
        at="2026-10-29T11:05:00",
    )
    print(f"request   pay {request.vendor} {request.amount:.2f} for '{request.purpose}'")
    print("          identical in every diagram below. no ceiling is consulted.")
    print()

    for name, holder in _character_histories().items():
        verdict = character_mod.judge(holder, request, base_ceiling=500.0)
        print(f"{name}")
        print(f"        conformance {verdict.conformance:.2f}   ->  {verdict.decision.upper()}")
        for reason in verdict.reasons:
            mark = "ok  " if reason.score >= 0.7 else ("..." if reason.score >= 0.4 else "MISS")
            print(f"          {mark}  {reason.component:<8} {reason.detail}")
            if reason.cites:
                print(f"                cites {', '.join(reason.cites)}")
        print(f"        because     {verdict.explanation}")
        print(f"        ceiling     500.00 base -> {verdict.suggested_ceiling:.2f} suggested")
        print()

    print("Not one of these is a rule. The payment is the same and the answer is different,")
    print("because the account is different. That is a judgment about character.")
    print()

    print("And the judgment bites the account it trusts most:")
    studio = _character_histories()["a design studio"]
    odd = character_mod.Request(
        vendor="Unregistered Holdings Ltd",
        purpose="urgent cryptocurrency settlement",
        amount=9600.0,
        at="2026-10-29T03:12:00",
    )
    verdict = character_mod.judge(studio, odd, base_ceiling=500.0)
    print(f"request   pay {odd.vendor} {odd.amount:.2f} for '{odd.purpose}' at 03:12")
    print(f"        conformance {verdict.conformance:.2f}   ->  {verdict.decision.upper()}")
    print(f"        because     {verdict.explanation}")
    print()
    print("A design studio is allowed to buy design. It is not allowed to become a different")
    print("company at three in the morning - and the agent can say exactly why.")
    print()
    return 0


STORIES = {
    "full": story_full,
    "partial": story_partial,
    "retry": story_retry,
    "overreach": story_overreach,
    "expired": story_expired,
    "defend": story_defend,
    "alert": story_alert,
    "market": story_market,
    "character": story_character,
}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grace.demo", description=__doc__)
    parser.add_argument("--story", choices=sorted(STORIES), default="partial")
    parser.add_argument("--rail", choices=["auto", "sandbox", "simulated"], default="auto")
    parser.add_argument("--all", action="store_true", help="run every story in order")
    args = parser.parse_args(argv)

    try:
        if args.all:
            for name in (
                "full",
                "partial",
                "retry",
                "overreach",
                "expired",
                "defend",
                "alert",
                "market",
                "character",
            ):
                STORIES[name](args.rail)
            return 0
        return STORIES[args.story](args.rail)
    except RailError as error:
        print(f"rail error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
