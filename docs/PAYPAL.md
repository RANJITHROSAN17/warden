# Where this sits in PayPal, and why it pays

## 1. The layers, from public descriptions

PayPal's architecture is widely described (in engineering talks and system-design write-ups) as
microservices on containers behind a load balancer, with an API gateway/BFF at the edge, an
orchestration layer sequencing each payment, graph-based fraud detection, an append-only
double-entry ledger, asynchronous event processing, batch settlement, and observability over all of
it.

**Provenance, stated plainly:** these are public reconstructions, not internal documentation. Treat
the layer list as an external model — which is enough for the argument, because the argument is
about a gap in the *pattern*, not about PayPal's private code.

| Layer | Decides today |
|---|---|
| Edge / gateway | rules, static config |
| Orchestration | hardcoded saga steps |
| Risk / fraud | PayPal's ML (industry leader) |
| Payments core | state machine |
| Ledger | deterministic, never heuristic |
| Settlement / back office | deterministic **+ human queue** |
| Async events | retry policy, then a human |
| Observability | thresholds, then a human |

## 2. The observation

Every serious control in the industry sits **before** the commitment: risk scores, spending limits,
merchant blocklists, mandates. The window **after** the commitment — where a capture can fail, a
timeout is ambiguous, and a partial order needs a partial settlement — is handled by saga
compensation rules and a human queue.

That asymmetry is defensible when payments are one-shot and slow. It stops being defensible when
agent volume arrives, because agents retry in milliseconds and expect resolution in seconds, and
because the queue grows with volume while humans do not.

## 3. What Grace adds

| | PayPal today | With Grace |
|---|---|---|
| Capture fails on a limit | retry policy, then queue | partial capture + release, receipted, in milliseconds |
| Rail timeout | ambiguous retry | treated as retry, bounded by an attempt budget |
| Authorisation lapses | ticket | explicitly expired, commitment released |
| "Why did this not capture?" | logs and a support conversation | a signed receipt with the rule, the reason, and the fix |

## 4. Why it is profitable, not just tidy

Revenue is settled volume. The gap is where settled volume is lost:

1. **Recovered volume.** A payment that would have failed entirely settles in part. That is settled
   volume PayPal would otherwise never book.
2. **Fewer stuck authorisations.** Committed money that lapses is volume that never converts.
   Deciding early, with a receipt, converts or releases it — both are better than a queue.
3. **Cost to serve.** Every ambiguous capture is a human minutes-long task. The receipt answers the
   question before it becomes a ticket.
4. **Disputes.** A signed record of what was authorised, what was attempted, what the rail said, and
   why the rest was released is the evidence a dispute needs — and it exists because the agent had to
   justify itself to the engine to act at all.

The fourth one compounds: as agent volume grows, dispute volume grows with it, and so does the value
of every transition already being signed.

## 5. What this deliberately does not do

- **It does not score fraud.** PayPal's models are the state of the art; a hackathon entry claiming
  otherwise is not credible.
- **It does not decide whether to buy.** That is the layer before; Grace starts where that decision
  ends.
- **It does not touch the ledger.** It writes receipts about payments, which is a different thing
  from writing the book.

## 6. The sentence to use

> PayPal has built world-class machinery for deciding whether money may move. Grace covers the
> window after that decision — what happens when the payment half-happens — and leaves a receipt for
> every move, including the ones it refused to make.

---

# Appendix: the second half — disputes

The gap is one place where a rule cannot decide. The other is the chargeback that may follow it.

A dispute is a deadline, a reason code, and a demand for evidence. The work is:

1. read what the cardholder claims,
2. know what that reason code actually requires,
3. find out which of those things exist,
4. decide whether contesting is worth the fee,
5. write the argument — and only the argument the evidence supports.

Steps 1–4 are judgment over unstructured material; step 5 is writing. This is the clearest case in
payments where a model is the right tool and a rule is the wrong one.

## Why PayPal earns from it

- **Losses.** A defended chargeback is not paid out. Fewer losses on the same volume.
- **Cost to serve.** Each dispute is human minutes today; the packet arrives assembled.
- **Retention.** Merchants change processor over dispute handling more often than over fees.
- **A structural advantage nobody else has.** The evidence is strongest when it comes from the
  payment record itself — and PayPal owns that record. A third-party dispute tool has to ask the
  merchant for screenshots. Grace reads the authorisation and capture that PayPal already signed.

That last point is the moat, and it is the reason this belongs inside the payment platform rather
than beside it.

## What it must never do

- **Invent evidence.** A missing requirement is reported missing, and the recommendation falls back
  to accepting the claim.
- **Contest everything.** A weak case costs a fee and a merchant's trust.
- **Let a model write the claims.** The narrative is a template over findings. A model can improve
  the prose; it cannot add a fact.
