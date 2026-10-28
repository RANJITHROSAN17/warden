# Compliance

Rule-by-rule, against the PayPal AI Hackathon requirements. Each row names the evidence in this
repository, so a reviewer can check it rather than take it on trust.

Status key: **met** · **met, needs one action from the maintainer** · **not met**

---

## What to build

| Requirement | Status | Evidence |
|---|---|---|
| Integrates the PayPal developer platform, sandbox environment | **met, needs credentials** | `src/warden/paypal.py` — Orders API v2 `intent=AUTHORIZE`, Payments API capture and void. `SandboxPayPal` targets `api-m.sandbox.paypal.com`. Runs real HTTP when `WARDEN_PAYPAL_CLIENT_ID`/`_SECRET` are set; see *Sandbox proof* below |
| Meaningfully incorporates AI | **met** | `src/warden/intent.py` — an LLM judges the basket against the agent's stated reason and must cite evidence. The score is a blocking rule, not an annotation |
| Working prototype, not a mockup | **met** | `python -m warden.demo` runs six scenarios through the real engine; `python -m warden.cli serve` serves the ledger. 14 tests, CI green 3.10–3.13 |
| Sufficiently documented | **met** | `README.md` (problem, architecture, run instructions, what-is-real table), `docs/why-paypal.md`, `openapi.yaml` |
| Comply with the Official Rules | **met** | MIT licence detectable in the About section; no real money; no PayPal marks used; no committed secrets (`CI` job scans for credential shapes) |

## What to submit

| Requirement | Status | Evidence / action |
|---|---|---|
| Text description | **met** | `SUBMISSION.md` |
| Functional demo — hosted URL or runnable instructions | **met** | `README.md` → *Run it in thirty seconds*. A judge needs only `pip install -e .` and one command. **Action:** add the hosted URL once deployed |
| Tools identified and explained | **met** | README → *Sponsor tools* and *How it was built*; `SUBMISSION.md` → *Sponsor tools used, and why* |
| Public repo, open source, licence visible in About | **met** | MIT, GitHub reports `spdx_id=MIT` |
| Demo video, under 3 min, on YouTube, public | **not met** | Script is written (`SUBMISSION.md`). **Action:** record and upload |
| Video must not include third-party trademarks or unlicensed music | **met by design** | Record the product, not the logos. See *Brand rules* below |

---

## The two risks worth naming

### 1. A judge may run the demo and see no PayPal call

The default demo runs on the **simulated rail**, because the repository cannot ship PayPal
credentials. That is honest — every receipt, ledger row and console line says `simulated` — but a
reviewer who only runs `python -m warden.demo` could reasonably conclude PayPal is decorative.

Four things remove that risk, in order of strength:

1. **Deploy with real sandbox credentials.** The hosted demo then shows `sandbox` in the health
   endpoint, in every receipt, and the Orders API actually creating authorisations.
2. **Record the video against the sandbox rail**, not the simulator — `model sandbox (real PayPal
   sandbox calls)`, `PAYPAL-ORDER-…` ids on screen. This is the strongest evidence available and
   it is why the video matters more here than on most projects.
3. **Ship a transcript.** After the first successful sandbox run, commit
   `docs/sandbox-run.md` with the request and response bodies (ids redacted). A judge can read the
   real calls without holding credentials.
4. **Never let the rail lie.** `python -m warden.cli preflight` reports which rail will run and
   fails loudly on a bad key. `--rail sandbox` errors rather than falling back.

### 2. "PayPal integration is central" has to be visible, not argued

The integration is central to the design: authorise → judge → capture or void is the product's
entire spine, and the void path *only exists* because of PayPal's authorisation window. Make that
argument in the video, in the README, and in the form — but let the sandbox run make it for you.

---

## Brand rules

The video must not include third-party trademarks without permission. Practical reading:

- **Fine:** screen recordings of PayPal's own checkout and sandbox pages, because they are the
  product working. Use ids like `PAYPAL-ORDER-…` on their own — do not redraw their wordmark.
- **Avoid:** the PayPal or NVIDIA logos in our own title cards, thumbnails, or as a watermark.
- **Avoid:** background music that is not licensed or royalty-free.
- Do not imply affiliation or endorsement. This project is not affiliated with, or endorsed by,
  PayPal; it is an entry to their hackathon built on the public sandbox API.

## Sandbox proof (run this before submitting)

```bash
cp .env.example .env          # fill in the sandbox client id and secret
export $(grep -v '^#' .env | xargs)

python -m warden.cli preflight        # must print: rail  sandbox - real calls will be made
python -m warden.demo --rail sandbox  # ids should read like PAYPAL-…, not SIM-…
```

Save that terminal output. If a reviewer asks whether the PayPal integration is real, that is the
answer, and it takes thirty seconds to reproduce.

## Housekeeping before the deadline

- [ ] `.env` is never committed; `git status` clean of it. (`.gitignore` covers it; CI scans for
      credential shapes.)
- [ ] Hosted demo reachable in a private browser window with no session
- [ ] Video is public on YouTube, under three minutes, and the link is on the form
- [ ] Repository licence still detected in the About panel
- [ ] The words "sandbox only, no real money" appear in the README (they do, twice)
