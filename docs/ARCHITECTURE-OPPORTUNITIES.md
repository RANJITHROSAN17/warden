# Where AI actually fits in PayPal's architecture

Written after reading the five architecture sources. The question is not "where can we bolt an LLM
on" — it is "which layer of this system has a decision that is currently made by a **hardcoded rule
or a human queue**, where a model could decide better and leave a receipt".

Sources consulted:

- freeCodeCamp — implementing PayPal in a NestJS + gRPC + Docker microservice architecture
- GeeksforGeeks — "a billion transactions per day with only 8 virtual machines": microservices,
  containerisation, load balancing, sharding, async/event-driven processing, caching, fault tolerance
- systemdesignhandbook.com — PayPal system design: API gateway, **idempotency**, **double-entry
  ledger**, **graph-based fraud detection**, settlement, observability
- LinkedIn — PayPal high-level deep dive (not readable without an account; not relied on)
- dev.to — a listicle of interview resources (no architecture content; not relied on)

**Be honest about the provenance:** these are public descriptions and interview-prep reconstructions,
not PayPal's internal documentation. Treat the layer list as a reasonable external model, and say so
if asked.

---

## 1. The layers, as those sources describe them

| # | Layer | What it does | Where decisions are made today |
|---|---|---|---|
| 1 | Edge / API gateway / BFF | Auth, rate limiting, routing | Rules and static config |
| 2 | Orchestration | Sequences a payment across services | Hardcoded saga steps |
| 3 | Risk / fraud | Score before authorising | PayPal's own ML — the industry leader |
| 4 | Core payment services | Authorise, capture, refund, void | Rules + state machine |
| 5 | **Ledger** | Double-entry, append-only truth | Deterministic, never heuristic |
| 6 | **Settlement / back office** | Batch reconciliation, FX, payouts | Deterministic + human ops queue |
| 7 | Async events | Kafka-style fan-out, retries, DLQs | Retry policy, then a human |
| 8 | Observability | Traces, metrics, alerts | Thresholds, then a human |

---

## 2. The seven candidate AI insertions, scored

Scored on: profit to PayPal (direct revenue or cost), customer benefit, novelty, and whether it is
buildable inside two weeks on the existing codebase.

| # | Layer | The idea | PayPal | Customer | Novel | Buildable | Total |
|---|---|---|---|---|---|---|---|
| **A** | 2 + 3 | **Authority before authorisation** — a mandate the payment must satisfy, judged against its stated purpose | +volume | +safety | High | **Done** | **15** |
| **B** | 4 | **Post-authorisation compensation** — an agent that decides capture / partial capture / void / retry after a partial failure, inside the original authority | +ops cost | +resolution speed | **High** | **Yes** | **16** |
| C | 6 | Reconciliation break resolution — match settlement reports to ledger lines | +ops cost | +payout timing | Medium | Hard | 12 |
| D | 3 | Decline explanation — turn a risk score into a plain-language cause and a fix | +conversion | +clarity | Medium | Medium | 11 |
| E | 7 | Dead-letter triage — an agent that reads a poison message and files a diagnosis | +ops cost | ~ | Low | Easy | 8 |
| F | 1 | API contract drift detection across services | ~ | ~ | Low | Easy | 6 |
| G | 8 | Incident triage from traces | +ops cost | ~ | Low | Easy | 7 |

**A is what Warden already is.** It is also the one the hackathon's brief invites and the one AP2
leaves open.

**B is the one nobody has built**, and it is the strongest technical idea on the list. Everything in
the field guards the *decision to spend*. Nothing guards the window **after** the decision, where
money is already authorised and can still go wrong.

---

## 3. Why B is the interesting one

### The window nobody governs

A payment is not one event. In the Orders API it is at least two:

```
create order → authorise → [ ...the gap... ] → capture
```

The gap is real. An authorisation is a commitment that expires; a capture can fail; a merchant can
ship part of an order; a refund can fail. In a microservice architecture described by these sources,
that gap is handled by **saga steps with hardcoded compensation rules and an ops queue behind them**.

So the industry has built enormous machinery for the decision *before* the money is committed
(risk, limits, mandates) and comparatively little for the window *after* it, where the failure modes
are messier and the customer is already waiting.

### What an agent can do there that a rule cannot

| Failure | Today | What an agent can decide |
|---|---|---|
| Capture fails — instrument declined | Retry, then queue for ops | Is the amount the problem, or the instrument? Capture what is available, void the rest, tell the buyer what shipped |
| Authorisation about to expire | Expiry sweep, or lose it | Capture now, or void deliberately and tell the merchant why |
| Partial fulfilment | Manual split | Capture only the fulfilled lines, release the remainder, receipt the difference |
| Vendor goes bad after authorisation | Human escalation | Void inside the revocation window — **a capture-first flow cannot do this at all** |

Every one of those is a decision with a **bounded** action space (capture, partial capture, void,
retry, escalate). That is exactly the shape where a model is useful and dangerous in equal measure —
which is why it must be fenced by the original mandate and produce a receipt for each compensation.

### Why it is a win for both sides

**The customer** does not get "payment failed, contact support". They get a partial order that ships,
or money released immediately, or a precise reason.
**PayPal** stops paying humans to resolve authorisations, stops leaving money in limbo, and — the
part that shows on the P&L — stops losing settled volume to failures that were recoverable.

And it compounds with A: the same mandate that authorised the spend also bounds the compensation.

---

## 4. What was actually built from this analysis

`--story compensate`, added this session. It runs the flow the analysis says is unguarded:

```
authorise 480.00   →  money committed, not taken
capture   480.00   →  DECLINED (instrument limit 300.00)
agent reads the failure, stays inside the original mandate:
capture   300.00   →  captured  (partial)
void      180.00   →  released, never taken
```

Three receipted states, one authorisation, no human, and no money stuck. The partial capture is a
real PayPal API capability (`capture` accepts an `amount`), not a simulation of one — the rail
passes it through in sandbox mode.

---

## 5. What I would not build, and why

- **Fraud/risk scoring.** PayPal's own models are the state of the art. A hackathon entry that
  claims to improve them is not credible, and a judge from PayPal will know it in one question.
- **Ledger heuristics.** The ledger is deterministic on purpose. Putting a model near the
  double-entry book is how you get an unauditable system. Receipts *about* the ledger: yes. The
  ledger deciding: never.
- **Reconciliation (C).** Genuinely valuable, genuinely hard to demo without settlement files, and
  the accuracy bar is punishing. Real project, wrong week.
- **DLQ triage and contract drift (E, F).** Cheap to build, invisible in a demo, and they are
  internal tooling rather than a product.

---

## 6. The one-line positioning this produces

> PayPal has built world-class machinery for deciding **whether** money may move. Warden covers the
> two windows around it — **why** it is moving, and **what happens when it half-moves** — and leaves
> a receipt that anyone can verify.

That sentence is architecture-literate, customer-literate, and true of the code in this repository.
