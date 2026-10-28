# Competitive landscape, and an honest verdict

Researched 29 October 2026. Every competitor below is named and linked in the chat summary; this
document holds the reasoning, not the citations.

---

## The short version

**Grace is not a new idea. It is a well-placed one.**

- Escrow for agent work: **crowded** — OKX, Ethereum ERC-8183, AEGIS, AgentPay, and several more.
- Dispute AI: **a mature, funded market** — Justt ($100M+, Forbes Fintech 50), Chargeflow (20k
  merchants), Chargebacks911, ChargePay, Midifier, Sift, Signifyd.
- Agent payment authorization: **solved by consortium** — AP2 (Google, donated to FIDO, 60–100
  partners including PayPal), Visa TAP, Mastercard Agent Pay, Stripe/OpenAI ACP, Stripe MPP.

Two things I said earlier were wrong, and both were wrong in the same direction — I claimed novelty
without checking who else was building:

1. **"Refusing to contest a hopeless dispute" is not our feature.** Justt already ships
   profitability-aware scoring that automatically avoids disputes unlikely to pay off. Chargeflow
   sells alert-based deflection at $29 per prevented chargeback — the 20-hour window is their
   product, not our insight.
2. **"Verification before release" is not our feature either.** ERC-8183 defines a Job primitive
   with escrow, milestones, and **proportional payment based on pre-defined rubrics**. AEGIS takes a
   deliverable hash, routes it through a validation registry, and releases above a score of 70.

---

## The four layers, who owns them, and where Grace sits

| Layer | Who is there | Maturity |
|---|---|---|
| **Checkout** — agent completes a purchase | Stripe + OpenAI **ACP**, Google **UCP** | shipping in production |
| **Authorization** — did the user permit this | **AP2** (Intent/Cart/Payment mandates), **Visa TAP**, **Mastercard Agent Pay** | consortium, 100+ partners |
| **Settlement** — the money moves | cards, **x402** (Coinbase), **Stripe MPP**, **OKX APP** | shipping |
| **Fulfilment trust** — was the work actually done | **ERC-8183**, **AEGIS**, OKX APP escrow, AgentPay, Purple Flea | crypto-only, mostly testnet |
| **Dispute defence** — after the money moved | **Justt**, **Chargeflow**, Chargebacks911 | mature, funded, commercial |

Grace sits across the bottom two rows — **and that is the only place in the stack with no incumbent
on card rails.**

Read the layers again: settlement has four serious options, authorization has three, checkout has
two. Verification has several — all on-chain, all in tokens. Dispute defence has seven — all
post-hoc SaaS, none inside the payment authorization.

**The empty cell is: verification-gated payment on fiat rails, inside the processor.**

---

## Where Grace genuinely overlaps, stated plainly

| Grace feature | Already exists as |
|---|---|
| hold money until the work is delivered | OKX Agent Payments Protocol; ERC-8183 Job; AEGIS escrow; PayPal's own authorize/capture |
| pay proportionally for partial work | ERC-8183 "proportional payment based on pre-defined rubrics" |
| a brief with acceptance criteria agreed first | ERC-8183 "job specification, machine-readable"; ACP cart constraints |
| verification decides release | AEGIS: deliverable hash → validation score → release |
| refuse to contest hopeless disputes | Justt profitability-aware scoring |
| 20-hour alert triage | Chargeflow Alerts / Verifi / Ethoca deflection |
| AI writes the evidence packet | Chargeflow (Visa CE 3.0), Justt (500+ data points per case) |
| signed receipts | AP2 mandates are W3C Verifiable Credentials |

That is eight overlaps. None of them is fatal on its own. Together they mean **Grace cannot be
pitched as novel technology.** It has to be pitched as a different composition for a different
participant.

---

## What is actually still open

**1. Fiat rails.** Every agent-escrow system I found settles in USDC or a token, on Ethereum or
Solana. A merchant paid in dollars, disputable through a card network, does not appear in the
landscape. ERC-8183 is a draft EIP; AEGIS is testnet with mainnet planned.

**2. Inside the processor.** Chargeflow and Justt are third-party SaaS: they must *ask* the merchant
for evidence or integrate into the PSP's API. PayPal already holds the signed authorization and
capture. Nobody in the dispute market has the payment record natively — that is a structural
advantage, not a feature.

**3. Continuity from payment to dispute.** The crypto escrow systems have no chargeback concept; the
chargeback vendors have no escrow concept. Grace's actual claim is that **the brief you agreed
before payment *is* the evidence packet you file after** — one artefact, two uses. I have not found
anyone doing that.

**4. The processor's own agent toolkit is missing the verbs.** PayPal's agent toolkit exposes
`list_disputes`, `get_dispute`, `accept_dispute_claim` — read, read, surrender. An agent with the
official toolkit can only lose. That gap is public and verifiable in a repo, which for a hackathon
is worth more than an abstract novelty claim.

---

## The verdict

**Useful — conditionally. Not novel — categorically.**

**Yes, it is useful**, for three reasons that survive the competition:

- It targets the one layer with no incumbent on card rails.
- Its advantage is structural (the payment record), not technological, so it does not depend on
  out-engineering Justt or OKX.
- It exists as running code on PayPal's own primitives, which is what a hackathon judge can evaluate.

**No, it is not new**, and any pitch implying otherwise will lose credibility the moment a judge
searches "agent escrow". The honest framing is *"this is being built on-chain; nobody has built it
on cards, and the processor is the only party that can."*

### What has to change

1. **Stop implying the dispute features are novel.** Justt and Chargeflow do this well and are
   funded. Position disputes as the *consequence* of the brief, not as the invention.
2. **Lead with the card/processor gap**, because that is the only defensible claim.
3. **Name ERC-8183 and OKX in the pitch.** A judge who knows the space will trust the entry more for
   having done the reading, and the contrast — "they do it on-chain, PayPal can do it on cards,
   where the merchants already are" — is a stronger story than pretending they don't exist.
4. **Do not call it a startup.** Call it what it is: the missing product in PayPal's own agent
   stack, prototyped.

### The question I cannot answer

**Would PayPal build this itself?** Probably — it already owns authorize/capture, the Resolution
Center, and sits on AP2. That is the strongest argument for the idea (it is a natural extension of
things PayPal already has) *and* the strongest argument against it (they do not need to buy it).

For a hackathon, the first framing wins. For a company, the second one does.
