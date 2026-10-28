# Devpost submission copy — Warden

Everything here is copy-paste ready. Ratings and claims match what the repository actually does;
where something is simulated, it says so.

---

## Project name

Warden

## Tagline

Your agent has a spending limit. That is not the same as permission.

## Track / category

PayPal AI Hackathon — no prescribed tracks; entered for the overall prizes and
**Best Use of Agentic Commerce**.

## Links

| | |
|---|---|
| Repository | https://github.com/RANJITHROSAN17/warden |
| Hosted demo | _(Render URL once deployed)_ |
| Video | _(YouTube link once uploaded)_ |

---

# 1. Three-minute video script

Target: **2:52**. Narration is plain and slightly fast; no music under the voice.

### 0:00 – 0:30 · Hook and problem

**On screen:** a terminal-style card showing two lines, then a browser with an agent's log.

> "This agent has a five-hundred dollar limit. It just tried to spend four hundred and eighty
> dollars on an espresso machine — and it told its boss it was buying printer paper.
>
> Every control we have would allow that. A virtual card checks *how much*. A merchant blocklist
> checks *who*. Nothing checks *why* — and an agent that has drifted off-purpose is still under
> its limit.
>
> Warden is the layer that asks why."

**Beat:** the espresso line should land on "printer paper" — pause half a second there.

### 0:30 – 1:45 · Live prototype

**On screen:** `python -m warden.demo` running in a terminal, then the AG Grid ledger.

> "The agent gets a mandate before it can spend: one purpose, one ceiling, a category scope, a
> minimum payee age, an expiry."

**0:38** — scenario 1 prints ALLOW.

> "A normal restock — paper and pens, fifty-six dollars. Every rule passes. Warden creates a
> PayPal order with intent *authorize*, and the receipt is signed."

**0:52** — scenario 2 prints HOLD, showing two failed rules.

> "Here is the same ceiling, and the purchase is under it. The agent says *restock printer paper*
> and the basket is an espresso machine. Two rules fire: the category is out of scope, and the
> intent match is zero. It is held."

**1:08** — scenario 3, over the ceiling.

> "Over the ceiling, and notice what it says: *thirty-four dollars over — remove thirty-four
> dollars of goods, or approve this one purchase.* Not *policy violation*. A refusal you cannot
> act on is indistinguishable from a bug."

**1:22** — scenario 4, new vendor.

> "A brand-new payee, thirty dollars. Small amount, brand-new vendor — held, because that is
> exactly where agent fraud lands."

**1:32** — scenario 5, the grant.

> "A human releases the toner purchase. But the release is bound to the fingerprint of that exact
> basket — it is not a raised ceiling. The next thing the agent wants to buy is checked again."

**1:40** — receipt verification, then the tampered receipt failing.

> "And every decision — paid or refused — is a signed receipt. Flip one field and verification
> fails."

**Switch to the browser, press "Run the five scenarios".**

> "This is the same ledger in AG Grid. Filter to *hold* and you get the report a finance team
> actually wants: everything the agent tried to buy and was stopped from buying, with the reason
> and the fix."

### 1:45 – 2:30 · Technical deep dive

**On screen:** the architecture diagram, then `policy.py`, then a terminal with the PayPal call.

> "Two things are doing the work.
>
> First, the rule engine — six rules, plain Python, no dependencies, each one reporting pass or
> fail with a sentence. It is short enough to read in one sitting, because a guard you cannot read
> is a guard you cannot trust.
>
> Second, intent binding. The agent has to state why it is buying. A judge scores the basket
> against that statement and must cite the evidence it used — and the judge's name goes inside the
> signed payload, so a decision cannot be re-run with a different judge and look the same.
>
> On the payment side this uses PayPal's Orders API with intent *authorize*: create the order, get
> the approval URL, authorise, then capture. The authorisation window is the product — Warden
> decides inside it, and can void instead of capturing."

**Beat:** show the JSON receipt on screen for two seconds. It is the most convincing artifact.

### 2:30 – 3:00 · Impact and what is next

> "Every company putting an agent near money is being asked the same question by their finance
> team: what is it allowed to buy, and can you prove it stayed inside the line?
>
> Today the answer is a card with a limit, or a human clicking approve on everything — which is
> not agentic at all.
>
> Warden answers it with three things that do not exist together today: intent binding, holds that
> come with a fix, and receipts a third party can verify.
>
> Next: webhook verification, asymmetric signing with a published key, and delegation, so a
> human's authority can be narrowed as it passes down a chain of agents.
>
> The agent should not be trusted because it has a limit. It should be trusted because every
> payment matches the reason it gave — and because that match is provable."

---

# 2. Devpost form responses

### Concise summary — how PayPal and AI are central

Warden is a spend-authority layer for AI agents. An agent cannot pay without a mandate, and no
payment is authorised until the basket has been checked against both the mandate and the reason
the agent gave for buying it.

**PayPal does the money movement, not a mock.** In sandbox mode Warden calls the **Orders API v2**
with `intent=AUTHORIZE` to create the order and surface PayPal's approval URL, then the **Payments
API** to capture or void the authorization. The authorize-then-capture window is the design: the
agent's request is judged while the money is committed but not yet taken, which is the only place
a hold can exist at all. Refunds and voids go through the same rail.

**AI makes the decision that no rule can.** A spending limit cannot tell whether a $480 purchase
is a restock or a drift. The intent judge — an LLM over any OpenAI-compatible endpoint, with a
deterministic lexical fallback — scores the basket against the agent's stated reason and must cite
the evidence. That score is a rule in the same engine as the ceiling, and it is recorded inside
the signed receipt along with the judge's identity, so the AI's contribution to a decision is
auditable after the fact rather than asserted at the time.

The agent itself is deliberately unremarkable: any tool-calling loop can call `POST /api/purchases`.
The guard does not trust its own agent, which is the whole architecture.

### Sponsor tools used, and why

| Tool | How it is used | Why it earns its place |
|---|---|---|
| **AG Grid** | The authorization ledger: every decision with outcome, amount, intent score, deciding rule, receipt id; quick filter, grouping, row-click drill-down to the signed receipt | The product's primary question is "what has this agent been doing", which is a dense, filterable table problem — not a chart problem. Filtering to `hold` produces the report a finance team cannot get from a card statement |
| **APIMatic** | `openapi.yaml` (OpenAPI 3.1, 9 paths, 7 schemas) is the generation input so an agent framework can get a typed client instead of hand-writing HTTP | The gateway is meant to sit under *any* agent runtime. SDK generation is how that stays true |
| **Render** | Hosts the demo (`uvicorn warden.api:app`) with `WARDEN_DB` on a mounted disk so the ledger survives a restart | Judges need a URL they can press, not a laptop |

### Anything else worth knowing

- Sandbox only. No real money moves anywhere in this repository.
- The simulated PayPal rail is a separate class with the same interface, and every receipt and
  ledger row records which rail produced it — `simulated` or `sandbox`. A simulated payment can
  never be mistaken for a real one.
- Receipts are HMAC-SHA256 with a key from the environment. Production would use asymmetric
  signing with a published public key; the README says so.
