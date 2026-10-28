# Seven ideas, scored against this panel

You have said, twice, that you are not convinced this is the best idea. That deserves more than a
defence of the work I have already done, so this is the whole shortlist with honest scores, written
so that one of them can beat the others.

Scoring uses the five published criteria at 1–5 each. **Design and Presentation are 40% of the
mark and are where Warden is weakest today**, so they are weighted as heavily here as the judges
will weight them.

| # | Idea | TI | Design | Impact | Innovation | Presentation | Total /25 |
|---|---|---|---|---|---|---|---|
| 1 | **Warden as shipped** — guardrail + proof | 5 | 2 | 4 | 4 | 3 | **18** |
| 2 | **Warden Market** — escrow for agent-to-agent work | 5 | 4 | 5 | 5 | 5 | **24** |
| 3 | **Dispute Defender** — chargeback evidence agent | 4 | 4 | 5 | 2 | 4 | **19** |
| 4 | **Bill Check** — pay the invoice only if it matches the quote | 4 | 3 | 4 | 4 | 4 | **19** |
| 5 | **Agent-Ready Store** — merchant agent for inbound buying agents | 4 | 4 | 4 | 4 | 5 | **21** |
| 6 | **Spend Lens** — observability for agent fleets | 3 | 5 | 3 | 2 | 3 | **16** |
| 7 | **Revenue Rescue** — recover failed recurring payments | 3 | 4 | 5 | 1 | 2 | **15** |

---

## 1. Warden as shipped

**Pitch:** an agent cannot spend without a mandate; every attempt is judged against the reason, and
every decision is a signed receipt.

**Who pays:** platform teams shipping payment agents, and their finance function.

**The wow:** five independent rules firing on a prompt-injected attempt. Strong, but it is a
*guardrail* — and a guardrail demos as a wall of text saying no.

**Honest risk:** it reads as compliance software. Compliance is respected and rarely loved, and
"Design" is 20% of the score.

## 2. Warden Market — escrow for agent-to-agent work ★ *now running*

**Pitch:** one agent hires another. The price is negotiated, the buyer's money is **authorised but
not captured**, the seller hands over an artefact, a verifier reads it against criteria written
before the job started, and only then does the money move. Fail, and the authorisation is voided.

**Who pays:** any platform where agents transact with third-party agents — procurement, contractor
networks, API marketplaces — plus PayPal, because this is the transaction volume AP2 was written for
and the enforcement half it does not ship.

**What the AI does:** negotiates the price across rounds, walks away rather than breach a ceiling,
and repairs its own bundle when refused.

**The wow, in one screen:** money going *into limbo and coming back out* — `AUTHORISED` then
`CAPTURED` on success, `AUTHORISED` then `VOIDED` on a failed read. Judges have seen "payment
succeeded" a thousand times. Very few have seen a payment that was **successfully not taken**.

**Honest risk:** "agent labour market" sounds like science fiction if you overclaim it. The
credible version is concrete: a company's procurement agent hiring an outside contractor agent, and
the money not leaving until the deliverable is checked. Do not pitch a robot economy; pitch one
invoice.

**What it reuses:** everything. The run below is `--story market`, built this session on the
existing engine — mandate, negotiation, evidence rule, authorize/capture/void, receipts. Nothing
was thrown away.

```
1  agreed. The buyer tries to settle immediately.
HOLD   1 before delivery            234.00 USD  intent 0.62
       because     delivery.evidence: billed without a delivered artefact: D1
       would make   attach evidence to D1 or remove it from the invoice
       this hold is the escrow: the money is committed, and it is not paid

2  the work arrives
seller hands over   pricing-brief.md   723 chars   sha256 aefe2c41b9d496ac
acceptance          covers JP, DE, BR  ·  at least 400 characters  ·  cites a source
   ok    covers JP / DE / BR            present
   ok    at least 400 characters        723 characters
   ok    cites a source                citation present
ALLOW  2 artefact verified          234.00 USD  intent 0.62
       held in     SIM-AUTH-0A12361030F6   authorised, not captured
capture             COMPLETED   234.00 USD paid
                    artefact sha256 aefe2c41b9d496ac recorded in the receipt

the claim that does not survive the read
ALLOW  3 on the claim alone         234.00 USD   ← the guard checks that evidence exists
                                                  not whether it is any good
   FAIL  covers JP / DE / BR          not mentioned anywhere in the artefact
   FAIL  at least 400 characters      85 characters
void                VOIDED   money never moved
                    seller asked to redo; the buyer keeps its money
```

That last block is the most valuable thirty seconds in the entire project, because it is the one
place the demo admits a limit and then shows the mechanism that covers it.

## 3. Dispute Defender — the chargeback evidence agent

**Pitch:** PayPal exposes `list_disputes`; nothing turns a dispute into a filed, evidence-backed
response. An agent reads the order, assembles the delivery evidence, writes the rebuttal that
matches the reason code, files it, and learns which arguments actually win.

**Who pays:** merchants, directly, per recovered dollar.

**The wow:** a chargeback timeline filling with evidence and flipping to `WON`.

**Honest risk:** this is a **crowded category** — Justt, Chargeflow, Midigator, and PayPal's own
dispute tooling. The PayPal judges will know that immediately, and "Innovation" is 20%. There is
also a real integrity constraint: the agent may only assemble evidence that exists, never
manufacture it, which bounds how clever the demo can be.

**Verdict:** highest raw Impact, weakest Innovation. Wins nothing that Warden Market does not also
win, and loses the Innovation column.

## 4. Bill Check — pay only if the invoice matches the quote

**Pitch:** the agent that audits the bill. A quote is recorded at order time; at payment time the
invoice lines are matched against it, and the capture is withheld over any unexplained delta.

**Who pays:** any business with recurring supplier invoices.

**The wow:** a bill being taken apart line by line, with one unexplained line refused.

**Honest risk:** three-way match is a decades-old AP practice (Coupa, Basware). Novel *for agent
payments*, not novel in the world.

## 5. Agent-Ready Store — the merchant side

**Pitch:** a storefront built for machine customers. Inbound buying agents get a machine-readable
catalogue, negotiate, and check out; the merchant agent protects margin with a floor and a policy.

**Who pays:** merchants who expect agent traffic.

**The wow:** two windows haggling, then a real sandbox checkout.

**Honest risk:** it is speculative demand — nobody has agent traffic yet — and it drops the proof
story that makes Warden defensible. It also overlaps heavily with what AP2 and the card networks are
already standardising.

## 6. Spend Lens — observability for agent fleets

**Pitch:** every agent's spend, with the reason attached, filterable and searchable.

**Who pays:** platform teams.

**The wow:** a beautiful grid.

**Honest risk:** this is a **feature of Warden**, not a company. Highest Design score of anything
here, lowest substance. Worth adding as a *view*, not as the submission.

## 7. Revenue Rescue — recovering failed recurring payments

**Pitch:** when a subscription payment fails, an agent negotiates a retry and a payment plan.

**Honest risk:** Butter Payments, Churnkey and FlexCharge do exactly this and are well funded.
Innovation 1/5. Do not enter this.

---

## The decision I would make

**Do not switch products. Change the frame.**

Warden's problem was never the engine — it is that the product was pitched as *compliance*, and
compliance does not make anyone lean forward. The same code, pitched as **the settlement layer for
agent-to-agent work**, scores 24 instead of 18 without deleting a single module:

- It moves the hero from *the guard that says no* to *the money that waits and then moves*.
- It makes the AI obviously necessary: two agents negotiating, one repairing its own bundle.
- It targets **Best Use of Agentic Commerce**, **Most Creative**, **Best Demo Delivery** and
  **Most Impactful** simultaneously, where the current framing only strongly targets one.
- It converts the weakest criterion (Design, 2/5) into a coherent two-sided product.
- And it keeps the injection story as the *supporting* act, where it belongs — proof that the buyer
  cannot be talked into overpaying.

**The one-line pitch to use from now on:**

> An AI agent can hire another AI agent. Warden holds the money until the work is proved — and
> produces the receipt that settles the argument six months later.

## If you want to leave Warden entirely

Then enter **Dispute Defender** (idea 3). It is the only alternative with a better Impact story, it
uses a real PayPal API surface that is currently under-served, and it is buildable in the time left.
Accept that it will score badly on Innovation and that the judges will have seen the category.

**Do not** build Revenue Rescue or Spend Lens as the submission. Both are the 15–16 range.

## What to build next, in order

1. **Make the market story the primary demo** (already running as `--story market`).
2. **Ledger view: show the money's state, not just the decision** — `authorised`, `captured`,
   `voided` as a visible lifecycle. This is the Design gap and it is a day of work.
3. **Negotiation view**: the price climbing and visibly stopping at the ceiling.
4. **APIMatic client generated from `openapi.yaml` and committed** — then claim it, not before.
5. **Render deploy + Postman collection** — two named judges, a few hours each.
6. **Video: injection → market → tamper → self-repair.**
