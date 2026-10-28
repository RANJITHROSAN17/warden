"""Five minutes of proof, runnable offline.

Five things that happen to a payment after it is authorised, and what the agent does about each.
The inputs are scripted; the outcomes are decided by the real engine and the real rules.

    python -m grace.demo --story partial
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from grace import compensate
from grace import dispute as dispute_mod
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
            "MERCHANDISE_NOT_RECEIVED",
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
            "MERCHANDISE_NOT_RECEIVED",
            "The item never arrived.",
            [
                dispute_mod.Evidence(
                    "ev_ship", "delivery_proof", "carrier scan 4471 9920", "merchant: logistics"
                )
            ],
        ),
        (
            "MERCHANDISE_NOT_RECEIVED",
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
        reason_code="MERCHANDISE_NOT_RECEIVED",
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


STORIES = {
    "full": story_full,
    "partial": story_partial,
    "retry": story_retry,
    "overreach": story_overreach,
    "expired": story_expired,
    "defend": story_defend,
}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grace.demo", description=__doc__)
    parser.add_argument("--story", choices=sorted(STORIES), default="partial")
    parser.add_argument("--rail", choices=["auto", "sandbox", "simulated"], default="auto")
    parser.add_argument("--all", action="store_true", help="run every story in order")
    args = parser.parse_args(argv)

    try:
        if args.all:
            for name in ("full", "partial", "retry", "overreach", "expired", "defend"):
                STORIES[name](args.rail)
            return 0
        return STORIES[args.story](args.rail)
    except RailError as error:
        print(f"rail error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
