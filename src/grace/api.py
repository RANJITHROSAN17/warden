"""HTTP surface: the payments in the gap, and the moves the agent made on each.

uvicorn grace.api:app --host 0.0.0.0 --port 8020
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from grace import character as character_mod
from grace import dispute as dispute_mod
from grace import receipts
from grace.engine import Engine
from grace.ledger import Ledger
from grace.models import Authority, Payee
from grace.rail import make_rail


def _find_web() -> Path:
    """Locate the dashboard whether we run from source or from an installed wheel.

    An installed package lives in site-packages, so the source-tree path does not exist in the
    container. Check, in order: an explicit override, the working directory, the source tree, then
    the copy shipped inside the package.
    """
    candidates = [
        Path(os.environ["GRACE_WEB"]) if os.environ.get("GRACE_WEB") else None,
        Path.cwd() / "web" / "index.html",
        Path(__file__).resolve().parents[2] / "web" / "index.html",
        Path(__file__).resolve().parent / "web" / "index.html",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return Path("web/index.html")


WEB = _find_web()

app = FastAPI(title="Grace", version="0.1.0", docs_url="/api/docs")


def require_token(x_grace_token: str = Header(default="")) -> None:
    """Guard every state-changing route.

    A demo on a public URL that lets anyone capture, void or re-seed payments is not a demo, it is
    an incident. When GRACE_API_TOKEN is unset the app still refuses to be silently open: the
    startup banner says so, and the token check passes only because there is nothing secret to
    protect yet.
    """
    expected = os.environ.get("GRACE_API_TOKEN", "")
    if not expected:
        return
    if x_grace_token != expected:
        raise HTTPException(status_code=401, detail="missing or wrong X-Grace-Token")


WRITE = [Depends(require_token)]

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


@app.on_event("startup")
def _report_what_is_running() -> None:
    """Say out loud which rail and which matcher are actually active.

    The review's point stands: a dashboard must never imply it is using PayPal or a model when it
    has quietly fallen back to the simulator or the lexicon.
    """
    from grace import character as character_mod

    rail_mode = getattr(engine.rail, "mode", "unknown")
    matcher = character_mod.matcher_from_env()
    token = "set" if os.environ.get("GRACE_API_TOKEN") else "NOT SET - write routes are open"
    lines = [
        "",
        "  Grace",
        f"    rail      {rail_mode}"
        + ("   (offline - no money moves)" if rail_mode == "simulated" else "   (PayPal sandbox)"),
        f"    matcher   {matcher.name}"
        + (
            "   (fallback - set GRACE_EMBED_URL for semantic matching)"
            if matcher.name == "lexical"
            else "   (embeddings)"
        ),
        f"    model     {'configured' if os.environ.get('GRACE_MODEL_URL') else 'not configured'}",
        f"    receipts  key {os.environ.get('GRACE_SIGNING_KEY_ID', 'local-dev')}",
        f"    api token {token}",
        "",
    ]
    print("\n".join(lines), flush=True)


@app.get("/api/runtime")
def runtime() -> dict[str, Any]:
    """The same report, over HTTP, so the dashboard can show it instead of implying it."""
    from grace import character as character_mod

    matcher = character_mod.matcher_from_env()
    return {
        "rail": getattr(engine.rail, "mode", "unknown"),
        "matcher": matcher.name,
        "matcher_is_fallback": matcher.name == "lexical",
        "model_configured": bool(os.environ.get("GRACE_MODEL_URL")),
        "api_token_required": bool(os.environ.get("GRACE_API_TOKEN")),
    }


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


# ── disputes ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/disputes")
def list_disputes() -> dict[str, Any]:
    return {"disputes": ledger.disputes()}


@app.post("/api/disputes/{dispute_id}/assess", dependencies=WRITE)
def assess_dispute(dispute_id: str) -> dict[str, Any]:
    """Gather what exists, map it to the reason code, and say whether fighting is worth a fee."""
    stored = ledger.get_dispute(dispute_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="no such dispute")

    found = dispute_mod.Dispute(
        id=stored["id"],
        payment_id=stored["payment_id"],
        reason_code=stored["reason_code"],
        amount=stored["amount"],
        currency=stored["currency"],
        claim=stored["claim"],
        due_at=stored["due_at"],
    )
    attached = [dispute_mod.Evidence(**item) for item in stored["attached"]]
    history = ledger.transitions(stored["payment_id"])
    evidence = dispute_mod.gather(found, history, attached)
    result = dispute_mod.assess(found, evidence)
    ledger.save_assessment(dispute_id, result.public())
    return {"assessment": result.public(), "evidence_gathered": len(evidence)}


@app.post("/api/disputes/{dispute_id}/file", dependencies=WRITE)
def file_dispute(dispute_id: str) -> dict[str, Any]:
    """File the response. Refuses to file a contest the evidence does not support."""
    stored = ledger.get_dispute(dispute_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="no such dispute")
    assessment = stored.get("assessment")
    if not assessment:
        raise HTTPException(status_code=409, detail="assess the dispute before filing it")
    if assessment["recommendation"] == "accept":
        ledger.mark_filed(dispute_id)
        return {
            "filed": "accepted",
            "reason": assessment["explanation"],
            "note": "accepting is a decision too, and it is recorded.",
        }
    signed = receipts.sign(
        {
            "receipt_version": 1,
            "kind": "dispute_response",
            "dispute": assessment["dispute_id"],
            "recommendation": assessment["recommendation"],
            "packet": assessment["packet"],
            "narrative": assessment["narrative"],
        }
    )
    ledger.mark_filed(dispute_id)
    return {
        "filed": "contested",
        "recommendation": assessment["recommendation"],
        "packet": assessment["packet"],
        "narrative": assessment["narrative"],
        "receipt": signed,
    }


CHARACTER_REQUEST = {
    "vendor": "PixelForge Design",
    "purpose": "campaign artwork for spring launch",
    "amount": 480.0,
    "at": "2026-10-29T11:05:00",
}

CHARACTER_ODD = {
    "vendor": "Unregistered Holdings Ltd",
    "purpose": "urgent cryptocurrency settlement",
    "amount": 9600.0,
    "at": "2026-10-29T03:12:00",
}


@app.get("/api/character")
def character() -> dict[str, Any]:
    """One payment, judged against three different businesses. The idea, in one JSON."""
    from grace.demo import _character_histories

    out = []
    for name, holder in _character_histories().items():
        normal = character_mod.judge(holder, character_mod.Request(**CHARACTER_REQUEST))
        odd = character_mod.judge(holder, character_mod.Request(**CHARACTER_ODD))
        out.append(
            {
                "account": name,
                "in_character": normal.public(),
                "out_of_character": odd.public(),
            }
        )
    return {"request": CHARACTER_REQUEST, "odd_request": CHARACTER_ODD, "accounts": out}


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


@app.post("/api/payments/{payment_id}/capture", dependencies=WRITE)
def capture(payment_id: str, body: CaptureIn) -> dict[str, Any]:
    payment = _payment_or_404(payment_id)
    action = engine.capture(payment, body.amount, actor=body.actor)
    return {"action": action.public(), "payment": payment.public()}


@app.post("/api/payments/{payment_id}/void", dependencies=WRITE)
def void(payment_id: str) -> dict[str, Any]:
    payment = _payment_or_404(payment_id)
    action = engine.void(payment)
    return {"action": action.public(), "payment": payment.public()}


@app.post("/api/payments/{payment_id}/retry", dependencies=WRITE)
def retry(payment_id: str) -> dict[str, Any]:
    payment = _payment_or_404(payment_id)
    action = engine.retry(payment)
    return {"action": action.public(), "payment": payment.public()}


@app.get("/api/transitions/{transition_id}/verify")
def verify(transition_id: str) -> dict[str, Any]:
    ok, message = ledger.verify(transition_id)
    return {"transition_id": transition_id, "ok": ok, "message": message}


@app.post("/api/transitions/{transition_id}/tamper-demo", dependencies=WRITE)
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


@app.post("/api/demo/seed", dependencies=WRITE)
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

    _seed_disputes(paid.id)
    return {
        "created": created,
        **ledger.summary(),
        "payments": ledger.payments(),
        "disputes": ledger.disputes(),
    }


def _seed_disputes(payment_id: str) -> None:
    """Four chargebacks against one captured payment, each with different evidence behind it."""
    shipped = [
        {
            "id": "ev_ship",
            "kind": "PROOF_OF_FULFILLMENT",
            "label": "carrier scan 4471 9920",
            "source": "merchant: logistics",
            "detail": "",
        },
        {
            "id": "ev_date",
            "kind": "PROOF_OF_DELIVERY_SIGNATURE",
            "label": "delivered 2026-10-14 09:12",
            "source": "merchant: logistics",
            "detail": "",
        },
        {
            "id": "ev_msg",
            "kind": "ONLINE_TRACKING_INFORMATION",
            "label": "customer confirmed receipt",
            "source": "merchant: inbox",
            "detail": "",
        },
    ]
    cases = [
        ("dsp_001", "ITEM_NOT_RECEIVED", "The item never arrived.", shipped),
        ("dsp_002", "ITEM_NOT_RECEIVED", "The item never arrived.", shipped[:1]),
        ("dsp_003", "ITEM_NOT_RECEIVED", "The item never arrived.", []),
        ("dsp_004", "UNAUTHORIZED", "I do not recognise this charge.", []),
    ]
    for dispute_id, reason_code, claim, attached in cases:
        found = dispute_mod.Dispute(
            id=dispute_id,
            payment_id=payment_id,
            reason_code=reason_code,
            amount=180.0,
            currency="USD",
            claim=claim,
            due_at="2026-11-04",
        )
        ledger.save_dispute(found, attached)
        history = ledger.transitions(payment_id)
        evidence = dispute_mod.gather(found, history, [dispute_mod.Evidence(**a) for a in attached])
        ledger.save_assessment(dispute_id, dispute_mod.assess(found, evidence).public())
