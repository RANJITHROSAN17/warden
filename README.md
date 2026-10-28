<div align="center">

# Grace

**Settlement for agent work. The money is held, checked against the brief, then released.**

An agent authorises a spend, the work is delivered, Grace checks the deliverable against the
criteria both sides agreed to, and captures only what was actually done. When someone disputes the
result, the same evidence trail answers the claim.

`PayPal Orders API` · `authorize → capture → void` · `partial capture` · `AP2-style mandates` ·
`signed receipts` · `MIT`

<sub>Built for the PayPal AI Hackathon. Not affiliated with or endorsed by PayPal.
Sandbox only — no real money moves anywhere in this repository.</sub>

</div>

---

## Where the money is

Every payment here is in exactly one of three places, and the dashboard says which:

```
   committed  ──▶  taken  ──▶  given back
   authorised,     captured,    authorised,
   not taken       settled      then released
```

That arc is the product. A card purchase crosses it in one step, because the thing being bought is
already on a shelf. **Work does not.** A contractor agent takes a job, delivers something, and only
then does anyone know whether it was worth paying for. Money has to sit in the middle — and PayPal's
authorise/capture primitives are built for exactly that.

## The problem

An agent can now hire another agent. Nothing lets it pay one safely:

1. **An autonomous buyer is an unbounded spender.** Nothing bounds what it can commit.
2. **Nothing holds the money for the work.** Pay upfront and the buyer can be robbed; pay on
   delivery and the seller can be. Someone has to hold it.
3. **"Done" is a claim, not a fact.** There is no check between a deliverable and a brief.
4. **Nothing survives a disagreement.** When the buyer says the work was not done, the evidence is
   whatever two agents each wrote about themselves.

## The market story

```bash
python -m grace.demo --story market
```

```
buyer     procurement agent wants 12 product descriptions
seller    a copywriting agent quotes 120.00 USD

brief     agreed before any money moved
          - twelve descriptions
          - 40-60 words each
          - names the material

held      pay_e7a582cfa5  120.00 USD authorised
          the money is committed and not taken; the seller cannot touch it yet

delivery  9 descriptions, 42 words each, all naming the material
        MISS  twelve descriptions          9 of 12 delivered
        ok    40-60 words each             all 9 items at least 40 words
        ok    names the material           every item names recycled

verdict   CAPTURE_PARTIAL  9 of 12 delivered. Paying for the work that was done, releasing the rest.
money     captured 90.00, released 30.00 of 120.00
```

The seller is not robbed. The buyer is not trusting a claim. Both movements are receipted.

**Two refusals make this safe to point at real money:**

- A criterion that cannot be checked mechanically makes the whole verdict `escalate`. Grace does not
  guess on a question it cannot test.
- Partial payment needs a criterion that measures *how much* of the work was done. Without one, a
  short delivery `release`s rather than inventing a percentage.

## The bound

Four rules, enforced in code, each refusal recorded as a signed action with the rule that stopped it
and the counterfactual that would have allowed it:

| Rule | What it prevents |
|---|---|
| `payment.open` | acting on a payment that is already closed |
| `capture.within_authorised` | taking more than was authorised, ever |
| `authority.partial_permitted` | splitting a capture the mandate forbade |
| `authority.attempt_budget` | retrying until something accidentally succeeds |

An unknown rail error is never retried and never treated as a decline — it is `ESCALATE`.

## The defence

A dispute is the same evidence trail read backwards: a reason code states what must be proved, and
Grace checks what exists.

```
alert     no delivery evidence
        decision    REFUND
        because     delivery_proof does not exist. A chargeback would be lost and would cost a
                    fee as well as the sale, so refund inside the 17.4 hours remaining.

alert     delivery evidence on file
        decision    CONTEST
        because     delivery_proof exists and is signed. Refunding would give away a case that
                    can be won.
```

PayPal gives a merchant **20 hours** on a pre-chargeback alert to refund and avoid the fee. That
deadline is shorter than a triage rota runs, which is why the decision has to be made by the agent
that already holds the evidence.

Reason codes use PayPal's own vocabulary — `ITEM_NOT_RECEIVED` (INR), `NOT_AS_DESCRIBED` (SNAD),
`UNAUTHORIZED`, `DUPLICATE`, `BILLING_ERROR`.

## The receipt

Every action — including every refusal — is signed with HMAC-SHA256 and can be re-verified:

```
genuine   {"ok": true,  "message": "signature matches"}
altered   {"ok": false, "message": "signature does not match payload - receipt was modified or the
                                    key is wrong"}
```

The buyer can check that the seller's evidence has not changed since the money moved.

## Run it

```bash
make install          # or: pip install -e ".[dev]"
make test             # 31 tests
make demo             # every story against the simulator
make dashboard        # http://localhost:8020 — the money lifecycle, credits, receipts
```

```
python -m grace.demo --story market     # agent hires agent, money held, pro-rata capture
python -m grace.demo --story retry      # a timeout is not a decline
python -m grace.demo --story defend     # three claims, one payment, three verdicts
python -m grace.demo --list             # all stories
```

`--rail sandbox` uses the PayPal sandbox (`PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`);
`--rail simulated` runs with no credentials.

## The MCP server

`python -m grace.mcp_server` speaks the MCP stdio transport with no dependencies. It exists because
of a gap in PayPal's own [agent toolkit](https://github.com/paypal/agent-toolkit): its dispute tools
are `list_disputes`, `get_dispute`, and `accept_dispute_claim` — read, read, and surrender. Grace
adds the missing verbs:

| Tool | Maps to |
|---|---|
| `grace_assess_dispute` | `GET /v1/customer/disputes/{id}` |
| `grace_contest_dispute` | `POST /v1/customer/disputes/{id}/provide-evidence` |
| `grace_offer_settlement` | `POST /v1/customer/disputes/{id}/make-offer` |
| `grace_triage_alerts` | the 20-hour refund-or-fight call |

## Honest notes

These are the things a judge should hear from us before they find them:

- **No model is called at runtime.** The decisions are rules and templates: a requirement map, a
  checker, and a narrative built over findings. The design deliberately keeps the model out of the
  facts — a model may draft the brief and make the argument read better, but it must not be able to
  add a claim no evidence supports. Wiring a model into brief-writing is the next step, not a
  finished claim.
- **The dispute endpoints are not called.** The MCP tools return the exact packet that *would* be
  posted, and name the endpoint. Calling the live Disputes API needs a merchant account with
  disputes; we have not done it, so we do not claim it.
- **Only the sandbox rail is exercised.** `SandboxPayPal` is implemented against the Orders v2 API;
  no production credentials were used.
- One real bug was found by building the dispute packet: `engine.authorise` wrote its transition
  twice, so every payment carried a duplicate authorisation record. In a dispute that reads as a
  second charge. Fixed, with a regression test.

## Layout

```
src/grace/engine.py      authorise, capture, void, retry, expire — the seven rules
src/grace/models.py      Authority, Payee, Payment, Check, Action — six states
src/grace/verify.py      a brief, a deliverable, and a verdict that can pay pro rata
src/grace/dispute.py     reason codes, evidence, assessment, narrative, settlement
src/grace/mcp_server.py  the dispute verbs PayPal's agent toolkit does not expose
src/grace/rail.py        PaymentRail; SandboxPayPal (Orders v2); SimulatedRail
src/grace/ledger.py      SQLite; every transition with a signed receipt
web/index.html           the dashboard: money lifecycle, credits, receipts
```

MIT.
