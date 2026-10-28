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
