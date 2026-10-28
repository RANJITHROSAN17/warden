# PayPal resources and partner tools: what is used, and how

Written against what is actually in this repository. If a row says "built", the file exists and CI
exercises it. If it says "not used", it is not used, and that is deliberate.

---

## PayPal's own resources

| Resource | Where it is used | Status |
|---|---|---|
| **`paypal-rest-api-specifications`** → `openapi/customer_disputes_v1.json` (v1.11) | vendored in `openapi/`; `src/grace/paypal_disputes.py` is written from it | **built** |
| **Disputes API** — 7 of its 14 endpoints | `paypal_disputes.py`, `mcp_server.py`, `postman/` | **built**, exercised against a simulator |
| **Orders v2** — create order, authorize, capture, void | `rail.py`, `SandboxPayPal` | **built**, sandbox only |
| **Pre-chargeback alerts / 20-hour window** | `dispute.triage_alert` | **built** |
| **Reason codes** (INR, SNAD, unauthorised, duplicate, billing error) | `dispute.REQUIREMENTS` | **built** |
| **81 `evidence_type` values** | `paypal_disputes.EVIDENCE_TYPES`, `dispute.REQUIREMENTS` | **built** |
| `evidence_info.tracking_info` shape (`carrier_name`, `tracking_number`) | `demo --story file` | **built** |
| Resolution Center | — | not applicable (a UI, not an API) |

### What this changed

The requirement map originally used **invented** field names — `delivery_proof`, `delivery_date`,
`customer_contact`. PayPal's spec has 81 evidence types and none of those are among them. Every
string is now real:

```
ITEM_NOT_RECEIVED  → PROOF_OF_FULFILLMENT · PROOF_OF_DELIVERY_SIGNATURE · ONLINE_TRACKING_INFORMATION
NOT_AS_DESCRIBED   → PROOF_OF_SIGNIFICANT_DIFFERENCE · ITEM_DESCRIPTION · PROOF_OF_FULFILLMENT
UNAUTHORIZED       → ADDITIONAL_INFORMATION · DELIVERY_ADDRESS · DETAILS_OF_PURCHASE
DUPLICATE          → DETAILS_OF_PURCHASE · COPY_OF_CONTRACT
BILLING_ERROR      → PROOF_OF_REFUND · DETAILS_OF_PURCHASE
```

A test asserts that every type this project emits exists in `paypal_disputes.EVIDENCE_TYPES`, and a
second test asserts the client's enums (`OFFER_TYPES`, `STAGES`) still match the vendored spec. If
PayPal ships v1.12 and something changes, the test fails rather than the request.

---

## Partner tools

Ten sponsors, all optional. Using one well makes you eligible for that partner's prize, so the
question is not "can I tick this box" but "does this tool make the product better".

### Used, and doing real work

**AG Grid** — `web/index.html`
The dispute queue is a data grid, not a table: 81 evidence types, coverage that wants a numeric
sort, exposure that wants re-ordering against a deadline. AG Grid Community renders the coverage
column as a bar and lets a merchant sort the queue by exposure instead of by deadline. *Fallback:*
if the CDN is unreachable the plain table underneath still works — verified in the offline preview.

**Postman** — `postman/Grace.postman_collection.json`
Eight requests in run order: OAuth, list disputes with the spec's own filters, Grace's assessment,
`provide-evidence`, `make-offer`, `accept-claim`, the character call, and Grace's own filing route.
The collection carries test scripts that assert the contract held, and it collects the access token
into a variable so only one request needs credentials.

**APIMatic** — `openapi/customer_disputes_v1.json`
PayPal's published OpenAPI document is vendored so a typed client can be generated from it rather
than hand-written. **The generated client is not committed** — APIMatic's generator needs an
account, and claiming a generated client exists when it does not would be exactly the dishonesty
this project keeps warning about. The honest state: the spec is in the repository, the hand-written
client is written from it, and generating the typed client is a one-command follow-up.

**Render** — `render.yaml`, `Dockerfile`
The judges' requirement is a demo they can interact with. This is a one-click blueprint: Docker
build, a 1 GB disk for the SQLite ledger so restarts keep their data, a health check on
`/api/health`, and PayPal credentials marked `sync: false` so they are entered in the Render UI and
never committed. With no credentials the app runs the simulator — the same code with the rail
swapped — so the demo works even before the secrets are set.

### Deliberately not used

| Tool | Why not |
|---|---|
| **Zapier** | Grace's decisions are already inside the payment flow. Wiring it to an external automation tool would add a hop and a failure mode for no gain. |
| **Elastic** | A useful search layer once there are thousands of disputes with evidence documents. At this scale SQLite and AG Grid filtering are enough, and adding a cluster to look serious would be vanity. |
| **Bryntum** | A scheduler for project work. Grace has deadlines, not Gantt charts. Wrong tool, and using it badly would be worse than not using it. |
| **Channel3** | Product/commerce search. Grace's vendors are known accounts, not a catalogue to search. |
| **KERNEL** (cloud browsers) | Would let the agent fetch a courier's tracking page itself. Genuinely interesting for evidence gathering, and the one I would add next — but it needs a live browser session and would be untested code in the submission. |
| **Astropods** (agent hosting) | Grace's agent is the decision layer, already served by FastAPI and reachable over MCP. Moving it to a hosting platform changes deployment, not the product. |

---

## Integration points, concretely

```
PayPal Disputes API  ──▶  paypal_disputes.PayPalDisputes   (7 endpoints, spec-derived)
       │                        │
       │                        └──▶  SimulatedDisputes     (same interface, no credentials)
       ▼
  Grace decision layer  ──▶  dispute.assess / settle / triage_alert
       │
       ├──▶  mcp_server.py     (stdio MCP, so any agent client can call it)
       ├──▶  api.py            (FastAPI, what Render serves)
       ├──▶  postman/          (the same calls, documented and scripted)
       └──▶  web/              (AG Grid + the dashboard)
```

---

## The one-line summary for a judge

> Every PayPal path and field in this repository is traceable to
> `openapi/customer_disputes_v1.json` or PayPal's published Orders v2 documentation. The partner
> tools that made the product better are used; the ones that would only have made a checklist look
> complete are not, and each is named above with the reason.
