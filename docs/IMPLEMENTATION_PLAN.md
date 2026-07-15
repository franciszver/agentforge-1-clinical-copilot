# Clinical Co-Pilot — Implementation Plan

- **Date:** 2026-07-14
- **Base:** OpenEMR 8.2.0-dev (pruned import of `Gauntlet-HQ/openemr-base-clean`)
- **Status:** founding document. Once the GitHub Project board is populated, the board and its issues become the operational plan and this document freezes. `docs/TEST_PLAN.md` stays living and owns all testing detail. Orientation for new contributors: `docs/DEVELOPERS_GUIDE.md`.

## 1. Overview

An AI Clinical Co-Pilot embedded in OpenEMR: a physician asks questions about the patient whose chart is open — "what changed since I last saw her?", "does anything she's taking conflict with starting ibuprofen?" — and gets answers where **every factual claim is deterministically re-verified against the record and cited**, from the desktop workstation or a phone.

### Architectural thesis

> **A small local model with a deterministic verification layer that independently re-checks every claim against the record beats a big cloud model you blindly trust — and nothing, ever, leaves the machine.**

## 2. Locked Decisions

| Decision | Choice | Rationale (short) |
|---|---|---|
| LLM | **Local-only**, Pi 5 / flagship-phone-class models (~3–4B), served by Ollama | PHI never leaves the machine — provable by firewall rule, not policy. BAA becomes moot. |
| Primary model | **Qwen3-4B-Instruct, Q4_K_M** (~2.2 GB) | Best sub-7B tool calling (62% BFCL v3); native Ollama tool + structured-output support. Alternates: Gemma E4B-class, Phi-4-Mini. |
| Agent stack | **Python + FastAPI + Pydantic** | Best local-LLM/eval ecosystem; security comes from architecture (token pass-through, no egress), not language. |
| Observability | **Hand-rolled**: SQLite trace store + self-built dashboard | Keeps the footprint small and the stack fully understood end-to-end; Langfuse self-host (4 containers, GBs of RAM) fights the small-footprint ethos. |
| Remote access | **Tailscale** (always on): `tailscale serve` for tailnet HTTPS; Funnel only for occasional public demos | Phone reaches the stack from anywhere over WireGuard with zero public exposure. |
| Mobile | **Mobile-first responsive UI** + standalone **PWA** route; Capacitor wrap documented as future path | Same codebase serves the embedded OpenEMR panel and the installable phone app. Native app deferred (PHI-on-device is its own security project). |
| UI embedding | OpenEMR **custom module** (`interface/modules/custom_modules/`) using Symfony render events | The sanctioned integration path; `oe-module-dashboard-context` is the working reference. |
| Data | OpenEMR demo dataset via `DEMO_MODE=standard` | Ships with the flex docker image (pinned, checksummed); realistic patients at zero build cost. Synthetic only. |
| Authorization | Agent calls OpenEMR's own REST/FHIR API **with the logged-in user's OAuth token**, plus patient-context binding (§4.2) | OpenEMR's ACLs enforce per-user access; the agent physically cannot fetch what its user can't. |
| Improvement | **Human-on-the-loop feedback loop**: 👍/👎 → review queue → promote-to-eval → re-run | System measurably improves over time without auto-finetuning (which would raise HIPAA training-authorization issues in a real deployment). |
| Visibility | **Public repo from day one** | Development happens in the open; no secrets in the tree; public repos get unlimited free Actions minutes. |
| Planning | **Public GitHub Project board** + repo issues as the operational plan | Well-scoped issues closed by reviewed PRs are the operational record; the board is a free view over them. This doc freezes once the board is populated. |
| Execution | **Orchestrator-only lead agent; tiered subagents implement** — Haiku for boilerplate (Sonnet reviews), Sonnet for implementation (Opus reviews); **strict TDD everywhere**; three gates (`/simplify` → `/security-review` → `/code-review`) on every PR before push to main; one issue = one branch = one PR; **nothing reaches main without a PR, and no PR merges without a linked board issue** | The orchestrator never edits files; every diff is reviewed by a fresh agent on an equal-or-better model before commit; red→green TDD history is visible in every PR; every merged change traces to a board object — the development process is documented and reviewable end-to-end. |

## 3. Target User & Use Cases

**Persona:** Primary-care physician, ~20-patient day, 90 seconds between rooms. Desktop workstation in the exam room, **phone in pocket everywhere else** — hallway, hospital stairwell, home call. The phone is why mobile-first is a requirement, not a nicety. (Full persona: `USERS.md`.)

| UC | Use case | Why an agent (not a dashboard) |
|---|---|---|
| UC1 | **Pre-visit brief:** "What changed since I last saw her?" | The answer spans encounters, labs, meds, and notes — a synthesis task, not a sorted list |
| UC2 | **Medication safety:** "What is she taking, and does anything conflict with starting ibuprofen?" | Requires cross-referencing meds + allergies + interaction data, then citing each source |
| UC3 | **Lab trend recall:** "Last three A1c values and dates?" | Faster asked than clicked (4+ navigations in stock OpenEMR) |
| UC4 | **Follow-up drill-down:** "Which visit was that from?" → "Show the note" | Inherently conversational — context from the previous turn is the query |
| UC5 | **Hallway recall (mobile):** same questions from the phone over Tailscale before walking in | The 90-second window often happens away from a workstation |

Every tool built must point at one of these. No capability without a use case.

## 4. System Architecture

```
                         Tailscale (WireGuard, always on)
   phone / any device ────────────────┐
                                      ▼
┌──────────────────────────── docker network (internal-only, NO internet egress) ─────┐
│                                                                                      │
│  ┌────────────────┐   session+CSRF   ┌─────────────────────────────┐                 │
│  │ OpenEMR (PHP)  │◄─────────────────┤ Browser: OpenEMR UI         │                 │
│  │  - demo data   │                  │  └ Co-Pilot panel (module)  │                 │
│  │  - REST/FHIR   │                  │ Standalone PWA route /copilot│                │
│  │  - OAuth2      │                  └──────────────┬──────────────┘                 │
│  │  - audit log   │                                 │ SSE chat (bearer token)        │
│  └───────┬────────┘                                 ▼                                │
│          │  FHIR/REST w/ USER's token   ┌──────────────────────────┐                 │
│          └──────────────────────────────┤ Agent service (FastAPI)  │                 │
│                                         │  - agent loop (Pydantic) │                 │
│  ┌────────────────┐   localhost only    │  - verification layer    │                 │
│  │ Ollama         │◄────────────────────┤  - /health /ready        │                 │
│  │  Qwen3-4B Q4   │                     │  - /feedback             │                 │
│  └────────────────┘                     └──────┬──────────┬────────┘                 │
│                                                │          │                          │
│                                   ┌────────────▼───┐  ┌───▼───────────────┐          │
│                                   │ Drug-interaction│  │ Trace store       │          │
│                                   │ SQLite (offline)│  │ (SQLite) + dash   │          │
│                                   └────────────────┘  └───────────────────┘          │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 OpenEMR module (the embedded UI)

New module `interface/modules/custom_modules/oe-module-clinical-copilot/`, cloned structurally from `oe-module-dashboard-context`:

- `openemr.bootstrap.php` registers the PSR-4 namespace via `ModulesClassLoader` and subscribes via the Kernel's Symfony event dispatcher.
- **UI injection:** `PatientSummaryCard\RenderEvent::EVENT_HANDLE` adds a Co-Pilot card to the patient dashboard; `PageHeadingRenderEvent` adds a persistent open-chat button. Current `pid`/`encounter`/`authUserID` come from the session, passed to JS via `js_escape()`.
- `public/ajax.php` follows the reference pattern: `globals.php` restores the session, `CsrfUtils::verifyCsrfToken()` gates every request. Its one job: broker the OAuth handshake and hand the panel a token + agent URL.
- Module registered in the `modules` table, enabled via Module Manager.

### 4.2 Auth flow

1. Co-Pilot is registered as a **confidential OAuth2 client** in OpenEMR (authorization_code + refresh grant, `user/*.read`-scoped SMART scopes).
2. First open per user: standard authorization_code redirect (one consent click). Agent stores the refresh token per user; access tokens live 1h, refresh 3 months.
3. Every tool call hits `/apis/default/api/...` or `/apis/default/fhir/...` with **that user's bearer token**. OpenEMR validates the JWT, maps to the user, and enforces ACL per endpoint. A nurse's session cannot fetch what nurses can't see — enforcement lives in OpenEMR, not in agent code.
4. Dev shortcut (documented as dev-only): password grant against the demo instance to unblock early phases.

**Patient-context binding:** role ACLs alone are not patient-granular — a physician's token can fetch *any* chart. The agent therefore enforces minimum-necessary scope itself: every conversation is anchored to the `pid` the panel was opened on, and the tool layer refuses any other patient id. This is defense-in-depth narrowing, not a second RBAC — role enforcement stays in OpenEMR. Production path (documented in ARCHITECTURE.md, not built): SMART patient-context tokens (`patient/*.read` + launch context), so the token itself carries the patient boundary.

**Chart-access audit trail:** three layers — OpenEMR's `api_log` captures every REST call automatically; the module writes `EventAuditLogger->newEvent()` per patient-chat open; the agent's trace store records user + patient + correlation id per turn. "Who accessed this chart through the Co-Pilot, and when" is answerable from OpenEMR's own audit log.

Required globals: `rest_api` and `rest_fhir_api` enabled (compose env already sets these in development-easy).

### 4.3 Agent service (FastAPI)

Lives in `services/copilot-agent/`. Endpoints:

- `POST /chat` — SSE token stream; multi-turn via conversation id
- `POST /feedback` — 👍/👎 + comment, linked to correlation id
- `GET /health` (process alive) · `GET /ready` (checks OpenEMR API, Ollama, trace store — real checks, not `return 200`)
- `GET /dashboard` — the observability page · `GET /docs` — auto-generated OpenAPI

**Agent loop, tuned for a 4B model:**
- **Single tool call per turn**, temperature 0; few-shot tool examples in the system prompt.
- **Two-call pattern:** reason in free text first, then extract the final answer into a Pydantic-schema-constrained JSON via Ollama's `format` parameter (grammar-constrained decoding). Constraining only the extraction step avoids the "constraint tax" on reasoning quality.
- **Privilege separation** (prompt-injection defense): the *planner* call sees only the user's question and tool signatures — never raw record text. A *quarantined* call summarizes raw tool output (which may contain adversarial text in notes) into structured JSON and **cannot invoke tools**.

**Tools (all Pydantic-schematized, each mapped to a UC):** `get_patient_summary`, `get_medications`, `get_allergies`, `get_problems`, `get_recent_labs`, `get_vitals`, `get_encounters`, `get_appointments`, `check_drug_interactions` (offline SQLite; no OpenEMR call).

### 4.4 Verification layer (trust but verify)

Runs on every response before it reaches the user:

1. **Source attribution:** the constrained output schema requires each factual claim to carry `source_refs` (tool-call id + record uuid + field). A deterministic checker re-validates every cited value against the cached tool results from this conversation. Claims with missing/failed citations are stripped and replaced with "not found in record" notices — never silently passed.
2. **Domain constraints:** deterministic, not LLM-based — (a) any medication mentioned is cross-checked against the patient's allergy list; (b) medication pairs run through the offline drug-interaction SQLite (DDInter-style severity levels). Violations attach a visible warning banner.
3. **Verdict:** every response gets `verified | partially_verified | blocked`, shown as a badge in the UI (tap a citation chip to see the underlying record) and logged to the trace store.

Known limitation, stated honestly: verification covers *claims about structured record data*; it cannot validate free-text clinical reasoning.

### 4.5 Observability (hand-rolled)

- Middleware assigns a **correlation id** per chat invocation; it appears on every log line, tool call, LLM call, and verification event.
- Spans written to **SQLite** (`traces.db`): request, each tool call (args hash, duration, ok/fail), each LLM call (model, tokens in/out, duration), verification result, feedback.
- **Dashboard** (single FastAPI page + Chart.js, responsive for phone): request count, error rate, p50/p95 latency, tokens/request, tool-call and retry counts, verification pass rate, eval pass-rate-over-time, feedback review queue.
- **Alerts:** threshold banners — p95 latency, error rate, tool-failure rate, verification-fail rate — each with a paragraph of "what it means / what to check."
- Token cost tracking reports **energy/time cost** (local) with a "what this would have cost on cloud APIs" comparison — feeds the TCO section of ARCHITECTURE.md.

### 4.6 Eval suite + feedback loop

**Eval harness:** pytest + YAML case files in `evals/`. Fully offline — deterministic assertions and reference-based key-fact matching; no cloud judges. Each case documents the failure mode it guards. Full strategy, authoring convention, and CI record/replay design: `docs/TEST_PLAN.md`.

Categories: hallucination bait, missing data, ambiguity, authorization probe, stale data, injection, constraint, regression (≥25 cases at Phase 4 close).

**Feedback loop (human-on-the-loop):**
1. Every response gets 👍/👎 + optional comment in the chat UI (thumb-sized, mobile-first).
2. Feedback lands in the trace store, linked to the full correlation-id trace.
3. Dashboard **review queue** shows 👎 responses and verification failures with their complete traces.
4. Reviewer clicks **"promote to eval case"** → generates a YAML regression case into `evals/regressions/`.
5. Improvement cycle: adjust prompts/tools/rules → re-run suite → pass-rate curve updates on the dashboard.
6. **Deliberately no auto-finetuning:** in a real deployment, training on PHI requires patient authorization or rigorous de-identification — the loop improves *the system around the model*, not the weights.

### 4.7 Mobile-first delivery (PWA)

- Chat UI built **360px-first**, then scaled up: thumb-reach send button, streaming text, tappable citation chips, no hover-dependent interactions. Dashboard responsive too.
- **Standalone route** `/copilot` with its own manifest (`display: standalone`): install prompts don't fire inside iframes, so the embedded OpenEMR panel and the installable PWA are the same app at two URLs.
- **Service worker: static assets only.** All API/chat routes are network-only — **no PHI ever enters Cache Storage** (a deliberate security control).
- Phone access path: Tailscale app on the phone → `https://<machine>.<tailnet>.ts.net` via `tailscale serve` → full Co-Pilot from anywhere, zero public exposure.
- **Future path (documented, not built):** Capacitor wrap for app-store presence; on-device inference (Qwen3-4B-class runs ~10–15 tok/s on flagship hardware via llama.cpp/MLC).

### 4.8 Access & demo

- **Daily/dev:** `tailscale serve` maps the stack to tailnet HTTPS. Always available to the developer's own devices; never public.
- **Live demos:** screen-share, or `tailscale funnel` toggled on for the session to mint a real public URL, then toggled off.
- Deployment = any box on your tailnet. Path to production (VPC, TLS everywhere, BAA-covered hosting, HA) in ARCHITECTURE.md.

## 5. Security Architecture (summary; full version in ARCHITECTURE.md)

**Trust boundaries:**
1. Browser ↔ OpenEMR: session + CSRF (existing OpenEMR controls)
2. Panel/PWA ↔ Agent: OpenEMR-issued OAuth bearer token; agent validates against OpenEMR
3. Agent ↔ OpenEMR API: same user token → per-user ACL enforced by OpenEMR
4. Agent ↔ Ollama: internal docker network, unreachable from outside
5. Everything ↔ Internet: **no egress** — agent/Ollama containers have no route out; remote access exists only via Tailscale's WireGuard mesh

**Controls:** prompt-injection privilege separation (§4.3); tool results treated as data, never instructions; every agent invocation audited (three layers, §4.2); **patient-context binding** (§4.2); minimum-necessary tools (scoped resources, never whole-chart dumps); demo data only.

## 6. Security Audit

Codebase analysis identified **~10 candidate security findings** in the OpenEMR base (API surface, data-at-rest, session/ACL). Each is verified in Phase 1 — reproduction, impact, remediation — and published with full context in `AUDIT.md`. Candidate details are withheld until verified; publishing an unverified vulnerability list about a codebase others deploy would be irresponsible. The audit also covers performance, architecture, data quality, and HIPAA-relevant controls (present: audit trail with SHA3-512 tamper checksums, AES-256 field/document encryption, breakglass emergency access) and gaps.

## 7. Performance & Capacity

**Expectations table (to be measured in Phase 5; research-informed priors):**

| Hardware tier | Model (Q4_K_M) | Expected speed | Verdict for this use |
|---|---|---|---|
| RTX 5060 laptop 8GB (dev/demo) | Qwen3-4B | ~40–100+ tok/s | Fully interactive; primary demo target |
| Raspberry Pi 5 8GB (CPU) | Llama 3.2 3B / Qwen3-4B | ~5–9 tok/s | Works but 30–60s/answer — "pre-visit brief generated ahead of time" mode, not live chat |
| Flagship phone | 3–4B via llama.cpp/MLC | ~10–15 tok/s | Future on-device path; today the phone is a *client* over Tailscale |

**Capacity test:** Locust/k6 at 5 and 10 concurrent chats against the laptop. Expected finding: the GPU serializes generations → queueing dominates p95. Deliverable: "one node serves N concurrent clinicians at acceptable latency; a 300-user clinic = M nodes at ~$X each." Baseline CPU/RAM/VRAM recorded during the run.

## 8. Execution Model & Build Phases

Each phase has a verify gate; nothing advances on vibes. Phases map to **GitHub milestones**; every task below is one **issue = one feature branch = one PR**, listed in execution order.

### 8.0 Execution model (applies to every phase)

**Agent roles — every phase runs this way:**
- **Orchestrator:** plans, sequences, spawns subagents, merges, and updates the board. **Never edits files directly.**
- **Implementation subagents:** **Sonnet** for all implementation; **Haiku** only for pure boilerplate (scaffolding, fixtures, config).
- **Review — fresh agent, equal-or-better model, before every commit:** Haiku diffs → **Sonnet** runs `/code-review`; Sonnet diffs → **Opus** runs `/code-review`. All findings fixed and tests re-run green before the commit lands.

**Two hard invariants (no exceptions after the Phase −1 bootstrap):**
1. **Nothing reaches `main` without a feature branch and a PR.** No direct pushes — branch protection enforces this mechanically.
2. **Every PR is tracked by a GitHub Project object.** Each PR links its board issue via `Closes #N`; a PR with no linked issue does not merge. Unplanned work discovered mid-phase gets an issue created on the board *first*, then a branch — the board never lags reality.

**Per-task pipeline (one issue = one branch = one PR):**
1. Issue moves to *In Progress*; branch `feat/p<N>-<slug>` (or `fix/`, `docs/`, `ci/`) off `main`.
2. **Red first (strict TDD, everywhere):** the failing artifact is written and shown failing *before* implementation — pytest for Python, PHPUnit for module PHP, Jest for JS logic, eval case for agent behavior, Panther/Selenium scenario for UI flows. The red commit is visible in PR history.
3. Implement to green; refactor; commit at each green boundary (conventional commits + `Assisted-by` trailer).
4. **Three gates on the full PR diff, before any push toward main:** `/simplify` → `/security-review` → `/code-review`. **Every finding fixed, full test suite re-run green after fixes.** No exceptions, including docs PRs.
5. Push branch, open PR (`Closes #N`); CI (`copilot-ci.yml`, minimal from Phase 0) must pass.
6. Merge to `main`; issue auto-closes; board updates.

**Strict TDD everywhere:** accepted cost ≈ +20–30% schedule vs a hybrid approach, concentrated in module/UI glue where unit tests must mock OpenEMR internals; accepted consciously and recorded in the decision log. Mitigation: every mock-based module test is paired with a Panther/Selenium scenario so assumptions about OpenEMR are checked against the real running stack. Full strategy: `docs/TEST_PLAN.md`.

**Task legend:** `S` = Sonnet dev / Opus review · `H` = Haiku dev / Sonnet review · `D` = docs-only (no TDD — no behavior — but branch → PR → three gates → merge still applies). Tasks marked *(user)* or *(orchestrator)* are manual/orchestration steps that produce no diff.

### Phase −1 — Repo public + planning infrastructure (in progress, 2026-07-14)

**Bootstrap exception:** the board and branch protection don't exist yet, so these tasks run directly on `main` — the three gates still run before every push. PR discipline starts at Phase 0.

| # | Task | Tier | Detail / done-when |
|---|---|---|---|
| P-1.1 | Push `main` to origin | *(orchestrator)* | Repo visible on GitHub, `git status` clean. |
| P-1.2 | Prune inherited workflows | S | Delete upstream OpenEMR release/certification/Codecov workflows from `.github/workflows/`. Done when the Actions tab lists only the curated set. Our own `copilot-ci.yml` lands in Phase 0. |
| P-1.3 | Commit public engineering plan + operating docs | S | This document, `docs/TEST_PLAN.md`, `docs/DEVELOPERS_GUIDE.md`, and the CLAUDE.md "Development Operations" section. Three gates; commit; push. |
| P-1.4 | Flip repo public | *(user + orchestrator)* | Pre-flight: secret scan of tracked files and history; local working notes confirmed untracked. Then `gh repo edit --visibility public`. |
| P-1.5 | Create public Project board + import plan | *(orchestrator)* | Public GitHub Project linked to the repo; one milestone per phase; every §8 task becomes an issue (verify gates → acceptance-criteria checklists; labels for phase and tier). **Add the Project URL to README.md at creation.** |
| P-1.6 | Protect `main` | *(orchestrator)* | Branch protection: PRs required; `copilot-ci.yml` status check required once it exists (P0.7). |

**Source of truth after import:** GitHub issues + board become the operational plan; this document freezes. **Exception: `docs/TEST_PLAN.md` stays living** — each piece of test infrastructure it specifies is tracked by its own board issue (P2.0 fixtures, P4.7 harness + record/replay, P4.9 promote-to-eval, P0.7/P5.2 CI).

*Verify:* repo public; curated Actions tab; board public and populated with all phases; direct pushes to `main` blocked.

### Phase 0 — Stack up (~1 day)

| # | Task | Tier | Red-first artifact → done-when |
|---|---|---|---|
| P0.1 | `build: docker-compose.copilot.yml` | H | Red: failing `scripts/smoke.sh` asserting compose config valid + agent/ollama services defined. Named file (base gitignores `docker-compose.override.yml`); agent + ollama + model volume, `DEMO_MODE=standard`, internal-only network (no egress). |
| P0.2 | `feat(agent): FastAPI skeleton + /health` | S | Red: pytest `GET /health == 200` fails (no service). Green: minimal app in `services/copilot-agent/` + Dockerfile. |
| P0.3 | `feat(agent): /ready with real dependency checks` | S | Red: pytests per dependency state (OpenEMR API up/down, Ollama up/down, trace store writable) using fakes. Green: real checks — no `return 200` theater. |
| P0.4 | `build: ollama service + Qwen3-4B pull` | H | Red: smoke test asserting model present in `ollama list` + one-prompt generation streams tokens. |
| P0.5 | `feat(agent): OAuth client registration + dev token flow` | S | Register confidential client; dev-only password grant documented as dev-only. Red: token-acquisition test + authenticated patient-API call succeeds; bad-credential path fails cleanly. |
| P0.6 | `feat(ui): chat shell page` | S | Red: Panther scenario asserting shell elements render at 360px and desktop widths. |
| P0.7 | `ci: minimal copilot-ci.yml` | H | Pytest for `services/copilot-agent` on every PR. Expanded in P5.2. |
| P0.8 | Wire `tailscale serve` | *(user)* | Stack reachable over the tailnet from the phone. |

**User scenarios (must pass before phase close):** (1) login shows demo patients; (2) `GET /ready` green on all three checks; (3) chat shell loads on desktop **and** phone.

### Phase 1 — Audit + docs skeleton (~1–1.5 days)

Issue titles stay neutral — "candidate finding batch", no exploit detail until verified into AUDIT.md. Audit tasks are investigations (red-first n/a); deliverable is a reproduction note per finding.

| # | Task | Tier | Detail / done-when |
|---|---|---|---|
| P1.1 | `audit: verify candidate findings — API surface batch` | S | 3 candidates. Each: repro steps + impact + remediation draft, private until write-up. |
| P1.2 | `audit: verify candidate findings — data-at-rest batch` | S | 3 candidates. |
| P1.3 | `audit: verify candidate findings — session/ACL batch` | S | 4 candidates. |
| P1.4 | `docs: AUDIT.md` | D | Security-led write-up from *verified* findings only; ~500-word summary first; perf/data-quality/compliance sections one paragraph each. |
| P1.5 | `docs: USERS.md` | D | Half-page persona + UC1–UC5 table; each UC answers "why an agent, not a dashboard." |
| P1.6 | `docs: ARCHITECTURE.md draft` | D | Diagram-first; trust boundaries (§5); 500-word summary. Final pass in P5.4. |

*Verify:* every audit claim in AUDIT.md has a reproduction note; docs merged via gated PRs.

### Phase 2 — Agent core (~2–3 days; longest phase)

| # | Task | Tier | Red-first artifact → done-when |
|---|---|---|---|
| P2.0 | `test(fixtures): canonical patients + seeding script` | S | First task of the phase — TEST_PLAN §7 infrastructure. Select canonical demo patients (allergy-conflict candidate, no-labs, stale-data-only, multi-encounter) and populate the TEST_PLAN fixture table; idempotent `evals/fixtures/seed.py`. Red: seed-script tests — idempotency, expected state present after run. |
| P2.1 | `feat(agent): Pydantic schemas for all tool I/O` | S | Red: round-trip + rejection tests (missing fields, wrong types, boundary values) for all 9 tool contracts. |
| P2.2 | `feat(agent): OpenEMR API client` | S | Red: tests against recorded fixtures — bearer pass-through, REST + FHIR base URLs, error taxonomy (401/403/404/timeout). |
| P2.3 | `feat(agent): tool — get_patient_summary` | S | Red: fixture tests (happy path, empty sections, 403) → integration test against a demo patient. UC1. |
| P2.4 | `feat(agent): tools — get_medications + get_allergies` | S | Red: incl. empty-list and unknown-patient cases. UC2 backbone. |
| P2.5 | `feat(agent): tools — get_problems, get_recent_labs, get_vitals` | S | Red: incl. the no-labs patient (seeds the "missing data" eval category). UC1/UC3. |
| P2.6 | `feat(agent): tools — get_encounters + get_appointments` | S | Red: incl. date-range filters. UC1/UC4. |
| P2.7 | `feat(agent): Ollama client — chat + constrained extraction` | S | Red: fixture tests for stream assembly + `format`-constrained JSON parse, incl. malformed-output retry path. |
| P2.8 | `feat(agent): planner loop — single tool per turn` | S | Red: minimal eval runner seeded early + tool-selection eval cases across UC1–UC4 phrasings (temp 0, few-shot system prompt). |
| P2.9 | `feat(agent): quarantined summarizer + two-call extraction` | S | Red: injection eval (adversarial text planted in a note) + unit test enforcing the summarizer has no tool access and the planner never sees raw record text. |
| P2.10 | `feat(agent): SSE /chat with multi-turn state` | S | Red: endpoint tests — stream frames, conversation resume by id, bad token → 401. |
| P2.11 | `feat(module): scaffold oe-module-clinical-copilot` | H | Red: PHPUnit bootstrap-registration test + Panther scenario: module enables via Module Manager without error. |
| P2.12 | `feat(module): UI injection via render events` | S | Red: Panther scenario asserting Co-Pilot card on patient dashboard + header button. Session context `js_escape()`d. |
| P2.13 | `feat(module): ajax token broker` | S | Red: PHPUnit CSRF-rejection test + Panther scenario acquiring token in the panel. |
| P2.14 | `feat(ui): chat panel — streaming, mobile-first` | S | Red: Jest for SSE parse/render logic + Panther at 360px and desktop. |
| P2.15 | `feat(ui): /copilot standalone PWA route` | S | Red: unit test asserting the service worker never caches API routes (no PHI in Cache Storage) + Panther manifest checks. |
| P2.16 | `feat(agent): patient-context binding` | S | Red: tool-layer tests — any tool call whose pid ≠ the conversation's anchored pid is refused; plus authorization-probe eval case. Closes the patient-granularity gap (§4.2). |
| P2.17 | `feat(module+agent): chart-access audit trail` | S | Red: PHPUnit — module writes `EventAuditLogger->newEvent()` on patient-chat open; pytest — trace store records user + patient + correlation id per turn. |
| P2.18 | `test(agent): ACL scoping end-to-end` | S | Red: integration cases — (a) nurse-role user asks for out-of-ACL data → tool-layer 403 → refusal, zero leaked PHI; (b) physician asks about a patient other than the open chart → binding refusal, attempt visible in the audit trail. |

**User scenarios (each must pass on desktop AND phone before phase close):** UC1 pre-visit brief; UC2 medication list; UC3 A1c trend; UC4 conversational drill-down; nurse-refusal; cross-patient probe refused with the attempt visible in OpenEMR's audit log.

### Phase 3 — Verification layer (~2 days; the flagship)

Almost entirely deterministic Python — strict TDD at its cheapest and most valuable. The citation checker targets ~100% branch coverage: this module *is* the trust story.

| # | Task | Tier | Red-first artifact → done-when |
|---|---|---|---|
| P3.1 | `feat(verify): source_refs response contract` | S | Red: schema tests — every factual claim carries tool-call id + record uuid + field; claim-without-ref rejected. |
| P3.2 | `feat(verify): deterministic citation checker` | S | Red: exhaustive pytest matrix — valid citation, wrong value, missing ref, ref to nonexistent tool call, type-coercion edge cases. |
| P3.3 | `feat(verify): claim stripping + notices` | S | Red: transformation tests — failed citations stripped and replaced with "not found in record" notices, never silently passed. |
| P3.4 | `feat(verify): allergy cross-check` | S | Red: match/no-match/case-variant/compound-name tests. |
| P3.5 | `feat(data): drug-interaction SQLite + ingest script` | S | Red: ingest tests — row counts, severity levels, known interaction pairs present. Checksummed artifact. |
| P3.6 | `feat(agent): check_drug_interactions tool` | S | Red: known-interaction, no-interaction, unknown-drug tests. Offline only. |
| P3.7 | `feat(verify): verdict computation + trace logging` | S | Red: decision-table tests for `verified / partially_verified / blocked`. |
| P3.8 | `feat(ui): citation chips, verdict badge, warning banner` | S | Red: Jest logic + Panther at 360px — tap chip opens underlying record; banner on conflict. |

**User scenarios:** (1) seeded uncited-claim response → claim stripped, notice visible; (2) recorded-allergy conflict → warning banner; (3) UC2 end-to-end with tappable citations, desktop and phone.

### Phase 4 — Observability + evals + feedback loop (~2–3 days)

| # | Task | Tier | Red-first artifact → done-when |
|---|---|---|---|
| P4.1 | `feat(obs): correlation-id middleware` | S | Red: propagation tests — id appears on every log line, tool call, LLM call, verification event. |
| P4.2 | `feat(obs): trace store — SQLite spans` | S | Red: schema + writer tests for request/tool/LLM/verification/feedback spans. |
| P4.3 | `feat(agent): /feedback endpoint` | S | Red: endpoint tests — 👍/👎 + comment persisted, linked to correlation id. |
| P4.4 | `feat(ui): feedback buttons in chat` | S | Red: Panther scenario — a 👎 lands in the trace store. |
| P4.5 | `feat(obs): dashboard page` | S | Red: aggregation-query tests + Panther at phone viewport. |
| P4.6 | `feat(obs): alert threshold banners` | S | Red: threshold-logic tests at boundary values — p95 latency, error rate, tool-failure rate, verification-fail rate. |
| P4.7 | `feat(evals): harness — pytest + YAML runner` | S | Red: runner tested against fixture cases (pass, fail, malformed YAML). Includes the **record/replay layer** (TEST_PLAN §9). Absorbs the minimal runner from P2.8. |
| P4.8 | `feat(evals): full case set — 8 categories, ≥25 cases` | S | Eval-first by definition; each case documents the failure mode it guards. |
| P4.9 | `feat(obs): review queue + promote-to-eval` | S | Red: generator tests (trace → valid YAML regression case) + Panther scenario for the promote click. |
| P4.10 | `feat(obs): eval pass-rate-over-time chart` | S | Red: aggregation tests over recorded eval runs. |

**User scenarios:** (1) any correlation id → reconstruct the complete trace from logs alone; (2) eval run visible live on the dashboard; (3) a 👎 becomes a runnable regression case in under 60 seconds — from the phone.

### Phase 5 — Hardening + polish (~2 days)

| # | Task | Tier | Detail / done-when |
|---|---|---|---|
| P5.1 | `test(perf): capacity run` | S | Locust/k6 at 5 and 10 concurrent chats; CPU/RAM/VRAM recorded; §7 expectations table becomes *measured*. Load script committed + reproducible. |
| P5.2 | `ci: expand copilot-ci.yml` | S | Eval **replay** + case-schema validation, module isolated tests, verification-layer coverage gate; README badges. Model inference never runs in CI (TEST_PLAN §9). |
| P5.3 | `docs: README rewrite` | D | Demo GIF up top, architecture diagram, 3-command quickstart, local-model pros/cons + hardware expectations, credits to the base repo. |
| P5.4 | `docs: ARCHITECTURE.md final + TCO` | D | Verification design, path to production, future mobile path, local-node vs cloud-API cost tiers. |
| P5.5 | `docs: AUDIT.md final` | D | Verified findings with remediations; cross-checked against upstream disclosures before publishing detail. |
| P5.6 | Fresh-clone quickstart validation | *(user)* | Clean machine, README instructions only, no undocumented steps. |

**User scenarios:** fresh clone → running stack via README alone; demo GIF records UC2 end-to-end.

## 9. Deliverables

- [ ] `README.md` — GIF, diagram, quickstart, expectations table, project-board link, credits
- [ ] `docs/IMPLEMENTATION_PLAN.md` — this document (frozen after board import)
- [ ] `docs/TEST_PLAN.md` — testing strategy (living)
- [ ] `docs/DEVELOPERS_GUIDE.md` — orientation hub (living)
- [ ] `AUDIT.md` — security-led audit
- [ ] `USERS.md` — persona + use cases
- [ ] `ARCHITECTURE.md` — 500-word summary, diagram, trust boundaries, verification design, TCO, path to production
- [ ] `interface/modules/custom_modules/oe-module-clinical-copilot/` — the module
- [ ] `services/copilot-agent/` — FastAPI service (+ Dockerfile)
- [ ] `evals/` — YAML cases + harness + results
- [ ] `docker/development-easy/docker-compose.copilot.yml` — agent, ollama, config
- [ ] `.github/workflows/copilot-ci.yml` — CI (pytest from Phase 0; evals replay + coverage gate from Phase 5)
- [ ] Demo GIF/video (60–90s) in README

Planning/working notes are kept out of the tree (untracked); the board and these documents are the public record.

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| 4B model unreliable at tool calling | Single-tool turns, temp 0, few-shot examples, grammar-constrained extraction; verification layer as the safety net; evals measure it honestly |
| OAuth authorization_code flow fights us early | Password-grant dev shortcut (documented dev-only), swap in authz-code before Phase 5 |
| Windows bind-mount performance | Stack already runs via Docker Desktop/WSL2; move repo into WSL2 filesystem if it bites |
| SSE streaming through proxies | Tailscale passes SSE fine |
| PWA install prompt inside iframe | Known limitation; standalone `/copilot` route is the install surface |
| Scope creep | Phase verify-gates; every capability must map to UC1–UC5; anything else goes to ARCHITECTURE.md future work |
| Strict TDD on module/UI glue is mock-heavy and slow | Cost (+20–30% schedule) accepted consciously and recorded; every mock-based module test paired with a Panther/Selenium scenario |
| Subagent pipeline overhead (spawn, review, gates per PR) | Tasks sized ~half-day so pipeline cost amortizes; Haiku only on boilerplate; gates run once per PR on the full diff |
