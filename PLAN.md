# Wayfinder Implementation Plan

**Objective:** implement every tool needed to run the examples in
[wayfinder-cli-user-guide.md](wayfinder-cli-user-guide.md), conforming to
[wayfinder-interaction-protocol-v0.1.md](wayfinder-interaction-protocol-v0.1.md) (WIP v0.1).
When this plan is complete, a user can clone the repo, run `./setup.sh`, and run any
example in the guide.

**Decisions already made:**

- **Language:** Python 3.11+ (tested on 3.11–3.13), packaged with `uv`, single
  `pyproject.toml`, all tools as console-script entry points.
- **Scope:** everything in the guide — the core seven tools (§2–§8, §10–§12),
  plus all ten §9 machines.
- **Wayfinder brain:** LLM-backed by default, with a deterministic *scripted*
  brain shipped alongside so tests, CI, and conformance runs never need an API
  key or network.
- **LLM provider:** any OpenAI/OpenRouter-compatible endpoint, selected by URL.
  Configuration is `WAYFINDER_LLM_BASE_URL`, `WAYFINDER_LLM_API_KEY`,
  `WAYFINDER_LLM_MODEL` (or the equivalent keys in `~/.config/wayfinder/config.toml`).
  Works with OpenRouter, OpenAI, and local servers (Ollama at
  `http://localhost:11434/v1`, LM Studio, llama.cpp).

---

## Phase 0 — Publish the spec to GitHub Pages

*This ships first, before any code.*

1. Add an MkDocs (Material theme) site under `docs/`:
   - Landing page from `README.md`.
   - The spec and the user guide rendered as site pages (kept as the canonical
     Markdown files at repo root; the site build copies/includes them so there
     is exactly one source of truth).
   - A stub "Reports" section (filled in by Phase 1).
2. Workflow `.github/workflows/pages.yml`:
   - Trigger: push to `main` (plus `workflow_dispatch`).
   - Build the MkDocs site, upload with `actions/upload-pages-artifact`,
     deploy with `actions/deploy-pages`.
3. Repo settings: enable Pages with "GitHub Actions" as the source.

**Acceptance:** the spec and guide are readable at
`https://curtcox.github.io/wayfinder/` and update on every push to `main`.

## Phase 1 — Quality infrastructure (local + CI)

Everything runs identically locally and on CI via one entry point: `make <target>`
(plus `pre-commit` for the fast subset).

| Concern | Tool | Local target |
|---|---|---|
| Lint + format | ruff (check + format) | `make lint` |
| Static types | mypy `--strict` | `make typecheck` |
| Security static analysis | bandit | `make security` |
| Dependency vulnerabilities | pip-audit | `make security` |
| Complexity gates | radon + xenon (fail above B) | `make complexity` |
| Docs hygiene | markdownlint + lychee link check | `make docs-lint` |
| Unit/integration tests | pytest + pytest-xdist | `make test` |
| Property-based tests | hypothesis | (part of `make test`) |
| Coverage | pytest-cov, gate ≥ 90% on core, ≥ 80% overall | `make coverage` |
| Everything | | `make check` |

CI workflows:

- **`ci.yml`** — on every PR and push: `make check` across a matrix
  (ubuntu + macos × Python 3.11/3.12/3.13). Produces JUnit XML, coverage XML/HTML,
  and tool reports as artifacts.
- **`main.yml`** — on push to `main`: runs the full suite, then builds the Pages
  site **including a Reports section** and deploys (this supersedes the Phase 0
  deploy job; `pages.yml` folds into it). Reports published on Pages:
  - pytest HTML report (full results, including skipped/xfailed with reasons)
  - coverage HTML report + badge JSON
  - conformance matrix: one row per Appendix B test vector (§15.1–§15.38),
    pass/fail, linked to the test source
  - ruff/mypy/bandit/pip-audit summaries
  - a generated `reports/index.html` linking everything, stamped with commit SHA and date

**Acceptance:** a red `main` build is impossible to miss; every push to `main`
refreshes `https://curtcox.github.io/wayfinder/reports/` with complete results.

## Phase 2 — Protocol core library (`src/wayfinder/core/`)

The foundation everything else uses. No CLI yet; pure library + exhaustive tests.

- **Canonical JSON:** RFC 8785 via the `rfc8785` package; helpers for
  "canonically byte-identical" comparison (strip `request_id`, §2).
- **Schemas:** JSON Schema (draft 2020-12) files under `schemas/` for every
  `wip.*/0.1` object, enforcing Appendix A rules (`additionalProperties: false`,
  closed enums, conditional requirements by `recommendation_type`/`update_type`/
  event type, `run_id` must be null, sha256 format, RFC 3339). Published on the
  Pages site. Runtime validation via `jsonschema`.
- **Event log:** append-only JSONL with hash chain (§6.1, §6.6), seq rules,
  verbatim byte-for-byte reads, corruption detection (`corrupt_event_log` on
  gap/duplicate/mismatch/partial line), fsync discipline.
- **Append lock:** the pinned `O_CREAT|O_EXCL` lock-file primitive with JSON
  body, expiry-based breaking, unparseable-body refusal (§6.5).
- **Artifacts:** content-addressed store, temp-write→fsync→verify→rename
  protocol, truncation, path containment (symlink-resolved), redaction
  replacement semantics (§2.2, §6.8, §11).
- **Reducer:** deterministic status replay (§7.5), never consults the clock;
  snapshot write/validate/replay (§6.7).
- **Idempotency store:** `create_id`/`update_id` → canonical-hash + result,
  replay semantics returning original events + *current* status (§1.4).
- **Update→event mapping:** the complete §6.3 table, atomic multi-event
  appends, terminal-action dedupe, authority checks (§5.1), freshness/lease/claim
  evaluation (§4.2 conditions 1–7, terminal-result rule).

Tests here include hypothesis property tests (replay determinism: any event
sequence reduces identically twice; hash chain round-trips; canonicalization
stability) and multiprocess lock-contention tests.

**Acceptance:** core coverage ≥ 90%; property tests green; concurrent writers
never corrupt a log.

## Phase 3 — `wayfinder` CLI + conformance suite

- All required commands (§1.1): `capabilities`, `goal create`, `status`,
  `next --mode=preview|issue [--supersede]`, `update`, `history`, `explain`,
  plus optional `verify` (implemented), `--request-id`, `--format`, `--store` /
  `WAYFINDER_STORE` (§6.0 — needed later by §10 deference).
- Exit-code and envelope rules (§1.2–§1.3): `wip.response/0.1` on success,
  `wip.error/0.1` on failure, stderr never carries protocol state, history
  streaming exception.
- Optional JSON-RPC stdio mode (§1.5): `initialize`, `shutdown`, all methods,
  error mapping, paging.
- **Brains** (pluggable, selected by config/flag):
  - `scripted` — a YAML/JSON playbook mapping observed state → next
    recommendation. Deterministic; used by all tests and CI example runs.
  - `llm` — Phase 5.
- **Conformance suite:** one test per Appendix B vector, §15.1–§15.38, driven
  through the real CLI as a subprocess (not library calls), so any independent
  implementation could be dropped in. Concurrency vectors (15.7, 15.27, 15.33)
  use real concurrent processes; 15.33 runs the lock protocol from an
  independent minimal script to prove cross-implementation exclusion.

**Acceptance:** all 38 conformance vectors pass; the conformance matrix appears
on the Pages report; every §2, §3, §5, §6, §11, §12 guide example works with the
scripted brain.

## Phase 4 — `wayfinder-exec` (dumb executor)

- `run` and `dry-run` subcommands, `--wayfinder` (alternate wayfinder command)
  and `--store`, `--goal-file` flags (§4, §10).
- The §11.1 loop exactly, including non-interactive exit on
  `question`/`blocked`/`unsafe`, backoff and loop-detection caps.
- Mechanical policy engine (§8.3): risk level/class allow/deny lists,
  `denied_argv0`, path containment, env-entry shape, `requires_shell`/`pty`
  rejection, approval gating. Policy loaded from
  `~/.config/wayfinder/policy.yaml` with the spec's defaults baked in.
- Execution: argv-exact spawn (no shell), env modes, stdin modes, process-group
  timeout kill, output capture with inline limits and artifact spillover,
  configurable local redaction patterns applied before hashing.
- Durability: `update_id`/`recommendation_id`/`action_id` persisted before
  spawn; interruption recovery per §11.3 (resume by replaying the same
  `update_id`, never blind re-execution).

Tests: every §11.2 MUST rule has a dedicated test; kill-the-executor-mid-action
integration tests; timeout/process-group tests; policy-denial tests
(15.9, 15.19 style).

**Acceptance:** §4 examples run end-to-end against the scripted brain; a
SIGKILLed executor resumes correctly; a second executor on the same store never
double-runs (lease claim).

## Phase 5 — LLM layer, LLM brain, and `wayfinder-wrap`

- **LLM client** (`src/wayfinder/llm/`): OpenAI-compatible chat-completions
  client (httpx), base-URL configurable, structured-output helper that
  validates every generated object against the Phase 2 schemas and retries with
  the validation error on failure. **Test double:** a local stub server + recorded
  fixture transcripts, so all LLM-path tests run offline; a small live smoke
  suite runs only when a key is configured (skipped otherwise, visibly, in the
  published report).
- **LLM brain:** prompts carry the goal, reduced status, and recent history;
  output is a schema-valid recommendation with honest risk/idempotency
  metadata. Guardrails are mechanical, not prompt-based: the brain's output is
  validated and policy still gates execution downstream.
- **`wayfinder-wrap <tool>`:** a WIP-conformant wayfinder whose brain is
  specialized on one tool. Same CLI surface as `wayfinder` (it *is* the
  `wayfinder` CLI with a wrapper brain), per §7: `wayfinder-wrap ffmpeg goal create`,
  driven by `wayfinder-exec run --wayfinder "wayfinder-wrap ffmpeg"`. Tool
  knowledge = system prompt + `--help`/man-page harvesting at wrap time.
  Network tools (curl, gh, aws) must emit honest risk metadata (§7.2) — enforced
  by schema + a risk-inference checklist test suite.

**Acceptance:** §7.1 (ffmpeg) and §7.2 (curl) examples run for real with a
configured endpoint, and offline in CI against the stub; wrapped actions carry
correct risk classes (asserted by tests using recorded fixtures).

## Phase 6 — Prose front-ends: `wayfinder-do`, `wayfinder-tell`, `wayfinder-ask`, `wayfinder-chat`

All four are thin LLM-backed actors on the *client* side of the protocol (§8):
they compose `wip.goal_create`/`wip.update` documents, call `wayfinder` and
`wayfinder-exec`, and handle bookkeeping (`update_id`s, `issued_event_seq`/hash,
timestamps).

- **`wayfinder-do`** — prose → goal_create (limits mapped into `policy`, paths
  into absolute `workspace_uri`), then drives the executor loop and narrates
  (§8.1 output format).
- **`wayfinder-tell`** — reads goal state, classifies the sentence into the
  right `update_type` (question_answer / observation ± invalidation /
  correction / override.replace with drafted metadata / approval / goal_cancel),
  submits, prints the one-line receipt (§8.2).
- **`wayfinder-ask`** — read-only synthesis over `status`/`history`/`explain`/
  `verify`, citing event seq numbers; supports `--recommendation` review mode
  (§8.3–§8.4) that always quotes `argv` verbatim; store-wide mode with no goal id.
- **`wayfinder-chat`** — interactive REPL over the same verbs (§8.5).

Tests: fixture-driven — each guide sentence from §8 has a recorded LLM fixture
asserting the exact structured document produced; the "audit reflex" invariant
(everything submitted is byte-visible in history) is asserted mechanically.

**Acceptance:** every §8 example runs live with a configured endpoint and
offline in CI; `wayfinder-tell "cancel this goal"` without authenticated owner
authority is rejected with `policy_denied` (authority cannot be manufactured).

## Phase 7 — The ten §9 machines

Grouped by dependency weight. Each ships with its own example under
`examples/`, its own tests, and a `wayfinder doctor` check.

**7a — offline, pip/package-manager only:**

1. **`wayfinder-make`** (§9.3) — brain walks the out-of-date target graph
   (`make -n -d` / `ninja -n` parsing); preview = dry run; observations dirty
   nodes. Make support required; Ninja/Bazel behind the same interface,
   implemented if present on the machine.
2. **`wayfinder-tw`** (§9.1) — Taskwarrior-backed brain: goal seeds tasks
   (via LLM parse of prose into tasks/deps), `next` ≈ `task ready limit:1`,
   `explain` prints urgency math. Requires the `task` binary (setup installs it).
3. **`wayfinder-bt`** (§9.5) — behavior-tree brain over `py_trees`; blackboard
   hydrated from the event log; condition/action/fallback nodes; standing goals
   end only by `goal_cancel`; tree files under `examples/trees/`.
4. **`wayfinder-plan`** (§9.2) — PDDL brain: LLM compiles prose → problem file
   against an operator-supplied domain; planner = `pyperplan` (pure-Python,
   pip-installable) by default, Fast Downward auto-detected if installed;
   replan on contradiction; `explain` returns the plan with preconditions.
5. **pexpect executor extension** (§9.8) — `wayfinder-exec-pty`: honors an
   `x_expect_dialogue` table under an advertised extension namespace, drives
   the pty with `pexpect`, records the redacted transcript as an artifact,
   resolves `send_secret_ref` through the local secret store.
   **Spec tension to document:** v0.1 reserves `pty` and requires executors to
   reject `pty: true`; this executor is an explicit, capability-advertised,
   policy-opt-in extension *beyond* strict v0.1 conformance, and the docs must
   say so plainly.

**7b — external services/accounts (each optional; setup + doctor gate them):**

6. **`wayfinder-exec-temporal`** (§9.6) — the §11 loop as a Temporal workflow
   (`temporalio` SDK); activities per protocol call and per spawned command;
   crash-resume proves `"replayed": true`. Dev-server based
   (`temporal server start-dev`) locally and in CI (Linux job downloads the CLI).
7. **`wayfinder-bridge gh`** (§9.4) — daemon mirroring goal events onto a GitHub
   issue (issue open/comment/label/close) and mapping allowlisted commenter
   accounts → actors for approvals/observations. `GITHUB_TOKEN` required; CI
   tests run against recorded API fixtures, plus an optional live test against a
   scratch repo when a token secret is present.
8. **Ansible profile** (§9.7) — no new binary: a `wayfinder-wrap ansible`
   knowledge pack (check-mode-as-preview, `changed=N` → `action_result.changed`
   mapping, honest `safe_to_run_if_already_done`). Requires `ansible-playbook`;
   example uses a localhost inventory so it runs without real hosts.
9. **`wayfinder-codex`** (§9.9) — an agentic coding brain: LLM agent session
   hydrated from the event log; every tool/command call the agent attempts is
   intercepted and issued as an `action` recommendation; reported
   `action_result`s are fed back as tool results; reasoning surfaces via
   `explain`. Uses the same configurable LLM endpoint.
10. **`wayfinder-web`** (§9.10) — browser-action brain: recommendations carry a
    structured step script (navigate/fill/click/await-download) executed by a
    runner invoked via `argv`. Backend: Browserbase (`BROWSERBASE_API_KEY`) via
    Playwright-over-CDP, with a **local Playwright fallback** when no key is set
    so the example and tests run anywhere; artifacts = the download + session
    recording. Credentials only as `secret_ref`.

**Acceptance:** each §9 subsection's transcript is reproducible with its
example script; offline machines (7a) are fully tested in CI; service machines
(7b) have recorded-fixture tests always on, live tests gated on secrets, and
their skip status is visible in the published report.

## Phase 8 — Deference (§10)

Mostly integration, enabled by flags built earlier:

- `wayfinder --store` / `wayfinder-exec --store --goal-file` respected everywhere.
- A delegation example: parent release goal whose recommendations run
  `wayfinder-exec run --wayfinder "wayfinder-wrap git" --store … --goal-file …`,
  sub-store independently auditable; risk-envelope declaration on the
  delegating action; inner-loop policy independence tested (a parent cannot
  launder a `network_write` through delegation — asserted by test).
- Timeout on the delegating action bounds the whole sub-run (test with a
  deliberately slow sub-goal).

**Acceptance:** §10.1's two-log audit example works verbatim; the laundering
test proves the inner executor still blocks.

## Phase 9 — Setup script, doctor, examples harness, and docs finish

- **`./setup.sh`** (macOS + Linux):
  1. Installs `uv` if missing; creates the venv; installs the package + extras.
  2. `--minimal` (core seven tools) vs `--full` (all §9 machines). Full mode
     detects brew/apt and offers to install: `jq`, `make`, `task` (Taskwarrior),
     `ansible`, `gh`, the Temporal CLI; Python-side extras (`pyperplan`,
     `py_trees`, `pexpect`, `temporalio`, `playwright` + browser download) come
     via package extras.
  3. Interactive credential setup (all optional, each explained):
     LLM base URL + API key + model (offers presets: OpenRouter, OpenAI,
     Ollama-localhost); `GITHUB_TOKEN` (bridge); `BROWSERBASE_API_KEY` (web);
     seeds the local secret store (`~/.config/wayfinder/secrets.toml`, mode 0600)
     used for `secret_ref` resolution.
  4. Writes `~/.config/wayfinder/config.toml` and finishes by running doctor.
- **`wayfinder doctor`** — checks every dependency and credential, reports
  per-example readiness ("§7.1 ffmpeg: ready · §9.4 gh bridge: missing
  GITHUB_TOKEN"), exit 0 with JSON like everything else.
- **Examples harness (`examples/`):** one runnable directory per guide section
  (`02-quickstart/`, `03-recommendation-types/`, … `10-deference/`), each with a
  `run.sh`, a self-contained fixture workspace (e.g., a tiny project with a
  deliberately failing test for §2; generated media via `ffmpeg -f lavfi` for
  §7.1), and a cleanup step. Every example runs in two modes:
  - `--scripted`: deterministic brains + LLM stub — this is what CI runs.
  - default: real LLM endpoint from config.
  A CI job executes every `--scripted` example end-to-end on every PR; the
  results table joins the Pages report.
- **Docs:** guide "tool names are illustrative placeholders" preamble gets a
  companion note pointing at this implementation; extension namespaces
  (pexpect dialogue) documented; the separate security document stub the guide
  references gets written (key management, secret store, policy configuration —
  the guide defers to it in §1, §7.2, §13).

**Acceptance — the definition of done for the whole plan:**

```bash
git clone https://github.com/curtcox/wayfinder && cd wayfinder
./setup.sh            # grants access / supplies keys as prompted
wayfinder doctor      # everything green (or explicitly, honestly optional)
examples/02-quickstart/run.sh      # …and every other examples/*/run.sh
```

- Every runnable example in the guide (§2–§8, §9.1–§9.10, §10, §11, appendix)
  executes successfully — live where it needs an LLM/service the user
  configured, and in `--scripted` mode regardless.
- `make check` is green locally and on CI.
- All 38 spec conformance vectors pass and are published.
- The spec, guide, schemas, coverage, and full test results are on GitHub Pages,
  refreshed on every push to `main`.

---

## Sequencing and risk notes

- Phases 0→1→2→3→4 are strictly ordered. Phase 5–6 depend on 3–4. Phase 7
  machines are independent of each other and can proceed in any order after 5.
  Phase 8 needs 4+5. Phase 9 hardens continuously but closes last.
- **Biggest correctness risks:** RFC 8785 canonicalization edge cases (unicode,
  number formatting) and the freshness/lease/claim rules (§4.2) — both get
  property-based tests early (Phase 2/3), because everything downstream trusts
  them.
- **Biggest scope risk:** the §9 service machines (Temporal, Browserbase,
  GitHub bridge). They are deliberately last, optional at setup time, and
  designed so their absence never blocks core examples or CI.
- **Known spec/guide tension** (track as issues when work starts):
  §9.8 uses `pty: true`, which v0.1 reserves and requires executors to reject —
  handled as an advertised, opt-in extension; and the guide references a
  security document that does not exist yet — written in Phase 9.
