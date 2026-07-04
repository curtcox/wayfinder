# Oracle Interaction Protocol v0.1 — Draft Specification

**Status:** Draft  
**Scope:** A protocol for a black-box Unix-style oracle that tracks progress toward a goal, recommends next steps, accepts updates, and can be driven by either a human or an automation tool.

This document specifies the artifacts and tool contracts needed on both sides of the oracle boundary:

1. A structured recommendation schema.
2. A structured observation/update schema.
3. A persistent event log.
4. A small status vocabulary.
5. Explicit idempotency/risk metadata.
6. Dry-run and explanation modes.
7. Human override/correction semantics.
8. A dumb executor that can later be swapped for a smarter one.

---

## Prior-art basis

This design borrows mechanisms from several established systems:

| Prior art | Relevant mechanism |
|---|---|
| [`make`](https://www.gnu.org/software/make/manual/html_node/Instead-of-Execution.html) | Dry-run/question modes such as `make -n` / `--dry-run` and `make -q`. |
| [Ansible playbooks](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_intro.html) | Idempotency, desired-state automation, check mode, task result categories such as changed/failed/unreachable. |
| [JSON-RPC 2.0](https://www.jsonrpc.org/specification) | Request/response correlation through opaque request IDs. |
| [Language Server Protocol 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/) | Initialization, capability negotiation, cancellation, partial results, tracing, client-managed lifecycle. |
| [CloudEvents](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md) | Event envelopes with context metadata and domain-specific payloads. |
| [Taskwarrior hooks](https://taskwarrior.org/docs/hooks/) | CLI task state, JSON/JSONL-style exchange, hook continuation/failure semantics. |
| [Temporal workflow execution](https://docs.temporal.io/workflow-execution) | Durable event history, workflow replay, recovery from persisted execution state. |
| [Expect](https://www.nist.gov/services-resources/software/expect) | Automating a human’s terminal dialogue with an interactive program. |
| [PDDL action structure](https://planning.wiki/ref/pddl/domain) | Preconditions and effects for actions. |
| [ReAct](https://arxiv.org/abs/2210.03629) | Interleaved action/observation loops. |

The oracle remains a black box. The protocol standardizes only the boundary between oracle, human, executor, and persistent history.

---

## 0. Core model

```text
goal + event log
      ↓
oracle next/status
      ↓
recommendation
      ↓
human or dumb executor acts
      ↓
observation/update
      ↓
append event
      ↺
```

The oracle owns recommendation logic. The executor owns execution. The event log is the durable shared memory.

### Non-negotiable invariants

1. **The oracle MUST NOT mutate the outside environment.** It may append oracle events.
2. **The executor MUST NOT infer hidden intent.** It executes only structured actions it supports.
3. **Every executable recommendation MUST have a stable `recommendation_id`, `action_id`, risk metadata, and idempotency metadata.**
4. **Every action attempt MUST be followed by an update**, even if it failed before starting.
5. **Corrections and overrides MUST be appended as new events**, not by editing history.
6. **Process exit codes signal protocol/tool failure, not goal success.** Goal/action status is in JSON.

---

# 1. Transport and CLI contract

The default transport is Unix CLI with JSON over stdin/stdout.

## 1.1 Required oracle commands

```bash
oracle capabilities --format=json
oracle goal create --format=json < goal.json
oracle status --goal-id GOAL --format=json
oracle next --goal-id GOAL --run-id RUN --mode=issue|preview --explain=none|summary|structured|debug --format=json
oracle update --goal-id GOAL --format=json < update.json
oracle history --goal-id GOAL --since-seq N --format=jsonl
oracle explain --goal-id GOAL --recommendation-id REC --format=json
```

## 1.2 Output rules

- `stdout`: machine-readable JSON or JSONL only.
- `stderr`: human diagnostics only.
- Exit `0`: command completed and emitted a syntactically valid response.
- Nonzero exit: protocol/tool failure. The oracle SHOULD still emit a JSON error object on stdout when possible.

Error object:

```json
{
  "schema": "oip.error/0.1",
  "error": {
    "code": "invalid_input | unsupported_capability | stale_recommendation | storage_conflict | internal_error | temporary_failure",
    "message": "Human-readable error.",
    "retryable": false,
    "details": {}
  }
}
```

## 1.3 Optional daemon/RPC mode

A long-running oracle MAY expose the same methods over JSON-RPC:

```json
{
  "jsonrpc": "2.0",
  "id": "req_01HZY...",
  "method": "oracle.next",
  "params": {
    "goal_id": "goal_01",
    "run_id": "run_01",
    "mode": "issue",
    "explain": "structured"
  }
}
```

Recommended methods:

```text
initialize
oracle.capabilities
goal.create
goal.status
oracle.next
oracle.update
goal.history
oracle.explain
shutdown
```

This gives LSP-like capability negotiation without making CLI users pay for a daemon. LSP’s lifecycle pattern is especially relevant because the client starts and manages the server process and initialization occurs before ordinary requests.

---

# 2. IDs, timestamps, and common types

IDs are opaque strings. Prefixes are recommended but not semantically required.

```text
goal_...   goal identity
run_...    one execution session for a goal
rec_...    oracle recommendation
act_...    executable or manual action
upd_...    submitted update
evt_...    persisted event
art_...    artifact reference
```

Common actor:

```json
{
  "type": "human | executor | oracle | system",
  "id": "curt | dumb-executor@host | oracle@host",
  "display_name": "Curt",
  "authority": "observer | operator | owner | policy_admin"
}
```

Common artifact reference:

```json
{
  "artifact_id": "art_01",
  "uri": "file:.oracle/artifacts/sha256/...",
  "media_type": "text/plain",
  "sha256": "abc123...",
  "bytes": 15322,
  "redacted": false,
  "description": "stderr from make test"
}
```

Large outputs SHOULD be stored as artifacts, not embedded in updates.

---

# 3. Structured recommendation schema

A recommendation is the oracle’s answer to “what next?”

```json
{
  "schema": "oip.recommendation/0.1",
  "protocol_version": "0.1",

  "goal_id": "goal_01HZ...",
  "run_id": "run_01HZ...",
  "recommendation_id": "rec_01HZ...",
  "issued_at": "2026-07-04T18:22:11Z",

  "oracle": {
    "name": "local-oracle",
    "version": "0.3.0",
    "instance_id": "oracle_host_abc",
    "capabilities": [
      "actions.shell",
      "explain.structured",
      "dry_run.preview",
      "history.event_log"
    ]
  },

  "basis": {
    "event_log_seq": 17,
    "event_log_head": "sha256:prevhash...",
    "state_version": "opaque-oracle-state-version",
    "assumptions": [
      "The current working tree is the intended workspace."
    ],
    "unknowns": [
      "The oracle has not verified network availability."
    ]
  },

  "recommendation_type": "action",
  "summary": "Run the project test suite.",
  "goal_status": "running",
  "confidence": 0.76,

  "action": {
    "action_id": "act_01HZ...",
    "kind": "shell",
    "title": "Run tests",
    "description": "Run the project's default test target.",
    "shell": {
      "argv": ["make", "test"],
      "command_for_display": "make test",
      "cwd": "/workspace/project",
      "env": {},
      "stdin": { "mode": "none" },
      "pty": false,
      "timeout_seconds": 600,
      "expected_exit_codes": [0]
    },
    "preconditions": [
      {
        "id": "pre_01",
        "kind": "path_exists",
        "path": "/workspace/project/Makefile",
        "on_unsatisfied": "report_blocked"
      }
    ],
    "expected_effects": [
      {
        "kind": "observation",
        "description": "Test results become known."
      }
    ],
    "success_criteria": [
      {
        "id": "succ_01",
        "kind": "exit_code",
        "operator": "in",
        "value": [0]
      }
    ]
  },

  "idempotency": {
    "level": "strong",
    "key": "idem_goal_01_make_test",
    "scope": "workspace",
    "safe_to_retry": true,
    "safe_to_run_if_already_done": true,
    "detects_noop": false,
    "partial_failure_recovery": "retry",
    "max_attempts": 2,
    "dedupe_strategy": "recommendation_id + action_id + idempotency_key"
  },

  "risk": {
    "level": "low",
    "classes": ["read_local", "execute_local"],
    "blast_radius": "workspace",
    "requires_approval": false,
    "destructive": false,
    "network": "not_required",
    "secrets": "not_required",
    "rollback": {
      "available": false,
      "reason": "No persistent mutation expected."
    }
  },

  "approval": {
    "required": false,
    "policy_reason": "Risk level low and no destructive classes."
  },

  "dry_run": {
    "oracle_preview_supported": true,
    "executor_dry_run_supported": true,
    "action_check_supported": false,
    "preview_command": {
      "argv": ["make", "-n", "test"],
      "cwd": "/workspace/project"
    }
  },

  "explanation": {
    "mode": "structured",
    "summary": "The next useful information is whether tests currently pass.",
    "evidence": [
      {
        "event_id": "evt_17",
        "description": "Previous update reported source changes completed."
      }
    ],
    "redactions": []
  },

  "expires_at": "2026-07-04T18:32:11Z"
}
```

## 3.1 Recommendation types

```text
action      executor may execute one structured action
inspect     read-only action intended to gather state
question    ask human for missing information
wait        do not execute; re-query after condition/time
blocked     oracle cannot suggest progress without external change
done        oracle believes goal is complete
no_op       nothing useful to do now
choice      multiple alternatives; dumb executor must ask human
unsafe      oracle refuses to suggest because all known next steps violate policy
```

The ReAct pattern is relevant here: actions interact with the environment and observations feed back into future reasoning/action steps.

## 3.2 Action kinds

v0.1 should keep action kinds deliberately small.

```text
shell       run local command
manual      show instruction to human; wait for update
question    ask human a typed question
wait        wait until time or condition
noop        explicit no-op, usually for done/blocked
```

### `shell` action rules

A shell action MUST provide `argv`. `command_for_display` is not authoritative.

```json
{
  "kind": "shell",
  "shell": {
    "argv": ["git", "status", "--short"],
    "command_for_display": "git status --short",
    "cwd": "/workspace/project",
    "env": {
      "CI": { "value": "1", "sensitive": false }
    },
    "stdin": { "mode": "none | inline | artifact | tty" },
    "pty": false,
    "timeout_seconds": 60,
    "expected_exit_codes": [0],
    "requires_shell": false
  }
}
```

If `requires_shell: true`, the executor MUST treat the action as higher risk because shell metacharacters and expansion are semantically relevant.

## 3.3 Preconditions and effects

This is intentionally PDDL-like but less formal. PDDL action definitions separate parameters, preconditions, and effects; preconditions describe conditions required before an action can be applied, while effects describe state changes caused by the action.

```json
{
  "preconditions": [
    {
      "id": "pre_01",
      "kind": "path_exists | command_available | env_present | fact | approval | custom",
      "path": "/workspace/project/Makefile",
      "command": "make",
      "fact": "workspace.language",
      "operator": "equals",
      "value": "c",
      "on_unsatisfied": "report_blocked | ask_human | skip | fail"
    }
  ],
  "expected_effects": [
    {
      "kind": "read | write | create | delete | network | observation | external_side_effect",
      "target": "/workspace/project",
      "description": "What should become true or known."
    }
  ]
}
```

---

# 4. Structured observation/update schema

An update is any new information sent back to the oracle.

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",

  "update_id": "upd_01HZ...",
  "goal_id": "goal_01HZ...",
  "run_id": "run_01HZ...",
  "recommendation_id": "rec_01HZ...",
  "action_id": "act_01HZ...",

  "created_at": "2026-07-04T18:23:44Z",
  "actor": {
    "type": "executor",
    "id": "dumb-executor@host",
    "authority": "operator"
  },

  "update_type": "action_result",

  "disposition": {
    "recommendation": "accepted",
    "reason": "Policy allowed low-risk local command."
  },

  "action_result": {
    "status": "failed",
    "changed": "no",
    "started_at": "2026-07-04T18:23:01Z",
    "ended_at": "2026-07-04T18:23:44Z",
    "duration_ms": 43000,

    "process": {
      "exit_code": 2,
      "signal": null,
      "timed_out": false
    },

    "criteria": [
      {
        "criterion_id": "succ_01",
        "status": "failed",
        "observed": 2,
        "message": "Expected exit code 0."
      }
    ],

    "artifacts": [
      {
        "artifact_id": "art_stdout_01",
        "uri": "file:.oracle/artifacts/sha256/stdout...",
        "media_type": "text/plain",
        "sha256": "stdoutsha...",
        "bytes": 8211,
        "redacted": false
      },
      {
        "artifact_id": "art_stderr_01",
        "uri": "file:.oracle/artifacts/sha256/stderr...",
        "media_type": "text/plain",
        "sha256": "stderrsha...",
        "bytes": 1532,
        "redacted": false
      }
    ],

    "observations": [
      {
        "kind": "diagnostic",
        "statement": "make reported missing target `test`.",
        "confidence": 0.95,
        "evidence": ["art_stderr_01"]
      }
    ],

    "error": {
      "code": "expected_exit_code_not_met",
      "message": "Command exited 2.",
      "retryable": false
    }
  }
}
```

## 4.1 Update types

```text
recommendation_disposition   accepted/rejected/skipped/overridden
action_started               action has begun
action_result                completed, failed, timed out, cancelled, blocked
observation                  fact learned without a direct action result
correction                   human or tool corrects oracle state/assumption
override                     human intentionally diverges from recommendation
question_answer              human answers oracle question
approval                     human grants/denies requested approval
heartbeat                    long-running executor progress
```

## 4.2 Observation facts

Use this when the user or executor learned something outside the recommended action.

```json
{
  "update_type": "observation",
  "observations": [
    {
      "kind": "fact",
      "subject": "workspace.package_manager",
      "predicate": "equals",
      "object": "pnpm",
      "confidence": 1.0,
      "source": "human",
      "evidence": []
    }
  ]
}
```

## 4.3 Correction

Use this when the oracle’s prior state, assumption, or recommendation was wrong.

```json
{
  "update_type": "correction",
  "correction": {
    "scope": "assumption | fact | recommendation | action | preference | policy",
    "target_id": "rec_01HZ...",
    "previous": "This repo uses npm.",
    "replacement": "This repo uses pnpm.",
    "reason": "packageManager field in package.json says pnpm.",
    "should_affect_future_recommendations": true
  }
}
```

---

# 5. Persistent event log

The event log is append-only JSONL. This follows the same broad pattern as Taskwarrior’s JSON exchange, where JSON objects are exchanged one per line, and hooks use exit status to continue or terminate processing.

Default local layout:

```text
.oracle/
  goals/
    goal_01HZ.../
      events.ndjson
      snapshots/
        00000050.json
      artifacts/
        sha256/
          ab/
            abc123...
      locks/
```

## 5.1 Event envelope

Compatible in spirit with CloudEvents: context metadata outside, domain data inside. CloudEvents describes events as records expressing an occurrence and its context, with event data plus context metadata.

```json
{
  "schema": "oip.event/0.1",
  "event_id": "evt_00000018",
  "type": "action.completed",
  "time": "2026-07-04T18:23:44Z",

  "goal_id": "goal_01HZ...",
  "run_id": "run_01HZ...",
  "seq": 18,

  "source": "executor://dumb-executor@host",
  "actor": {
    "type": "executor",
    "id": "dumb-executor@host",
    "authority": "operator"
  },

  "subject": "act_01HZ...",
  "correlation_id": "rec_01HZ...",
  "causation_id": "evt_00000017",

  "prev_event_hash": "sha256:prev...",
  "event_hash": "sha256:this...",

  "data": {}
}
```

## 5.2 Required event types

```text
goal.created
goal.updated
goal.cancelled
goal.completed

oracle.status.reported
recommendation.requested
recommendation.issued
recommendation.superseded

recommendation.accepted
recommendation.rejected
recommendation.overridden
recommendation.expired

action.started
action.completed
action.failed
action.blocked
action.cancelled
action.timed_out
action.output_recorded

observation.recorded
correction.recorded

approval.requested
approval.granted
approval.denied

question.asked
question.answered

executor.heartbeat
executor.policy_denied
```

## 5.3 Append and replay rules

- Events are immutable.
- `seq` is strictly increasing per goal.
- Writers MUST acquire a per-goal append lock.
- `prev_event_hash` and `event_hash` SHOULD be used for tamper-evident logs.
- Snapshots MAY be written for speed, but the event log remains canonical.
- Replaying events up to `seq=N` must reconstruct the same visible state.

Temporal is strong workflow prior art here: it uses durable workflow executions, event history, replay, and recovery from latest recorded state.

---

# 6. Status vocabulary

Keep statuses small and put nuance in `reason_code`.

## 6.1 Core statuses

```text
pending      exists but not started
running      actively executing or progressing
waiting      waiting on time, external event, or human
blocked      cannot proceed without external correction/resource
succeeded    completed successfully
failed       terminal unsuccessful outcome
cancelled    intentionally stopped
skipped      intentionally not attempted
superseded   replaced by newer recommendation/action
unknown      state cannot be determined
```

## 6.2 Common reason codes

```text
needs_user_input
needs_approval
missing_capability
missing_dependency
missing_credentials
policy_denied
unsafe_action
stale_recommendation
precondition_failed
timeout
nonzero_exit
success_criteria_failed
external_system_unavailable
oracle_uncertain
```

## 6.3 Result `changed`

Borrowing from Ansible-style result reporting, actions distinguish execution success from whether state changed. Ansible reports whether tasks succeeded/failed and whether each task made a change; it also separates unreachable communication attempts.

```text
changed: "yes" | "no" | "partial" | "unknown"
```

---

# 7. Idempotency and risk metadata

## 7.1 Idempotency schema

```json
{
  "idempotency": {
    "level": "strong | conditional | weak | none | unknown",
    "key": "idem_goal_action_context",
    "scope": "process | workspace | host | account | external_system | global",
    "safe_to_retry": true,
    "safe_to_run_if_already_done": true,
    "detects_noop": true,
    "dedupe_strategy": "idempotency_key | precondition_probe | postcondition_probe | artifact_hash | none",
    "precheck": {
      "available": true,
      "description": "How executor can test whether work is already done."
    },
    "postcheck": {
      "available": true,
      "description": "How executor can verify final state."
    },
    "partial_failure_recovery": "retry | reconcile | rollback | manual | impossible | unknown",
    "max_attempts": 3
  }
}
```

Recommended interpretation:

```text
strong        safe to repeat; same final state expected
conditional   safe only if listed preconditions still hold
weak          probably safe but side effects may accumulate
none          not safe to retry without approval
unknown       executor must treat as unsafe for automatic retry
```

Ansible’s desired-state/idempotency and check mode are directly relevant prior art for this section.

## 7.2 Risk schema

```json
{
  "risk": {
    "level": "none | low | medium | high | critical",
    "classes": [
      "read_local",
      "execute_local",
      "write_workspace",
      "write_host",
      "delete",
      "network_read",
      "network_write",
      "external_side_effect",
      "secrets_access",
      "privileged",
      "cost",
      "privacy",
      "irreversible"
    ],
    "blast_radius": "none | workspace | repository | host | account | organization | public | unknown",
    "requires_approval": true,
    "destructive": false,
    "network": "not_required | optional | required",
    "secrets": "not_required | may_access | required",
    "estimated_cost": {
      "amount": 0,
      "currency": "USD",
      "confidence": 0.5
    },
    "rollback": {
      "available": true,
      "kind": "automatic | manual | impossible | unknown",
      "instructions": "How to revert."
    }
  }
}
```

## 7.3 Default dumb-executor policy

```yaml
auto_execute:
  max_risk_level: low
  allowed_classes:
    - read_local
    - execute_local
    - write_workspace
  denied_classes:
    - delete
    - write_host
    - network_write
    - external_side_effect
    - secrets_access
    - privileged
    - cost
    - irreversible

retry:
  require_safe_to_retry: true
  max_attempts_cap: 3

shell:
  require_argv: true
  allow_requires_shell: false
```

---

# 8. Dry-run and explanation modes

There are two dry-run layers.

## 8.1 Oracle preview

```bash
oracle next --goal-id GOAL --run-id RUN --mode=preview --format=json
```

`preview` returns what the oracle would recommend but MUST NOT append `recommendation.issued`. The recommendation is not executable unless reissued in `issue` mode.

## 8.2 Issued recommendation

```bash
oracle next --goal-id GOAL --run-id RUN --mode=issue --format=json
```

`issue` appends `recommendation.issued` and returns an executable recommendation.

## 8.3 Executor dry-run

```bash
oracle-exec --goal-id GOAL --dry-run
```

The executor SHOULD:

1. Fetch `oracle next --mode=preview`.
2. Validate schema.
3. Evaluate local policy.
4. Evaluate cheap preconditions.
5. Print what it would do.
6. Execute nothing.
7. Optionally append `observation.recorded` only if configured to record dry-run observations.

## 8.4 Action-level dry-run

The recommendation MAY provide a specific dry-run/check command:

```json
{
  "dry_run": {
    "action_check_supported": true,
    "preview_command": {
      "argv": ["make", "-n", "test"],
      "cwd": "/workspace/project"
    }
  }
}
```

This mirrors `make -n` / `--dry-run` and Ansible `--check`, but the executor must treat preview output as advisory, not proof of success.

## 8.5 Explanation modes

```text
none        omit explanation
summary     human-readable summary
structured  assumptions, evidence, success criteria, risk rationale
debug       implementation-defined; may expose internals
```

Structured explanation schema:

```json
{
  "explanation": {
    "mode": "structured",
    "summary": "Why this is the next step.",
    "assumptions": [],
    "evidence": [],
    "alternatives_considered": [],
    "why_not_done": "Completion has not been verified.",
    "risk_rationale": "Read-only or workspace-local.",
    "redactions": []
  }
}
```

The structured fields are authoritative. The prose explanation is advisory.

---

# 9. Human override and correction semantics

Expect is the key prior art for replacing a person who drives an interactive program: it automates programs by scripting a dialogue that may have multiple paths. This spec avoids fragile prompt scraping by making the dialogue structured.

## 9.1 Human powers

A human operator may:

```text
accept recommendation
reject recommendation
replace recommendation with another action
mark recommendation unsafe
mark goal done
mark goal blocked
answer oracle question
record observation
correct oracle assumption/fact
change policy/preference
force execution subject to policy
```

## 9.2 Override update

```json
{
  "schema": "oip.update/0.1",
  "goal_id": "goal_01HZ...",
  "run_id": "run_01HZ...",
  "recommendation_id": "rec_01HZ...",
  "update_type": "override",
  "actor": {
    "type": "human",
    "id": "curt",
    "authority": "owner"
  },
  "override": {
    "decision": "reject | replace | defer | mark_done | mark_blocked | force | unsafe",
    "reason": "The recommended npm command is wrong; this repo uses pnpm.",
    "replacement_action": {
      "kind": "shell",
      "shell": {
        "argv": ["pnpm", "test"],
        "cwd": "/workspace/project",
        "timeout_seconds": 600,
        "expected_exit_codes": [0]
      }
    },
    "applies_to_future_recommendations": true
  }
}
```

## 9.3 Conflict resolution

Default precedence:

```text
policy_admin > owner > operator > executor > oracle > observer
```

Rules:

1. A human override does not erase the oracle recommendation.
2. The oracle’s next response MUST either honor the override or return `blocked` with `reason_code: unsupported_override`.
3. A dumb executor MUST NOT execute a replacement action unless it passes the same risk/idempotency policy checks as oracle-issued actions.
4. `force` only bypasses oracle recommendation logic; it MUST NOT bypass executor safety policy unless the local policy explicitly allows owner override.

---

# 10. Dumb executor spec

The dumb executor is deliberately boring. Its job is to preserve the contract.

## 10.1 Executor loop

```pseudo
capabilities = oracle.capabilities()

while true:
    status = oracle.status(goal_id)

    if status.goal_status in ["succeeded", "failed", "cancelled"]:
        exit according to local policy

    rec = oracle.next(goal_id, run_id, mode="issue", explain="structured")

    validate_schema(rec)
    reject_if_unknown_required_capability(rec)
    reject_if_stale_basis(rec)

    if rec.recommendation_type == "done":
        oracle.update(goal.completed or recommendation.accepted)
        exit 0

    if rec.recommendation_type in ["blocked", "unsafe"]:
        display_to_human(rec)
        exit blocked

    if rec.recommendation_type in ["question", "choice"]:
        display_to_human(rec)
        wait_for_or_collect_human_update()
        oracle.update(update)
        continue

    if rec.recommendation_type == "wait":
        wait_until(rec.wait.until or local max)
        continue

    if rec.recommendation_type not in ["action", "inspect"]:
        oracle.update(policy_denied)
        continue

    policy_decision = evaluate_risk_policy(rec.risk, rec.idempotency)
    if policy_decision.requires_human:
        request_approval()
        oracle.update(approval_result)
        if denied: continue

    precondition_result = check_supported_preconditions(rec.action.preconditions)
    if not precondition_result.ok:
        oracle.update(action.blocked)
        continue

    oracle.update(recommendation.accepted)
    oracle.update(action.started)

    result = execute_exactly_one_action(rec.action)

    verify_success_criteria(result, rec.action.success_criteria)
    store_artifacts(result.stdout, result.stderr)

    oracle.update(action_result)

    continue
```

## 10.2 Executor MUST rules

The executor MUST:

- Execute at most one action per recommendation.
- Store stdout/stderr as artifacts if above local size threshold.
- Never retry unless `idempotency.safe_to_retry` is true and policy allows retry.
- Never execute an action with unknown `risk.classes` unless policy explicitly allows unknown risk.
- Never execute `shell.command_for_display`.
- Prefer `argv`.
- Treat `requires_shell: true` as elevated risk.
- Report precondition failure as `blocked`, not `failed`.
- Report policy refusal as `executor.policy_denied`.
- Re-query the oracle after each update.

## 10.3 Executor MAY rules

The executor MAY:

- Run in interactive mode and ask the human for approvals.
- Run in dry-run mode and never execute.
- Support only a subset of action kinds.
- Maintain its own local state file to prevent duplicate execution.

Minimal executor state:

```json
{
  "schema": "oip.executor_state/0.1",
  "goal_id": "goal_01HZ...",
  "run_id": "run_01HZ...",
  "last_seen_event_seq": 18,
  "attempts": {
    "act_01HZ...": {
      "recommendation_id": "rec_01HZ...",
      "idempotency_key": "idem_goal_01_make_test",
      "attempt_count": 1,
      "last_status": "failed"
    }
  }
}
```

---

# 11. Capabilities schema

```json
{
  "schema": "oip.capabilities/0.1",
  "protocol_version": "0.1",
  "oracle": {
    "name": "local-oracle",
    "version": "0.3.0"
  },
  "transports": ["cli", "jsonrpc-stdio"],
  "recommendation_types": ["action", "inspect", "question", "wait", "blocked", "done", "unsafe"],
  "action_kinds": ["shell", "manual", "question", "wait", "noop"],
  "explanation_modes": ["none", "summary", "structured"],
  "dry_run_modes": ["preview", "issue"],
  "event_log": {
    "format": "jsonl",
    "hash_chain": true,
    "history_query": true
  },
  "limits": {
    "max_inline_output_bytes": 8192,
    "max_recommendation_bytes": 1048576
  }
}
```

---

# 12. Status response schema

```json
{
  "schema": "oip.status/0.1",
  "goal_id": "goal_01HZ...",
  "run_id": "run_01HZ...",
  "observed_at": "2026-07-04T18:24:00Z",

  "goal_status": "running",
  "reason_code": null,

  "progress": {
    "summary": "Source changes are complete; tests have not passed yet.",
    "percent": null,
    "completed_steps": 3,
    "known_remaining_steps": null
  },

  "last_recommendation_id": "rec_01HZ...",
  "last_event_seq": 18,
  "event_log_head": "sha256:...",
  "needs": []
}
```

---

# 13. Example end-to-end exchange

## 13.1 Executor asks for next issued recommendation

```bash
oracle next --goal-id goal_01 --run-id run_01 --mode=issue --explain=structured --format=json
```

Oracle returns `rec_01`, action `make test`.

## 13.2 Executor accepts and starts

```json
{
  "schema": "oip.update/0.1",
  "goal_id": "goal_01",
  "run_id": "run_01",
  "recommendation_id": "rec_01",
  "action_id": "act_01",
  "update_type": "recommendation_disposition",
  "disposition": {
    "recommendation": "accepted",
    "reason": "Policy permits low-risk local command."
  }
}
```

## 13.3 Executor reports failure

```json
{
  "schema": "oip.update/0.1",
  "goal_id": "goal_01",
  "run_id": "run_01",
  "recommendation_id": "rec_01",
  "action_id": "act_01",
  "update_type": "action_result",
  "action_result": {
    "status": "failed",
    "changed": "no",
    "process": {
      "exit_code": 2,
      "timed_out": false
    },
    "observations": [
      {
        "kind": "diagnostic",
        "statement": "No rule to make target `test`.",
        "confidence": 0.95
      }
    ],
    "error": {
      "code": "success_criteria_failed",
      "message": "Expected exit code 0; got 2.",
      "retryable": false
    }
  }
}
```

## 13.4 Oracle next recommendation

Oracle may now recommend `pnpm test`, ask a question, or report blocked.

---

# 14. Prior-art mapping

| Spec decision | Prior art | Borrowed lesson |
|---|---|---|
| `preview` and action-level dry-run | [`make -n`](https://www.gnu.org/software/make/manual/html_node/Instead-of-Execution.html), [Ansible check mode](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_intro.html) | Show intended action without mutating target state. |
| `changed` result | [Ansible](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_intro.html) | Separate “succeeded” from “mutated state.” |
| JSON request/response IDs | [JSON-RPC](https://www.jsonrpc.org/specification) | Correlate calls and responses with opaque IDs. |
| Initialization/capabilities | [LSP](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/) | Negotiate supported features before normal work. |
| Event envelope | [CloudEvents](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md) | Standard event context plus domain data. |
| JSONL log and hooks | [Taskwarrior hooks](https://taskwarrior.org/docs/hooks/) | One JSON object per line and hook-style continuation/failure semantics. |
| Durable replayable history | [Temporal](https://docs.temporal.io/workflow-execution) | Retries, logs, artifacts, resume, and recovery from persisted history. |
| Human replacement by script | [Expect](https://www.nist.gov/services-resources/software/expect) | Automate interactive dialogue, but avoid brittle prompt scraping. |
| Preconditions/effects | [PDDL](https://planning.wiki/ref/pddl/domain) | Make action applicability and expected effects explicit. |
| Action/observation loop | [ReAct](https://arxiv.org/abs/2210.03629) | Alternate action with observation feedback. |

---

# 15. Open questions to settle before implementation

1. **Should the canonical event log live with the oracle or with the goal workspace?** Default above assumes workspace-local `.oracle/`.
2. **Should shell actions be allowed in v0.1, or should every action be a named capability?** Shell is flexible but harder to secure.
3. **Is the oracle allowed to keep private durable state beyond the event log?** Default: yes, but the event log must be sufficient for interop/debugging.
4. **Should human approval be synchronous CLI prompting or only event-based?** Event-based is cleaner for automation; prompting is friendlier.
5. **What is the maximum acceptable automation risk without explicit human approval?** Recommendation: `low` only: local read/execute and workspace-local writes.

---

# Appendix A. Minimal JSON Schema skeletons

These are intentionally incomplete but useful for implementers as a starting point.

## A.1 Recommendation skeleton

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.org/oip/recommendation.schema.json",
  "title": "OIP Recommendation",
  "type": "object",
  "required": [
    "schema",
    "protocol_version",
    "goal_id",
    "run_id",
    "recommendation_id",
    "issued_at",
    "recommendation_type",
    "goal_status"
  ],
  "properties": {
    "schema": { "const": "oip.recommendation/0.1" },
    "protocol_version": { "const": "0.1" },
    "goal_id": { "type": "string" },
    "run_id": { "type": "string" },
    "recommendation_id": { "type": "string" },
    "issued_at": { "type": "string", "format": "date-time" },
    "recommendation_type": {
      "enum": ["action", "inspect", "question", "wait", "blocked", "done", "no_op", "choice", "unsafe"]
    },
    "goal_status": {
      "enum": ["pending", "running", "waiting", "blocked", "succeeded", "failed", "cancelled", "skipped", "superseded", "unknown"]
    },
    "summary": { "type": "string" },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "action": { "type": "object" },
    "idempotency": { "type": "object" },
    "risk": { "type": "object" },
    "explanation": { "type": "object" }
  }
}
```

## A.2 Update skeleton

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.org/oip/update.schema.json",
  "title": "OIP Update",
  "type": "object",
  "required": [
    "schema",
    "protocol_version",
    "update_id",
    "goal_id",
    "run_id",
    "created_at",
    "actor",
    "update_type"
  ],
  "properties": {
    "schema": { "const": "oip.update/0.1" },
    "protocol_version": { "const": "0.1" },
    "update_id": { "type": "string" },
    "goal_id": { "type": "string" },
    "run_id": { "type": "string" },
    "recommendation_id": { "type": "string" },
    "action_id": { "type": "string" },
    "created_at": { "type": "string", "format": "date-time" },
    "actor": { "type": "object" },
    "update_type": {
      "enum": [
        "recommendation_disposition",
        "action_started",
        "action_result",
        "observation",
        "correction",
        "override",
        "question_answer",
        "approval",
        "heartbeat"
      ]
    },
    "action_result": { "type": "object" },
    "observations": { "type": "array" },
    "correction": { "type": "object" },
    "override": { "type": "object" }
  }
}
```

## A.3 Event skeleton

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.org/oip/event.schema.json",
  "title": "OIP Event",
  "type": "object",
  "required": [
    "schema",
    "event_id",
    "type",
    "time",
    "goal_id",
    "seq",
    "source",
    "actor",
    "data"
  ],
  "properties": {
    "schema": { "const": "oip.event/0.1" },
    "event_id": { "type": "string" },
    "type": { "type": "string" },
    "time": { "type": "string", "format": "date-time" },
    "goal_id": { "type": "string" },
    "run_id": { "type": "string" },
    "seq": { "type": "integer", "minimum": 0 },
    "source": { "type": "string" },
    "actor": { "type": "object" },
    "subject": { "type": "string" },
    "correlation_id": { "type": "string" },
    "causation_id": { "type": "string" },
    "prev_event_hash": { "type": "string" },
    "event_hash": { "type": "string" },
    "data": { "type": "object" }
  }
}
```
