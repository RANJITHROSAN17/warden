# Design: the gap, the states, and the rules

## 1. Why this window

```
create_order → authorize → [ GAP ] → capture → (void | expiry)
```

In the gap the money is committed and nothing has settled. Four things can go wrong:

| Failure | What a rule does | What it should do |
|---|---|---|
| Issuer declines the full amount | fails the payment | take what is available, release the rest |
| Rail times out | ambiguous — retry or give up? | retry, because the amount is not the problem |
| Authorisation lapses | fails the payment | release the commitment; a new instruction is needed |
| Order ships in part | usually nothing | capture the fulfilled part only |

A hardcoded rule cannot tell a timeout from a decline, and a human queue is slow enough that the
authorisation often lapses while the ticket waits. The gap is the one place in a payment where an
agent with a **bounded action space** beats both.

## 2. The states

```
                    ┌──────────── void ────────────┐
                    │                              ▼
authorise ──▶ authorised ──capture(full)──▶ captured
                    │  │
                    │  └──capture(part)──▶ part_captured ──void──▶ part_released
                    │
                    └──expiry──▶ expired
```

| State | Open? | Money |
|---|---|---|
| `authorised` | yes | committed, nothing taken |
| `part_captured` | yes | part taken, remainder still available |
| `captured` | no | fully settled |
| `part_released` | no | part settled, remainder handed back |
| `voided` | no | nothing taken, everything handed back |
| `expired` | no | nothing taken, commitment lapsed |

`part_released` exists because the common honest ending is neither "paid" nor "failed" — it is
"mostly paid". A dashboard with only two states has to lie about it.

## 3. The seven rules

Rules run in this order for a capture, and every one reports its result whether it passes or fails.

| Rule | Passes when | Why it exists |
|---|---|---|
| `payment.open` | state is `authorised` or `part_captured` | stops a double capture — taking the same money twice |
| `authorisation.not_expired` | `now < expires_at` | settling against a commitment that ended is a bug, not a sale |
| `capture.amount_valid` | amount > 0 | a zero capture is a silent no-op that looks like a success |
| `capture.within_authorised` | amount ≤ remaining | **the bound**: an agent may not take more than it was given |
| `authority.partial_permitted` | full capture, or the authority allows partials | some payers want all-or-nothing |
| `authority.attempt_budget` | attempts < max | retry storms and slow-motion loops |
| `authority.retry_permitted` | retry requested, and allowed | the payer's choice, not the agent's |

Refusals carry a counterfactual, and they are signed. The counterfactual is not politeness — it is
what turns a dead end into a recoverable one.

## 4. Policy vs rail

Two different authorities answer two different questions:

- **the policy** — may this move happen? (the engine's rules)
- **the rail** — did it work? (PayPal's answer, or the issuer's)

An action can be `allowed` by policy and still fail at the rail. That is not a contradiction and it
must not be flattened, because the remedies differ: a policy refusal means change the request; a
rail refusal means compensate. The demo prints `STOP` for the first and `RAIL` for the second.

## 5. The compensation agent

`compensate.plan(payment, authority, failure)` returns a plan; it never executes. The engine still
checks every rule, so a failure message — however persuasive — cannot widen the agent's powers.

| Failure signal | Plan | Reason |
|---|---|---|
| `RAIL_TIMEOUT`, `NETWORK` | retry | the rail failed without answering; the amount is not the problem |
| timeout, budget spent | escalate | a human should own a payment that keeps failing |
| `INSTRUMENT_DECLINED` with a known limit below the remainder | partial capture | take what is available, release the rest |
| `INSTRUMENT_DECLINED`, no partials allowed | escalate | the authority demands a full capture |
| `INSTRUMENT_DECLINED`, nothing available | void | release the commitment, tell the payer |
| `AUTHORIZATION_EXPIRED` | void | ask for a new instruction |
| anything unrecognised | **escalate** | do not guess with money |

The last row is the important one. An unrecognised failure is not a puzzle to solve at 3am with
someone else's money.

`apply()` executes a plan and stops: at most one capture attempt, then a void, never a loop. The
attempt budget in the authority is the backstop if that logic is ever wrong.

## 6. What is deliberately not here

- **No risk scoring.** The decision whether to commit money was made before the gap; that is not
  this layer's job and PayPal's own models own it.
- **No ledger heuristics.** The book is deterministic. Grace writes receipts *about* payments; it
  never guesses at double-entry.
- **No model in the money path.** The planner is deterministic today, and the reason is not
  laziness: the claim being tested is that a bounded agent with receipts is safe. That claim holds
  whichever planner fills the plan in.
