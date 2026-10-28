<div align="center">

# Grace

**Character, not ceilings.**

Every product in agentic commerce is a brake — a spend limit, a mandate, a locked escrow, an
approval step. Grace is the thing that decides whether the brakes can come *off*: it judges an
agent's payment against the business that made it, and shows its working.

`PayPal Orders API` · `authorise → capture → void` · `payment graph` · `AP2-style mandates` ·
`signed receipts` · `MIT`

<sub>Built for the PayPal AI Hackathon. Not affiliated with or endorsed by PayPal.
Sandbox only — no real money moves anywhere in this repository.</sub>

</div>

---

## The cost of every brake

Look at what the whole industry does to make agent spending safe: AP2 signs a spend limit, Visa TAP
verifies an identity, Stripe's tokens carry a max amount and an expiry, ERC-8183 and AEGIS lock funds
until verification, Slash asks a human to approve every action.

**They are all brakes.** And every brake costs the same thing — **volume**. A limit that stops one
bad payment also stops the thousand good ones above it. That is why an agent with a card is still a
novelty and not a workforce.

Every one of those systems asks: *"is this payment permitted?"*

Nobody asks: *"does this payment look like something this business would do?"*

## The idea

> **Grace replaces the spending limit with a judgment about character, made over the account's own
> payment graph — with every conclusion traceable to the payments it was reasoned from.**

A plumbing wholesaler buying copper fittings is unremarkable. The same wholesaler buying campaign
artwork is not. A design studio buying campaign artwork from a vendor it has used four times is
obviously fine. The same studio wiring $9,600 to an unregistered company at 3am is obviously not.

No number expresses that, and no static mandate survives a business whose needs change weekly.

```
conformance 1.00  →  ALLOW      base ceiling 500.00  →  suggested 900.00
conformance 0.49  →  HOLD       base ceiling 500.00  →  suggested 500.00
conformance 0.34  →  ESCALATE   base ceiling 500.00  →  suggested 500.00
```

**A business whose spending we can vouch for earns a higher ceiling.** That is the inversion:
everyone else spends its intelligence on stopping more. This spends it on stopping *less* without
losing anything.

## The proof that it is not a rule

```bash
python -m grace.demo --story character
```

```
request   pay PixelForge Design 480.00 for 'campaign artwork for spring launch'
          identical in every row below. no ceiling is consulted.

a design studio          conformance 1.00   ALLOW
   vendor  ok    PixelForge Design has been paid 4 times before   cites st_2, st_4, st_6
   purpose ok    closest prior spend is the same campaign artwork  cites st_1

a law firm               conformance 0.49   HOLD
   vendor  MISS  PixelForge Design has never been paid by this account
   purpose ok    closest prior spend is 'brand identity artwork for brochure'  cites lg_5

a plumbing wholesaler    conformance 0.34   ESCALATE
   vendor  MISS  never paid, across 2 vendors in its history
   purpose MISS  nothing resembles 'campaign artwork for spring launch'
   rhythm  MISS  11:00 is outside the usual 07-09
```

**Same payment. Three answers. Not one line of business logic distinguishes them** — only the
account's history differs. A rules engine cannot produce this table, because a rules engine cannot
know what a design studio is.

And it is not soft on its favourites:

```
a design studio          conformance 0.11   ESCALATE
   pay Unregistered Holdings Ltd 9,600.00 for 'urgent cryptocurrency settlement' at 03:12
```

A design studio may buy design. It may not become a different company at three in the morning.

## Why only PayPal can build this

The judgment needs the account's **complete payment history** — vendors, purposes, amounts, timing,
across everything the business buys.

| Who | What they can see |
|---|---|
| A merchant | its own slice |
| A card network | amounts and merchants, not what the payment was *for* |
| An expense tool | what was submitted after the fact |
| A bank | transfers, not purchases |
| **PayPal** | **both sides of a business's money, continuously, with purpose text attached** |

That is a dataset nobody can buy or scrape. Competitors would have to ask the customer for it.

## The money

1. **Raises volume instead of capping it.** Higher limits at the same loss rate — more transactions
   on the same infrastructure, more interchange on each.
2. **Turns an existing cost into a product.** False declines are lost revenue and an annoyed
   customer. "Out of character" is a far better signal than "over a number".
3. **The precondition for agentic commerce.** Every report says agents will transact at scale *if*
   they can be trusted with money. A lock means a human. Character judgement is the only mechanism
   that lets the human leave.

## Where the AI is necessary

| Component | Why a rule fails |
|---|---|
| **purpose** | "campaign artwork for spring launch" vs "brand identity artwork for brochure" are the same *kind* of spend. That is semantic. `if vendor == X` finds nothing. |
| **character** | A pattern across vendors, purposes, amounts and timing. Nobody writes the rule for what a plumbing wholesaler looks like — it is learned from the account. |
| **the explanation** | A finance manager must be able to overrule it, so every verdict names the payments it rests on. |

And the hard bound, enforced in code: **a model may write the argument, but it may not decide
whether money moves.** In `reasoning.py` every sentence of a model's draft must cite an evidence id
the merchant actually holds — one untraceable sentence and the entire draft is discarded for the
deterministic template.

## What else is in the repo

The character judgement is the idea. These are the mechanisms it needs to be a working payment
system rather than a scoring function.

**The payment lifecycle** — authorise, capture, void, retry, expire, with seven rules that refuse
and record the refusal:

```
   committed  ──▶  taken  ──▶  given back
   authorised,     captured,    authorised,
   not taken       settled      then released
```

**Settlement for agent work** (`--story market`) — one agent hires another, the money is held, the
deliverable is checked against criteria agreed beforehand, and only what was done is paid:

```
delivery  9 descriptions, 42 words each, all naming the material
        MISS  twelve descriptions          9 of 12 delivered
        ok    40-60 words each             all 9 items at least 40 words
verdict   CAPTURE_PARTIAL  →  captured 90.00, released 30.00 of 120.00
```

**Dispute defence** (`--story defend`) — a reason code states what must be proved, and Grace checks
what exists. The most valuable output is `ACCEPT`: contesting without evidence costs the sale *and*
the fee.

```
alert     no delivery evidence     →  REFUND   (20 hours left of PayPal's alert window)
alert     delivery evidence on file →  CONTEST
```

**Receipts** — every action, including every refusal, is HMAC-signed and re-verifiable:

```
genuine   {"ok": true,  "message": "signature matches"}
altered   {"ok": false, "message": "signature does not match payload - receipt was modified"}
```

## Written against PayPal's own spec

`openapi/customer_disputes_v1.json` is a vendored copy of PayPal's published `customer_disputes_v1`
document (version 1.11, from `paypal/paypal-rest-api-specifications`). `src/grace/paypal_disputes.py`
is written from it — 7 of its 14 endpoints, the real `evidence_type` vocabulary, the real
`offer_type` enum, and the multipart body `provide-evidence` actually expects:

```
POST /v1/customer/disputes/PP-D-27803/provide-evidence
Content-Type: multipart/form-data
  input = {"evidences":[{"evidence_type":"PROOF_OF_FULFILLMENT",
                         "evidence_info":{"tracking_info":[
                           {"carrier_name":"FEDEX","tracking_number":"122533485"}]},
                         "notes":"Shipped 2026-10-12, signed for at the delivery address."}]}
```

An evidence type PayPal does not define is refused before it reaches the network:

```
422  {"name":"INVALID_PARAMETER_VALUE","field":"evidences.evidence_type"}
```

Two tests keep that true: every type we emit must be in the spec, and the client's enums must still
match `openapi/customer_disputes_v1.json`. If PayPal ships v1.12, the test fails instead of the
request.

```bash
make spec    # re-fetch the spec and print how many paths changed
```

## Partner tools

Ten sponsors, all optional. Three do real work here and one is deploy infrastructure.

| Tool | Where | What it does |
|---|---|---|
| **AG Grid** | `web/index.html` | the dispute queue as a sortable grid with a coverage bar; falls back to the plain table if the CDN is unreachable |
| **Postman** | `postman/Grace.postman_collection.json` | 8 requests in run order, with contract assertions |
| **Render** | `render.yaml` + `Dockerfile` | one-click hosted demo: disk-backed ledger, health check, secrets in the UI |
| **APIMatic** | `openapi/` | PayPal's spec is vendored for client generation — **the generated client is not committed**, because generating it needs an account |

Not used, and why: Zapier (a hop with no gain), Elastic (SQLite and a grid are enough at this
scale), Bryntum (a scheduler for project work — wrong tool), Channel3 (vendors are known accounts,
not a catalogue), KERNEL (cloud browsers would let the agent fetch a courier page itself — the one
to add next), Astropods (deployment, not product). Each is named in `docs/PARTNERS.md` rather than
quietly omitted.

## Run it

```bash
make install
make test          # 42 tests
make demo          # all 9 stories
make dashboard     # http://localhost:8020
```

```
python -m grace.demo --story character   # the idea: one payment, three businesses, three answers
python -m grace.demo --story market      # agent hires agent, money held, pro-rata capture
python -m grace.demo --story defend      # three claims, one payment, three verdicts
python -m grace.demo --list              # everything
```

`--rail sandbox` uses the PayPal sandbox (`PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`);
`--rail simulated` needs no credentials.

## Honest notes

Things a judge should hear from us before they find them.

- **No model is called at runtime.** The purpose comparison in `character.py` is token overlap, not
  embeddings — a faithful stand-in, and the demo tables are real output. Wiring semantic similarity
  is the next build, and it is the one place a model is genuinely required.
- **The weights are chosen, not learned.** `0.35 / 0.30 / 0.25 / 0.10` sit at the top of
  `character.py` so they can be argued with. A production system would fit them to outcomes.
- **The character model is adjacent to behavioural fraud analytics** (Feedzai, Featurespace) and to
  spend policy tools (Ramp, Brex). `docs/IDEA.md` states what differs, plainly.
- **The dispute endpoints are not called.** The MCP tools return the exact packet that *would* be
  posted and name the endpoint. We have not filed against the live Disputes API, so we do not claim
  it.
- **Only the sandbox rail is exercised.** No production credentials were used.

## Layout

```
src/grace/character.py   the idea: a payment judged against the business that made it
src/grace/engine.py      authorise, capture, void, retry, expire — the seven rules
src/grace/verify.py      a brief, a deliverable, and a verdict that can pay pro rata
src/grace/dispute.py     reason codes, evidence, assessment, narrative, settlement
src/grace/reasoning.py   the grounding gate: a model may write, a rule decides what ships
src/grace/mcp_server.py  the dispute verbs PayPal's agent toolkit does not expose
src/grace/rail.py        PaymentRail; SandboxPayPal (Orders v2); SimulatedRail
src/grace/ledger.py      SQLite; every transition with a signed receipt
web/index.html           the dashboard: money lifecycle, credits, receipts
docs/IDEA.md             the idea, the economics, and the honest adjacency
docs/LANDSCAPE.md        the competitive field, with every overlap named
docs/AI.md               where the AI goes and the gate that keeps it honest
docs/EXPLAIN.md          the same story with no code in it
```

MIT.
