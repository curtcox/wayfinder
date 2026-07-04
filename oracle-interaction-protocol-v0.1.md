# Oracle Interaction Protocol v0.1 - Draft Specification

**Status:** Draft  
**Scope:** A Unix/CLI-oriented protocol for a black-box oracle that tracks progress toward a goal, recommends next actions, accepts observations, and can be driven by either a human or a deliberately dumb executor.

The oracle remains opaque. This specification standardizes only the protocol boundary: commands, schemas, event history, executor obligations, and safety defaults.

## Prior-Art Basis

| Prior art | Borrowed mechanism | v0.1 constraint |
|---|---|---|
| `make -n` / `make -q` | Preview and question modes. | Preview is advisory and non-executable. |
| Ansible | Idempotency, check mode, `changed` vs success. | Action success and mutation are separate fields. |
| JSON-RPC 2.0 | Opaque request IDs. | CLI responses also allow `request_id`. |
| LSP | Initialization and capability negotiation. | Capabilities are explicit before execution. |
| CloudEvents | Event envelope plus domain data. | Event data is schema-governed per event type. |
| Taskwarrior | JSONL exchange. | History is one canonical JSON event per line. |
| Temporal | Durable history and replay. | Visible protocol state MUST replay from the event log. |
| Expect | Automating terminal dialogue. | Dialogue is structured, not prompt-scraped. |
| PDDL | Preconditions and effects. | Preconditions are limited machine-checkable predicates. |
| ReAct | Action/observation loop. | Prose is advisory; structured fields are authoritative. |

---

# 0. Core Model

```text
goal + canonical event log
      |
      v
oracle next/status
      |
      v
recommendation
      |
      v
human or dumb executor acts
      |
      v
observation/update
      |
      v
append event(s)
      |
      v
repeat
```

## 0.1 Non-Negotiable Invariants

1. The oracle MUST NOT mutate the outside environment. It MAY append oracle events.
2. The executor MUST NOT infer hidden intent. It executes only supported structured actions.
3. Every executable recommendation MUST have a stable `recommendation_id`, `action.action_id`, `risk`, `idempotency`, `basis`, and `expires_at`.
4. Every action attempt MUST be followed by an update, even if execution failed before starting.
5. Corrections, overrides, and redactions MUST be appended as new events. History MUST NOT be edited.
6. Process exit codes signal protocol/tool failure, not goal or action success. Goal/action status is in JSON.
7. The event log is canonical for visible protocol state. Private oracle state MAY exist, but an implementation MUST NOT require it to interpret history, validate open recommendations, or reconstruct status fields defined by this spec.
8. Structured fields are authoritative. Human-readable prose fields MUST NOT change executor behavior.

## 0.2 v0.1 Core Surface

v0.1 deliberately keeps the interoperable core small.

Required recommendation types:

```text
action
question
wait
blocked
done
unsafe
```

Required action kinds:

```text
shell
noop
```

Implementations MAY advertise additional types or action kinds in `capabilities`, but executors MUST reject unknown types or unknown required fields unless local policy explicitly allows a namespaced extension.

---

# 1. Transport and CLI Contract

The default transport is Unix CLI with JSON over stdin/stdout.

## 1.1 Required Oracle Commands

```bash
oracle capabilities --format=json
oracle goal create --format=json < goal.json
oracle status --goal-id GOAL --format=json
oracle next --goal-id GOAL --run-id RUN --mode=preview|issue --explain=none|summary|structured|debug --format=json
oracle update --goal-id GOAL --format=json < update.json
oracle history --goal-id GOAL --since-seq N --format=jsonl
oracle explain --goal-id GOAL --recommendation-id REC --format=json
```

## 1.2 Output and Exit Rules

- `stdout` MUST contain machine-readable JSON for every non-history command.
- `oracle history` stdout MUST contain JSONL: one `oip.event/0.1` object per line, ordered by increasing `seq`.
- `stderr` is for human diagnostics only. Clients MUST ignore stderr for protocol state.
- Exit `0` means the command completed and emitted a syntactically valid protocol response.
- Nonzero exit means protocol/tool failure. If stdout is non-empty on nonzero exit, it MUST be an `oip.error/0.1` object.
- Goal failure, action failure, policy denial, and blocked status MUST be represented in JSON with exit `0` if the command itself completed.

Recommended CLI exit codes:

```text
0 success
1 invalid_input
2 storage_conflict
3 temporary_failure
4 unsupported_capability
5 stale_recommendation
6 internal_error
```

## 1.3 Response Envelope

Every non-history command MUST return either a command-specific result object or an error object. Implementations SHOULD include `request_id` when the caller provides one through an implementation-defined CLI flag or JSON-RPC request.

Successful command envelope:

```json
{
  "schema": "oip.response/0.1",
  "protocol_version": "0.1",
  "request_id": "req_01",
  "command": "oracle.next",
  "result": {}
}
```

Error object:

```json
{
  "schema": "oip.error/0.1",
  "protocol_version": "0.1",
  "request_id": "req_01",
  "error": {
    "code": "invalid_input",
    "message": "Human-readable error.",
    "retryable": false,
    "retry_after_seconds": null,
    "event_log_head": "sha256:...",
    "details": {}
  }
}
```

Allowed error codes:

```text
invalid_input
unsupported_capability
stale_recommendation
storage_conflict
corrupt_event_log
artifact_integrity_failed
policy_denied
temporary_failure
internal_error
```

## 1.4 Command Results

`oracle capabilities` result MUST be an `oip.capabilities/0.1` object.

`oracle goal create` input MUST be an `oip.goal_create/0.1` object. Result MUST include:

```json
{
  "goal": { "schema": "oip.goal/0.1" },
  "events": [{ "schema": "oip.event/0.1", "type": "goal.created" }],
  "status": { "schema": "oip.status/0.1" }
}
```

`oracle status` MUST NOT append events by default. Its result MUST be an `oip.status/0.1` object.

`oracle next --mode=preview` MUST NOT append events, create executable leases, or allocate an executable recommendation. The returned recommendation MUST have `"executable": false`.

`oracle next --mode=issue` MUST atomically append exactly one `recommendation.issued` event and return the recommendation embedded in that event. The returned recommendation MUST have `"executable": true`.

`oracle update` MUST be idempotent by `update_id`. Re-submitting byte-identical update content with the same `update_id` MUST return the original appended events and status. Reusing `update_id` with different content MUST fail with `invalid_input`. Result MUST include:

```json
{
  "update_id": "upd_01",
  "appended_events": [{ "schema": "oip.event/0.1" }],
  "seq_start": 4,
  "seq_end": 6,
  "event_log_head": "sha256:...",
  "status": { "schema": "oip.status/0.1" }
}
```

`oracle history --since-seq N` MUST return events with `seq > N`. Use `--since-seq 0` to read from the beginning.

`oracle explain` MUST return an explanation for a known recommendation in history. It MUST fail with `invalid_input` for unknown IDs. It MUST NOT fabricate an explanation for a recommendation that was never issued or previewed in the current command result.

## 1.5 Optional JSON-RPC Mode

A long-running oracle MAY expose equivalent methods over JSON-RPC 2.0. If it does, method parameters and results MUST be equivalent to the CLI command contracts above.

Required method names for JSON-RPC implementations:

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

JSON-RPC request `id` is the protocol `request_id`. JSON-RPC errors MUST map to `oip.error/0.1` codes in the error `data` field. Cancellation is optional; if unsupported, the server MUST advertise that in capabilities.

---

# 2. IDs, Timestamps, and Common Types

IDs are opaque strings. Prefixes are recommended but not semantically required.

```text
goal_...   goal identity
run_...    one execution session for a goal
rec_...    oracle recommendation
lease_...  executable recommendation lease
act_...    executable or manual action
upd_...    submitted update
evt_...    persisted event
art_...    artifact reference
req_...    command or RPC request
```

Timestamps MUST be RFC 3339 UTC strings. Implementations MUST preserve received timestamps but MAY add their own event `time` when appending.

## 2.1 Actor

```json
{
  "type": "human",
  "id": "curt",
  "display_name": "Curt",
  "authority": "owner",
  "authenticated": true
}
```

Required fields: `type`, `id`, `authority`.  
Allowed `type`: `human`, `executor`, `oracle`, `system`.  
Allowed `authority`: `observer`, `operator`, `owner`, `policy_admin`.

Authority is only meaningful inside the local trust domain. If the implementation cannot authenticate the actor, it MUST set `authenticated: false` or omit the field; policy MUST NOT treat unauthenticated authority as sufficient for privileged actions.

## 2.2 Artifact Reference

```json
{
  "artifact_id": "art_01",
  "uri": "file:.oracle/goals/goal_01/artifacts/sha256/ab/abc123...",
  "media_type": "text/plain",
  "sha256": "abc123...",
  "bytes": 15322,
  "redacted": false,
  "redaction": null,
  "description": "stderr from make test"
}
```

Artifact `uri` values using `file:` MUST be relative to the goal workspace unless absolute paths are explicitly allowed by policy. Artifacts MUST be content-addressed by post-redaction bytes. `bytes` is the byte count of stored bytes. Executors MUST verify the digest before submitting an artifact reference.

---

# 3. Goal Schema

`oracle goal create` input:

```json
{
  "schema": "oip.goal_create/0.1",
  "protocol_version": "0.1",
  "created_at": "2026-07-04T18:00:00Z",
  "actor": {
    "type": "human",
    "id": "curt",
    "authority": "owner"
  },
  "description": "Make the project tests pass.",
  "workspace_uri": "file:/workspace/project",
  "policy": {
    "max_auto_risk_level": "low"
  },
  "metadata": {}
}
```

Created goal:

```json
{
  "schema": "oip.goal/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "created_at": "2026-07-04T18:00:00Z",
  "actor": {
    "type": "human",
    "id": "curt",
    "authority": "owner"
  },
  "description": "Make the project tests pass.",
  "workspace_uri": "file:/workspace/project",
  "status": "pending",
  "metadata": {}
}
```

`description` and `workspace_uri` are required. `policy` and `metadata` are optional.

---

# 4. Recommendation Schema

A recommendation is the oracle's answer to "what next?"

```json
{
  "schema": "oip.recommendation/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "run_id": "run_01",
  "recommendation_id": "rec_01",
  "issued_at": "2026-07-04T18:22:11Z",
  "executable": true,
  "lease": {
    "lease_id": "lease_01",
    "lease_expires_at": "2026-07-04T18:32:11Z"
  },
  "oracle": {
    "name": "local-oracle",
    "version": "0.3.0",
    "instance_id": "oracle_host_abc"
  },
  "basis": {
    "event_log_seq": 17,
    "event_log_head": "sha256:prevhash...",
    "state_version": "opaque-oracle-state-version"
  },
  "recommendation_type": "action",
  "summary": "Run the project test suite.",
  "goal_status": "running",
  "confidence": 0.76,
  "action": {
    "action_id": "act_01",
    "kind": "shell",
    "title": "Run tests",
    "shell": {
      "argv": ["make", "test"],
      "command_for_display": "make test",
      "cwd": "file:/workspace/project",
      "env": { "mode": "inherit", "set": {} },
      "stdin": { "mode": "none" },
      "pty": false,
      "timeout_seconds": 600,
      "expected_exit_codes": [0],
      "requires_shell": false
    },
    "preconditions": [
      {
        "id": "pre_01",
        "kind": "path_exists",
        "path": "file:/workspace/project/Makefile",
        "on_unsatisfied": "report_blocked"
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
    "dedupe_strategy": "idempotency_key",
    "partial_failure_recovery": "retry",
    "max_attempts": 2
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
      "kind": "unknown",
      "reason": "No persistent mutation expected."
    }
  },
  "expires_at": "2026-07-04T18:32:11Z",
  "explanation": {
    "mode": "structured",
    "summary": "The next useful information is whether tests currently pass.",
    "evidence": [
      {
        "event_id": "evt_00000017",
        "description": "Previous update reported source changes completed."
      }
    ],
    "redactions": []
  }
}
```

For `recommendation_type: "action"`, the fields `action`, `risk`, `idempotency`, `basis`, and `expires_at` are required. For `done`, `blocked`, `unsafe`, `question`, and `wait`, `action` MUST be absent unless the type's schema explicitly permits it.

## 4.1 Recommendation Types

```text
action      executor may execute one structured action
question    ask human for missing information
wait        do not execute; re-query after time or event condition
blocked     oracle cannot suggest progress without external change
done        oracle believes the goal is complete
unsafe      oracle refuses to suggest because all known next steps violate policy
```

`done` is a recommendation type, not an action result. Goal terminal state is represented as status `succeeded`, `failed`, or `cancelled`.

## 4.2 Recommendation Leases and Staleness

An issued recommendation is executable only if all conditions are true:

1. A `recommendation.issued` event exists for its `recommendation_id`.
2. `executable` is true.
3. The current event log head matches `basis.event_log_head`, or every intervening event is explicitly marked `invalidates_open_recommendations: false`.
4. `expires_at` and `lease.lease_expires_at` are in the future.
5. No terminal action event exists for the same `recommendation_id` and `action.action_id`.
6. No newer non-parallel `recommendation.issued` event has superseded it.

If any condition fails, the executor MUST NOT execute the action and the oracle MUST reject execution updates with `stale_recommendation`.

`oracle next --mode=issue` MUST be atomic with respect to the event log head. If another writer changes the head during issuance, the command MUST retry against the new head or fail with `storage_conflict`.

## 4.3 Action Kinds

### `shell`

A shell action MUST provide `shell.argv`. `command_for_display` is advisory and MUST NOT be executed.

`shell.env.mode` MUST be one of:

```text
inherit      inherit process environment and apply `set`
replace      use only `set`
minimal      implementation-defined minimal safe environment plus `set`
```

Environment entries in `set` MUST be objects:

```json
{
  "value": "1",
  "sensitive": false
}
```

If `requires_shell: true`, the executor MUST treat the action as elevated risk and MUST NOT run it automatically under the default policy.

Timeout behavior: on timeout, the executor MUST terminate the child process or process group, record `timed_out: true`, and record signal information when available. Implementations SHOULD send a graceful termination signal before force-killing.

### `noop`

`noop` is executable only to acknowledge a state transition such as `done`, `blocked`, or `wait`. It MUST NOT mutate the outside environment.

## 4.4 Preconditions and Success Criteria

Allowed v0.1 precondition kinds:

```text
path_exists
command_available
env_present
approval
```

Allowed `on_unsatisfied` values:

```text
report_blocked
ask_human
skip
fail
```

Unsupported preconditions MUST be treated as blocked with reason `missing_capability`; they MUST NOT be ignored.

Allowed v0.1 success criteria:

```text
exit_code
artifact_exists
observation_recorded
```

If `success_criteria` is absent for a shell action, it defaults to exit code in `shell.expected_exit_codes`, or `[0]` if that field is absent.

---

# 5. Update and Observation Schema

An update is any new information submitted to the oracle.

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_01",
  "goal_id": "goal_01",
  "run_id": "run_01",
  "recommendation_id": "rec_01",
  "action_id": "act_01",
  "basis_event_seq": 18,
  "basis_event_head": "sha256:...",
  "created_at": "2026-07-04T18:23:44Z",
  "actor": {
    "type": "executor",
    "id": "dumb-executor@host",
    "authority": "operator"
  },
  "update_type": "action_result",
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
    "artifacts": [],
    "observations": [
      {
        "kind": "diagnostic",
        "statement": "make reported missing target test.",
        "confidence": 0.95,
        "evidence": []
      }
    ],
    "error": {
      "code": "success_criteria_failed",
      "message": "Command exited 2.",
      "retryable": false
    }
  }
}
```

Required update fields: `schema`, `protocol_version`, `update_id`, `goal_id`, `run_id`, `created_at`, `actor`, `update_type`.

If an update refers to a recommendation or action, it MUST include `recommendation_id`, `action_id`, `basis_event_seq`, and `basis_event_head`.

Exactly one payload matching `update_type` MUST be present, except `recommendation_disposition` MAY be included before action payloads when the update intentionally combines disposition and result.

## 5.1 Update Types

```text
recommendation_disposition
action_started
action_result
observation
correction
override
question_answer
approval
heartbeat
policy_denied
```

Minimum payload fields:

```text
recommendation_disposition.disposition       disposition value and reason
action_started.started_at                    RFC 3339 timestamp
action_result                                action result object
observation.observations                     array of observation objects
correction                                   correction object with scope, target_id, replacement, reason
override                                     override object with decision and reason
question_answer                             answer object with question_id and answer
approval                                    approval object with decision granted|denied, approver, reason
heartbeat                                   heartbeat object with status, observed_at, optional message
policy_denied                               policy_denied object with reason_code and reason
```

## 5.2 Disposition Values

```text
accepted
rejected
skipped
overridden
expired
```

## 5.3 Action Result Status

```text
completed
failed
timed_out
cancelled
blocked
skipped
```

Precondition failure MUST be reported as `blocked`, not `failed`. Policy refusal MUST be reported as `executor.policy_denied`, not command failure.

## 5.4 Observation Facts

Observation objects MUST be one of:

```text
fact
diagnostic
artifact
message
```

Fact example:

```json
{
  "kind": "fact",
  "subject": "workspace.package_manager",
  "predicate": "equals",
  "object": "pnpm",
  "confidence": 1.0,
  "source": "human",
  "evidence": []
}
```

---

# 6. Persistent Event Log

The event log is append-only JSONL and is canonical for visible protocol state.

Default local layout:

```text
.oracle/
  goals/
    goal_01/
      events.ndjson
      snapshots/
        00000050.json
      artifacts/
        sha256/
          ab/
            abc123...
      locks/
        append.lock
```

## 6.1 Event Envelope

```json
{
  "schema": "oip.event/0.1",
  "protocol_version": "0.1",
  "event_id": "evt_00000018",
  "type": "action.failed",
  "time": "2026-07-04T18:23:44Z",
  "goal_id": "goal_01",
  "run_id": "run_01",
  "seq": 18,
  "source": "executor://dumb-executor@host",
  "actor": {
    "type": "executor",
    "id": "dumb-executor@host",
    "authority": "operator"
  },
  "subject": "act_01",
  "correlation_id": "rec_01",
  "causation_id": "evt_00000017",
  "invalidates_open_recommendations": true,
  "prev_event_hash": "sha256:prev...",
  "event_hash": "sha256:this...",
  "data": {}
}
```

Required fields: `schema`, `protocol_version`, `event_id`, `type`, `time`, `goal_id`, `seq`, `source`, `actor`, `prev_event_hash`, `event_hash`, `data`.

`seq` MUST start at 1 for `goal.created` and increase by exactly 1 per goal.

## 6.2 Required Event Types

```text
goal.created
goal.updated
goal.cancelled
goal.completed

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
action.skipped
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

`oracle.status.reported` is not a v0.1 canonical event. Status reads MUST NOT pollute event history.

## 6.3 Update-to-Event Mapping

`oracle update` MUST apply this deterministic mapping:

| Update | Event(s) |
|---|---|
| `recommendation_disposition=accepted` | `recommendation.accepted` |
| `recommendation_disposition=rejected` | `recommendation.rejected` |
| `recommendation_disposition=skipped` | `recommendation.rejected` with `data.disposition="skipped"` |
| `recommendation_disposition=overridden` | `recommendation.overridden` |
| `action_started` | `action.started` |
| `action_result.status=completed` | `action.completed` |
| `action_result.status=failed` | `action.failed` |
| `action_result.status=timed_out` | `action.timed_out` |
| `action_result.status=cancelled` | `action.cancelled` |
| `action_result.status=blocked` | `action.blocked` |
| `action_result.status=skipped` | `action.skipped` |
| `observation` | `observation.recorded` |
| `correction` | `correction.recorded` |
| `approval.decision=granted` | `approval.granted` |
| `approval.decision=denied` | `approval.denied` |
| `question_answer` | `question.answered` |
| `heartbeat` | `executor.heartbeat` |
| `policy_denied` | `executor.policy_denied` |

If an update contains artifact references, the oracle MUST verify artifact integrity before appending events. It MAY append `action.output_recorded` events before the terminal action event. If verification fails, no event from that update may be appended.

## 6.4 Append, Locking, and Recovery

- Writers MUST acquire the per-goal append lock before reading the current head for append.
- The default lock path is `.oracle/goals/{goal_id}/locks/append.lock`.
- Implementations MUST use an atomic lock primitive available on the local platform. Stale lock handling MUST be conservative; if ownership cannot be proven, fail with `storage_conflict`.
- An append operation MUST write complete UTF-8 JSON lines ending in LF and MUST fsync the event file or containing directory when the platform exposes that operation.
- A partial final line or hash mismatch makes the log corrupt. Implementations MUST NOT append to a corrupt log except through an explicit repair mode outside v0.1.
- Events MUST be immutable after append.
- Snapshots MAY be written for speed, but the event log remains canonical.

## 6.5 Hash Chain and Canonicalization

`prev_event_hash` and `event_hash` are REQUIRED.

Hash algorithm:

1. Set `event_hash` to `null`.
2. Serialize the event using RFC 8785 JSON canonicalization.
3. Compute SHA-256 over the UTF-8 canonical bytes.
4. Store as `sha256:<lowercase-hex>`.

For `seq=1`, `prev_event_hash` MUST be `null`. For `seq>1`, it MUST equal the previous event's `event_hash`.

## 6.6 Snapshots and Migration

Snapshot schema:

```json
{
  "schema": "oip.snapshot/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "seq": 50,
  "event_log_head": "sha256:...",
  "created_at": "2026-07-04T18:30:00Z",
  "state": {}
}
```

A snapshot is valid only if its `event_log_head` matches the event at `seq`. Replaying from a snapshot plus later events MUST reconstruct the same visible status as replaying from event 1.

Events include their own `protocol_version`. Future migrations MUST be represented as appended events or out-of-band tooling that preserves the original log.

---

# 7. Status Vocabulary

## 7.1 Core Statuses

```text
pending
running
waiting
blocked
succeeded
failed
cancelled
skipped
superseded
unknown
```

## 7.2 Common Reason Codes

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
corrupt_event_log
artifact_integrity_failed
unsupported_override
```

## 7.3 Result `changed`

```text
yes
no
partial
unknown
```

## 7.4 Status Response

```json
{
  "schema": "oip.status/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "run_id": "run_01",
  "observed_at": "2026-07-04T18:24:00Z",
  "goal_status": "running",
  "reason_code": null,
  "progress": {
    "summary": "Source changes are complete; tests have not passed yet.",
    "percent": null,
    "completed_steps": 3,
    "known_remaining_steps": null
  },
  "last_issued_recommendation_id": "rec_01",
  "open_recommendation_id": "rec_01",
  "last_event_seq": 18,
  "event_log_head": "sha256:...",
  "needs": []
}
```

`last_issued_recommendation_id` means the latest recommendation issued into history. `open_recommendation_id` means the latest executable recommendation that has not been superseded, rejected, expired, or completed.

---

# 8. Idempotency and Risk Metadata

## 8.1 Idempotency

```json
{
  "level": "strong",
  "key": "idem_goal_action_context",
  "scope": "workspace",
  "safe_to_retry": true,
  "safe_to_run_if_already_done": true,
  "detects_noop": true,
  "dedupe_strategy": "idempotency_key",
  "precheck": {
    "available": true,
    "description": "How executor can test whether work is already done."
  },
  "postcheck": {
    "available": true,
    "description": "How executor can verify final state."
  },
  "partial_failure_recovery": "retry",
  "max_attempts": 3
}
```

Allowed `level`: `strong`, `conditional`, `weak`, `none`, `unknown`.  
Allowed `scope`: `process`, `workspace`, `host`, `account`, `external_system`, `global`.  
Allowed `dedupe_strategy`: `idempotency_key`, `precondition_probe`, `postcondition_probe`, `artifact_hash`, `none`.  
Allowed `partial_failure_recovery`: `retry`, `reconcile`, `rollback`, `manual`, `impossible`, `unknown`.

If `level` is `none` or `unknown`, the executor MUST NOT automatically retry.

## 8.2 Risk

```json
{
  "level": "low",
  "classes": ["read_local", "execute_local"],
  "blast_radius": "workspace",
  "requires_approval": false,
  "destructive": false,
  "network": "not_required",
  "secrets": "not_required",
  "estimated_cost": {
    "amount": 0,
    "currency": "USD",
    "confidence": 1.0
  },
  "rollback": {
    "available": false,
    "kind": "unknown",
    "instructions": null
  }
}
```

Allowed risk levels: `none`, `low`, `medium`, `high`, `critical`.  
Allowed classes: `read_local`, `execute_local`, `write_workspace`, `write_host`, `delete`, `network_read`, `network_write`, `external_side_effect`, `secrets_access`, `privileged`, `cost`, `privacy`, `irreversible`.  
Allowed blast radius: `none`, `workspace`, `repository`, `host`, `account`, `organization`, `public`, `unknown`.  
Allowed network values: `not_required`, `optional`, `required`.  
Allowed secrets values: `not_required`, `may_access`, `required`.

Unknown risk classes MUST be denied by default.

## 8.3 Default Dumb-Executor Policy

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
  default_env_mode: minimal
```

The executor MUST NOT trust oracle risk metadata as proof of safety. It MUST enforce local policy based on the structured action, cwd, argv, env, artifact paths, and advertised risk.

---

# 9. Dry-Run and Explanation Modes

## 9.1 Oracle Preview

`oracle next --mode=preview` returns a non-executable recommendation. It MUST NOT append events, create leases, or reserve IDs needed for execution.

The executor MAY use preview for display, validation, policy evaluation, or cheap precondition checks. It MUST re-query with `--mode=issue` before execution.

## 9.2 Issued Recommendation

`oracle next --mode=issue` appends `recommendation.issued` and returns an executable recommendation.

## 9.3 Executor Dry-Run

A dry-run executor SHOULD:

1. Fetch `oracle next --mode=preview`.
2. Validate schema and capabilities.
3. Evaluate local policy.
4. Evaluate cheap supported preconditions.
5. Print what it would do.
6. Execute nothing.
7. Append no events unless explicitly configured to record dry-run observations.

## 9.4 Explanation Modes

```text
none
summary
structured
debug
```

`debug` MUST be capability-gated and SHOULD be disabled by default because it may reveal implementation details. Explanations are advisory; structured action, risk, idempotency, and precondition fields are authoritative.

---

# 10. Human Override and Correction Semantics

## 10.1 Human Powers

A human with sufficient local authority MAY:

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

## 10.2 Override Update

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_override_01",
  "goal_id": "goal_01",
  "run_id": "run_01",
  "recommendation_id": "rec_01",
  "created_at": "2026-07-04T18:25:00Z",
  "actor": {
    "type": "human",
    "id": "curt",
    "authority": "owner",
    "authenticated": true
  },
  "update_type": "override",
  "override": {
    "decision": "replace",
    "reason": "The recommended npm command is wrong; this repo uses pnpm.",
    "replacement_action": {
      "action_id": "act_human_01",
      "kind": "shell",
      "shell": {
        "argv": ["pnpm", "test"],
        "cwd": "file:/workspace/project",
        "timeout_seconds": 600,
        "expected_exit_codes": [0],
        "requires_shell": false
      }
    },
    "risk": {
      "level": "low",
      "classes": ["read_local", "execute_local"],
      "blast_radius": "workspace",
      "requires_approval": false,
      "destructive": false,
      "network": "not_required",
      "secrets": "not_required"
    },
    "idempotency": {
      "level": "strong",
      "key": "idem_goal_01_pnpm_test",
      "scope": "workspace",
      "safe_to_retry": true,
      "safe_to_run_if_already_done": true,
      "dedupe_strategy": "idempotency_key",
      "partial_failure_recovery": "retry",
      "max_attempts": 2
    },
    "applies_to_future_recommendations": true
  }
}
```

Allowed override decisions:

```text
reject
replace
defer
mark_done
mark_blocked
force
unsafe
```

A replacement action MUST include the same risk and idempotency metadata required for oracle-issued executable actions, or the executor MUST refuse to run it until the oracle or human supplies that metadata.

## 10.3 Conflict Resolution

Default authority precedence:

```text
policy_admin > owner > operator > executor > oracle > observer
```

Rules:

1. A human override does not erase the oracle recommendation.
2. The oracle's next response MUST either honor the override or return `blocked` with `reason_code: unsupported_override`.
3. A dumb executor MUST NOT execute a replacement action unless it passes the same schema, stale-head, risk, and idempotency checks as oracle-issued actions.
4. `force` only bypasses oracle recommendation logic. It MUST NOT bypass executor safety policy unless local policy explicitly allows authenticated owner override.

---

# 11. Dumb Executor Spec

The dumb executor preserves the contract; it does not reason about hidden intent.

## 11.1 Required Loop

```pseudo
capabilities = oracle.capabilities()
status = oracle.status(goal_id)
verify_event_log_head(status.event_log_head)

while status.goal_status not in ["succeeded", "failed", "cancelled"]:
    rec = oracle.next(goal_id, run_id, mode="issue", explain="structured")

    validate_schema(rec)
    reject_if_unknown_required_capability(rec)
    reject_if_non_executable_or_stale(rec)

    if rec.recommendation_type == "done":
        oracle.update(recommendation_disposition=accepted)
        exit 0

    if rec.recommendation_type in ["blocked", "unsafe", "question", "wait"]:
        display_to_human_or_wait(rec)
        submit_required_update_if_any()
        status = oracle.status(goal_id)
        continue

    if rec.recommendation_type != "action":
        oracle.update(recommendation_disposition=skipped, reason=missing_capability)
        status = oracle.status(goal_id)
        continue

    decision = evaluate_local_policy(rec.action, rec.risk, rec.idempotency)
    if decision.denied:
        oracle.update(policy_denied)
        status = oracle.status(goal_id)
        continue
    if decision.requires_human:
        oracle.update(approval request/result)
        if denied: continue

    preconditions = check_supported_preconditions(rec.action.preconditions)
    if not preconditions.ok:
        oracle.update(action_result.status=blocked)
        status = oracle.status(goal_id)
        continue

    oracle.update(recommendation_disposition=accepted)
    oracle.update(action_started)

    result = execute_exactly_one_action(rec.action)
    artifacts = store_and_hash_outputs(result)
    oracle.update(action_result with artifacts)

    status = oracle.status(goal_id)
```

## 11.2 Executor MUST Rules

The executor MUST:

- Execute at most one action per recommendation.
- Validate the current event log head before execution.
- Treat stale recommendations as non-executable.
- Deny unknown action kinds and unknown risk classes by default.
- Deny unsupported preconditions as blocked, not ignore them.
- Never execute `shell.command_for_display`.
- Execute `shell.argv` without shell expansion unless `requires_shell: true` and policy permits it.
- Treat `requires_shell: true` as elevated risk requiring approval under the default policy.
- Never retry unless `idempotency.safe_to_retry` is true and policy allows retry.
- Track local attempts by `recommendation_id`, `action_id`, and `idempotency.key`.
- Capture command failure as an action result, not as protocol failure.
- Retry failed `oracle update` submissions using the same `update_id` after an action has executed.
- Store stdout/stderr as separate artifacts when they exceed local inline limits.
- Redact secrets before publishing artifacts when local policy requires it.
- Re-query the oracle after each successful update.

## 11.3 Interruption Rule

If interruption occurs after external action execution but before update submission, the executor MUST resume by submitting the missing `action_result` with the original `update_id` if known. If the result is unknown, it MUST submit an `observation` or `action_result.status=blocked` describing the uncertainty; it MUST NOT blindly re-execute unless retry policy and idempotency permit it.

---

# 12. Capabilities Schema

```json
{
  "schema": "oip.capabilities/0.1",
  "protocol_version": "0.1",
  "oracle": {
    "name": "local-oracle",
    "version": "0.3.0",
    "instance_id": "oracle_host_abc"
  },
  "transports": ["cli", "jsonrpc-stdio"],
  "schema_dialect": "https://json-schema.org/draft/2020-12/schema",
  "recommendation_types": ["action", "question", "wait", "blocked", "done", "unsafe"],
  "action_kinds": ["shell", "noop"],
  "precondition_kinds": ["path_exists", "command_available", "env_present", "approval"],
  "success_criteria_kinds": ["exit_code", "artifact_exists", "observation_recorded"],
  "event_types": [
    "goal.created",
    "goal.updated",
    "goal.cancelled",
    "goal.completed",
    "recommendation.issued",
    "recommendation.superseded",
    "recommendation.accepted",
    "recommendation.rejected",
    "recommendation.overridden",
    "recommendation.expired",
    "action.started",
    "action.completed",
    "action.failed",
    "action.blocked",
    "action.skipped",
    "action.cancelled",
    "action.timed_out",
    "action.output_recorded",
    "observation.recorded",
    "correction.recorded",
    "approval.requested",
    "approval.granted",
    "approval.denied",
    "question.asked",
    "question.answered",
    "executor.heartbeat",
    "executor.policy_denied"
  ],
  "explanation_modes": ["none", "summary", "structured"],
  "dry_run_modes": ["preview", "issue"],
  "event_log": {
    "format": "jsonl",
    "hash_chain": true,
    "history_query": true,
    "canonicalization": "RFC8785"
  },
  "limits": {
    "max_inline_output_bytes": 8192,
    "max_recommendation_bytes": 1048576,
    "max_artifact_bytes": 104857600
  },
  "extensions": {
    "namespaces": []
  }
}
```

Capabilities MUST enumerate every enum value the oracle may emit outside the required core.

---

# 13. Security and Safety Requirements

Shell execution:

- `argv` MUST be an array of strings.
- `requires_shell: false` means no shell metacharacter interpretation.
- `requires_shell: true` MUST require explicit approval under default policy.

Paths:

- Workspace-relative and `file:` paths MUST resolve inside the allowed workspace unless policy allows broader access.
- Executors MUST reject artifact paths that escape the artifact root through symlinks, `..`, or absolute path substitution.

Environment and secrets:

- Sensitive environment values MUST NOT be written to event logs.
- Artifact redaction MUST happen before hashing and submission.
- Actions with possible secret access MUST include `secrets_access` or `secrets: may_access|required`; executors SHOULD also detect obvious secret access from env requests and deny if metadata is missing.

Network and external side effects:

- `network_write`, `external_side_effect`, `cost`, and `privileged` are denied by default.
- Dependency installation commands SHOULD be treated as network and supply-chain relevant unless proven local.

Replay and tamper resistance:

- Executors MUST verify the event hash chain before using history to justify execution.
- A corrupt event log MUST stop automatic execution.

Prose injection:

- Executors MUST ignore `summary`, `description`, `command_for_display`, and explanation text for behavioral decisions.

---

# 14. Appendix A: Minimum JSON Schema Requirements

The normative schemas are the object definitions in this document. Implementations SHOULD publish JSON Schema files matching these requirements. At minimum, validators MUST enforce:

- Required common fields and `protocol_version`.
- Closed core enums, with namespaced extension values allowed only when advertised in capabilities.
- Conditional recommendation requirements by `recommendation_type`.
- Conditional update payload requirements by `update_type`.
- `additionalProperties: false` for core objects except explicitly named `metadata`, `details`, `extensions`, or `x_*` fields.
- RFC 3339 date-time strings.
- SHA-256 digest format `sha256:<lowercase-hex>`.
- `oneOf` schemas for action kinds and observation kinds.

## 14.1 Recommendation Validation Rules

```text
IF recommendation_type == action:
  require action, action.action_id, risk, idempotency, basis, expires_at
  require executable boolean
  if executable == true require lease

IF recommendation_type in [done, blocked, unsafe, question, wait]:
  forbid action unless that type's extension schema explicitly allows it
```

## 14.2 Update Validation Rules

```text
recommendation_disposition -> require disposition
action_started -> require recommendation_id, action_id, action_started
action_result -> require recommendation_id, action_id, action_result
observation -> require observations
correction -> require correction
override -> require override
question_answer -> require question_answer
approval -> require approval
heartbeat -> require heartbeat
policy_denied -> require policy_denied
```

## 14.3 Event Validation Rules

```text
require event_hash and prev_event_hash
require data schema appropriate for type
require seq == previous seq + 1
require prev_event_hash == previous event_hash
```

---

# 15. Appendix B: Interoperability Test Vectors

Each implementation SHOULD pass these tests before claiming v0.1 compatibility.

## 15.1 Successful Shell Action

Initial log: `goal.created seq=1`.  
Oracle response: issued `action` with `shell.argv=["true"]`, expected exit `[0]`.  
Executor behavior: accepts, starts, runs, reports completed.  
Expected events: `recommendation.issued`, `recommendation.accepted`, `action.started`, `action.completed`.  
Pass: command exit code is represented in JSON and event hash chain verifies.

## 15.2 Failed Shell Action

Initial log: `goal.created seq=1`.  
Oracle response: issued `shell.argv=["false"]`, expected exit `[0]`.  
Executor behavior: reports `action_result.status=failed`, `process.exit_code=1`.  
Expected events: `action.failed`.  
Pass: `oracle update` exits 0 and status carries failure details.

## 15.3 Unsupported Action Kind

Oracle response: issued `action.kind="http"` without advertised extension support.  
Executor behavior: does not execute.  
Expected events: `recommendation.rejected` or `executor.policy_denied` with `reason_code=missing_capability`.  
Pass: no external side effect occurs.

## 15.4 Unsupported Precondition

Oracle response: precondition `kind="custom"` without advertised support.  
Executor behavior: does not execute.  
Expected event: `action.blocked` with `reason_code=missing_capability`.  
Pass: unsupported precondition is not ignored.

## 15.5 Stale Recommendation

Initial log: recommendation basis seq 4, current log seq 5 due to correction.  
Oracle response: stale issued recommendation or stale update submission.  
Executor behavior: refuses to execute.  
Expected result: `stale_recommendation` error or blocked update.  
Pass: no `action.started` event is appended.

## 15.6 Duplicate Executor Attempt

Initial log: `action.completed` exists for `rec_1/act_1`.  
Oracle response: same recommendation is observed by executor B.  
Executor behavior: no execution; submits skipped/observation if needed.  
Expected events: no second terminal action event for the same recommendation/action.  
Pass: external action runs at most once.

## 15.7 Concurrent `next --mode=issue`

Initial log: `goal.created seq=1`.  
Two clients call `issue` concurrently.  
Expected result: one atomic issued recommendation, or one winner and one `storage_conflict`; if reuse is implemented, both receive the same open recommendation.  
Pass: no two non-parallel executable recommendations exist.

## 15.8 Human Override Replacement

Initial log: oracle recommends `npm test`.  
Update: human replaces with `pnpm test` and supplies risk/idempotency.  
Executor behavior: validates replacement and policy before execution.  
Expected events: `recommendation.overridden`, then action lifecycle events for replacement.  
Pass: original recommendation remains in history.

## 15.9 Policy-Denied Destructive Action

Oracle response: `shell.argv=["rm","-rf","build"]`, risk includes `delete`.  
Executor behavior: denies under default policy.  
Expected event: `executor.policy_denied`.  
Pass: command is not run.

## 15.10 Timeout

Oracle response: `shell.argv=["sleep","60"]`, `timeout_seconds=1`.  
Executor behavior: terminates process, records timeout.  
Expected event: `action.timed_out`, with `process.timed_out=true`.  
Pass: no child process remains under executor control.

## 15.11 Partial Artifact Write

Executor stores stdout artifact but digest verification fails.  
Executor behavior: does not submit invalid reference; retries artifact write or reports storage failure.  
Expected result: no event references invalid artifact.  
Pass: every artifact reference hash verifies.

## 15.12 Corrupted Event Log

Initial log: final JSONL line is truncated or hash mismatch occurs.  
Oracle/executor behavior: detects corruption.  
Expected result: `corrupt_event_log`; no automatic execution or append.  
Pass: implementation refuses to build on unverifiable history.

## 15.13 Replay From Snapshot

Initial data: snapshot at seq 50 with hash H and events 51-55.  
Replayer behavior: verifies snapshot base and applies later events.  
Expected result: same status/head as full replay from seq 1.  
Pass: replay is deterministic.
