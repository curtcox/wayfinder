# Wayfinder Interaction Protocol v0.1 - Draft Specification

**Status:** Draft (revision 2 — interoperability fixes)
**Scope:** A Unix/CLI-oriented protocol for a black-box wayfinder that tracks progress toward a goal, recommends next actions, accepts observations, and can be driven by either a human or a deliberately dumb executor. In the conceptual sense, a wayfinder plays the role of an oracle: it answers "what next?" without exposing how it reasons.

The wayfinder remains opaque. This specification standardizes only the protocol boundary: commands, schemas, event history, executor obligations, and safety defaults.

## Changes in Revision 2

- Runs are removed from v0.1. `run_id` is a reserved field name and MUST be `null` or absent everywhere.
- Recommendation freshness is anchored at the `recommendation.issued` event's own hash, not the pre-issuance head.
- Issuance while a recommendation is open fails with `storage_conflict` by default; supersession is an explicit `--supersede` opt-in that atomically appends `recommendation.superseded`.
- The update-to-event mapping is complete: every update type and disposition, including all `override` decisions and `expired`, maps to defined events. `goal_cancel` is added. `goal.updated` and `question.asked` are removed (reserved names).
- Goal failure and cancellation paths are defined (`mark_failed` override, `goal_cancel` update).
- Recommendation acceptance claims the lease for one executor; a second actor cannot start the same action.
- The append-lock primitive is pinned (`O_CREAT|O_EXCL` lock file with JSON body).
- Idempotent replays return original events plus **current** status, marked `replayed: true`.
- JSON-RPC shapes for `initialize`, `shutdown`, `goal.history`, and error-code mapping are defined.
- Artifact storage location, URI resolution, and the write protocol are defined; a shared filesystem is an explicit v0.1 assumption.
- Non-action recommendation payloads are nested under type-named objects (`blocked`, `done`, `unsafe`, `question`, `wait`).
- Executor-side retry is removed: retries happen only through wayfinder re-issuance; an action with any terminal event is never re-executed.
- Update fields `basis_event_seq`/`basis_event_head` are renamed `issued_event_seq`/`issued_event_hash`.
- The goal object's `status` field is renamed `goal_status`.
- Exit-code table extended; `--request-id` standardized; `wayfinder verify` added as an optional command; numerous enum, optionality, and security clarifications.

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
wayfinder next/status
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

1. The wayfinder MUST NOT mutate the outside environment. It MAY append wayfinder events, but only as the defined result of `wayfinder goal create`, `wayfinder next --mode=issue`, or `wayfinder update`. The wayfinder MUST NOT append events spontaneously.
2. The executor MUST NOT infer hidden intent. It executes only supported structured actions.
3. Every executable recommendation MUST have a stable `recommendation_id`, `action.action_id`, `risk`, `idempotency`, `basis`, and `expires_at`.
4. Every action attempt MUST be followed by an update, even if execution failed before starting.
5. Corrections, overrides, and redactions MUST be appended as new events. History MUST NOT be edited.
6. Process exit codes signal protocol/tool failure, not goal or action success. Goal/action status is in JSON.
7. The event log is canonical for visible protocol state. Private wayfinder state MAY exist, but an implementation MUST NOT require it to interpret history, validate open recommendations, or reconstruct status fields defined by this spec.
8. Structured fields are authoritative. Human-readable prose fields MUST NOT change executor behavior.
9. An external action MUST run at most once per `recommendation_id`/`action_id` pair. Retries happen only through wayfinder re-issuance of a new recommendation (§8.1).

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

Implementations MAY advertise additional types or action kinds in `capabilities`, but executors MUST reject unknown types or unknown required fields unless local policy explicitly allows a namespaced extension. Extension enum values MUST use the form `{namespace}.{name}` where `{namespace}` is advertised in `capabilities.extensions.namespaces`.

**Runs.** v0.1 has no run concept. `run_id` is a reserved field name: if present in any v0.1 object it MUST be `null`, and implementations MUST NOT assign it semantic meaning. The `run_...` ID prefix and the `--run-id` CLI flag are reserved for future versions.

---

# 1. Transport and CLI Contract

The default transport is Unix CLI with JSON over stdin/stdout. v0.1 assumes the wayfinder and executor share one local filesystem and one wayfinder store (§6.0). Remote transports are out of scope for v0.1 except the optional JSON-RPC mode (§1.5).

## 1.1 Required Wayfinder Commands

```bash
wayfinder capabilities [--format=json]
wayfinder goal create [--request-id REQ] [--format=json] < goal.json
wayfinder status --goal-id GOAL [--request-id REQ] [--format=json]
wayfinder next --goal-id GOAL --mode=preview|issue [--supersede] \
    [--explain=none|summary|structured|debug] [--request-id REQ] [--format=json]
wayfinder update --goal-id GOAL [--request-id REQ] [--format=json] < update.json
wayfinder history --goal-id GOAL --since-seq N [--limit M] [--format=jsonl]
wayfinder explain --goal-id GOAL --recommendation-id REC [--request-id REQ] [--format=json]
```

Optional command (SHOULD be implemented; advertised in `capabilities.features.verify`):

```bash
wayfinder verify --goal-id GOAL [--format=json]
```

`--format` defaults to `json` (`jsonl` for `wayfinder history`); the flag MAY be omitted. Implementations MUST support a `--request-id` flag on all non-history commands; its value is the protocol `request_id` (§1.3).

## 1.2 Output and Exit Rules

- `stdout` MUST contain machine-readable JSON for every non-history command.
- `wayfinder history` stdout MUST contain JSONL: one `wip.event/0.1` object per line, ordered by increasing `seq`.
- `stderr` is for human diagnostics only. Clients MUST ignore stderr for protocol state.
- Exit `0` means the command completed and emitted a syntactically valid protocol response.
- Nonzero exit means protocol/tool failure. For non-history commands, if stdout is non-empty on nonzero exit, it MUST be a `wip.error/0.1` object.
- **History streaming exception:** if `wayfinder history` fails after streaming has begun, it MUST exit nonzero and MAY append a final line containing a `wip.error/0.1` object. Clients MUST verify the hash chain of received lines and MUST treat any nonzero exit as "history may be incomplete."
- Goal failure, action failure, policy denial, and blocked status MUST be represented in JSON with exit `0` if the command itself completed.

Recommended CLI exit codes (informative; clients MUST branch on the JSON `error.code`, not the exit code):

```text
0 success
1 invalid_input
2 storage_conflict
3 temporary_failure
4 unsupported_capability
5 stale_recommendation
6 internal_error
7 policy_denied
8 corrupt_event_log
9 artifact_integrity_failed
```

## 1.3 Response Envelope

Every successful non-history command MUST return a `wip.response/0.1` envelope. The command-specific result object MUST appear in `result`. A conforming v0.1 CLI MUST NOT emit raw success result objects. Error responses MUST emit a `wip.error/0.1` object directly and MUST NOT wrap the error in `wip.response/0.1`.

If the caller provides a request ID via `--request-id` or a JSON-RPC request `id`, the wayfinder MUST copy it unchanged into the response or error object. If no request ID was provided, `request_id` MUST be omitted or `null`.

Successful command envelope:

```json
{
  "schema": "wip.response/0.1",
  "protocol_version": "0.1",
  "request_id": "req_01",
  "command": "wayfinder.next",
  "result": {}
}
```

Error object:

```json
{
  "schema": "wip.error/0.1",
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

`error.code`, `error.message`, and `error.retryable` are required. `retry_after_seconds` is meaningful only when `retryable: true` and MAY be `null`. `event_log_head` is OPTIONAL and meaningful only for storage-related errors. `details` is an open object.

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

### `wayfinder capabilities`

Result MUST be a `wip.capabilities/0.1` object. This command MUST work before any goal exists.

### `wayfinder goal create`

Input MUST be a `wip.goal_create/0.1` object. Goal creation MUST be idempotent by `create_id`. The wayfinder MUST persist, keyed by `create_id`, the SHA-256 of the canonical request bytes (§2) and the identity of the created goal. Re-submitting canonically byte-identical goal-create content with the same `create_id` MUST return the original goal and original `goal.created` event, the **current** status derived from the full log, and `"replayed": true`. Reusing `create_id` with different canonical content MUST fail with `invalid_input`. Result MUST include:

```json
{
  "goal": { "schema": "wip.goal/0.1" },
  "events": [{ "schema": "wip.event/0.1", "type": "goal.created" }],
  "status": { "schema": "wip.status/0.1" },
  "replayed": false
}
```

### `wayfinder status`

`wayfinder status` MUST NOT append events. Its result MUST be a `wip.status/0.1` object.

### `wayfinder next --mode=preview`

MUST NOT append events, create executable leases, or allocate an executable recommendation. The returned recommendation MUST have `"executable": false`.

### `wayfinder next --mode=issue`

MUST atomically append exactly one `recommendation.issued` event — preceded in the same atomic append by `recommendation.superseded` events when `--supersede` applies — and return the recommendation embedded in that event. If the returned recommendation has `recommendation_type:"action"`, it MUST have `"executable": true`; otherwise it MUST have `"executable": false`.

If an open executable recommendation already exists for the goal:

- Without `--supersede`, the command MUST fail with `storage_conflict` and append nothing.
- With `--supersede`, the wayfinder MUST atomically append one `recommendation.superseded` event per open executable recommendation, followed by the new `recommendation.issued` event, in one append operation (§6.5). The new recommendation's `supersedes` array MUST list exactly the recommendation IDs superseded in that operation.

`wayfinder next --mode=issue` MUST be atomic with respect to the event log head. If another writer changes the head during issuance, the command MUST retry against the new head or fail with `storage_conflict`.

If the goal is in a terminal state (`succeeded`, `failed`, `cancelled`), `wayfinder next` MUST fail with `invalid_input`.

### `wayfinder update`

MUST be idempotent by `update_id`. The wayfinder MUST persist, keyed by `update_id`, the SHA-256 of the canonical update bytes and the appended seq range. Re-submitting canonically byte-identical update content with the same `update_id` MUST return the originally appended events (`appended_events`, `seq_start`, `seq_end` unchanged), the **current** status and `event_log_head`, and `"replayed": true`; it MUST NOT append new events. Reusing `update_id` with different canonical content MUST fail with `invalid_input`.

If `--goal-id` and the update body's `goal_id` are both present and differ, the command MUST fail with `invalid_input`.

Result MUST include:

```json
{
  "update_id": "upd_01",
  "appended_events": [{ "schema": "wip.event/0.1" }],
  "seq_start": 21,
  "seq_end": 21,
  "event_log_head": "sha256:...",
  "status": { "schema": "wip.status/0.1" },
  "replayed": false
}
```

### `wayfinder history`

`wayfinder history --since-seq N` MUST return events with `seq > N`. Use `--since-seq 0` to read from the beginning. `--limit M` caps the number of returned lines; if the result is truncated by `--limit`, the client resumes with `--since-seq` set to the last received `seq`. Events MUST be emitted verbatim as stored (byte-for-byte lines); re-serialization would break hash verification by readers.

### `wayfinder explain`

MUST return a `wip.explanation/0.1` result for a known issued recommendation in history:

```json
{
  "schema": "wip.explanation/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "recommendation_id": "rec_01",
  "explanation": {
    "mode": "structured",
    "summary": "…",
    "evidence": [],
    "redactions": []
  }
}
```

It MUST fail with `invalid_input` for unknown IDs and for preview-only recommendation IDs. Preview explanations are addressable only inline in the `wayfinder next --mode=preview` response.

### `wayfinder verify` (optional)

Verifies the goal's hash chain and, when feasible, artifact digests. Result MUST be:

```json
{
  "schema": "wip.verify/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "ok": true,
  "last_event_seq": 21,
  "event_log_head": "sha256:...",
  "problems": []
}
```

`problems` entries are objects `{ "kind": "hash_mismatch"|"truncated_line"|"artifact_missing"|"artifact_digest_mismatch"|"seq_gap", "seq": 12, "detail": "…" }`. Verification problems MUST NOT cause a nonzero exit; `ok:false` with exit 0 reports them. Nonzero exit is reserved for tool failure.

## 1.5 Optional JSON-RPC Mode

A long-running wayfinder MAY expose equivalent methods over JSON-RPC 2.0. If it does, method parameters and results MUST be equivalent to the CLI command contracts above.

Required method names for JSON-RPC implementations:

```text
initialize
wayfinder.capabilities
goal.create
goal.status
wayfinder.next
wayfinder.update
goal.history
wayfinder.explain
shutdown
```

Rules:

- JSON-RPC request `id` is the protocol `request_id`.
- JSON-RPC success `result` MUST be the same command-specific result object that would appear in the CLI `wip.response/0.1.result` field. JSON-RPC MUST NOT nest a `wip.response/0.1` envelope inside `result`.
- `initialize` params: `{ "protocol_version": "0.1", "client": { "name": string, "version": string } }`. Result: the server's `wip.capabilities/0.1` object. `initialize` MUST be the first call on a connection.
- `shutdown` takes no params and returns `null`.
- `goal.history` params: `{ "goal_id": string, "since_seq": integer, "limit": integer? }`. Result:

```json
{
  "events": [{ "schema": "wip.event/0.1" }],
  "truncated": false,
  "next_since_seq": null
}
```

  `truncated: true` means more events exist; the client continues from `next_since_seq`. Page size is bounded by `capabilities.limits.max_history_events_per_page`.
- Protocol failures MUST use JSON-RPC error code `-32000` with a complete `wip.error/0.1` object in `error.data`. The standard codes `-32700`, `-32600`, `-32601`, and `-32602` retain their JSON-RPC meanings for transport-level failures.
- Batch requests and server-initiated notifications are not part of v0.1.
- Cancellation is optional; support is advertised in `capabilities.features.cancellation`.

---

# 2. IDs, Timestamps, and Common Types

IDs are opaque strings. Prefixes are recommended but not semantically required. `event_id` MUST be unique within its goal's event log; all other IDs MUST be unique within the wayfinder store.

```text
goal_...   goal identity
run_...    RESERVED in v0.1 (no run concept)
rec_...    wayfinder recommendation
lease_...  executable recommendation lease
act_...    executable or manual action
upd_...    submitted update
evt_...    persisted event
art_...    artifact reference
req_...    command or RPC request
```

Timestamps MUST be RFC 3339 UTC strings. Timestamps inside submitted payloads are preserved verbatim by the wayfinder; the event envelope `time` field is assigned by the appender at append time.

**Clock authority.** The wayfinder's clock is authoritative for evaluating `expires_at`, `lease.lease_expires_at`, and expiry dispositions at the moment an update or issuance is processed. Executors SHOULD apply a local safety margin (e.g., refuse to start actions within a few seconds of expiry) to tolerate skew.

When this specification says canonically byte-identical content, it means the object serialized with RFC 8785 JSON canonicalization after removing the transport-only field `request_id` (and no other field). Implementations MUST compare the canonical bytes, not pretty-printed input bytes.

`confidence` fields, wherever they appear, MUST be numbers in the closed range [0, 1].

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

Required fields: `type`, `id`, `authority`. Optional fields: `display_name`, `authenticated`.
Allowed `type`: `human`, `executor`, `wayfinder`, `system` (`system` denotes an automated non-executor process such as a scheduler or migration tool).
Allowed `authority`: `observer`, `operator`, `owner`, `policy_admin`.

Authority is only meaningful inside the local trust domain. `authenticated: true` MUST be derived by the wayfinder from a local authentication mechanism (e.g., OS user identity of the submitting process); it MUST NOT be trusted merely because the submitted JSON asserts it. If the implementation cannot authenticate the actor, it MUST record `authenticated: false` or omit the field; policy MUST NOT treat unauthenticated authority as sufficient for privileged actions.

## 2.2 Artifact Reference

```json
{
  "schema": "wip.artifact/0.1",
  "protocol_version": "0.1",
  "artifact_id": "art_01",
  "uri": "file:.wayfinder/goals/goal_01/artifacts/sha256/ab/abc123...",
  "media_type": "text/plain",
  "sha256": "sha256:abc123...",
  "bytes": 15322,
  "truncated": false,
  "redacted": false,
  "redaction": null,
  "description": "stderr from make test"
}
```

Required fields: `schema`, `protocol_version`, `artifact_id`, `uri`, `media_type`, `sha256`, `bytes`, `redacted`. Optional fields: `truncated` (default `false`), `redaction`, `description`.

When `redacted: true`, `redaction` MUST be an object `{ "reason": string, "redacted_by": actor? }`; otherwise `redaction` MUST be `null` or absent.

**URI resolution.** A `file:` artifact URI with a relative path (e.g., `file:.wayfinder/...`) MUST be resolved against the goal workspace root (the directory that contains the `.wayfinder` store, §6.0) and MUST resolve inside `.wayfinder/goals/{goal_id}/artifacts/`. Absolute `file:` URIs are rejected unless local policy explicitly allows them. Implementations MUST normalize artifact paths, reject `..` segments, and reject paths that escape the artifact root after resolving symlinks.

**Content addressing and write protocol.** Artifacts MUST be content-addressed by post-redaction bytes at `artifacts/sha256/{first-2-hex}/{full-hex}`. `bytes` is the byte count of stored bytes. A writer MUST write artifact content to a temporary file on the same filesystem, fsync it, verify the digest, then atomically rename it to its content address. Content-addressed writes require no lock: an existing file whose digest matches satisfies the write. An event that references an artifact MUST NOT be appended until the artifact file is durable. Executors MUST verify the digest before submitting an artifact reference, and wayfinders MUST verify it before appending an event that references the artifact.

If captured output exceeds `capabilities.limits.max_artifact_bytes`, the executor MUST truncate it (keeping at least the head; SHOULD keep head and tail), store the truncated bytes, and set `truncated: true`.

---

# 3. Goal Schema

`wayfinder goal create` input:

```json
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_01",
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
  "schema": "wip.goal/0.1",
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
  "goal_status": "pending",
  "policy": {
    "max_auto_risk_level": "low"
  },
  "metadata": {}
}
```

Required `wip.goal_create/0.1` fields: `schema`, `protocol_version`, `create_id`, `created_at`, `actor`, `description`, `workspace_uri`. Optional: `policy`, `metadata`. `create_id` is an opaque idempotency key for the goal creation request and MUST be unique within the wayfinder storage domain.

`workspace_uri` MUST be an absolute `file:` URI. The wayfinder MUST fail with `invalid_input` if the directory does not exist at creation time.

The only `policy` key defined in v0.1 is `max_auto_risk_level`, whose value MUST be a §8.2 risk level. Unknown policy keys MUST be rejected with `invalid_input` unless namespaced (`{namespace}.{key}`) and advertised. The created goal MUST echo the accepted `policy`.

Allowed `goal_status` values for a goal: `pending`, `running`, `waiting`, `blocked`, `succeeded`, `failed`, `cancelled`.

---

# 4. Recommendation Schema

A recommendation is the wayfinder's answer to "what next?"

The example below is issued as event `seq=18`; its `basis` records the pre-issuance log position (`seq=17`).

```json
{
  "schema": "wip.recommendation/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "recommendation_id": "rec_01",
  "issued_at": "2026-07-04T18:22:11Z",
  "executable": true,
  "parallel": false,
  "supersedes": [],
  "lease": {
    "lease_id": "lease_01",
    "lease_expires_at": "2026-07-04T18:32:11Z"
  },
  "wayfinder": {
    "name": "local-wayfinder",
    "version": "0.3.0",
    "instance_id": "wayfinder_host_abc"
  },
  "basis": {
    "event_log_seq": 17,
    "event_log_head": "sha256:head17...",
    "state_version": "opaque-wayfinder-state-version"
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
      "env": { "mode": "minimal", "set": {} },
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
      "instructions": null
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

For `recommendation_type: "action"`, the fields `action`, `risk`, `idempotency`, `basis`, and `expires_at` are required. For `done`, `blocked`, `unsafe`, `question`, and `wait`, `action` MUST be absent, and the payload object named after the type MUST be present (§4.1).

All recommendations MUST include `schema`, `protocol_version`, `goal_id`, `recommendation_id`, `issued_at`, `executable`, `wayfinder`, `basis`, `recommendation_type`, `summary`, `goal_status`, and `confidence`. Issued executable recommendations MUST include `lease`; `lease.lease_expires_at` MUST NOT be later than `expires_at`. Preview recommendations MUST set `executable:false`, MUST omit `lease`, and MUST NOT reserve the `recommendation_id` for later execution.

`basis.event_log_seq` and `basis.event_log_head` record the log position the wayfinder reasoned from — the head **before** the `recommendation.issued` event. They are provenance for the wayfinder's decision. They are NOT the executor's freshness anchor and NOT the values used in update `issued_event_seq`/`issued_event_hash` (§4.2, §5).

v0.1 does not support parallel executable recommendations. `parallel` MUST be `false` or absent; validators MUST reject `parallel: true`. `supersedes` MUST be an array of recommendation IDs listing exactly the recommendations superseded in the same atomic issuance (§1.4); it MUST be empty otherwise.

## 4.1 Recommendation Types

```text
action      executor may execute one structured action
question    ask human for missing information
wait        do not execute; re-query after time
blocked     wayfinder cannot suggest progress without external change
done        wayfinder believes the goal is complete
unsafe      wayfinder refuses to suggest because all known next steps violate policy
```

Each non-action type carries its payload in an object named after the type:

```text
question    question: { question_id, prompt }
wait        wait: { until_time }          (until_event is RESERVED in v0.1)
blocked     blocked: { reason_code, reason }
done        done: { reason }
unsafe      unsafe: { reason_code, reason }
```

`reason_code` values come from §7.2. `wait.until_time` is a required RFC 3339 timestamp; `wait.until_event` is a reserved field name with no v0.1 semantics — recommendations using it MUST be rejected by validators.

`done` is a recommendation type, not an action result. Goal terminal state is represented as status `succeeded`, `failed`, or `cancelled`.

## 4.2 Recommendation Leases, Claims, and Staleness

An issued recommendation is executable by a prospective executor only if all conditions are true:

1. A `recommendation.issued` event exists for its `recommendation_id`. Let `issue_hash` be that event's `event_hash`.
2. `executable` is true.
3. **Freshness.** The current event log head equals `issue_hash`, or every event appended after the `recommendation.issued` event has an *effective* `invalidates_open_recommendations` value of `false`. The effective value is the explicit field value when present, otherwise the default from §6.3. (The `recommendation.issued` event itself is not "after" itself; a recommendation is always fresh immediately after issuance.)
4. `expires_at` and `lease.lease_expires_at` are in the future (§2 clock authority).
5. No terminal action event exists for the same `recommendation_id` and `action.action_id`.
6. No `recommendation.superseded` event targets it.
7. **Claim.** No `recommendation.accepted` or `action.started` event exists for the same `recommendation_id` whose `actor.id` differs from the prospective executor's `actor.id`. The first accepting actor claims the lease.

If any condition fails, the executor MUST NOT execute the action, and the wayfinder MUST reject execution-initiating updates (`recommendation_disposition=accepted`, `action_started`) with `stale_recommendation` — except as provided by the terminal-result rule below. The wayfinder MUST reject an `action_started` or `accepted` update for a recommendation already claimed by a different `actor.id` with `storage_conflict`.

**Lifecycle events do not self-invalidate.** `recommendation.accepted`, `action.started`, and `action.output_recorded` events MUST be appended with explicit `invalidates_open_recommendations: false`.

**Terminal-result acceptance rule.** Once an `action.started` event exists for a `recommendation_id`/`action_id`, the wayfinder MUST accept a well-formed terminal `action_result` update from the same actor for that pair — regardless of any events appended after `action.started` — unless a terminal action event for the pair already exists. An action that has externally run must always be able to record its outcome.

## 4.3 Action Kinds

All actions MUST include `action_id`, `kind`, and `title`. The object for the selected kind MUST be present, and objects for other action kinds MUST be absent. Unknown action kinds MUST be rejected unless they use an advertised namespaced extension and local policy allows it (canonical outcome: `recommendation_disposition=rejected` with `reason_code=missing_capability`, §11.1).

`preconditions` and `success_criteria` are optional arrays; when absent they are treated as empty (subject to the shell default below).

### `shell`

A shell action MUST provide `shell.argv`. `command_for_display` is advisory and MUST NOT be executed.

Required `shell` fields:

```text
argv
cwd
env.mode
env.set
stdin.mode
pty
timeout_seconds
expected_exit_codes
requires_shell
```

`argv` MUST be a non-empty array of strings. `argv[0]` MUST be either an absolute path resolving inside the workspace, or a bare command name resolved via the executor's PATH; executors MUST NOT resolve `argv[0]` against the current directory.

`cwd` MUST be a `file:` URI. The executor MUST resolve it with symlinks followed and verify the resolved path is inside the goal workspace, unless local policy explicitly allows a broader path. The same resolved-path rule applies to `path_exists` precondition paths.

`timeout_seconds` MUST be a positive integer.

`shell.env.mode` MUST be one of:

```text
inherit      inherit process environment and apply `set`
replace      use only `set`
minimal      implementation-defined minimal safe environment plus `set`
```

Under the default policy, `env.mode: "inherit"` requires approval (§8.3): the executor's environment routinely contains credentials.

Environment entries in `set` MUST be objects, either `{ "value": string, "sensitive": false }` or `{ "secret_ref": string, "sensitive": true }`. An event log MUST NOT contain an environment entry with both `sensitive:true` and `value`. Executors MUST resolve `secret_ref` only through local policy-approved secret stores.

`stdin.mode` MUST be one of `none`, `inline`, or `artifact`. If `inline`, `stdin.text` MUST be present and MUST NOT exceed `capabilities.limits.max_inline_stdin_bytes` unless local policy explicitly allows it. `stdin.text` MUST NOT contain secret material: it is embedded in the immutable event log via the `recommendation.issued` event and cannot be truly redacted. Secret-bearing stdin MUST use `stdin.artifact` (with an access-controlled artifact) or a future `secret_ref` mechanism. If `artifact`, `stdin.artifact` MUST be a `wip.artifact/0.1` reference.

`pty` is RESERVED in v0.1: it MUST be `false`, and executors MUST reject `pty: true`.

**Output capture.** The executor captures stdout and stderr as bytes. Output up to `capabilities.limits.max_inline_output_bytes` per stream MAY be included inline in `action_result.output.stdout` / `action_result.output.stderr` as UTF-8 strings (invalid sequences replaced). Larger output MUST be stored as separate artifacts and referenced from `action_result.artifacts`. Output exceeding `max_artifact_bytes` is truncated per §2.2.

If `requires_shell: true`, v0.1 executors MUST NOT run the action automatically under the default policy. The command MUST still be represented as `argv`; how to invoke a shell is implementation-defined and therefore not interoperable for automatic execution in v0.1. Portable automatic shell execution requires `requires_shell:false`.

Timeout behavior: on timeout, the executor MUST terminate the child process group (or the child process where process groups are unavailable), record `timed_out: true`, and record signal information when available. Implementations SHOULD send a graceful termination signal before force-killing.

### `noop`

`noop` is executable only to acknowledge a state transition such as `done`, `blocked`, or `wait`. It MUST NOT mutate the outside environment. Its kind object is the empty object: `"noop": {}`.

## 4.4 Preconditions and Success Criteria

Allowed v0.1 precondition kinds:

```text
path_exists
command_available
env_present
approval
```

`command_available` and `env_present` MUST be evaluated against the environment the action would actually run in (after applying `env.mode` and `set`), not the executor's login environment. `approval` is satisfied by a matching approval event (§5.6).

Precondition objects require `id` and `kind`; `on_unsatisfied` is optional and defaults to `report_blocked`.

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

If `success_criteria` is absent for a shell action, it defaults to exit code in `shell.expected_exit_codes`, or `[0]` if that field is absent. If `success_criteria` is present, it is authoritative and `expected_exit_codes` is not consulted for success evaluation.

---

# 5. Update and Observation Schema

An update is any new information submitted to the wayfinder.

The example below reports the failure of the action issued as event `seq=18` in §4; `issued_event_seq`/`issued_event_hash` identify that `recommendation.issued` event.

```json
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_01",
  "action_id": "act_01",
  "issued_event_seq": 18,
  "issued_event_hash": "sha256:head18...",
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
    "output": {
      "stdout": "",
      "stderr": "make: *** No rule to make target 'test'.  Stop.\n"
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

Required update fields: `schema`, `protocol_version`, `update_id`, `goal_id`, `created_at`, `actor`, `update_type`.

If an update refers to a recommendation, it MUST include `recommendation_id`; if it refers to an action, also `action_id`. Updates that initiate or complete an action (`recommendation_disposition` for it, `action_started`, `action_result`) MUST include `issued_event_seq` and `issued_event_hash` identifying the `recommendation.issued` event that authorized the action (its `seq` and `event_hash`). Corrections, observations, and heartbeats that merely reference a recommendation MAY omit them.

Required `action_result` fields: `status`, `changed`, `started_at`, `ended_at`. `process` is required for shell actions. `output`, `duration_ms`, `criteria`, `artifacts`, `observations`, and `error` are optional.

Exactly one payload object matching `update_type` MUST be present, with one exception: an update MAY combine `recommendation_disposition` with an `action_started` or `action_result` payload when it intentionally combines disposition and result. In that case the wayfinder MUST append the disposition event before the action event in the same atomic append operation. If any event in the operation cannot be appended, no event from that update may be appended.

## 5.1 Update Types

```text
recommendation_disposition
action_started
action_result
observation
correction
redaction
override
question_answer
approval
heartbeat
policy_denied
goal_cancel
```

Minimum payload fields:

```text
recommendation_disposition.disposition       disposition value; reason optional
action_started.started_at                    RFC 3339 timestamp
action_result                                action result object (§5)
observation.observations                     array of observation objects (§5.4)
correction                                   correction object with scope, target_id, replacement, reason
redaction                                    redaction object with target_event_id or target_artifact_id, replacement_artifact?, reason
override                                     override object with decision and reason (§10.2)
question_answer                              answer object with question_id and answer
approval                                     approval object with decision requested|granted|denied, approver?, reason
heartbeat                                    heartbeat object with status, observed_at, optional message
policy_denied                                policy_denied object with reason_code and reason
goal_cancel                                  goal_cancel object with reason; reason_code optional
```

An `observation` update MAY set a top-level `invalidates_open_recommendations: false` to mark itself informational; the resulting `observation.recorded` event carries that explicit value instead of the default.

**Authority requirements.** `goal_cancel` and `override` decisions `mark_done` and `mark_failed` require an authenticated actor with authority `owner` or `policy_admin`. Other `override` decisions require authority `operator` or higher. The wayfinder MUST reject updates that do not meet these requirements with `policy_denied`.

## 5.2 Disposition Values

```text
accepted
rejected
skipped
expired
```

(`overridden` was removed: overrides use `update_type=override`.) An `expired` disposition MAY be submitted by any actor; the wayfinder MUST verify against its own clock that the recommendation's `expires_at` or `lease.lease_expires_at` has passed, and MUST reject the update with `invalid_input` otherwise.

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

Observation objects MUST be one of the following kinds, with these required fields:

```text
fact         subject, predicate, object            (+ optional confidence, source, evidence)
diagnostic   statement                             (+ optional confidence, evidence)
artifact     artifact (wip.artifact/0.1 reference) (+ optional description)
message      text                                  (+ optional audience: "human"|"wayfinder")
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

`evidence` entries are objects `{ "event_id": string, "description": string? }` or `{ "artifact_id": string, "description": string? }`.

## 5.5 Heartbeats

`heartbeat.status` MUST be one of `running`, `waiting`. Heartbeats never invalidate open recommendations.

## 5.6 Approvals

An `approval.granted` event satisfies an `approval` precondition or a `requires_approval` risk gate when its `data` matches the pending action's `recommendation_id` and (when present) `action_id`. An approval is valid only while its recommendation remains executable (§4.2); it does not survive supersession, expiry, or re-issuance. Approvals name their scope explicitly via `recommendation_id`/`action_id`; blanket approvals are not part of v0.1.

---

# 6. Persistent Event Log

The event log is append-only JSONL and is canonical for visible protocol state.

## 6.0 Store Resolution

The wayfinder store root defaults to `.wayfinder/` directly under the goal workspace root. Implementations MAY support overriding it via a `WAYFINDER_STORE` environment variable or a `--store` flag; all cooperating processes (wayfinder CLI invocations, executors, verifiers) MUST resolve the same store, and v0.1 assumes they share one local filesystem.

Default local layout:

```text
.wayfinder/
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

Artifact files SHOULD be created with mode `0600` and directories with mode `0700`.

## 6.1 Event Envelope

```json
{
  "schema": "wip.event/0.1",
  "protocol_version": "0.1",
  "event_id": "evt_00000021",
  "type": "action.failed",
  "time": "2026-07-04T18:23:45Z",
  "goal_id": "goal_01",
  "seq": 21,
  "source": "executor://dumb-executor@host",
  "actor": {
    "type": "executor",
    "id": "dumb-executor@host",
    "authority": "operator"
  },
  "subject": "act_01",
  "correlation_id": "rec_01",
  "causation_id": "evt_00000020",
  "invalidates_open_recommendations": true,
  "prev_event_hash": "sha256:head20...",
  "event_hash": "sha256:head21...",
  "data": {}
}
```

Required fields: `schema`, `protocol_version`, `event_id`, `type`, `time`, `goal_id`, `seq`, `source`, `actor`, `prev_event_hash`, `event_hash`, `data`.

Optional fields: `subject` (the primary entity the event is about, e.g., an action or recommendation ID), `correlation_id` (groups events belonging to one recommendation lifecycle), `causation_id` (the event that directly caused this one), `invalidates_open_recommendations` (effective value defaults per §6.3 when absent), and `run_id` (reserved; MUST be `null` if present).

`source` is an opaque string identifying the writing component; the form `{actor_type}://{actor_id}` is RECOMMENDED.

`seq` MUST start at 1 for `goal.created` and increase by exactly 1 per goal. `time` is assigned by the appender at append time.

Writers MUST NOT normalize events (e.g., inject default field values) before hashing or storage: the hash covers exactly the serialized form written to the log, and readers apply §6.3 defaults at interpretation time.

## 6.2 Required Event Types

```text
goal.created
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
redaction.recorded

approval.requested
approval.granted
approval.denied

question.answered

executor.heartbeat
executor.policy_denied
```

The event type names `goal.updated`, `question.asked`, and `wayfinder.status.reported` are RESERVED in v0.1: conforming implementations MUST NOT emit them, and a reducer encountering them MUST fail per §7.5. Status reads MUST NOT pollute event history.

## 6.3 Update-to-Event Mapping

`wayfinder update` MUST apply this deterministic mapping:

| Update | Event(s) |
|---|---|
| `recommendation_disposition=accepted` | `recommendation.accepted`; if accepting a `done` recommendation, also `goal.completed` with `terminal_status="succeeded"` in the same atomic append |
| `recommendation_disposition=rejected` | `recommendation.rejected` |
| `recommendation_disposition=skipped` | `recommendation.rejected` with `data.disposition="skipped"` |
| `recommendation_disposition=expired` | `recommendation.expired` |
| `override.decision=replace` | `recommendation.overridden` with `data.replacement_recommendation` |
| `override.decision=reject` | `recommendation.overridden` |
| `override.decision=defer` | `recommendation.overridden` |
| `override.decision=force` | `recommendation.overridden` |
| `override.decision=unsafe` | `recommendation.overridden` |
| `override.decision=mark_blocked` | `recommendation.overridden` |
| `override.decision=mark_done` | `recommendation.overridden`, then `goal.completed` with `terminal_status="succeeded"` in the same atomic append |
| `override.decision=mark_failed` | `recommendation.overridden`, then `goal.completed` with `terminal_status="failed"` in the same atomic append |
| `action_started` | `action.started` |
| `action_result.status=completed` | `action.completed` |
| `action_result.status=failed` | `action.failed` |
| `action_result.status=timed_out` | `action.timed_out` |
| `action_result.status=cancelled` | `action.cancelled` |
| `action_result.status=blocked` | `action.blocked` |
| `action_result.status=skipped` | `action.skipped` |
| `observation` | `observation.recorded` |
| `correction` | `correction.recorded` |
| `redaction` | `redaction.recorded` |
| `approval.decision=requested` | `approval.requested` |
| `approval.decision=granted` | `approval.granted` |
| `approval.decision=denied` | `approval.denied` |
| `question_answer` | `question.answered` |
| `heartbeat` | `executor.heartbeat` |
| `policy_denied` | `executor.policy_denied` |
| `goal_cancel` | `goal.cancelled` |

If an update contains artifact references, the wayfinder MUST verify artifact integrity before appending events. It MAY append `action.output_recorded` events before the terminal action event. If verification fails, no event from that update may be appended.

For any update that refers to a `recommendation_id` and `action_id`, the wayfinder MUST reject a second terminal action event for the same pair unless the submitted update is an idempotent replay of the original `update_id`. Terminal action events are `action.completed`, `action.failed`, `action.timed_out`, `action.cancelled`, `action.blocked`, and `action.skipped`.

Default `invalidates_open_recommendations` values (applied by readers when the field is absent):

```text
goal.created                         false
goal.cancelled                       true
goal.completed                       true
recommendation.issued                true
recommendation.superseded            true
recommendation.accepted              false
recommendation.rejected              true
recommendation.overridden            true
recommendation.expired               true
action.started                       false
action.output_recorded               false
terminal action events               true
observation.recorded                 true
correction.recorded                  true
approval.requested                   false
approval.granted                     true
approval.denied                      true
question.answered                    true
executor.heartbeat                   false
executor.policy_denied               true
redaction.recorded                   true
```

Note that `recommendation.issued` defaults `true` so that it invalidates *other* open recommendations; it never invalidates itself, because freshness is evaluated over events appended after the issuance (§4.2 condition 3). An event MAY override the default by setting `invalidates_open_recommendations` explicitly, but `recommendation.accepted`, `action.started`, and `action.output_recorded` MUST carry explicit `false` (§4.2).

## 6.4 Event Data Schemas

The `data` object is schema-governed by event type. v0.1 events MUST use the following minimum payloads:

```text
goal.created                 { goal }
goal.cancelled               { reason_code?, reason }
goal.completed               { terminal_status: "succeeded"|"failed"|"cancelled", reason? }
recommendation.issued        { recommendation }
recommendation.superseded    { recommendation_id, superseded_by, reason? }
recommendation.accepted      { recommendation_id, action_id?, disposition: "accepted", reason? }
recommendation.rejected      { recommendation_id, action_id?, disposition: "rejected"|"skipped", reason_code?, reason? }
recommendation.overridden    { recommendation_id, override, replacement_recommendation? }
recommendation.expired       { recommendation_id, expired_at, reason? }
action.started               { recommendation_id, action_id, started_at }
action.completed             { recommendation_id, action_id, action_result }
action.failed                { recommendation_id, action_id, action_result }
action.blocked               { recommendation_id, action_id, action_result }
action.skipped               { recommendation_id, action_id, action_result }
action.cancelled             { recommendation_id, action_id, action_result }
action.timed_out             { recommendation_id, action_id, action_result }
action.output_recorded       { recommendation_id, action_id, artifacts }
observation.recorded         { observations }
correction.recorded          { correction }
redaction.recorded           { target_event_id?, target_artifact_id?, replacement_artifact?, reason }
approval.requested           { recommendation_id?, action_id?, approval }
approval.granted             { recommendation_id?, action_id?, approval }
approval.denied              { recommendation_id?, action_id?, approval }
question.answered            { recommendation_id?, question_answer }
executor.heartbeat           { heartbeat }
executor.policy_denied       { recommendation_id?, action_id?, policy_denied }
```

`recommendation.issued.data.recommendation` MUST contain the exact `wip.recommendation/0.1` returned to the caller. Action terminal events MUST contain the exact accepted `action_result` payload, after artifact verification and redaction.

## 6.5 Append, Locking, and Recovery

- Writers MUST acquire the per-goal append lock before reading the current head for append.
- The lock path is `.wayfinder/goals/{goal_id}/locks/append.lock`.
- **Lock primitive.** The lock is acquired by creating `append.lock` with `O_CREAT|O_EXCL` (or the platform equivalent, e.g., `CREATE_NEW` on Windows) and writing a JSON body `{ "holder": string, "pid": integer, "acquired_at": rfc3339, "expires_at": rfc3339 }`, then released by unlinking the file. A lock whose `expires_at` has passed MAY be broken by unlinking it and retrying acquisition. If the lock body cannot be parsed, implementations MUST NOT break the lock and MUST fail with `storage_conflict`. A single-process wayfinder daemon MAY substitute internal locking only if it exclusively owns the store directory.
- An append operation MUST write complete UTF-8 JSON lines ending in LF and MUST fsync the event file or containing directory when the platform exposes that operation.
- Artifacts referenced by an event MUST be durable (written, fsynced, digest-verified) before the event is appended.
- A partial final line or hash mismatch makes the log corrupt. Implementations MUST NOT append to a corrupt log except through explicit repair. The only sanctioned v0.1 repair is out-of-band tooling that, after backing up the file, truncates a **partial final line only**. Hash-mismatch corruption MUST NOT be auto-repaired.
- Events MUST be immutable after append.
- Snapshots MAY be written for speed, but the event log remains canonical. Implementations MUST NOT truncate or compact events below a snapshot in v0.1.

An update that maps to multiple events MUST be appended atomically while holding the append lock. Implementations MUST NOT expose a prefix of the mapped events as a completed update result.

## 6.6 Hash Chain and Canonicalization

`prev_event_hash` and `event_hash` are REQUIRED.

Hash algorithm:

1. Set the `event_hash` member to `null`. The member MUST remain present with value `null`; it is not removed.
2. Serialize the event using RFC 8785 JSON canonicalization.
3. Compute SHA-256 over the UTF-8 canonical bytes.
4. Store as `sha256:<lowercase-hex>`.

For `seq=1`, `prev_event_hash` MUST be `null`. For `seq>1`, it MUST equal the previous event's `event_hash`.

A reader encountering a duplicate `seq`, a `seq` gap, a non-monotonic `seq`, or a `prev_event_hash` mismatch MUST fail with `corrupt_event_log`.

**Threat model.** The hash chain detects accidental corruption and post-hoc partial edits; it does not authenticate history. Anyone who can write the log file can rewrite the entire chain. Deployments that need authenticated history MUST layer signing on top (out of scope for v0.1).

## 6.7 Snapshots and Migration

Snapshot schema:

```json
{
  "schema": "wip.snapshot/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "seq": 50,
  "event_log_head": "sha256:...",
  "created_at": "2026-07-04T18:30:00Z",
  "state": {}
}
```

Snapshot files are named by zero-padded `seq` (`snapshots/00000050.json`). A snapshot is valid only if its `event_log_head` equals the `event_hash` of the event at its `seq`. Replaying from a snapshot plus later events MUST reconstruct the same visible status as replaying from event 1.

Snapshot `state` is implementation-private in v0.1. A conforming implementation MUST be able to ignore all snapshots and reconstruct visible state from events alone. `wayfinder status` MAY serve reads from a validated snapshot plus the event suffix.

Events include their own `protocol_version`. A reader encountering an event whose `protocol_version` is newer than any version it supports MUST fail with `unsupported_capability`. Future migrations MUST be represented as appended events or out-of-band tooling that preserves the original log.

## 6.8 Redaction Semantics

`redaction.recorded` events cannot alter already-appended events: event-payload redaction in v0.1 is **advisory only** (a signal to renderers and downstream consumers). This is why secrets MUST never enter event payloads (§4.3 stdin rule, §13 environment rules).

Artifact content, by contrast, is genuinely replaceable: a redaction with `replacement_artifact` supplies new post-redaction bytes under a new content address. After a covering `redaction.recorded` event is appended, the original artifact file MAY be deleted. A replayer resolving an artifact reference that has a covering redaction MUST use the replacement, or treat the artifact as valid-but-unavailable; it MUST NOT report `artifact_integrity_failed` for the redacted original.

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

Goals use only the subset listed in §3. `skipped`, `superseded`, and `unknown` apply to recommendations, actions, and heartbeats.

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
wayfinder_uncertain
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

The example reflects the §4–§6 timeline after `action.failed seq=21`: the terminal action event cleared the open recommendation without terminating the goal.

```json
{
  "schema": "wip.status/0.1",
  "protocol_version": "0.1",
  "goal_id": "goal_01",
  "run_id": null,
  "observed_at": "2026-07-04T18:24:00Z",
  "goal_status": "running",
  "reason_code": null,
  "progress": {
    "summary": "Source changes are complete; the test suite still fails.",
    "percent": null,
    "completed_steps": 3,
    "known_remaining_steps": null
  },
  "last_issued_recommendation_id": "rec_01",
  "open_recommendation_id": null,
  "last_event_seq": 21,
  "event_log_head": "sha256:head21...",
  "needs": []
}
```

`last_issued_recommendation_id` means the latest recommendation issued into history. `open_recommendation_id` means the latest executable recommendation that has not been superseded, rejected, expired, overridden, or terminated by a terminal action event. `run_id` is reserved and MUST be `null`.

`progress.percent` MUST be `null` or a number in [0, 100]. `observed_at` is transport metadata and is excluded from any determinism comparison.

`needs` is an array of objects:

```json
{ "kind": "approval", "reason_code": "needs_approval", "summary": "Approval required for rec_02." }
```

Allowed `needs.kind`: `user_input`, `approval`, `capability`, `dependency`, `credentials`. `kind` and `summary` are required.

## 7.5 Required Status Replay

`wayfinder status` and any conforming replayer MUST derive visible status by applying events in increasing `seq` order and verifying the hash chain first.

Replay MUST be deterministic: the reducer MUST NOT consult wall-clock time. In particular, a recommendation past its `expires_at` remains `open_recommendation_id` until an event (`recommendation.expired`, `recommendation.rejected`, `recommendation.overridden`, `recommendation.superseded`, or a terminal action event) clears it; §4.2 condition 4 separately prevents its execution.

Minimum reducer rules:

1. `goal.created` initializes `goal_status:"pending"`, `last_event_seq`, and `event_log_head`.
2. `recommendation.issued` sets `goal_status` to the recommendation's `goal_status` unless the current goal status is terminal. It sets `last_issued_recommendation_id`. If the recommendation is executable, it becomes `open_recommendation_id`.
3. Encountering an executable `recommendation.issued` while `open_recommendation_id` is already set — without an intervening event that cleared it — MUST fail with `corrupt_event_log`.
4. `recommendation.superseded`, `recommendation.rejected`, `recommendation.overridden`, and `recommendation.expired` clear `open_recommendation_id` when they target the current open recommendation.
5. `action.started` sets `goal_status:"running"` unless the current goal status is terminal.
6. Terminal action events clear `open_recommendation_id` when they target the current open recommendation. They MUST NOT by themselves mark the goal terminal; goals terminate only via `goal.completed` or `goal.cancelled`.
7. An issued `question` recommendation sets `goal_status:"waiting"` and `reason_code:"needs_user_input"`.
8. `executor.policy_denied` sets `goal_status:"blocked"` and `reason_code:"policy_denied"` unless the current goal status is terminal.
9. `recommendation.overridden` with `data.override.decision="mark_blocked"` sets `goal_status:"blocked"` and `reason_code` to `data.override.reason_code` when present, otherwise `null`.
10. `correction.recorded`, `observation.recorded`, and `question.answered` do not by themselves determine terminal status; they may invalidate open recommendations according to their effective event flag. Correction content is input to wayfinder reasoning, not to this reducer.
11. `goal.completed` sets `goal_status` to `data.terminal_status` and clears `open_recommendation_id`.
12. `goal.cancelled` sets `goal_status:"cancelled"` and clears `open_recommendation_id`.

If the reducer encounters an unknown or reserved core event type or invalid event data, status MUST fail with `corrupt_event_log` or `unsupported_capability`; it MUST NOT guess.

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

Required fields: `level`, `scope`, `safe_to_retry`, `safe_to_run_if_already_done`, `dedupe_strategy`. `key` is required when `dedupe_strategy` is `idempotency_key`. Optional with defaults: `detects_noop` (false), `partial_failure_recovery` (`unknown`), `max_attempts` (1), `precheck`, `postcheck`.

Allowed `level`: `strong`, `conditional`, `weak`, `none`, `unknown`.
Allowed `scope`: `process`, `workspace`, `host`, `account`, `external_system`, `global`.
Allowed `dedupe_strategy`: `idempotency_key`, `precondition_probe`, `postcondition_probe`, `artifact_hash`, `none`.
Allowed `partial_failure_recovery`: `retry`, `reconcile`, `rollback`, `manual`, `impossible`, `unknown`.

`precheck.description` and `postcheck.description` are advisory prose (invariant 8): a dumb executor MUST NOT act on them.

**Retry model.** v0.1 has no in-executor retry. An executor MUST NOT re-execute an action for which any terminal action event exists (invariant 9); a failed action can only be retried by the wayfinder issuing a **new** recommendation with a new `recommendation_id` and `action_id`. `idempotency.max_attempts` bounds the total number of issuances of equivalent actions (same `idempotency.key`); `safe_to_retry` governs whether the wayfinder may re-issue after failure and whether interruption recovery may re-execute (§11.3). If `level` is `none` or `unknown`, equivalent actions MUST NOT be automatically re-issued or re-executed.

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

Required fields: `level`, `classes`, `blast_radius`, `requires_approval`, `destructive`, `network`, `secrets`. Optional: `estimated_cost`, `rollback` (default `{ "available": false, "kind": "unknown" }`).

`rollback` fields: `available` (required boolean), `kind` one of `none`, `command`, `snapshot`, `manual`, `unknown`, and optional advisory `instructions` (string or null).

`requires_approval: true` and an `approval` precondition are the same mechanism: both are satisfied by a matching `approval.granted` event (§5.6).

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
    - network_read
    - network_write
    - external_side_effect
    - secrets_access
    - privileged
    - cost
    - irreversible

approval_required:
  - requires_shell: true
  - env_mode: inherit

shell:
  require_argv: true
  allow_requires_shell: false
  allow_pty: false
  default_env_mode: minimal
  denied_argv0:
    - rm
    - sudo
    - doas
    - dd
    - mkfs
    - shutdown
    - reboot
```

The `denied_argv0` list is a crude mechanical backstop, not a safety analysis; local policy SHOULD extend it. The executor MUST NOT trust wayfinder risk metadata as proof of safety. Its own enforcement is limited to **mechanical** checks — declared risk metadata against policy, resolved `cwd`/path containment, `argv[0]` allow/deny lists, env entry shape, `requires_shell`/`pty` flags, and artifact path rules. A dumb executor is not expected (or permitted) to semantically analyze commands; that would be hidden judgment.

---

# 9. Dry-Run and Explanation Modes

## 9.1 Wayfinder Preview

`wayfinder next --mode=preview` returns a non-executable recommendation. It MUST NOT append events, create leases, or reserve IDs needed for execution.

The executor MAY use preview for display, validation, policy evaluation, or cheap precondition checks. It MUST re-query with `--mode=issue` before execution.

## 9.2 Issued Recommendation

`wayfinder next --mode=issue` appends `recommendation.issued` and returns an executable recommendation.

## 9.3 Executor Dry-Run

A dry-run executor SHOULD:

1. Fetch `wayfinder next --mode=preview`.
2. Validate schema and capabilities.
3. Evaluate local policy.
4. Evaluate cheap supported preconditions.
5. Print what it would do.
6. Execute nothing.
7. Append no events unless explicitly configured to record dry-run observations (such observations SHOULD set `invalidates_open_recommendations: false`, §5.1).

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
mark goal failed
mark goal blocked
cancel goal
answer wayfinder question
record observation
correct wayfinder assumption/fact
change policy/preference
force execution subject to policy
```

## 10.2 Override Update

```json
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_override_01",
  "goal_id": "goal_01",
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
      "title": "Run tests with pnpm",
      "shell": {
        "argv": ["pnpm", "test"],
        "cwd": "file:/workspace/project",
        "env": { "mode": "minimal", "set": {} },
        "stdin": { "mode": "none" },
        "pty": false,
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
mark_failed
mark_blocked
force
unsafe
```

The `override` object requires `decision` and `reason`. `mark_blocked` MAY include `reason_code` (§7.5 rule 9). Authority requirements are in §5.1.

A replacement action MUST include the same risk and idempotency metadata required for wayfinder-issued executable actions, or the executor MUST refuse to run it until the wayfinder or human supplies that metadata.

When `override.decision` is `replace`, the wayfinder MUST materialize the replacement as a normal executable recommendation in `recommendation.overridden.data.replacement_recommendation`. The replacement recommendation MUST include `basis`, `lease`, `expires_at`, `risk`, `idempotency`, and `action`, and MUST set `supersedes` to include the original recommendation. A dumb executor MUST treat the replacement exactly like a wayfinder-issued executable recommendation and MUST NOT execute a bare `replacement_action` directly. For freshness (§4.2), the replacement's issuance anchor is the `recommendation.overridden` event that carries it.

## 10.3 Conflict Resolution

Default authority precedence:

```text
policy_admin > owner > operator > executor > wayfinder > observer
```

Rules:

1. A human override does not erase the wayfinder recommendation.
2. The wayfinder's next response MUST either honor the override or return `blocked` with `reason_code: unsupported_override`.
3. A dumb executor MUST NOT execute a replacement action unless it passes the same schema, freshness, claim, risk, and idempotency checks as wayfinder-issued actions.
4. `force` only bypasses wayfinder recommendation logic. It MUST NOT bypass executor safety policy unless local policy explicitly allows authenticated owner override.

---

# 11. Dumb Executor Spec

The dumb executor preserves the contract; it does not reason about hidden intent.

## 11.1 Required Loop

```pseudo
capabilities = wayfinder.capabilities()
status = wayfinder.status(goal_id)
verify_event_log_head(status.event_log_head)

while status.goal_status not in ["succeeded", "failed", "cancelled"]:
    rec = wayfinder.next(goal_id, mode="issue", explain="structured")

    validate_schema(rec)
    reject_if_unknown_required_capability(rec)

    if rec.recommendation_type == "done":
        wayfinder.update(recommendation_disposition=accepted)   # wayfinder appends goal.completed
        exit 0

    if rec.recommendation_type == "wait":
        if now < rec.wait.until_time: sleep_until(rec.wait.until_time)
        status = wayfinder.status(goal_id)
        continue

    if rec.recommendation_type in ["blocked", "unsafe", "question"]:
        # Non-interactive executors MUST exit here (exit 0, status in JSON)
        # rather than poll; interactive executors display and wait for input.
        display_or_exit(rec)
        status = wayfinder.status(goal_id)
        continue

    if rec.recommendation_type != "action":
        wayfinder.update(recommendation_disposition=rejected,
                      reason_code=missing_capability)
        status = wayfinder.status(goal_id)
        continue

    reject_if_non_executable_stale_or_claimed(rec)     # §4.2 conditions 1-7

    decision = evaluate_local_policy(rec.action, rec.risk, rec.idempotency)
    if decision.denied:
        wayfinder.update(policy_denied)
        status = wayfinder.status(goal_id)
        continue
    if decision.requires_human:
        wayfinder.update(approval request/result)
        if denied: continue

    preconditions = check_supported_preconditions(rec.action.preconditions)
    if not preconditions.ok:
        wayfinder.update(action_result.status=blocked)
        status = wayfinder.status(goal_id)
        continue

    wayfinder.update(recommendation_disposition=accepted)   # claims the lease (§4.2.7)
    persist_locally(update_id, recommendation_id, action_id)  # before spawning
    wayfinder.update(action_started)

    result = execute_exactly_one_action(rec.action)
    artifacts = store_and_hash_outputs(result)
    wayfinder.update(action_result with artifacts)

    status = wayfinder.status(goal_id)
```

No update is required for `wait`, `blocked`, or `unsafe` recommendations the executor merely observes; dispositions for them are optional. Executors SHOULD apply backoff and a loop-detection cap when the wayfinder repeatedly issues recommendations the executor cannot act on; wayfinders SHOULD NOT re-issue a structurally identical action after `executor.policy_denied` for it.

## 11.2 Executor MUST Rules

The executor MUST:

- Execute at most one action per recommendation, and never execute an action for which any terminal action event exists.
- Validate freshness and claim (§4.2) against the current event log before execution.
- Treat stale, expired, superseded, or already-claimed recommendations as non-executable.
- Deny unknown action kinds and unknown risk classes by default.
- Deny unsupported preconditions as blocked, not ignore them.
- Never execute `shell.command_for_display`.
- Execute `shell.argv` without shell expansion unless `requires_shell: true` and policy permits it.
- Treat `requires_shell: true` and `env.mode: "inherit"` as elevated risk requiring approval under the default policy.
- Reject `pty: true` in v0.1.
- Never re-execute after failure: retries occur only through wayfinder re-issuance (§8.1).
- Track attempts locally by `recommendation_id`, `action_id`, and `idempotency.key`.
- Durably record `update_id`, `recommendation_id`, and `action_id` before spawning the child process.
- Capture command failure as an action result, not as protocol failure.
- Retry failed `wayfinder update` submissions using the same `update_id` after an action has executed.
- Kill the child's process group on timeout when the platform supports process groups.
- Store stdout/stderr as separate artifacts when they exceed local inline limits.
- Redact secrets before hashing and publishing artifacts, using locally configured redaction patterns (redaction patterns are local policy input, not wayfinder-supplied).
- Re-query the wayfinder after each successful update.

## 11.3 Interruption Rule

If interruption occurs after external action execution but before update submission, the executor MUST resume by submitting the missing `action_result` with the original `update_id` (which it durably recorded before spawning, §11.2). If the result is unknown, it MUST submit an `observation` or `action_result.status=blocked` describing the uncertainty; it MUST NOT blindly re-execute unless `idempotency.safe_to_retry` is true and local policy allows it.

---

# 12. Capabilities Schema

```json
{
  "schema": "wip.capabilities/0.1",
  "protocol_version": "0.1",
  "protocol_versions": ["0.1"],
  "wayfinder": {
    "name": "local-wayfinder",
    "version": "0.3.0",
    "instance_id": "wayfinder_host_abc"
  },
  "transports": ["cli", "jsonrpc-stdio"],
  "schema_dialect": "https://json-schema.org/draft/2020-12/schema",
  "recommendation_types": ["action", "question", "wait", "blocked", "done", "unsafe"],
  "action_kinds": ["shell", "noop"],
  "precondition_kinds": ["path_exists", "command_available", "env_present", "approval"],
  "success_criteria_kinds": ["exit_code", "artifact_exists", "observation_recorded"],
  "update_types": [
    "recommendation_disposition",
    "action_started",
    "action_result",
    "observation",
    "correction",
    "redaction",
    "override",
    "question_answer",
    "approval",
    "heartbeat",
    "policy_denied",
    "goal_cancel"
  ],
  "event_types": [
    "goal.created",
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
    "redaction.recorded",
    "approval.requested",
    "approval.granted",
    "approval.denied",
    "question.answered",
    "executor.heartbeat",
    "executor.policy_denied"
  ],
  "explanation_modes": ["none", "summary", "structured", "debug"],
  "dry_run_modes": ["preview", "issue"],
  "features": {
    "supersede": true,
    "verify": true,
    "cancellation": false,
    "pty": false
  },
  "event_log": {
    "format": "jsonl",
    "hash_chain": true,
    "history_query": true,
    "canonicalization": "RFC8785"
  },
  "limits": {
    "max_inline_output_bytes": 8192,
    "max_inline_stdin_bytes": 8192,
    "max_recommendation_bytes": 1048576,
    "max_artifact_bytes": 104857600,
    "max_history_events_per_page": 1000
  },
  "extensions": {
    "namespaces": []
  }
}
```

Capabilities MUST enumerate every enum value the wayfinder may emit outside the required core. The `features` object advertises: `supersede` (support for `wayfinder next --supersede`), `verify` (the optional `wayfinder verify` command), `cancellation` (JSON-RPC cancellation), and `pty` (always `false` in v0.1).

---

# 13. Security and Safety Requirements

Shell execution:

- `argv` MUST be an array of strings.
- `requires_shell: false` means no shell metacharacter interpretation.
- `requires_shell: true` MUST require explicit approval under default policy and MUST NOT be automatically executed by a conforming v0.1 dumb executor.
- `argv[0]` MUST NOT be resolved against the current directory (§4.3).
- Executors MUST evaluate the mechanical checks of §8.3 against `argv`, `cwd`, `env`, stdin, and artifact paths even when the wayfinder's `risk` metadata claims a lower risk.

Paths:

- Workspace-relative and `file:` paths MUST resolve inside the allowed workspace unless policy allows broader access. Path containment MUST be checked on the symlink-resolved path.
- Executors MUST reject artifact paths that escape the artifact root through symlinks, `..`, or absolute path substitution.

Environment and secrets:

- Sensitive environment values MUST NOT be written to event logs.
- Sensitive environment entries MUST use `secret_ref`; plaintext `value` with `sensitive:true` is invalid.
- `stdin.text` MUST NOT carry secrets (§4.3): event payloads cannot be truly redacted (§6.8).
- `env.mode: "inherit"` requires approval under the default policy: inherited environments routinely contain credentials.
- Artifact redaction MUST happen before hashing and submission.
- Actions with possible secret access MUST include `secrets_access` or `secrets: may_access|required`; executors SHOULD also detect obvious secret access from env requests and deny if metadata is missing.

Actor identity:

- Any process with store access can claim any `actor` identity in submitted JSON. The trust boundary of v0.1 is the filesystem. `authenticated: true` MUST be wayfinder-derived from a local mechanism (§2.1), never client-asserted, and privileged operations (§5.1) MUST require it.

Human approval surfaces:

- Interfaces that ask a human to approve an action MUST display `shell.argv` (and `cwd`) verbatim. They MUST NOT present only `title`, `summary`, or `command_for_display`: those are wayfinder-controlled prose and can misdescribe the command.

Network and external side effects:

- `network_read`, `network_write`, `external_side_effect`, `cost`, and `privileged` are denied by default.
- Dependency installation commands SHOULD be treated as network and supply-chain relevant unless proven local.

Replay and tamper resistance:

- Executors MUST verify the event hash chain before using history to justify execution.
- A corrupt event log MUST stop automatic execution.
- The hash chain is tamper-evident, not tamper-proof (§6.6); do not present it as authentication.

Prose injection:

- Executors MUST ignore `summary`, `description`, `title`, `command_for_display`, `explanation.*`, `observation.statement`, `precheck`/`postcheck` descriptions, and all other prose fields for behavioral decisions.

Storage hygiene:

- Artifact files SHOULD be mode `0600`, store directories `0700` (§6.0).

---

# 14. Appendix A: Minimum JSON Schema Requirements

The normative schemas are the object definitions in this document. Implementations SHOULD publish JSON Schema files matching these requirements. At minimum, validators MUST enforce:

- Required common fields and `protocol_version`.
- Mandatory `wip.response/0.1` envelopes for successful non-history CLI commands.
- Closed core enums, with namespaced extension values (`{namespace}.{name}`) allowed only when advertised in capabilities.
- Conditional recommendation requirements by `recommendation_type`.
- Conditional update payload requirements by `update_type`.
- Event `data` payloads by event type.
- `additionalProperties: false` for core objects except explicitly named `metadata`, `details`, `extensions`, or `x_*` fields.
- RFC 3339 date-time strings.
- SHA-256 digest format `sha256:<lowercase-hex>`.
- `oneOf` schemas for action kinds and observation kinds.
- `run_id`, when present anywhere, MUST be `null`.

## 14.1 Recommendation Validation Rules

```text
IF recommendation_type == action:
  require action, action.action_id, action.title, risk, idempotency, basis, expires_at
  require executable boolean
  if executable == true require lease; lease.lease_expires_at <= expires_at
  forbid parallel == true in v0.1

IF recommendation_type in [done, blocked, unsafe, question, wait]:
  forbid action
  require the payload object named after the type (§4.1)
  require executable == false; forbid lease
```

## 14.2 Shell Validation Rules

```text
shell -> require argv, cwd, env, stdin, pty, timeout_seconds, expected_exit_codes, requires_shell
argv -> non-empty array of strings
cwd -> file URI resolving (symlink-resolved) inside workspace unless policy allows broader access
env.set entries -> either {value:string,sensitive:false} or {secret_ref:string,sensitive:true}
stdin.mode -> one of none, inline, artifact
stdin.text -> present iff mode == inline; bounded by max_inline_stdin_bytes
pty -> MUST be false in v0.1
requires_shell == true -> deny automatic execution under default v0.1 policy
```

## 14.3 Update Validation Rules

```text
recommendation_disposition -> require disposition; MAY be combined with action_started
                              or action_result in a single update (§5)
action_started -> require recommendation_id, action_id, issued_event_seq,
                  issued_event_hash, action_started
action_result -> require recommendation_id, action_id, issued_event_seq,
                 issued_event_hash, action_result
observation -> require observations
correction -> require correction
redaction -> require redaction
override -> require override (decision, reason)
question_answer -> require question_answer
approval -> require approval
heartbeat -> require heartbeat
policy_denied -> require policy_denied
goal_cancel -> require goal_cancel (reason)
all other combinations of multiple payload objects -> invalid
```

## 14.4 Event Validation Rules

```text
require event_hash and prev_event_hash
require data schema appropriate for type
require seq == previous seq + 1 (readers MUST fail corrupt_event_log on gaps,
  duplicates, or non-monotonic seq)
require prev_event_hash == previous event_hash
reject reserved event types (goal.updated, question.asked, wayfinder.status.reported)
forbid duplicate terminal action event for recommendation_id/action_id except
  idempotent update replay
recommendation.accepted, action.started, action.output_recorded MUST carry
  explicit invalidates_open_recommendations == false
```

---

# 15. Appendix B: Interoperability Test Vectors

Each implementation SHOULD pass these tests before claiming v0.1 compatibility.

## 15.1 Successful Shell Action

Initial log: `goal.created seq=1`.
Wayfinder response: issued `action` with `shell.argv=["true"]`, expected exit `[0]`.
Executor behavior: accepts, starts, runs, reports completed.
Expected events: `recommendation.issued`, `recommendation.accepted`, `action.started`, `action.completed`.
Pass: command exit code is represented in JSON and event hash chain verifies.

## 15.2 Failed Shell Action

Initial log: `goal.created seq=1`.
Wayfinder response: issued `shell.argv=["false"]`, expected exit `[0]`.
Executor behavior: reports `action_result.status=failed`, `process.exit_code=1`.
Expected events: `action.failed`.
Pass: `wayfinder update` exits 0 and status carries failure details.

## 15.3 Unsupported Action Kind

Wayfinder response: issued `action.kind="http"` without advertised extension support.
Executor behavior: does not execute; submits `recommendation_disposition=rejected` with `reason_code=missing_capability`.
Expected events: `recommendation.rejected` with `reason_code=missing_capability`.
Pass: no external side effect occurs, and the outcome is this single canonical event shape.

## 15.4 Unsupported Precondition

Wayfinder response: precondition `kind="custom"` without advertised support.
Executor behavior: does not execute.
Expected event: `action.blocked` with `reason_code=missing_capability`.
Pass: unsupported precondition is not ignored.

## 15.5 Stale Recommendation

Initial log: `recommendation.issued seq=4`, then `correction.recorded seq=5` (effective invalidates=true).
Executor behavior: refuses to execute; any `action_started` update is rejected.
Expected result: `stale_recommendation` error or blocked update.
Pass: no `action.started` event is appended.

## 15.6 Duplicate Executor Attempt

Initial log: `action.completed` exists for `rec_1/act_1`.
Wayfinder response: same recommendation is observed by executor B.
Executor behavior: no execution; submits skipped/observation if needed.
Expected events: no second terminal action event for the same recommendation/action.
Pass: external action runs at most once.

## 15.7 Concurrent `next --mode=issue`

Initial log: `goal.created seq=1`.
Two clients call `issue` concurrently (neither passes `--supersede`).
Expected result: one atomic issued recommendation, and the other call returns `storage_conflict`.
Pass: no two non-parallel executable recommendations exist.

## 15.8 Human Override Replacement

Initial log: wayfinder recommends `npm test`.
Update: human replaces with `pnpm test` and supplies risk/idempotency.
Executor behavior: validates replacement and policy before execution.
Expected events: `recommendation.overridden`, then action lifecycle events for replacement.
Pass: original recommendation remains in history.

## 15.9 Policy-Denied Destructive Action

Wayfinder response: `shell.argv=["rm","-rf","build"]`, risk includes `delete`.
Executor behavior: denies under default policy (`delete` class and `denied_argv0`).
Expected event: `executor.policy_denied`.
Pass: command is not run.

## 15.10 Timeout

Wayfinder response: `shell.argv=["sleep","60"]`, `timeout_seconds=1`.
Executor behavior: terminates the process group, records timeout.
Expected event: `action.timed_out`, with `process.timed_out=true`.
Pass: no child process remains under executor control.

## 15.11 Partial Artifact Write

Executor stores stdout artifact but digest verification fails.
Executor behavior: does not submit invalid reference; retries artifact write or reports storage failure.
Expected result: no event references invalid artifact.
Pass: every artifact reference hash verifies.

## 15.12 Corrupted Event Log

Initial log: final JSONL line is truncated or hash mismatch occurs.
Wayfinder/executor behavior: detects corruption.
Expected result: `corrupt_event_log`; no automatic execution or append.
Pass: implementation refuses to build on unverifiable history.

## 15.13 Replay From Snapshot

Initial data: snapshot at seq 50 with hash H and events 51-55.
Replayer behavior: verifies snapshot base and applies later events.
Expected result: same status/head as full replay from seq 1.
Pass: replay is deterministic.

## 15.14 Mandatory CLI Envelope

Initial log: `goal.created seq=1`.
Command: `wayfinder status --goal-id goal_01 --format=json`.
Expected result: stdout is one JSON object with `schema="wip.response/0.1"` and `result.schema="wip.status/0.1"`.
Pass: a client can parse every successful non-history command through the same envelope shape.

## 15.15 Idempotent Goal Create

Command input: same `wip.goal_create/0.1` object with `create_id="create_01"` submitted twice.
Expected result: second response returns the original goal, original `goal.created` event, current status, and `replayed:true`.
Pass: no second goal or second `goal.created` event is created.

## 15.16 Conflicting Goal Create

Command input: reuse `create_id="create_01"` with a different canonical goal-create object.
Expected result: `invalid_input`; no event is appended.
Pass: retry safety does not allow accidental goal mutation.

## 15.17 Same-Action Lifecycle Is Not Stale

Initial log: `recommendation.issued seq=2` (hash H2) with `basis.event_log_seq=1`.
Updates: executor submits accepted and started updates, then submits terminal `action_result` with `issued_event_seq=2` and `issued_event_hash=H2`.
Expected result: terminal event is accepted despite intervening `recommendation.accepted` and `action.started`.
Pass: normal lifecycle events do not make the executing action stale.

## 15.18 Duplicate Terminal Action Event

Initial log: terminal `action.completed` exists for `recommendation_id=rec_01`, `action_id=act_01`.
Update: different `update_id` submits another terminal result for the same pair.
Expected result: `invalid_input` or `stale_recommendation`; no second terminal action event is appended.
Pass: duplicate external execution cannot be legitimized by the log.

## 15.19 Secret Environment Value Rejected

Wayfinder response: shell action includes `env.set.API_KEY={"value":"secret","sensitive":true}`.
Executor behavior: rejects schema/policy and does not execute.
Expected result: no command execution; optional `executor.policy_denied` or `recommendation.rejected`.
Pass: plaintext sensitive values do not enter event history.

## 15.20 Preview Is Not Explainable Later

Command: `wayfinder next --mode=preview` returns `recommendation_id=rec_preview_01`.
Command: later `wayfinder explain --recommendation-id rec_preview_01`.
Expected result: `invalid_input`.
Pass: preview-only recommendations are not treated as durable history.

## 15.21 Replacement Override Materializes Recommendation

Initial log: open executable recommendation `rec_01`.
Update: human override with `decision="replace"`.
Expected event: `recommendation.overridden` with `data.replacement_recommendation` containing a full executable recommendation, including `basis`, `lease`, `expires_at`, `risk`, `idempotency`, and `action`.
Pass: executor can validate the replacement without hidden judgment.

## 15.22 JSON-RPC Result Shape

Command: JSON-RPC `goal.status` request with `id="req_01"`.
Expected result: JSON-RPC `result` is a `wip.status/0.1` object, not an embedded `wip.response/0.1` envelope.
Pass: CLI and JSON-RPC correlation rules are equivalent without double wrapping.

## 15.23 Accepting Done Completes Goal

Initial log: issued non-executable `recommendation_type="done"` recommendation.
Update: executor submits `recommendation_disposition=accepted` for that recommendation.
Expected events: `recommendation.accepted` followed atomically by `goal.completed` with `terminal_status="succeeded"`.
Pass: replayed status is `goal_status="succeeded"` and `open_recommendation_id=null`.

## 15.24 Fresh Immediately After Issuance

Initial log: `goal.created seq=1`; `wayfinder next --mode=issue` appends `recommendation.issued seq=2` (hash H2); head is H2.
Executor behavior: evaluates §4.2 conditions and proceeds.
Expected events: `recommendation.accepted seq=3`, `action.started seq=4`.
Pass: the recommendation is NOT judged stale when the only post-basis event is its own issuance.

## 15.25 Defaulted Flag Counts for Freshness

Initial log: `goal.created seq=1`, `recommendation.issued seq=2`, `executor.heartbeat seq=3` with `invalidates_open_recommendations` absent.
Executor behavior: evaluates freshness using the §6.3 default (`false` for heartbeats).
Expected result: recommendation remains executable.
Pass: readers apply defaults; no explicit field is required on the heartbeat event.

## 15.26 Terminal Result Accepted After Post-Start Invalidation

Initial log: `goal.created seq=1`, issued seq=2, accepted seq=3, `action.started seq=4`, `observation.recorded seq=5` (effective invalidates=true).
Update: same executor submits terminal `action_result` for the started action.
Expected events: `action.completed seq=6` (or the appropriate terminal type).
Pass: an already-started action can always land its terminal result (§4.2 terminal-result rule).

## 15.27 Claimed Lease Blocks a Second Executor

Initial log: `goal.created seq=1`, issued seq=2, `recommendation.accepted seq=3` by actor `exec-A`.
Input: actor `exec-B` submits `action_started` for the same recommendation/action.
Expected result: wayfinder rejects with `storage_conflict`; no `action.started` by `exec-B`; the external command never runs under `exec-B`.
Pass: §4.2 condition 7 prevents concurrent duplicate execution.

## 15.28 Supersession Is Explicit and Atomic

Initial log: `goal.created seq=1`, issued `rec_01` seq=2 (open).
Input: `wayfinder next --mode=issue` without `--supersede`, then with `--supersede`.
Expected result: first call fails `storage_conflict` with no events; second call atomically appends `recommendation.superseded seq=3` (targeting `rec_01`) and `recommendation.issued seq=4` (`rec_02` with `supersedes:["rec_01"]`).
Pass: the log gains either zero or exactly two events; `open_recommendation_id` replays to `rec_02`.

## 15.29 Expiry Is Event-Driven, Not Clock-Driven

Initial log: `goal.created seq=1`, issued seq=2 with `expires_at` in the past; no further events.
Behavior: `wayfinder status` at two different wall-clock times; then an actor submits `recommendation_disposition=expired`.
Expected results: both status reads are identical (modulo `observed_at`) with `open_recommendation_id="rec_01"`; execution attempts fail per §4.2 condition 4; after the update, `recommendation.expired seq=3` is appended and `open_recommendation_id` replays to `null`.
Pass: the reducer never consults the clock; expiry becomes visible only through the event.

## 15.30 Override mark_done Completes the Goal

Initial log: `goal.created seq=1`, issued `rec_01` seq=2.
Update: `update_type=override`, `decision="mark_done"`, authenticated `owner` actor.
Expected events: `recommendation.overridden seq=3` and `goal.completed seq=4` with `terminal_status="succeeded"`, appended atomically.
Pass: replayed `goal_status="succeeded"`; a partial append (only one of the two events) never becomes visible.

## 15.31 Goal Cancel

Initial log: `goal.created seq=1`.
Update: `update_type=goal_cancel` with `reason`, authenticated `owner` actor.
Expected events: `goal.cancelled seq=2`; subsequent `wayfinder next --mode=issue` fails `invalid_input`.
Pass: replayed `goal_status="cancelled"`; unauthenticated or `operator` actors are rejected with `policy_denied`.

## 15.32 Idempotent Replay Returns Current Status

Initial log: `goal.created seq=1`, issued seq=2; update U1 (accepted) appends seq=3; another actor appends `observation.recorded seq=4`. U1 is re-submitted byte-identically.
Expected result: response contains the original seq=3 event (`seq_start=seq_end=3`), `replayed:true`, and `status.last_event_seq=4`.
Pass: no new events are appended; status reflects the current log.

## 15.33 Cross-Implementation Lock Exclusion

Setup: implementation A holds `append.lock` (created via `O_CREAT|O_EXCL`, valid `expires_at`); independent implementation B runs `wayfinder update` on the same goal directory.
Expected result: B fails with `storage_conflict` or waits for release; after both writers finish, the chain verifies with strictly monotonic `seq` and no duplicates.
Pass: two independent codebases achieve mutual exclusion through the pinned primitive.

## 15.34 JSON-RPC History Shape

Initial log: `goal.created seq=1` plus 4 more events.
Input: JSON-RPC `goal.history {"goal_id":"goal_01","since_seq":0}` with `id="req_9"`.
Expected result: `result.events` is an array of 5 `wip.event/0.1` objects canonically identical to the CLI JSONL lines; `truncated:false`; `id` echoed; no envelope nesting.
Pass: CLI JSONL and RPC array are event-for-event equal.

## 15.35 History Failure Mid-Stream

Setup: log with 100 events; hash mismatch at event 50.
Input: `wayfinder history --since-seq 0`.
Expected result: events 1-49 streamed as valid JSONL; nonzero exit; final line MAY be a `wip.error/0.1` object with `code="corrupt_event_log"`.
Pass: client detects incompleteness from the exit code and verifies the received prefix; no fabricated events after the corruption point.

## 15.36 Redacted Artifact Replacement

Initial log: terminal action event references artifact `art_01` (contains a token).
Update: `redaction` with `target_artifact_id="art_01"` and `replacement_artifact` (new post-redaction bytes, new sha256).
Expected events: `redaction.recorded`; original artifact file MAY be deleted.
Pass: a replayer resolving `art_01` uses the replacement or reports valid-but-unavailable; it never reports `artifact_integrity_failed` for the covered original; full replay still succeeds.

## 15.37 Non-Action Payload Nesting

Input: `wayfinder next --mode=issue` where the wayfinder returns `recommendation_type="blocked"`.
Expected result: the recommendation validates with `blocked:{reason_code,reason}` nested, `action` absent, `executable:false`, and no `lease`.
Pass: an independent validator with `additionalProperties:false` accepts the object byte-for-byte.

## 15.38 Reserved run_id

Input: an update or recommendation containing `"run_id": "run_01"`.
Expected result: `invalid_input` (non-null `run_id` is rejected); the same object with `"run_id": null` or the field absent is accepted.
Pass: no implementation assigns semantics to `run_id` in v0.1.
