# Where the AI goes, and what it buys

One rule decides everything below:

> **A model decides what to look for and how to say it. A rule decides whether money moves.**

That is not caution for its own sake. In payments, a wrong sentence is cheap and a confident
fabrication is fatal — so the model is placed where being wrong costs a little, and the money is
placed where being wrong costs everything.

---

## The five places, ranked by whether AI is actually needed

### 1. Turning a request into a checkable brief — **necessary**

> *"Get me 12 product descriptions for the Q4 catalogue."*

A rule cannot turn that into acceptance criteria, because the criteria do not exist yet — someone has
to decide what "good" means for this buyer, this product, this budget. That is open-ended language
work, which is the model's job.

**What it buys:** the entire payment mechanism. Without criteria there is no pro-rata capture, no
release-on-failure, no dispute evidence. The brief *is* the contract, so the model writing the brief
is the model creating the thing the money depends on.

**What keeps it safe:** the brief is shown and approved *before* a single unit is authorised.
Criteria must be mechanical — `count`, `min_words`, `mentions` — or they are flagged `manual`, and a
manual criterion escalates the verdict to a human. The model proposes; the buyer disposes.

### 2. Reading a free-text claim into a reason code — **necessary**

> *"it never came"* · *"no reconozco este cargo"* · *"charged twice??"*

PayPal has a fixed vocabulary — INR, SNAD, UNAUTHORIZED, DUPLICATE, BILLING_ERROR — and each one
demands a different set of evidence. Mapping everyday language onto that closed set is
classification over messy input, and every keyword rule eventually meets *"it never came"* written as
*"didnt arrive smh"*.

**What it buys:** the right requirement list. Ask for too much and you miss the decisive item you
never knew you needed; ask for too little and you file an incomplete case, losing the amount **and**
the fee. The mapping is the difference between a winnable case and a donation.

**What keeps it safe:** the model picks from a closed enum, and a wrong pick cannot create a false
claim — the worst case is conceding a case that was winnable. That is a bounded, honest failure. An
unrecognised code returns `accept` with *"do not guess at a claim"*, never a guess.

### 3. Extracting evidence from unstructured sources — **necessary**

A courier email, a support thread, a signed scan, a timestamp. None of it is in a schema, and every
merchant formats it differently. Extraction is where a model beats a regex decisively.

**What it buys:** coverage. In the seeded case, two evidence items already existed in the payment's
own record and nobody had assembled them — finding them moves coverage from **0.33 to 1.00**, which
is the difference between `CONTEST_PARTIAL` and `CONTEST`, i.e. between a risky case and a won one.

**What keeps it safe:** every extracted item carries a `source` and a human-readable label. It is
checkable by eye in one line. Extraction that fails produces *missing* evidence, which pushes the
verdict toward `accept` — the safe direction.

### 4. Writing the response — **necessary, and the most dangerous**

The argument must be complete, ordered, and readable, in the shape a bank or an adjudicator expects.
That is writing, and writing is what models do.

**What it buys:** fewer rejected packets and far less merchant time. A well-formed response with the
right evidence wins more often than the same facts in a pile.

**What keeps it safe:** `reasoning.py`. Every sentence in a model's draft must cite an evidence id
the merchant actually holds. One untraceable sentence and the **entire draft is discarded** and the
deterministic template ships instead:

```
draft     "The parcel was delivered (ev_ship). The buyer has a documented history of
           fraudulent claims. Delivery is timestamped (ev_date)."

gate      1 sentence asserts something no evidence id supports
           rejected: ['The buyer has a documented history of fraudulent claims.']

shipped   the template - the fabrication never reaches the packet
```

Silence is survivable. A fabrication ends the merchant's account and the platform's trust.

### 5. Settlement dialogue — **necessary, lower value**

Deciding *whether* to offer, and writing the message that offers it, inside PayPal's 20-day amicable
window. The number comes from a deterministic boundary (coverage × risk); the wording is the model's.

**What it buys:** cases resolved before escalation, so no fee and no wait.

---

## Where AI must not go

| Not AI | Why |
|---|---|
| The verdict (`contest` / `partial` / `accept`) | it is a comparison of findings against a requirement map. Deterministic, auditable, and repeatable by the counterparty. |
| The amount captured | arithmetic over the brief. A model that can pick a number can be talked into a number. |
| The authorisation decision | the mandate. A ceiling is a ceiling. |
| The receipt | HMAC. Nothing about trust should depend on a sampling temperature. |

**Correction to something I said earlier:** I claimed the *assessment* itself required AI. It does
not, and it should not. What requires AI is reading the claim, finding the evidence, and writing the
argument — the verdict is deliberately a rule over those three. That is a stronger design, and the
README now says so.

---

## What it brings, in the product's own terms

| Without a model | With a model, gated |
|---|---|
| buyer writes criteria by hand, or none → no pro-rata payment exists | brief appears from a sentence, approved before money moves |
| claim → reason code by keyword → wrong evidence demanded | claim → closed enum → correct requirement list |
| evidence lives in five systems, assembled by hand | extraction fills coverage, verdicts strengthen |
| response is a template, readable but flat | model prose, every sentence evidence-checked |
| disputes escalate, fees accrue | settlement priced, offered, resolved |

For PayPal: fewer paid chargebacks, fewer ops minutes per case, and a trail that is *signed* — which
matters because in an agent-to-agent market, the payment record is often the only evidence that
exists.

---

## The honest state of it

- `reasoning.py` is built and tested (6 tests) — the gate, the provider seam, the fallback.
- `TemplateProvider` is the default. **No model is called at runtime today.**
- `HTTPProvider` speaks an OpenAI-compatible endpoint and is enabled only when `GRACE_MODEL_URL` is
  set. It has not been exercised, and this document does not claim it has.
- Wiring #1 (brief authoring) and #2 (claim → reason code) is the next build, and both are testable
  without a live key by pinning the gate's behaviour on fixed transcripts.
