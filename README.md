<div align="center">

# Grace

**The agent that finishes the payment — and defends it afterwards.**

A payment is authorised, then it waits. Grace is what happens in that wait, and what happens
when someone disputes it later.

`PayPal Orders API` · `Authorize → Capture → Void` · `partial capture` · `disputes` · `signed receipts` · `MIT`

<sub>Built for the PayPal AI Hackathon. Not affiliated with or endorsed by PayPal.
Sandbox only — no real money moves anywhere in this repository.</sub>

</div>

---

## Two moments where AI is genuinely required

**1. The gap after authorisation.** Money committed, customer waiting, capture can still fail.

**2. The chargeback afterwards.** A deadline, a reason code, and a demand for evidence. Merchants
lose these for boring reasons — the evidence existed but nobody assembled it, or it was never there
and nobody noticed before paying a fee to lose anyway.

A spending limit is a rule. Both of these are *judgment*: reading a failure, reading a claim,
matching it against what actually exists, and deciding. That is the work a model does and a rule
cannot.

```
                    ┌──────────────────┐
   authorise ──────▶│  the gap         │──── capture / partial / void
                    └──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
   chargeback ─────▶│  the defence     │──── contest / contest partly / accept
                    └──────────────────┘
```

## The problem

A payment is not one event. It is at least three:

```
create order → AUTHORISE → [ the gap ] → CAPTURE
```

In the gap the money is **already committed**, the customer is **already waiting**, and things can
still go wrong: the issuer declines the full amount, the rail times out, the authorisation lapses,
the order ships in part.

Most payment controls in the industry guard the decision *before* the money is committed — risk
scores, spending limits, mandates. Almost nothing governs the window *after*, and that window is
where a payment becomes a half-finished thing: money held, nothing shipped, nobody sure what to do.

Today that window is handled by saga steps with hardcoded compensation rules and a human queue
behind them. Grace replaces the queue with a **bounded agent**: it reads the failure, chooses the
smallest correct next move, and every move — including every refusal — is checked against the
authority that allowed the payment in the first place, and signed.

## What it does

```
1  capture the authorised amount
RAIL  capture            480.00 agent              issuer declined 480.00: INSTRUMENT_DECLINED
        state       authorised   captured 0.00 of 480.00   open 480.00

2  the agent reads the failure
        plan        partial_capture 300.0
        because     the issuer will clear 300.00 but not 480.00; take what is
                    available and release the rest

3  the plan goes back through the same rules
OK    capture            300.00 compensation-agent captured 300.00 of 480.00
OK    void               180.00 compensation-agent released 180.00 USD; it was never taken
        state       part_released   captured 300.00 of 480.00   closed
```

**The buyer gets goods today instead of a support ticket. The seller is paid for what it can ship.
The remaining 180.00 never leaves — there is nothing to refund, because the money never moved.**

`RAIL` is not `STOP`: the policy allowed the capture and the issuer refused it. Those are different
facts, they are recorded separately, and the difference is the whole product.

## Five things that happen after authorisation

```bash
python -m grace.demo --all
```

| Story | What goes wrong | What the agent does |
|---|---|---|
| `full` | nothing | captures in full — the baseline |
| `partial` | issuer clears only part | captures what is available, releases the rest |
| `retry` | rail times out | retries, because a timeout is not a decline |
| `overreach` | agent tries to take more than authorised | refused, receipted, then does the right thing |
| `expired` | authorisation lapses | refuses the capture; the commitment has ended |

## The defence

```bash
python -m grace.demo --story defend
```

A chargeback arrives. Every reason code demands specific evidence, so the agent maps what exists
against what the code requires — including evidence that is **already in the payment's own signed
record**, because a capture the rail confirmed is a stronger fact than a screenshot.

```
dispute 1   MERCHANDISE_NOT_RECEIVED   180.00 USD
        evidence    3 attached, 2 from the payment's own receipts
        ok    delivery_proof         ev_ship
        ok    delivery_date          ev_date
        ok    customer_contact       ev_msg
        verdict     CONTEST  coverage 1.00

dispute 2   (delivery proof only)
        ok    delivery_proof         ev_ship
        MISS  delivery_date          no evidence of this kind exists
        MISS  customer_contact       no evidence of this kind exists
        verdict     CONTEST_PARTIAL  coverage 0.33

dispute 3   (no evidence at all)
        MISS  delivery_proof         no evidence of this kind exists
        verdict     ACCEPT  coverage 0.00
```

**The most valuable recommendation is the third one.** An agent that contests everything loses money
politely; knowing which claim the evidence cannot support is the product.

```
Response to dispute dsp_001 (MERCHANDISE_NOT_RECEIVED, 180.00 USD).

The cardholder states: The item never arrived.

In response, the merchant relies on the following records:
  - delivery_proof: evidenced by ev_ship (carrier scan 4471 9920 — merchant: logistics).
  - delivery_date: evidenced by ev_date (delivered 2026-10-14 09:12 — merchant: logistics).
  - customer_contact: evidenced by ev_msg (customer confirmed receipt — merchant: inbox).

The merchant does not assert any fact that is not listed above.
```

That last line is generated by a template, not a model, and that is deliberate: **a model may make
the argument read better; it must not be able to add a claim no evidence supports.** Every sentence
maps to an evidence id, and evidence that answers nothing is listed separately as
`declined_evidence` rather than padding the packet.

## The bound

The compensation agent has real power: it can take part of the money, retry it, or give it back.
It **cannot** take more than the authority allowed, retry past its budget, capture after expiry, or
behave as though a partial capture was a full one.

| Rule | What it prevents |
|---|---|
| `capture.within_authorised` | taking more than was committed |
| `capture.amount_valid` | zero or negative captures |
| `authority.partial_permitted` | partial settlement where the authority demands a full one |
| `authority.attempt_budget` | retry storms and slow-motion capture loops |
| `authority.retry_permitted` | retrying where the payer forbade it |
| `payment.open` | double capture — taking the same money twice |
| `authorisation.not_expired` | settling against a lapsed commitment |

Every refusal carries a **counterfactual** ("capture at most 480.00, or authorise a larger amount")
and a signed receipt, because "why didn't this capture?" is the question that ends up in a support
ticket six weeks later.

## The receipt

Every transition — allowed or refused — is signed with HMAC-SHA256 over a canonical payload of the
payment, the checks, the rail response, and the reason. Anyone can re-verify it without trusting the
database that produced it. Press **Tamper demo** in the dashboard:

```
VALID     as signed          signature valid
INVALID   one field flipped  signature does not match payload
```

The altered copy is never stored; the real receipt is byte-identical afterwards, and a test asserts
it.

## Run it

```bash
pip install -e ".[server,dev]"

python -m grace.demo --all          # five stories, offline, no credentials
python -m grace.cli preflight       # what rail and signing key this process will use
python -m grace.cli serve           # dashboard on http://localhost:8020
pytest                              # 18 tests
```

**Sandbox mode.** Set `GRACE_PAYPAL_CLIENT_ID` and `GRACE_PAYPAL_CLIENT_SECRET` and Grace will call
the real PayPal Orders API — `create_order`, `authorize`, `capture` (including partial amounts), and
`void`. It will **not** silently fall back to the simulator if the credentials are wrong; a rail that
pretends to work is the failure this project exists to catch.

## Honest notes

- Signing is HMAC with a local key. Production wants a KMS and asymmetric keys. The shape is right;
  the key management is not.
- The compensation agent is deterministic today. The interesting part is not that a model could
  write the plan — it is that the plan is bounded and every step is receipted regardless of who wrote
  it. A model-backed planner is a drop-in that changes the prose, not the powers.
- Simulated failures (`instrument_limit`, `transient_failures`) exist so the failure paths are
  exercised rather than described. Against the sandbox you need real declines.
- This is infrastructure, not a consumer app. It is deliberately boring in the places money is
  boring.

## Layout

```
src/grace/
  models.py      authority, payment, the five states, a signed Action
  engine.py      the state machine and the seven rules
  compensate.py  the agent: read a failure, choose the smallest correct move
  rail.py        PayPal Orders API, or a simulator with real failure modes
  ledger.py      payments, transitions, receipts, and where the money is
  api.py         HTTP surface + the dashboard's data
  demo.py        five stories
docs/
  DESIGN.md      the state machine, the rules, and why each one exists
  PAYPAL.md      where this sits in PayPal's architecture, and why it pays
```
