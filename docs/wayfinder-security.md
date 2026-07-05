# Wayfinder Security Notes

**Audience:** operators and integrators running `wayfinder`, `wayfinder-exec`, and
the LLM-backed prose front-ends.

This document covers authentication expectations, credential storage, policy
configuration, and the trust boundary around the event log. The
[CLI user guide](wayfinder-cli-user-guide.md) summarizes the five rules every
CLI user should internalize; this file is the longer treatment those sections
defer to.

---

## 1. Trust boundary: the filesystem

The Wayfinder Interaction Protocol v0.1 assumes the **goal store directory is
trusted**. Anyone who can write `.wayfinder/` (or a custom `--store` root) can:

- append events claiming any `actor` identity,
- break or replace the hash chain (detected on `verify`, not prevented), and
- read artifacts written by executors.

**Mitigations:**

- Run stores under directories with restrictive permissions (`0700` for the
  store root, `0600` for sensitive files).
- Treat remote/network-mounted stores as shared trust zones — same as handing
  someone write access to your shell history.
- Run `wayfinder verify --goal-id GOAL` after abnormal shutdown, before
  archiving a goal, or whenever integrity is in doubt.

The hash chain provides **tamper evidence**, not authentication.

---

## 2. Actor authority and authentication

`actor` objects in `goal_create` and `update` documents carry `authority` and
optional `authenticated`. Sensitive transitions — `goal_cancel`, accepting
`done`, policy overrides, and some approval paths — require an **authenticated
owner** or **policy_admin**.

How `authenticated: true` is established is **local policy**. This
implementation does not ship a network identity provider. By default:

- CLI subprocesses accept whatever the caller puts on stdin.
- Executors identify themselves with `--executor-id` and
  `actor.type: "executor"`.
- Prose front-ends (`wayfinder-tell`, `wayfinder-do`, …) submit updates as
  configured human actors.

Deployments that need real authentication should gate access to the store and
wrap the CLI behind an environment that sets `actor` from verified credentials
(for example, SSO-backed automation or signed update envelopes in a future
protocol revision).

---

## 3. Secrets and the event log

**Rule:** secrets never belong in the event log.

Concretely:

| Surface | Requirement |
|---|---|
| `action.shell.env.set` | Plaintext secret values are rejected at execution time. Use `secret_ref` and resolve at spawn. |
| `stdin.text` | Must not contain credentials; prefer `secret_ref` or local files outside the log. |
| `action_result.output` | Redact before hashing and submission. Spilled secrets require a `redaction` update. |
| Artifacts | Apply local redaction patterns before `artifact_id` references land in events. |

### Local secret store

Resolved references read from `~/.config/wayfinder/secrets.toml` (mode
`0600`), or `WAYFINDER_SECRETS` when set. Keys are flat names or `section/key`
paths:

```toml
github_token = "ghp_..."
[deploy]
api_key = "..."
```

`wayfinder-exec-pty` resolves `send_secret_ref` through this store at execution
time only; the resolved value is never written to the log.

`./setup.sh` seeds an empty `secrets.toml` when missing. Populate it during
setup or by hand — never commit it.

---

## 4. LLM credentials

LLM endpoint settings load from environment variables (highest precedence) or
`~/.config/wayfinder/config.toml`:

| Variable | Config key | Purpose |
|---|---|---|
| `WAYFINDER_LLM_BASE_URL` | `[llm] base_url` | OpenAI-compatible API base |
| `WAYFINDER_LLM_API_KEY` | `[llm] api_key` | Bearer token |
| `WAYFINDER_LLM_MODEL` | `[llm] model` | Model id |

Works with OpenRouter, OpenAI, Ollama (`http://localhost:11434/v1`), LM Studio,
and other OpenAI-compatible servers.

**Scripted brain workflows** (CI, examples with `--scripted`, conformance tests)
do not need an API key. `wayfinder doctor` reports LLM readiness honestly.

Store `config.toml` as mode `0600`. Never pass API keys on the command line
where shell history can capture them.

---

## 5. Executor policy

`wayfinder-exec` enforces **mechanical** policy only — not LLM judgment. Default
policy (also baked into code when no file is present) allows auto-execution of
`low`-risk actions in classes `read_local`, `execute_local`, and
`write_workspace`. It denies by default:

- `delete`, host writes, network classes, secrets access, privileged/cost/
  irreversible classes,
- `requires_shell: true`,
- inherited environments without explicit approval.

Override by writing `~/.config/wayfinder/policy.yaml`. The executor loads this
path unless `--policy` is set. Policy changes affect **future** executions;
they are not retroactive.

Approvals submitted through `wayfinder update` are **scoped to one
recommendation/action** and die with that recommendation. Standing permissions
belong in policy configuration — a deliberate, auditable act.

---

## 6. Review `argv`, not prose

Structured fields are law; prose is advisory. Executors spawn `action.shell.argv`
verbatim in `cwd`. Fields like `summary`, `title`, and `command_for_display` can
misdescribe the command. Approval UIs and humans must read `argv` and `cwd`
before accepting or approving.

Wrapped tools (`wayfinder-wrap`) and LLM brains still emit schema-valid
recommendations; policy and the executor gate what actually runs.

---

## 7. Network and destructive actions

Network and destructive capabilities require honest `risk` metadata on
recommendations. Default policy blocks auto-execution of elevated risk levels and
denied classes. When automation stops at a `question`, `blocked`, `unsafe`, or
approval gate, that is the system working — not a failure of the protocol
exchange.

---

## 8. Extensions beyond strict v0.1

Some optional machines advertise capabilities outside strict v0.1 conformance:

- **`wayfinder-exec-pty`** — drives a PTY when an action advertises
  `x_expect_dialogue` under an extension namespace. Strict v0.1 requires
  executors to reject `pty: true`; this tool is an opt-in, capability-advertised
  extension. Enable only with explicit policy consent.
- **Service-backed machines** (`wayfinder-bridge gh`, `wayfinder-web` with
  Browserbase, `wayfinder-exec-temporal`) introduce third-party trust zones.
  Scope tokens minimally; use dedicated scratch resources in CI.

`wayfinder doctor` reports which optional integrations are configured.

---

## 9. Operational checklist

1. Restrict store directory permissions.
2. Keep `secrets.toml` and `config.toml` at mode `0600`.
3. Review `argv` before every accept/approve.
4. Redact artifacts before they are hashed into the log.
5. Run `wayfinder verify` before archiving goals.
6. Use `--scripted` / recorded fixtures in CI; use live LLM endpoints only where
   needed and with keys outside the repository.
