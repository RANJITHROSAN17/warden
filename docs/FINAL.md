# The final idea

One page. Everything before this was narrowing.

---

## The product

> **Grace runs a merchant's PayPal dispute queue.**
> It reads the case, gathers what evidence actually exists, decides whether to refund or fight,
> and answers inside PayPal's deadlines — using PayPal's own Disputes API, not a parallel universe.

## What PayPal already does — and why that is good news

Read from `developer.paypal.com/disputes/overview` (last updated 11 June 2026):

- A **Resolution Center** (no-code) and a **Disputes API** (programmatic) for the whole lifecycle.
- **Internal disputes** (INR, SNAD): buyer files, 180-day window, PayPal holds the payment, 20 days
  to resolve amicably, escalation to a claim, 10-day adjudication, appeals.
- **External disputes**: chargebacks via the card issuer, plus **pre-chargeback alerts** where the
  merchant has **20 hours** to refund and avoid the chargeback and its fee.
- Evidence submission, buyer messaging, offers, partial refunds, appeals — all supported.

**Therefore: we do not build dispute plumbing. It is solved, and rebuilding it would be the same
mistake as rebuilding checkout.** Anything we ship calls these endpoints.

This also kills an earlier version of the plan. "An agent that files chargeback responses" is not a
product — the filing is an HTTP call. The product is everything *around* the call.

## The gap, precisely

The API moves a response. It does not answer:

1. **Which of the ~40 evidence fields does this reason code actually need?** Merchants guess, and
   lose on missing evidence that existed in another system.
2. **Should this case be fought at all?** Contesting a hopeless case costs the sale *and* the fee.
3. **Where is the evidence?** In the order system, the courier, the inbox, the CRM — none of which
   talk to PayPal.
4. **Who is watching the clock?** 20 hours for an alert, 20 days for an inquiry. A triage rota does
   not run at night.
5. **And now: what happens when the buyer was an AI agent?** There is no human to message, no
   conversation history, no "customer says". The evidence *is* the transaction record.

Points 1–4 are judgment over unstructured material — the work a model does and a rule cannot.
Point 5 is new territory that only a payment platform can serve, because PayPal holds the record.

## Why Grace wins on this, and a competitor does not

| | Third-party dispute tools (Justt, Chargeflow…) | Grace |
|---|---|---|
| Where evidence comes from | merchant uploads screenshots | **the signed authorisation and capture PayPal already holds** |
| Cost of a wrong contest | merchant's fee | same, but the decision is made before filing |
| Agent-initiated payments | nothing to work with | the transaction record is the case |
| Plumbing | their own | **PayPal's Disputes API** |

That first row is the moat: a third-party tool must *ask* for evidence. A platform-native agent can
*read* it — and in an agent economy, that signed record is often the only evidence that exists.

## What AI does, exactly

| Step | A rule cannot do this because… | What the agent does |
|---|---|---|
| Read the claim | free text, varying phrasing per buyer | maps it to a PayPal reason code and its evidence requirements |
| Gather | evidence is spread across systems and shapes | pulls the requirement list, fetches each item, notes what is missing |
| Decide | depends on evidence strength, amount, and deadline | refund / contest / partial — with the reasoning written down |
| Answer | the response must be a specific form | fills PayPal's evidence fields and writes the statement |
| Learn | outcomes differ by reason code and evidence | records what won, so the next case is decided better |

The **hard bound**: the written response is a template over findings. A model can improve the prose;
it cannot add a claim that no evidence supports. That is a guarantee, not a policy.

## The three decisions that make it a product

1. **It refuses to fight.** No decisive evidence ⇒ refund inside the 20-hour window. The most
   valuable output is `ACCEPT`, because it is the one that saves a fee.
2. **It is deadline-first.** Every case is ordered by hours remaining, not by amount.
3. **It never invents.** Missing evidence is reported missing and the claim is narrowed or dropped.

## What exists today

| Piece | State |
|---|---|
| Reason-code requirement map, PayPal vocabulary (INR, SNAD, unauthorized, duplicate, billing error) | built |
| Evidence gathering from the signed payment record + merchant attachments | built |
| `contest` / `contest_partial` / `accept` assessment with reasoning | built |
| Response packet + statement, mapped to evidence ids, `declined_evidence` listed | built |
| 20-hour pre-chargeback alert triage (refund vs contest) | built |
| `POST /api/disputes/{id}/file` — refuses a contest the evidence does not support | built |
| Dashboard: queue, coverage, recommendation, response text | built |
| Payment lifecycle (authorise → capture → partial → void) that produces the evidence | built |
| **Calling the real Disputes API endpoints** (`/v1/customer/disputes/...`) | **not built — the honest gap** |
| Learning from outcomes per reason code | not built |
| Buyer messaging / amicable resolution before escalation | not built |

The gap is stated because it is the difference between a demo and a product, and a PayPal engineer
will ask in the first minute.

## The demo, in four beats

1. **An alert with 17.4 hours left and no delivery scan** → `REFUND`. *"Contesting without evidence
   gives away the sale and a fee."*
2. **The same alert, with a signed capture and a carrier scan** → `CONTEST`. Then the response
   appears: every requirement mapped to an evidence id, and the line *"The merchant does not assert
   any fact that is not listed above."*
3. **A weak claim** → `ACCEPT`, and the system says why. This is the credibility beat.
4. **Tamper a receipt** → `VALID` becomes `INVALID`. The evidence is the product.

## Why PayPal gains

- **Losses:** fewer paid chargebacks on the same volume.
- **Fees avoided:** every refunded alert inside 20 hours is a chargeback fee not incurred.
- **Ops:** the queue arrives triaged; humans handle only genuine disputes.
- **Retention:** merchants change processor over dispute handling more than over rates.
- **A moat that compounds:** every agent-initiated payment increases dispute volume *and* the value
  of a platform-native, signed evidence trail.

## What we will not do

- Rebuild dispute plumbing that PayPal already ships.
- Contest everything to look aggressive.
- Let a model write facts.
- Claim a Disputes API integration until the endpoints are actually called.

---

**One sentence, for a judge:**

> PayPal already gives merchants a disputes API. Grace is the agent that knows which cases are worth
> answering — and answers them with evidence from the payment record itself, inside the deadline.
