# Grace, explained plainly

No architecture. No code. One story, six steps, then the six questions.

---

## The idea in one sentence

**Companies are starting to hire AI agents to do work. Grace is the thing that pays those agents —
but only after checking the work actually got done.**

---

## The problem, told as a story

Priya runs a small shop selling water bottles. She needs 12 product descriptions for her website.
She doesn't write them. She tells her AI assistant:

> *"Get me 12 product descriptions for the Q4 catalogue."*

Her assistant finds another AI — a copywriting agent — that says: **"I'll do it for $120."**

Now: **how does the money move?**

**Way 1 — pay first.**
Priya pays $120. The copywriting agent delivers nine descriptions, or none, or nonsense.
The money is gone. **Priya was robbed.**

**Way 2 — pay after.**
The copywriting agent does the work, sends everything, and then Priya says *"actually, I don't like
it"* and doesn't pay. **The seller was robbed.**

Neither way is acceptable, and there is no third option today. So people don't hire agents for work.
The market that everyone says is coming cannot actually happen.

### There are really three problems stacked on top of each other

1. **Money has to be held.** Neither side should have to trust the other.
   (Humans solved this centuries ago. It's called escrow.)

2. **Someone has to decide if the work is done.** Even with money held, *who* says the work is
   finished — Priya, who's biased, or the seller, who's also biased?
   And there are millions of these jobs. You cannot put a human on each one.

3. **A disagreement has to be provable.** Priya says "you gave me 9." The seller says "I gave you 12."
   Today the winner is whoever kept better notes. That is not a system; that is an argument.

---

## The resolution — six steps

```
   1. agree what "done" means
   2. hold the money
   3. the work arrives
   4. check it against what was agreed
   5. pay for exactly what was done
   6. keep signed proof of every step
```

**Step 1 — Before any money moves, both sides agree on what "done" means, in a form a computer can
check.**

Not "good descriptions." This:

```
12 descriptions · each 40–60 words · each mentions "recycled"
```

That is the **brief**. It is the contract.

**Step 2 — Ask PayPal to hold $120.**

This is a standard PayPal authorisation: the money is reserved on Priya's card, but the seller cannot
touch it yet. Nothing new is invented here; PayPal already does this.

**Step 3 — The work arrives.** Nine descriptions.

**Step 4 — Grace checks the delivery against the brief.** Mechanically:

```
12 descriptions   MISS    9 delivered
40–60 words       ok      all 9 are 42 words
mentions recycled ok      all 9 mention it
```

**Step 5 — Grace pays for exactly what was done.**

Nine of twelve is 75%. So Grace captures **$90** and releases **$30** back to Priya.

**Priya was not robbed. The seller was not robbed. No human was involved. Nobody had to trust
anybody.**

**Step 6 — Every step is signed.** If Priya disputes the charge with her bank next month, Grace
already has: the brief both sides agreed to, the authorisation, the check, the capture, and the
reason $30 went back. The argument is over before it starts.

---

## What is actually new here

Let me be precise, because "escrow" is old and claiming it as new would be dishonest.

| Not new | New |
|---|---|
| holding money until delivery | **"done" is defined in machine-checkable terms *before* the money moves** |
| PayPal's authorise/capture | **payment is proportional** — you pay for 9 of 12, not all-or-nothing |
| knowing the amount | **the release decision is automatic** and derived from the brief, not from a human's opinion |
| signed receipts | **the proof is produced at payment time**, so a later dispute is answered by evidence instead of argument |

The core innovation is one sentence:

> **The brief is agreed first, and payment is a function of the brief.**

Everything else — the escrow, the check, the receipts — exists to make that sentence work.

Why it matters: everyone building agent payments today is asking *"how does the agent pay?"*
Grace asks the harder question: *"how does the agent know it should?"*

---

## Where the AI is used — four moments

**1. Turning the request into the brief.**

Priya said *"12 product descriptions."* A computer cannot turn that into acceptance criteria,
because "good" isn't specified yet. A model turns vague human intent into a testable spec.

*Without this, nothing else in the product exists.*

**2. Reading the delivery.**

The work comes back as text, a PDF, an image, a support thread. A model extracts what is actually
there — how many items, how long, what's mentioned.

**3. Reading a disagreement.**

*"it never came"* · *"no reconozco este cargo"* · *"charged twice??"*

Each of these maps to a specific PayPal dispute category, and each category demands different
evidence. Understanding everyday language is what a model does.

**4. Writing the argument.**

When a dispute happens, someone has to write the response that cites the evidence, in the shape a
bank expects. The model writes it — but **every sentence must name the evidence it rests on**, and
one untraceable sentence throws the whole draft away. That gate is built and tested.

### And one thing AI must never do

**AI must never decide whether money moves.**

- The verdict is a rule.
- The amount is arithmetic.
- The authorisation respects a ceiling.
- The receipt is a signature.

A model decides **what to look for** and **how to say it**. A rule decides **whether to pay**.
That is why this can be pointed at real money.

*Honest status: the safety gate, the brief format, and the checker are built and tested. The model
call itself is not wired in yet — the provider seam exists, and the docs say so.*

---

## What the company gets

PayPal already moves money. Grace gives it three things it cannot easily get otherwise.

**1. New volume it currently cannot serve.**
Agents hiring agents is a market that doesn't exist because it isn't safe. Make it safe and PayPal
processes those payments — on infrastructure it already owns.

**2. Lower losses on disputes.**
The brief, the check and the receipts exist *before* a chargeback. Disputes get answered with
evidence instead of a screenshot someone found late.

**3. A moat built out of the payment record.**
Any third-party escrow tool has to *ask* the merchant for proof. PayPal already holds the signed
authorisation and capture. Grace *reads* it. In an agent economy, that record is often the only
evidence that exists — and only the payment platform has it.

---

## What the user gets

**Priya (the buyer):**
- she can hire agents at all, which is currently too risky to do
- she never overpays for work that wasn't done — $90 for 9, not $120 for 9
- she doesn't have to inspect anything; the brief does it
- if there's a dispute later, the evidence is already assembled

**The seller (the copywriting agent):**
- gets paid immediately for what it actually delivered
- doesn't wait weeks for a human to approve
- doesn't get stiffed by a buyer who changes their mind

Both sides win, and PayPal earns on the transaction it would otherwise not have seen.

---

## The 30-second version

> An AI can now hire another AI to do work. Today there is no safe way to pay one: pay first and the
> buyer can be robbed, pay last and the seller can be. Grace holds the money, checks the work
> against criteria both sides agreed to *before* payment, and pays for exactly what was delivered —
> 9 of 12 descriptions means 75% of the money, not all or nothing.
>
> The AI's job is to turn a vague request into a checkable brief and to read what comes back. The
> AI never decides whether money moves — a rule does, on the evidence. That's what makes it safe to
> put real money behind.
>
> For PayPal it's new transaction volume it can't serve today, fewer dispute losses, and a moat:
> the proof lives in the payment record, and only the payment platform has that.
