# Oracle CLI User Guide

**Audience:** command-line users driving an oracle that conforms to the
[Oracle Interaction Protocol v0.1](oracle-interaction-protocol-v0.1.md) (OIP).
**Assumes:** you are comfortable with a Unix shell, JSON, and `jq`.

This guide covers day-to-day use: creating goals, getting and acting on
recommendations, reporting results, letting the executor drive, wrapping other
command-line tools, and composing oracles. It is not the protocol reference —
when this guide and the spec disagree, the spec wins.

**Tool names used here.** The spec defines the `oracle` command. The names
`oracle-exec` (the dumb executor) and `oracle-wrap` (the universal CLI-tool
wrapper) are illustrative placeholders — substitute whatever your installation
provides. Security, key management, and permission policy are covered in a
separate document; this guide only flags the places where they matter.

---

## 1. The mental model

The oracle is a black box that knows (or figures out) *what to do next* to
advance a goal. It never touches your system. Everything that happens is a loop
between three parties:

```text
you / executor:  "here is a goal"            -> oracle goal create
oracle:          "do this next"              -> oracle next
you / executor:  run the action (or don't)
you / executor:  "here is what happened"     -> oracle update
                 ...repeat until done...
```

Three things make this different from just asking a chatbot for shell commands:

1. **Everything is recorded.** Every goal has an append-only, hash-chained
   event log (`.oracle/goals/<goal_id>/events.ndjson`). Nothing is ever edited;
   corrections and redactions are new events. You can always audit exactly what
   was recommended, what ran, and what came back.
2. **The oracle never executes anything.** You do, or a deliberately dumb
   executor does. The oracle only appends events as the defined result of the
   commands you run.
3. **Structured fields are law, prose is advisory.** A recommendation's
   `summary` or `title` can say anything; only `action.shell.argv` is what
   actually runs. When reviewing an action, always read `argv` and `cwd`, never
   just the summary. (This is a spec requirement for approval UIs, and a good
   habit for humans.)

### Command cheat sheet

```bash
oracle capabilities                              # what this oracle supports
oracle goal create < goal.json                   # start a goal
oracle status --goal-id GOAL                     # where things stand
oracle next --goal-id GOAL --mode=preview        # peek, no commitment
oracle next --goal-id GOAL --mode=issue          # get an executable recommendation
oracle update --goal-id GOAL < update.json       # report anything back
oracle history --goal-id GOAL --since-seq 0      # the full event log (JSONL)
oracle explain --goal-id GOAL --recommendation-id REC   # why it recommended that
oracle verify --goal-id GOAL                     # check log + artifact integrity
```

Every non-history command prints exactly one JSON object on stdout — a
`oip.response/0.1` envelope on success, an `oip.error/0.1` object on failure.
`stderr` is for humans only. Exit code 0 means "the command worked", **not**
"the goal/action succeeded" — success and failure of goals and actions live in
the JSON.

Useful reflex: pipe everything through `jq`.

```bash
oracle status --goal-id goal_01 | jq '.result | {goal_status, open_recommendation_id, needs}'
```

---

## 2. Quick start: a complete session by hand

This walkthrough drives one goal to completion manually. In practice you will
usually let `oracle-exec` do steps 3–5 (see §4), but doing it by hand once
teaches you what the executor does on your behalf.

### 2.1 Create a goal

Goal creation takes a JSON document on stdin. `create_id` is your idempotency
key: if the command times out and you rerun it with the same body, you get the
same goal back (`"replayed": true`) instead of a duplicate.

```bash
oracle goal create --request-id req_create_1 <<'EOF'
{
  "schema": "oip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_fix_tests_2026_07_04",
  "created_at": "2026-07-04T18:00:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "description": "Make the project tests pass.",
  "workspace_uri": "file:/home/curt/project",
  "policy": { "max_auto_risk_level": "low" }
}
EOF
```

Notes:

- `workspace_uri` must be an **absolute** `file:` URI to an existing
  directory. The oracle store lives at `.oracle/` under it by default.
- `actor` is who you are. `authority` matters later: cancelling a goal or
  marking it done/failed requires an *authenticated* `owner` or
  `policy_admin`. (How authentication is established is local policy — see the
  security document.)

Capture the goal ID:

```bash
GOAL=$(oracle goal create < goal.json | jq -r '.result.goal.goal_id')
```

### 2.2 Peek before committing: preview

```bash
oracle next --goal-id "$GOAL" --mode=preview --explain=summary | jq '.result'
```

Preview appends **nothing** to the log and the returned recommendation is
marked `"executable": false`. Use it to see what the oracle is thinking, sanity-
check `argv`, or evaluate policy — then throw it away. A previewed
`recommendation_id` cannot be executed or `explain`ed later; you must issue.

### 2.3 Issue a recommendation

```bash
oracle next --goal-id "$GOAL" --mode=issue --explain=structured > next.json
jq '.result' next.json
```

Issuing atomically appends one `recommendation.issued` event. A typical action
recommendation (abbreviated):

```json
{
  "recommendation_id": "rec_01",
  "recommendation_type": "action",
  "executable": true,
  "summary": "Run the project test suite.",
  "lease": { "lease_id": "lease_01", "lease_expires_at": "2026-07-04T18:32:11Z" },
  "expires_at": "2026-07-04T18:32:11Z",
  "action": {
    "action_id": "act_01",
    "kind": "shell",
    "title": "Run tests",
    "shell": {
      "argv": ["make", "test"],
      "cwd": "file:/home/curt/project",
      "env": { "mode": "minimal", "set": {} },
      "stdin": { "mode": "none" },
      "pty": false,
      "timeout_seconds": 600,
      "expected_exit_codes": [0],
      "requires_shell": false
    }
  },
  "risk": { "level": "low", "classes": ["read_local", "execute_local"], "...": "..." },
  "idempotency": { "level": "strong", "safe_to_retry": true, "...": "..." }
}
```

Read three things before acting:

1. **`action.shell.argv` and `cwd`** — the literal command. Ignore
   `command_for_display`; it is prose.
2. **`risk`** — level, classes (`network_write`? `delete`? `irreversible`?),
   whether `requires_approval` is set.
3. **`expires_at` / `lease.lease_expires_at`** — after these pass, the
   recommendation cannot be started; get a fresh one.

You will also need the **issuance event's `seq` and `event_hash`** to report
back. Get them from the log:

```bash
oracle history --goal-id "$GOAL" --since-seq 0 \
  | jq -c 'select(.type == "recommendation.issued")' | tail -1 \
  | jq '{seq, event_hash}'
```

```json
{ "seq": 2, "event_hash": "sha256:head02..." }
```

### 2.4 Accept, run, report

Updates are JSON documents on stdin, each with a unique `update_id`
(your idempotency key — reuse it byte-identically to retry safely).

**Accept** (this claims the lease — after this, no other actor may start the
same action):

```bash
oracle update --goal-id "$GOAL" <<'EOF'
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_accept_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_01",
  "action_id": "act_01",
  "issued_event_seq": 2,
  "issued_event_hash": "sha256:head02...",
  "created_at": "2026-07-04T18:22:30Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "update_type": "recommendation_disposition",
  "recommendation_disposition": { "disposition": "accepted" }
}
EOF
```

**Report started**, then actually run the command yourself:

```bash
oracle update --goal-id "$GOAL" <<'EOF'
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_started_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_01",
  "action_id": "act_01",
  "issued_event_seq": 2,
  "issued_event_hash": "sha256:head02...",
  "created_at": "2026-07-04T18:23:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "update_type": "action_started",
  "action_started": { "started_at": "2026-07-04T18:23:00Z" }
}
EOF

cd /home/curt/project && make test; echo "exit=$?"
```

(Shortcut: a single update may combine `recommendation_disposition` with
`action_started` or `action_result`; the oracle appends both events
atomically.)

**Report the result** — success here, but the shape is identical for failure;
just change `status` and the process fields:

```bash
oracle update --goal-id "$GOAL" <<'EOF'
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_result_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_01",
  "action_id": "act_01",
  "issued_event_seq": 2,
  "issued_event_hash": "sha256:head02...",
  "created_at": "2026-07-04T18:24:10Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "update_type": "action_result",
  "action_result": {
    "status": "completed",
    "changed": "no",
    "started_at": "2026-07-04T18:23:00Z",
    "ended_at": "2026-07-04T18:24:08Z",
    "process": { "exit_code": 0, "signal": null, "timed_out": false },
    "output": { "stdout": "All 42 tests passed.\n", "stderr": "" }
  }
}
EOF
```

Rules that matter here:

- **Every action attempt must be followed by a result update**, even if the
  command never started. There is no such thing as a silently abandoned action.
- `action_result.status` is one of `completed`, `failed`, `timed_out`,
  `cancelled`, `blocked`, `skipped`. A failed *command* is still a
  *successful protocol exchange* — `oracle update` exits 0 and the failure
  lives in JSON.
- `changed` records whether the world was mutated (`yes`/`no`/`partial`/
  `unknown`), independently of success. `make test` succeeded but changed
  nothing.
- Once an action has any terminal event, it is **never re-executed**. Retries
  happen only when the oracle issues a *new* recommendation.

### 2.5 Loop until done

```bash
oracle next --goal-id "$GOAL" --mode=issue | jq '.result'
```

When the oracle believes the goal is complete it returns a non-executable
`done` recommendation:

```json
{
  "recommendation_id": "rec_02",
  "recommendation_type": "done",
  "executable": false,
  "summary": "Tests pass; goal achieved.",
  "done": { "reason": "make test exited 0 on the current workspace." }
}
```

Accepting a `done` recommendation is what actually completes the goal (the
oracle atomically appends `recommendation.accepted` + `goal.completed`):

```bash
oracle update --goal-id "$GOAL" <<'EOF'
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_accept_done_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_02",
  "created_at": "2026-07-04T18:25:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "update_type": "recommendation_disposition",
  "recommendation_disposition": { "disposition": "accepted" }
}
EOF

oracle status --goal-id "$GOAL" | jq -r '.result.goal_status'
# succeeded
```

---

## 3. Reading recommendations: the six types

`oracle next` returns exactly one of six types. Only `action` is executable;
the other five carry their payload in an object named after the type.

| Type | Meaning | What you do |
|---|---|---|
| `action` | Run this one structured action. | Review `argv`/`risk`, accept, run, report. |
| `question` | The oracle needs information. | Answer with a `question_answer` update (§6.1). |
| `wait` | Nothing useful until `wait.until_time`. | Sleep, then `oracle next` again. |
| `blocked` | No progress possible without external change. | Read `blocked.reason_code`/`reason`, fix the world, record an observation. |
| `done` | Goal appears complete. | Accept it (completes the goal) or reject it with a reason. |
| `unsafe` | Every known next step violates policy. | Read `unsafe.reason`; involve a human/policy decision. |

Example `question`:

```json
{
  "recommendation_type": "question",
  "executable": false,
  "question": {
    "question_id": "q_01",
    "prompt": "Which package manager does this repository use: npm or pnpm?"
  }
}
```

Example `wait`:

```json
{
  "recommendation_type": "wait",
  "executable": false,
  "wait": { "until_time": "2026-07-04T19:00:00Z" }
}
```

### 3.1 Staleness, leases, and expiry — in practice

An issued recommendation goes stale when meaningful new information lands in
the log after it (observations, corrections, another issuance…). Routine
lifecycle events — accepting it, starting it, recording its output — do *not*
make it stale. Practical consequences:

- If you sit on a recommendation and someone records an observation, your
  `accepted`/`action_started` update will be rejected with
  `stale_recommendation`. Just run `oracle next --mode=issue` again.
- The **first actor to accept claims the lease.** A second actor trying to
  start the same action gets `storage_conflict`. This is how two executors on
  the same store avoid double-running a command.
- Past `expires_at`, execution is refused, but status still shows the
  recommendation as open until an event clears it. Any actor may submit a
  `recommendation_disposition` of `expired` to tidy up (the oracle checks its
  own clock before accepting it).
- **Once started, always reportable.** If your action already ran, the oracle
  must accept your terminal `action_result` even if the recommendation was
  invalidated mid-flight. Results of things that really happened always land.

### 3.2 Only one open recommendation at a time

v0.1 has no parallelism. If an executable recommendation is open and you ask
for another, `oracle next --mode=issue` fails with `storage_conflict`. Your
options:

```bash
# Option A: dispose of the open one (reject it with a reason)
# Option B: atomically supersede it with a fresh one:
oracle next --goal-id "$GOAL" --mode=issue --supersede | jq '.result'
```

`--supersede` appends `recommendation.superseded` + `recommendation.issued` in
one atomic operation; the new recommendation's `supersedes` array names the
replaced one.

---

## 4. Letting the executor drive

Manually shepherding every action gets old fast. The dumb executor runs the
loop for you: issue → validate → policy-check → accept → execute → report →
repeat, until the goal is terminal or it hits something it cannot or may not
do.

```bash
# Run the loop for a goal
oracle-exec run --goal-id "$GOAL"

# See what it WOULD do without executing anything (uses --mode=preview)
oracle-exec dry-run --goal-id "$GOAL"
```

What "dumb" means for you:

- It executes `argv` verbatim, with no shell interpretation, and never
  improvises. It cannot be talked into anything by prose fields.
- It enforces **mechanical** policy only: risk level/class allowlists,
  `argv[0]` denylists, path containment, env rules. By default it auto-runs
  only `low`-risk actions in the classes `read_local`, `execute_local`,
  `write_workspace`. Everything else — deletion, network, secrets, host
  writes, cost — is denied or needs approval (§6.2).
- When it hits a `question`, `blocked`, or `unsafe` recommendation in
  non-interactive mode, it exits (code 0, status in JSON) rather than spin.
  You resolve the impasse, then run it again.
- If it is interrupted after a command ran but before reporting, on restart it
  submits the missing result with the same `update_id` — it never blindly
  re-executes.

A common working style: run `oracle-exec` until it stops, look at
`oracle status` and the last few history events to see why, unblock (answer a
question, grant an approval, record an observation, fix the environment), and
run it again.

```bash
oracle status --goal-id "$GOAL" | jq '.result | {goal_status, reason_code, needs}'
oracle history --goal-id "$GOAL" --since-seq 0 | tail -5 | jq -c '{seq, type}'
```

---

## 5. Watching and auditing: status, history, explain, verify

### 5.1 Status

```bash
oracle status --goal-id "$GOAL" | jq '.result'
```

```json
{
  "goal_status": "running",
  "reason_code": null,
  "progress": { "summary": "Source changes complete; tests still failing.", "completed_steps": 3 },
  "last_issued_recommendation_id": "rec_04",
  "open_recommendation_id": "rec_04",
  "last_event_seq": 12,
  "event_log_head": "sha256:head12...",
  "needs": [
    { "kind": "approval", "reason_code": "needs_approval", "summary": "Approval required for rec_04." }
  ]
}
```

`needs` is your to-do list: `user_input`, `approval`, `capability`,
`dependency`, or `credentials` entries tell you exactly what is holding the
goal up. Status is a pure read — it never appends events.

### 5.2 History

History is JSONL, one event per line, exactly as stored:

```bash
# Everything
oracle history --goal-id "$GOAL" --since-seq 0

# Incremental polling: remember the last seq you saw
oracle history --goal-id "$GOAL" --since-seq 12

# Compact life story of the goal
oracle history --goal-id "$GOAL" --since-seq 0 \
  | jq -c '{seq, type, actor: .actor.id, subject}'

# What did action act_03 actually output?
oracle history --goal-id "$GOAL" --since-seq 0 \
  | jq 'select(.type | startswith("action.")) | select(.data.action_id == "act_03") | .data.action_result.output'
```

A nonzero exit from `history` means the stream may be incomplete — treat what
you received as a prefix and verify before relying on it.

### 5.3 Explain

```bash
oracle explain --goal-id "$GOAL" --recommendation-id rec_04 | jq '.result.explanation'
```

Returns the oracle's reasoning (`summary`, `evidence` pointing at event IDs,
`redactions`) for any recommendation that was actually issued. Explanations
are advisory — useful for humans, never a basis for executor behavior.
Preview-only recommendations cannot be explained after the fact.

### 5.4 Verify

```bash
oracle verify --goal-id "$GOAL" | jq '.result | {ok, problems}'
```

Checks the hash chain and artifact digests. Run it whenever something smells
off, before archiving a goal, or after any abnormal shutdown. `ok: false`
lists problems (`hash_mismatch`, `truncated_line`, `artifact_missing`, …) with
exit 0; nonzero exit is reserved for the tool itself failing. A corrupt log
stops all automatic execution — recovery is out-of-band and deliberately
conservative (only a partial final line may ever be truncated, after backup).

---

## 6. Talking back to the oracle

`oracle update` is the single door for everything you want the oracle to know.
Beyond dispositions and action results, the update types you will actually use:

### 6.1 Answering questions

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_answer_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_05",
  "created_at": "2026-07-04T19:02:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "update_type": "question_answer",
  "question_answer": { "question_id": "q_01", "answer": "pnpm" }
}
```

### 6.2 Approvals

When a recommendation has `risk.requires_approval: true` or an `approval`
precondition, someone with authority grants (or denies) it. Approvals are
scoped to the specific recommendation (and action) — they do not survive
supersession or expiry, and there are no blanket approvals in v0.1.

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_approve_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_04",
  "action_id": "act_04",
  "created_at": "2026-07-04T19:05:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "update_type": "approval",
  "approval": { "decision": "granted", "reason": "Reviewed argv; publishing this release is intended." }
}
```

Before approving, always look at the real command, not the summary:

```bash
oracle history --goal-id "$GOAL" --since-seq 0 \
  | jq 'select(.type=="recommendation.issued") | select(.data.recommendation.recommendation_id=="rec_04")
        | .data.recommendation.action.shell | {argv, cwd, env}'
```

### 6.3 Observations: telling the oracle what you know

Observations are how new facts enter the oracle's world — things you did
outside the loop, things you noticed, artifacts you produced. By default an
observation invalidates the open recommendation (the oracle should rethink);
set `invalidates_open_recommendations: false` for purely informational notes.

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_obs_01",
  "goal_id": "goal_01",
  "created_at": "2026-07-04T19:10:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "update_type": "observation",
  "observation": {
    "observations": [
      {
        "kind": "fact",
        "subject": "workspace.package_manager",
        "predicate": "equals",
        "object": "pnpm",
        "confidence": 1.0,
        "source": "human"
      },
      {
        "kind": "diagnostic",
        "statement": "CI is red on main for unrelated reasons; ignore CI status.",
        "confidence": 0.9
      }
    ]
  }
}
```

Observation kinds: `fact` (subject/predicate/object triple), `diagnostic`
(free-form statement), `artifact` (an `oip.artifact/0.1` reference), and
`message` (text for a `human` or `oracle` audience).

### 6.4 Corrections

When the oracle believes something wrong, correct it — history is never
edited; the correction is a new event the oracle must weigh:

```json
{
  "update_type": "correction",
  "correction": {
    "scope": "observation",
    "target_id": "evt_00000009",
    "replacement": "The failing test is flaky, not related to the recent change.",
    "reason": "Reproduced the failure on an untouched checkout."
  }
}
```

(Wrap in the standard update envelope, as above.)

### 6.5 Overrides: taking the wheel

Overrides let a human reject, replace, force, or short-circuit the oracle's
recommendation. The most useful one is `replace` — "no, run *this* instead":

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_override_01",
  "goal_id": "goal_01",
  "recommendation_id": "rec_06",
  "created_at": "2026-07-04T19:20:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner", "authenticated": true },
  "update_type": "override",
  "override": {
    "decision": "replace",
    "reason": "This repo uses pnpm, not npm.",
    "replacement_action": {
      "action_id": "act_human_01",
      "kind": "shell",
      "title": "Run tests with pnpm",
      "shell": {
        "argv": ["pnpm", "test"],
        "cwd": "file:/home/curt/project",
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
      "max_attempts": 2
    },
    "applies_to_future_recommendations": true
  }
}
```

You must supply the same risk and idempotency metadata the oracle would have —
the executor refuses replacements without it, and it applies the same policy
checks as always (`force` bypasses the *oracle's* judgment, never the
executor's safety policy, unless local policy explicitly says otherwise).

The full decision set: `reject`, `replace`, `defer`, `mark_done`,
`mark_failed`, `mark_blocked`, `force`, `unsafe`. `mark_done` and
`mark_failed` terminate the goal and require an authenticated `owner` or
`policy_admin`.

### 6.6 Cancelling a goal

```json
{
  "schema": "oip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_cancel_01",
  "goal_id": "goal_01",
  "created_at": "2026-07-04T19:30:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner", "authenticated": true },
  "update_type": "goal_cancel",
  "goal_cancel": { "reason": "Requirements changed; abandoning this approach." }
}
```

After cancellation (or success/failure), `oracle next` refuses with
`invalid_input` — terminal goals stay terminal.

---

## 7. Wrapping other command-line tools

Every command-line tool can be driven through the oracle protocol via a
wrapper. `oracle-wrap <tool>` presents an OIP-conformant oracle whose expertise
is that one tool: you give it a goal in plain language, it issues concrete,
fully-specified invocations of the tool as `shell` actions, and it interprets
the results.

The wrapped oracle speaks exactly the same CLI, so everything in this guide
applies unchanged — same commands, same event log, same executor.

### 7.1 Example: ffmpeg without remembering ffmpeg flags

```bash
mkdir -p /work/media && cd /work/media

oracle-wrap ffmpeg goal create <<'EOF'
{
  "schema": "oip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_transcode_01",
  "created_at": "2026-07-04T20:00:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "description": "Transcode input.mov to a 1080p H.264 MP4 named output.mp4.",
  "workspace_uri": "file:/work/media"
}
EOF

# Let the executor drive the wrapped oracle
oracle-exec run --oracle "oracle-wrap ffmpeg" --goal-id goal_media_01
```

The wrapper's recommendation is an ordinary `shell` action —
`argv: ["ffmpeg", "-i", "input.mov", ...]` — subject to the same review,
policy, and audit trail as anything else. `oracle history` shows you exactly
which ffmpeg invocations ran and what they produced.

### 7.2 Example: tools that make remote requests

Wrappers work for network tools too — `curl`, `gh`, `aws`, `kubectl`, anything.
The difference is entirely in risk metadata and policy, not mechanics:

```bash
oracle-wrap curl goal create <<'EOF'
{
  "schema": "oip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_fetch_status_01",
  "created_at": "2026-07-04T20:10:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "description": "Fetch https://api.example.com/v1/status and save the JSON body as status.json.",
  "workspace_uri": "file:/work/checks"
}
EOF
```

The issued action will carry honest risk metadata, e.g.:

```json
"risk": {
  "level": "medium",
  "classes": ["network_read", "write_workspace"],
  "blast_radius": "workspace",
  "requires_approval": true,
  "network": "required",
  "secrets": "not_required"
}
```

Under the **default** executor policy, all network classes (`network_read`,
`network_write`, `external_side_effect`, `cost`) are denied for auto-execution.
So the executor will stop and status will show a `needs` entry. You have three
options, in increasing order of standing permission:

1. **Approve this one action** with an `approval` update (§6.2) — scoped,
   audited, expires with the recommendation. Best for one-offs.
2. **Run it yourself** manually (§2.4) and report the result — you are the
   policy.
3. **Loosen local executor policy** for specific risk classes/tools — a
   standing decision that belongs in your policy configuration, covered in the
   security document, not here.

The same pattern covers mutating remote calls (`gh release create`,
`aws s3 rm`, `kubectl apply`): they arrive marked `network_write` /
`external_side_effect` / possibly `irreversible`, and nothing runs until a
human explicitly says so. Actions needing credentials reference them as
`secret_ref` entries resolved through your local secret store — secret values
never appear in commands, logs, or events. (Key management: security document.)

---

## 8. Deference: oracles delegating to oracles

An oracle can defer parts of a goal to other oracles — other instances of
itself, or wrapped tools — using the same protocol. Because an oracle can never
execute anything (invariant 1), deference is visible and mediated by the
executor: **the parent oracle recommends actions that drive a sub-oracle, and
the executor runs those like any other action.** Delegation is just more
`argv`.

### 8.1 What it looks like

Say the parent goal is "cut and publish release 2.4.0". The parent oracle
knows the release notes step is really a `git` problem and the publish step is
really a `gh` problem. Its recommendations come out like:

```json
{
  "recommendation_type": "action",
  "summary": "Delegate changelog generation to the git wrapper oracle.",
  "action": {
    "action_id": "act_delegate_git_01",
    "kind": "shell",
    "title": "Run sub-goal: generate changelog since v2.3.0",
    "shell": {
      "argv": [
        "oracle-exec", "run",
        "--oracle", "oracle-wrap git",
        "--store", "/work/release/.oracle-sub/git",
        "--goal-file", "subgoals/changelog.json"
      ],
      "cwd": "file:/work/release",
      "env": { "mode": "minimal", "set": {} },
      "stdin": { "mode": "none" },
      "pty": false,
      "timeout_seconds": 900,
      "expected_exit_codes": [0],
      "requires_shell": false
    }
  },
  "risk": { "level": "low", "classes": ["read_local", "execute_local", "write_workspace"], "...": "..." }
}
```

From the executor's point of view this is one opaque command with one exit
code — it neither knows nor cares that the command is itself an
oracle/executor loop. The sub-goal gets its own store, its own event log, its
own recommendations and actions, all independently auditable:

```bash
# The parent's log shows the delegation as a single action
oracle history --goal-id goal_release_01 --since-seq 0 | jq -c '{seq, type}'

# The sub-goal's log shows what the git wrapper actually did
oracle --store /work/release/.oracle-sub/git \
  history --goal-id goal_git_01 --since-seq 0 | jq -c '{seq, type}'
```

Results flow back the ordinary way: the sub-run's output/artifacts land in the
parent's `action_result`, and the parent oracle reasons over them to pick its
next step.

### 8.2 Rules of thumb for delegation

- **Risk composes.** A delegated sub-goal's action inherits the risk of what
  the sub-oracle might do. A well-behaved parent declares the sub-goal's real
  risk envelope (a `gh` publish sub-goal is `network_write` even though
  `argv[0]` is `oracle-exec`). Policy on the *sub*-executor still applies
  independently — the inner loop enforces its own approvals, so a sneaky or
  sloppy parent cannot launder a dangerous action through delegation.
- **Timeouts bound the whole sub-run.** `timeout_seconds` on the delegating
  action caps the entire inner loop.
- **One level at a time when debugging.** If a delegated action fails, read
  the sub-store's history first — the parent only sees the summary that came
  back.
- **Deep composition works** — a sub-oracle can itself delegate — but each
  level adds a store, a policy surface, and a place to look when things go
  wrong. Prefer shallow trees.
- The oracle may also consult other oracles *privately* while thinking (its
  internal state is out of scope). You will see the effects only in
  `explanation.evidence`. Anything that touches the world, though, must
  surface as a recommendation and go through an executor.

---

## 9. Artifacts: large and binary outputs

Command output up to the inline limit (see
`oracle capabilities | jq '.result.limits'`) rides inside `action_result.output`.
Anything larger is stored as a content-addressed **artifact** under
`.oracle/goals/<goal_id>/artifacts/sha256/…` and referenced from events:

```json
{
  "schema": "oip.artifact/0.1",
  "protocol_version": "0.1",
  "artifact_id": "art_01",
  "uri": "file:.oracle/goals/goal_01/artifacts/sha256/ab/abc123...",
  "media_type": "text/plain",
  "sha256": "sha256:abc123...",
  "bytes": 15322,
  "redacted": false,
  "description": "stderr from make test"
}
```

To read one, resolve the relative URI against the workspace root:

```bash
less /home/curt/project/.oracle/goals/goal_01/artifacts/sha256/ab/abc123...
```

The executor handles storage, hashing, and truncation automatically. If you
report results by hand and the output is big, store the file at its content
address, verify the digest (`shasum -a 256`), and reference it in
`action_result.artifacts` — the oracle re-verifies the digest before accepting.
If an artifact captured a secret, use a `redaction` update with a
`replacement_artifact` to genuinely replace its bytes; event payloads, by
contrast, can never be truly redacted, which is why secrets must never enter
them in the first place.

---

## 10. When things go wrong

Every error is a JSON object with a stable `error.code` — branch on that, not
the exit code.

| `error.code` | What it means | What to do |
|---|---|---|
| `invalid_input` | Malformed/ill-formed request, unknown ID, reused idempotency key with different content, `next` on a terminal goal. | Fix the request. Check required fields against the spec. |
| `storage_conflict` | Open recommendation already exists; lease claimed by another actor; append lock contention. | For issuance: dispose or `--supersede`. For claims: someone else owns the action — leave it. For locks: retry. |
| `stale_recommendation` | The world changed after issuance; execution refused. | `oracle next --mode=issue` for a fresh one. |
| `policy_denied` | Actor lacks authority, or executor policy refused the action. | Check `actor.authority`/authentication; grant an approval; or run manually. |
| `temporary_failure` | Transient; `retryable: true`, honor `retry_after_seconds`. | Retry the same request (same `update_id`/`create_id` — replays are safe). |
| `unsupported_capability` | Feature/enum this oracle doesn't support, or events from a newer protocol version. | Check `oracle capabilities`; upgrade or avoid the feature. |
| `corrupt_event_log` | Hash chain broken, seq gap, truncated line. | Stop automation. `oracle verify`. Recover out-of-band per the spec. |
| `artifact_integrity_failed` | Artifact bytes don't match their digest. | Re-store the artifact; never reference unverified content. |
| `internal_error` | Oracle bug. | Report it; the log is still your source of truth. |

General habits that keep you out of trouble:

- **Mint idempotency keys deliberately** (`create_id`, `update_id`), keep the
  request bodies around, and retry with identical bytes. `"replayed": true`
  in a response means "this already happened; here is the original result and
  the current status" — it is confirmation, not an error.
- **Use `--request-id`** on scripted calls so you can correlate responses in
  your own logs.
- **Don't parse stderr.** Ever. Protocol state is stdout JSON only.
- **Trust the log over your memory.** `oracle history` is canonical; if your
  script's idea of state disagrees with a replay of the events, the events win.

---

## 11. Security notes (the short version)

Full treatment — authentication, key management, secret stores, policy
configuration — lives in the separate security document. The five things every
CLI user must internalize:

1. **Review `argv`, not prose.** `summary`, `title`, and
   `command_for_display` are oracle-controlled text and can misdescribe the
   command. What runs is `argv` in `cwd`. Always.
2. **Secrets never go in events.** No plaintext credentials in `env.set`
   (use `secret_ref`), never in `stdin.text`, and redact artifacts *before*
   they are hashed and submitted. The event log is immutable; a leaked secret
   in an event payload is leaked forever.
3. **Approvals are scoped and perishable.** They bind to one
   recommendation/action and die with it. There are no blanket approvals —
   standing permissions are policy configuration, a deliberate act.
4. **The trust boundary is the filesystem.** Anyone who can write the store
   can claim any identity and rewrite the chain; the hash chain detects
   accidents and tampering after the fact, it does not authenticate. Protect
   the store directory accordingly (`0700`/`0600`).
5. **Network and destructive actions don't auto-run.** The default policy
   denies `delete`, network classes, secrets access, host writes, and
   irreversible actions, and refuses `requires_shell: true` and inherited
   environments without approval. When automation stops and asks, that is the
   system working.

---

## Appendix: a pocket session

```bash
# 1. Start
GOAL=$(oracle goal create < goal.json | jq -r '.result.goal.goal_id')

# 2. Automate what's safe
oracle-exec run --goal-id "$GOAL"

# 3. See why it stopped
oracle status --goal-id "$GOAL" | jq '.result | {goal_status, reason_code, needs}'

# 4. Unblock (whichever applies)
oracle update --goal-id "$GOAL" < answer.json     # answer a question
oracle update --goal-id "$GOAL" < approval.json   # approve a risky action
oracle update --goal-id "$GOAL" < observation.json# tell it what you changed

# 5. Repeat 2-4 until:
oracle status --goal-id "$GOAL" | jq -r '.result.goal_status'
# succeeded

# 6. Audit
oracle verify  --goal-id "$GOAL" | jq '.result.ok'
oracle history --goal-id "$GOAL" --since-seq 0 | jq -c '{seq, type, actor: .actor.id}'
```
