"""HTTP surface: the payments in the gap, and the moves the agent made on each.

uvicorn grace.api:app --host 0.0.0.0 --port 8020
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from grace import receipts
from grace.engine import Engine
from grace.ledger import Ledger
from grace.models import Authority, Payee
from grace.rail import make_rail

WEB = Path(__file__).resolve().parents[2] / "web" / "index.html"

app = FastAPI(title="Grace", version="0.1.0", docs_url="/api/docs")

ledger = Ledger(os.environ.get("GRACE_DB", ":memory:"))
engine = Engine(ledger, make_rail(os.environ.get("GRACE_PAYPAL_MODE", "auto")))


class CaptureIn(BaseModel):
    amount: float | None = None
    actor: str = "agent"


def _payment_or_404(payment_id: str):
    payment = ledger.get_payment(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="no such payment")
    return payment


def _authority_or_404(payment) -> Authority:
    authority = ledger.get_authority(payment.authority_id)
    if authority is None:
        raise HTTPException(status_code=404, detail="no such authority")
    return authority


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return WEB.read_text()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "grace",
        "paypal_mode": engine.rail.mode,
        "signing": receipts.ALGORITHM,
    }


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    """Where the money is: open in the gap, captured, or released without ever moving."""
    return ledger.summary()


@app.get("/api/payments")
def payments() -> dict[str, Any]:
    return {"payments": ledger.payments()}


@app.get("/api/payments/{payment_id}")
def payment_detail(payment_id: str) -> dict[str, Any]:
    payment = _payment_or_404(payment_id)
    return {
        "payment": payment.public(),
        "authority": _authority_or_404(payment).public(),
        "transitions": ledger.transitions(payment_id),
    }


@app.post("/api/payments/{payment_id}/capture")
def capture(payment_id: str, body: CaptureIn) -> dict[str, Any]:
    payment = _payment_or_404(payment_id)
    action = engine.capture(payment, body.amount, actor=body.actor)
    return {"action": action.public(), "payment": payment.public()}


@app.post("/api/payments/{payment_id}/void")
def void(payment_id: str) -> dict[str, Any]:
    payment = _payment_or_404(payment_id)
    action = engine.void(payment)
    return {"action": action.public(), "payment": payment.public()}


@app.post("/api/payments/{payment_id}/retry")
def retry(payment_id: str) -> dict[str, Any]:
    payment = _payment_or_404(payment_id)
    action = engine.retry(payment)
    return {"action": action.public(), "payment": payment.public()}


@app.get("/api/transitions/{transition_id}/verify")
def verify(transition_id: str) -> dict[str, Any]:
    ok, message = ledger.verify(transition_id)
    return {"transition_id": transition_id, "ok": ok, "message": message}


@app.post("/api/transitions/{transition_id}/tamper-demo")
def tamper_demo(transition_id: str) -> dict[str, Any]:
    """Flip one field of a signed receipt and show both verdicts. Nothing is stored."""
    receipt = ledger.receipt(transition_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="no such transition")

    genuine_ok, genuine_message = receipts.verify(receipt)
    altered = copy.deepcopy(receipt)
    payload = altered.get("payload", {})
    before = payload.get("allowed", True)
    payload["allowed"] = not bool(before)
    altered_ok, altered_message = receipts.verify(altered)
    return {
        "transition_id": transition_id,
        "changed_field": "payload.allowed",
        "changed_from": before,
        "changed_to": payload["allowed"],
        "genuine": {"ok": genuine_ok, "message": genuine_message},
        "tampered": {"ok": altered_ok, "message": altered_message},
        "note": "The altered copy is returned, never stored. The real receipt is untouched.",
    }


@app.post("/api/demo/seed")
def seed() -> dict[str, Any]:
    """Run the five stories into the live ledger so the dashboard has something to show."""
    payee = Payee(id="V-ACME", name="Acme Office Supply")
    authority = Authority(
        id="auth_q4_office",
        purpose="Q4 office restock",
        ceiling=500.0,
        ttl_minutes=30,
    )
    created: list[str] = []

    # 1. captured in full
    paid = engine.authorise(authority, payee, 120.0)
    engine.capture(paid)
    created.append(paid.id)

    # 2. declined, partially captured, remainder released. Only the simulator can force the
    #    decline; against the sandbox this becomes a plain capture, which is the honest result.
    from grace import compensate
    from grace.rail import SimulatedRail

    partial = engine.authorise(authority, payee, 480.0)
    limited = isinstance(engine.rail, SimulatedRail)
    if limited:
        engine.rail.instrument_limit = 300.0
    failed = engine.capture(partial)
    if limited:
        chosen = compensate.plan(partial, authority, failed.rail)
        compensate.apply(engine, partial, chosen)
        engine.rail.instrument_limit = None
    created.append(partial.id)

    # 3. released without ever being taken
    released = engine.authorise(authority, payee, 240.0)
    engine.void(released)
    created.append(released.id)

    # 4. still open - money committed, waiting
    waiting = engine.authorise(authority, payee, 310.0)
    created.append(waiting.id)

    # 5. lapsed before capture
    lapsed = engine.authorise(authority, payee, 190.0)
    late = Engine(
        ledger,
        engine.rail,
        clock=lambda: datetime.now(timezone.utc) + timedelta(minutes=45),
    )
    late.expire(lapsed)
    created.append(lapsed.id)

    return {"created": created, **ledger.summary(), **{"payments": ledger.payments()}}
