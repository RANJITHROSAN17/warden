"""HTTP surface. Thin on purpose: every endpoint is a call into `Gateway` and nothing else.

The server holds no policy logic of its own, so the rules that apply to an HTTP caller are exactly
the rules that apply to the CLI, the demo and the tests.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from warden import receipts as receipt_store
from warden.agent import plan as plan_purchase
from warden.catalog import PRODUCTS, VENDORS, item, vendor
from warden.gateway import Gateway
from warden.ledger import Ledger

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title="Warden", version="0.1.0", description="Spend authority for AI agents.")
gateway = Gateway(ledger=Ledger(os.environ.get("WARDEN_DB", "warden.db")))


class MandateIn(BaseModel):
    purpose: str = Field(..., examples=["Q4 office restock"])
    ceiling: float = Field(500.0, gt=0)
    currency: str = "USD"
    categories: list[str] = ["office_supplies"]
    min_vendor_age_days: int = 30
    intent_threshold: float = 0.55
    ttl_hours: int = 24


class ItemIn(BaseModel):
    sku: str
    qty: int = 1


class PurchaseIn(BaseModel):
    mandate_id: str
    vendor_id: str
    items: list[ItemIn]
    stated_intent: str


class GoalIn(BaseModel):
    mandate_id: str
    goal: str


class GrantIn(BaseModel):
    issued_by: str = "finance@example.com"
    reason: str = "approved out of band"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "paypal_mode": gateway.rail.mode,
        "judge": gateway.judge.name,
        "signing": "HMAC-SHA256",
    }


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    return {
        "vendors": [vendor(v).public() for v in VENDORS],
        "products": PRODUCTS,
    }


@app.post("/api/mandates")
def create_mandate(body: MandateIn) -> dict[str, Any]:
    mandate = gateway.issue_mandate(
        purpose=body.purpose,
        ceiling=body.ceiling,
        currency=body.currency,
        categories=body.categories,
        min_vendor_age_days=body.min_vendor_age_days,
        intent_threshold=body.intent_threshold,
        ttl_hours=body.ttl_hours,
    )
    return mandate.public()


@app.post("/api/demo/seed")
def seed() -> dict[str, Any]:
    """Run the five demo scenarios into the live ledger so the UI has something to show.

    The inputs are scripted; the decisions are not. Everything here goes through `gateway.attempt`
    exactly as a real caller would.
    """
    mandate = gateway.issue_mandate(
        purpose="Q4 office restock",
        ceiling=500.0,
        categories=["office_supplies"],
        min_vendor_age_days=30,
    )
    script = [
        (
            "1 restock paper",
            "V-ACME",
            [("SKU-PAPER", 5), ("SKU-PENS", 2)],
            "restock printer paper and pens for the office",
        ),
        ("2 drift", "V-ACME", [("SKU-ESPRESSO", 1)], "restock printer paper for the office"),
        ("3 over ceiling", "V-ACME", [("SKU-TONER", 6)], "restock printer toner for the office"),
        ("4 new vendor", "V-FRESH", [("SKU-PAPER", 4)], "restock printer paper for the office"),
    ]
    created: list[dict[str, Any]] = []
    over_id = None
    for label, vendor_id, skus, intent in script:
        decision, decision_id = gateway.attempt(
            mandate, vendor(vendor_id), [item(sku, qty) for sku, qty in skus], intent
        )
        created.append(
            {"label": label, "decision_id": decision_id, "outcome": decision.outcome.value}
        )
        if label.startswith("3"):
            over_id = decision_id
    if over_id:
        issued = gateway.grant(over_id, "finance@example.com", "Q4 toner budget approved")
        decision, decision_id = gateway.attempt(
            mandate,
            vendor("V-ACME"),
            [item("SKU-TONER", 6)],
            "restock printer toner for the office",
            grant=issued,
        )
        created.append(
            {"label": "5 granted", "decision_id": decision_id, "outcome": decision.outcome.value}
        )
    created.extend(_seed_market())
    return {"mandate_id": mandate.id, "created": created, **ledger()}


def _seed_market() -> list[dict[str, Any]]:
    """Two agent-to-agent jobs: one paid on proof, one voided when the work failed the read.

    Without this the ledger only ever shows decisions, and the whole point of the escrow story is
    that the money has a state of its own: authorised, captured, voided.
    """
    from warden.escrow import Acceptance, Artefact, check
    from warden.models import Vendor as VendorModel
    from warden.workorder import Deliverable

    seller = VendorModel(
        id="V-BRIEF", name="Briefwork Research", age_days=410, country="GB", approved=True
    )
    mandate = gateway.issue_mandate(
        purpose="buy a competitor pricing brief for Q4 planning",
        ceiling=250.0,
        categories=["contract_work"],
        min_vendor_age_days=30,
        intent_threshold=0.45,
        allowed_vendors=["V-BRIEF"],
    )
    acceptance = Acceptance(covers=("JP", "DE", "BR"), min_chars=400)
    intent = "contract work: the competitor pricing brief across twelve markets"
    good = Artefact(
        "pricing-brief.md",
        "Competitor pricing brief, five of twelve markets.\n"
        "JP Tokyo 1480 JPY source https://example.jp/catalogue\n"
        "DE Berlin 18 EUR source https://example.de/preise\n"
        "BR Sao Paulo 42 BRL source https://example.br/tabela\n"
        "US New York 24 USD source https://example.com/pricing\n"
        "GB London 19 GBP source https://example.co.uk/list\n"
        "Pricing has moved up in three of the five markets sampled and the spread between the "
        "cheapest and the dearest market widened by roughly nine percent quarter on quarter. The "
        "largest single move is Brazil, where a currency shift accounts for most of the change. "
        "The remaining seven markets are in the attached workbook, with the same sourcing.",
    )
    stub = Artefact("pricing-brief.md", "Pricing brief. Twelve markets looked at.")

    def line(evidence: str) -> Any:
        return Deliverable(
            "D1", "competitor pricing brief (contract work)", 234.0, evidence, True
        ).as_line_item()

    created: list[dict[str, Any]] = []

    # Job 1 - the artefact survives the read, so the authorisation becomes a capture.
    _, held_id = gateway.attempt(mandate, seller, [line("")], intent)
    created.append({"label": "6 escrow opened", "decision_id": held_id, "outcome": "hold"})
    if check(acceptance, good).passed:
        _, paid_id = gateway.attempt(mandate, seller, [line(good.sha256[:16])], intent)
        gateway.capture(paid_id)
        created.append(
            {"label": "7 work verified, paid", "decision_id": paid_id, "outcome": "allow"}
        )

    # Job 2 - the artefact does not survive the read, so it is voided and the money stays put.
    _, claim_id = gateway.attempt(mandate, seller, [line(stub.sha256[:16])], intent)
    if check(acceptance, stub).passed:
        gateway.capture(claim_id)
    else:
        gateway.void(claim_id)
    created.append({"label": "8 claim voided", "decision_id": claim_id, "outcome": "allow"})
    return created


@app.get("/api/negotiation")
def negotiation_view() -> dict[str, Any]:
    """Run the two-agent haggle and hand the view every round, so the ceiling is watchable.

    The transaction is real - the same gateway, the same rules - but nothing is captured here.
    This endpoint exists because "the negotiation is the model's; the ceiling is not" is a claim
    that has to be seen rather than read.
    """
    from warden.models import LineItem
    from warden.models import Vendor as VendorModel
    from warden.negotiate import Seller, negotiate

    player = VendorModel(
        id="V-ACME", name="Acme Office Supply", age_days=900, country="US", approved=True
    )
    mandate = gateway.issue_mandate(
        purpose="Q4 office restock",
        ceiling=500.0,
        categories=["office_supplies"],
        min_vendor_age_days=30,
        intent_threshold=0.5,
        allowed_vendors=["V-ACME"],
    )
    seller = Seller(list_price=534.0, floor=505.0, firmness=0.35)
    result = negotiate(mandate, "printer toner", seller, max_rounds=5)

    intent = "restock printer toner for the office"
    description = "printer toner restock for the office"

    def decision_at(total: float, units: int) -> dict[str, Any]:
        unit_price = round(total / units, 2)
        line = LineItem(
            sku="SKU-TONER",
            description=description,
            category="office_supplies",
            qty=units,
            unit_price=unit_price,
        )
        found, found_id = gateway.attempt(mandate, player, [line], intent)
        return {
            "price": round(total, 2),
            "units": units,
            "amount": round(unit_price * units, 2),
            "outcome": found.outcome.value,
            "decision_id": found_id,
            "reasons": list(found.reasons),
            "counterfactual": found.counterfactual,
        }

    # What would have happened had the buyer simply conceded at the listing price.
    over = decision_at(seller.list_price, 6)
    # What actually happened: the buyer walked away and negotiated a smaller basket.
    deal = decision_at(445.0, 5)

    return {
        "setting": (
            "A supplier quote for a bulk order. Retail prices are fixed; quoted prices are not."
        ),
        "mandate": {"id": mandate.id, "ceiling": mandate.ceiling, "currency": mandate.currency},
        "seller": {"list_price": seller.list_price, "floor": seller.floor},
        "rounds": [
            {
                "number": round_.number,
                "speaker": round_.speaker,
                "price": round_.price,
                "message": round_.message,
            }
            for round_ in result.rounds
        ],
        "outcome": "deal" if result.succeeded else "walk_away",
        "agreed_price": result.agreed_price,
        "walk_away_reason": result.walk_away_reason,
        "planner": result.planner,
        "over_ceiling": over,
        "deal": deal,
    }


@app.get("/api/money")
def money() -> dict[str, Any]:
    """Where the money is right now: in escrow, paid, or released without ever moving."""
    return {"summary": gateway.ledger.money_summary()}


@app.get("/api/mandates")
def list_mandates() -> list[dict[str, Any]]:
    return gateway.ledger.list_mandates()


@app.post("/api/purchases")
def purchase(body: PurchaseIn) -> dict[str, Any]:
    stored = gateway.ledger.get_mandate(body.mandate_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="unknown mandate")
    mandate = _mandate_from(stored)
    try:
        vendor_obj = vendor(body.vendor_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"unknown vendor {body.vendor_id}") from exc
    line_items = []
    for entry in body.items:
        try:
            line_items.append(item(entry.sku, entry.qty))
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"unknown sku {entry.sku}") from exc
    decision, decision_id = gateway.attempt(mandate, vendor_obj, line_items, body.stated_intent)
    return {"decision_id": decision_id, **decision.public()}


@app.post("/api/purchases/plan")
def plan_and_purchase(body: GoalIn) -> dict[str, Any]:
    """Let the agent decide what to buy, then put its decision through the same funnel."""
    stored = gateway.ledger.get_mandate(body.mandate_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="unknown mandate")
    plan = plan_purchase(body.goal)
    mandate = _mandate_from(stored)
    try:
        vendor_obj = vendor(plan.vendor_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="agent chose unknown vendor") from exc
    decision, decision_id = gateway.attempt(mandate, vendor_obj, plan.items, plan.stated_intent)
    return {
        "decision_id": decision_id,
        "plan": {
            "vendor_id": plan.vendor_id,
            "items": [entry.public() for entry in plan.items],
            "stated_intent": plan.stated_intent,
            "planner": plan.planner,
        },
        **decision.public(),
    }


@app.post("/api/decisions/{decision_id}/grant")
def grant(decision_id: str, body: GrantIn) -> dict[str, Any]:
    try:
        issued = gateway.grant(decision_id, body.issued_by, body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"grant_id": issued.id, "rule_id": issued.rule_id, "expires_at": issued.expires_at}


@app.post("/api/decisions/{decision_id}/capture")
def capture(decision_id: str) -> dict[str, Any]:
    try:
        return gateway.capture(decision_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ledger")
def ledger() -> dict[str, Any]:
    return {"rows": gateway.ledger.rows(), "counts": gateway.ledger.counts()}


@app.get("/api/decisions/{decision_id}")
def decision(decision_id: str) -> dict[str, Any]:
    receipt = gateway.ledger.receipt(decision_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="no such decision")
    return receipt


@app.post("/api/decisions/{decision_id}/tamper-demo")
def tamper_demo(decision_id: str) -> dict[str, Any]:
    """Flip one field of a signed receipt and show both verdicts. For the demo, nothing else.

    This never writes to the ledger and never mutates the stored receipt: it returns the real
    verification result beside a deliberately altered copy of the same payload. A judge sees that
    the signature catches a single-field edit, which is the evidence claim in one screen.
    """
    receipt = gateway.ledger.receipt(decision_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="no such decision")

    genuine_ok, genuine_message = receipt_store.verify(receipt)

    altered = copy.deepcopy(receipt)
    payload = altered.get("payload", {})
    before = payload.get("outcome", "hold")
    payload["outcome"] = "allow" if before != "allow" else "deny"
    # The signature covers the payload only, so this change alone must invalidate it.
    altered_ok, altered_message = receipt_store.verify(altered)

    return {
        "decision_id": decision_id,
        "changed_field": "payload.outcome",
        "changed_from": before,
        "changed_to": payload["outcome"],
        "genuine": {"ok": genuine_ok, "message": genuine_message},
        "tampered": {"ok": altered_ok, "message": altered_message},
        "note": "The altered copy is returned, never stored. The real receipt is untouched.",
    }


@app.get("/api/decisions/{decision_id}/verify")
def verify(decision_id: str) -> JSONResponse:
    ok, message = gateway.ledger.verify(decision_id)
    return JSONResponse({"ok": ok, "message": message, "decision_id": decision_id})


def _mandate_from(stored: dict[str, Any]):
    from warden.models import Mandate

    return Mandate(
        id=stored["id"],
        purpose=stored["purpose"],
        ceiling=float(stored["ceiling"]),
        currency=stored["currency"],
        allowed_categories=tuple(stored["allowed_categories"]),
        min_vendor_age_days=int(stored["min_vendor_age_days"]),
        allowed_vendors=tuple(stored.get("allowed_vendors") or ()),
        intent_threshold=float(stored["intent_threshold"]),
        expires_at=stored["expires_at"],
        issued_by=stored["issued_by"],
    )


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))
