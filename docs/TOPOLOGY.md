# Where the buyer and the seller actually are

A fair question to ask of any "two agents negotiate" demo, and worth answering precisely, because
the phrase can mean three different things.

## Layer 1 — the negotiation itself: in one process, in a `for` loop

`src/warden/negotiate.py`, function `negotiate()`. Two Python objects take turns:

| Side | Class | What it does | Line |
|---|---|---|---|
| Buyer | `DeterministicBuyer` / `ModelBuyer` | `counter(seller_price, round, item)` → an offer and a sentence | 53 / 74 |
| Seller | `Seller` | `respond(offer, round, item)` → a counter and a sentence | 108 |

`negotiate()` alternates between them for up to `max_rounds`, stopping when the buyer's offer meets
or beats the seller's counter, or walking away.

**There is no network call, no message queue, and no PayPal in this file.** Check it:

```bash
grep -n "paypal\|rail\|httpx" src/warden/negotiate.py   # → nothing
```

The "seller" is a stand-in for a merchant agent. It is a local object with a list price, a private
floor, and a concession rate. That is the honest description: **the counterparty is simulated.**

## Layer 2 — the payment: at the boundary, in the gateway

The moment a price is agreed, the negotiation is over and the money path begins:

```
negotiate(...)  →  agreed price
      ↓
LineItem(unit_price=agreed, qty=…)      ← the only thing carried across the boundary
      ↓
Gateway.attempt(mandate, vendor, items, stated_intent)   gateway.py:117
      ↓
six rules → allow / hold / deny
      ↓
rail.create_order() → rail.authorize() → capture() | void()    paypal.py
```

**PayPal never sees the haggling.** It sees one order, at one price, from one payee. There is no
negotiation primitive in the PayPal API, and Warden does not pretend there is one.

This boundary is deliberate: the negotiation is the model's business, and the money is the
mandate's. Everything the seller agreed is reduced to a number that the rules then check
independently — which is exactly why the buyer cannot talk its way past the ceiling.

## Layer 3 — where this would happen in the real world

| Venue | How the two agents talk | Status |
|---|---|---|
| **One orchestrator** (what the demo does) | Two objects in one process | Real, and the most common arrangement today, in LangGraph/CrewAI/AutoGen style systems |
| **A2A (Agent2Agent)** | JSON-RPC over HTTP, agent cards, task lifecycle | Published and standardised; the actual wire protocol for cross-vendor agent dialogue |
| **MCP** | Tool calls — a buyer agent calling a merchant's `get_quote` tool | Widely deployed, but tool-shaped rather than conversational |
| **Plain HTTP / REST** | A merchant exposes `POST /offer`; the buyer posts offers | Most likely near-term for procurement portals and RFP exchanges |
| **A hosted marketplace** | Both agents are tenants of the same platform | Where bidding marketplaces will put it |

The credible near-term venue is **B2B, not consumer**: a procurement agent haggling with a supplier
agent, or a buyer agent bidding on a contractor's quote. That is one invoice between two companies,
not a robot economy.

## What it would take to make the counterparty real

`Seller` has exactly one method that matters, `respond(offer, round, item) -> (price, message)`.
Swapping the local object for an A2A client or an HTTP merchant is a **transport change, not a
product change**:

1. Stand up a `merchant` FastAPI app exposing `POST /quote` and `POST /offer`.
2. Add an `HttpSeller` with the same `respond()` signature that POSTs and parses.
3. Run the buyer as a second process against it.

Then the demo is two processes, a real network boundary, and Warden sitting between them — which is
a materially stronger claim than two objects in a loop. It is also the honest way to show a judge
what "agent-to-agent" means without asserting more than the code does.

## Layer 4 — what is actually negotiable, and where PayPal sits

The sharpest objection to any negotiation demo: **on a website, the price is fixed.** That is
correct, and it rules out the setting most demos reach for.

| Where | Is the price fixed? | Would PayPal touch it? |
|---|---|---|
| Retail checkout, shop shelf, SKU | **Yes.** Nothing to negotiate | Yes — order, capture, done |
| Supplier quote for a bulk order | No — quoted per order | Yes, via invoice or order |
| Contractor or freelance bid | No — bids | Yes, commonly |
| Freight, print, custom manufacturing | No — RFQ | Yes |
| Dispute settlement, partial refund | No — negotiated | Yes, as a partial refund |
| Used goods, marketplaces, travel rates | Sometimes | Yes |

**PayPal has no negotiation primitive, and this project does not pretend it does.** The haggling
happens between two agents; PayPal sees one settled order at one agreed price. The nearest thing
PayPal ships to a negotiation artefact is the AP2 mandate — an authorisation bound to terms — which
is exactly the object Warden enforces.

That is why the demo is a **supplier quote for a bulk order**, not a shopping cart. Retail has
nothing to negotiate and the scenario would be dishonest.

### Where this is already live

A2A, now under the Agentic AI Foundation, cites agentic commerce as a live deployment: **Google
Cloud and PayPal run shopping and merchant agents over A2A across product discovery, pricing and
order fulfilment, with AP2 providing the payment authorisation layer.** Note the word *pricing* —
the negotiation already exists at the agent layer, and the authorisation already exists at the
payment layer. What is missing between them is a mandate that can hold a quoted price to a stated
purpose, and evidence that the work happened.

