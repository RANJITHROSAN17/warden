# What Warden brings to PayPal

Written for a PayPal judge, a PayPal PM, or a partner conversation. Every claim about PayPal's
current position is quoted from PayPal's own published material, with the source named. Claims
about Warden's behaviour are things you can run.

---

## 1. The gap is in PayPal's own documentation

PayPal's Seller Protection guidance (30 April 2026) extends coverage to eligible disputes
"including situations where AI shopping agents act on a customer's behalf", and it states
plainly:

> "PayPal does not assess AI intent or behavior."

That sentence is the entire opportunity. PayPal has decided that an agent-initiated purchase gets
the same protection as a human one — and PayPal absorbs the risk without any signal about whether
the purchase matched what the agent said it would do.

PayPal's own AP2 paper describes the direction of travel:

> "Issuer / Network who authorizes transactions using standard risk models **supplemented by
> mandate context**."

> "Incorporating Cart, Intent, and Payment mandates into these pipelines could add cryptographic
> artifacts that can be validated independently."

PayPal has published the theory — mandate context, cryptographic artefacts, dispute-grade
evidence. Warden is a working enforcement point and proof layer for exactly that, today, on the
sandbox.

## 2. What it gives PayPal, ranked by strength

| # | Advantage | Mechanism | Why PayPal cares | How it would be measured |
|---|---|---|---|---|
| 1 | **It unlocks enterprise agent spend** | A mandate with purpose, ceiling, category scope, payee maturity and expiry — plus a signed receipt per decision | Enterprises are not turning agents loose on money without a control they can show an auditor. 46% of large enterprises say the AI platform should bear culpability, and nearly two thirds want a liability framework before they scale. A control layer turns "we can't" into "we can" — which is incremental PayPal TPV, not a discount | Agent-enabled merchant accounts; agent-initiated TPV |
| 2 | **Fewer disputes on agent traffic** | Intent is checked *before* capture; out-of-scope purchases are held, not settled | Every drifted agent purchase that settles becomes a claim PayPal adjudicates between a blameless merchant and a customer whose agent misbehaved. Prevention at authorization is cheaper than adjudication after capture | Dispute rate on agent-initiated transactions; cost per claim |
| 3 | **Mandate context PayPal's risk models can actually consume** | An intent score with cited evidence, recorded inside a signed payload | PayPal's AP2 paper says issuers should authorize "using standard risk models supplemented by mandate context". Today that context is a document; Warden makes it a number and a sentence per transaction | Risk-model lift on agent traffic; false-positive rate |
| 4 | **Dispute-grade evidence, generated at the moment of decision** | HMAC-signed receipt: mandate, rules evaluated, intent verdict, evidence, timestamps | PayPal already adjudicates billions in disputes with authentication logs and delivery proof. A signed record of *why the purchase happened* is a new class of evidence — and the AP2 paper explicitly invites these artefacts into the pipeline | Representment win rate; evidence completeness |
| 5 | **A protocol-agnostic enforcement point** | Warden sits above the rail: AP2, ACP, UCP, MCP or a raw Orders API call all funnel through the same check | PayPal's stated strategy is one integration across leading protocols. An enforcement layer that does not care which protocol produced the intent is what makes that promise safe | Integrations that reuse the same gateway |

## 3. The strategic read

**PayPal's stated position:** it backs MCP, AP2, ACP, UCP and Mastercard Agent Pay, and offers
merchants "a single integration to leading protocols and surfaces" while remaining merchant of
record. Its differentiation is trust and dispute handling, not protocol invention.

**What that implies:** whoever owns the *enforcement point* — the thing that decides whether an
agent's intent justifies a payment, and produces the artefact when it does — owns the layer
PayPal has publicly committed to but not yet filled. Standards create the need for enforcement;
they do not remove it. Kubernetes RBAC exists and OPA still became a company.

**Where PayPal could put it without inventing anything new:**

1. As a merchant-facing control: "agent spend policies" alongside Seller Protection.
2. As a risk input: intent score as a feature in the authorization decision.
3. As dispute infrastructure: receipts as representment evidence.
4. As the reference implementation for AP2 mandate enforcement, which the ecosystem currently
   lacks in production.

## 4. What it costs PayPal, stated honestly

- **It is a liability shift, not just a feature.** PayPal currently says it does not assess AI
  intent. Adding an intent check means owning the consequences of that check being wrong. That is
  a policy decision, not an engineering one.
- **False holds reduce authorization rates.** Every rule that holds a good purchase is revenue
  not booked. Warden's answer is that holds carry a counterfactual and a one-time grant path, but
  a mis-tuned threshold will cost conversions before it saves disputes.
- **It is early.** PayPal management said on the Q4 2025 call that agentic commerce would not
  materially affect 2026 growth, and that agentic payments become more significant from 2028. So
  this is a strategic position, not a near-term revenue line.
- **PayPal could build it themselves.** The honest framing is not "acquire this" — it is "here is
  a working reference implementation of the layer you have described, and a specification anyone
  can check."

## 5. The one-line version

> PayPal has said it does not assess AI intent. Warden is what that looks like when it does — with
> the intent as a blocking rule, the refusal explained, and the decision signed.

---

### Sources

- PayPal Community Blog, *Agent Payments Protocol: Building Verifiable Trust for Agentic Commerce* —
  mandate context in authorization, cryptographic audit trails, dispute pipelines.
- PayPal Seller Protection guidance, 30 April 2026 — coverage for agent-initiated purchases, and
  the statement that PayPal "does not assess AI intent or behavior".
- PayPal *Agentic Commerce Pulse Report*, 2026 — 46% of large enterprises place culpability with
  the AI platform; nearly two thirds want a standardized liability framework.
- PayPal Q4 2025 earnings commentary — agentic commerce not material to 2026 growth; significant
  from 2028.
