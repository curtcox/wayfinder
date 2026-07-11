# Wayfinder CLI User Guide

**Audience:** command-line users driving a wayfinder that conforms to the
[Wayfinder Interaction Protocol v0.1](wayfinder-interaction-protocol-v0.1.md) (WIP).
**Assumes:** you are comfortable with a Unix shell, JSON, and `jq`.

This guide covers day-to-day use: creating goals, getting and acting on
recommendations, reporting results, letting the executor drive, working in
plain English through LLM front-ends, wrapping other command-line tools,
seeing what different wayfinder and executor implementations look like under the
hood, and composing wayfinders. It is not the protocol reference —
when this guide and the spec disagree, the spec wins.

**Tool names used here.** The spec defines the `wayfinder` command. The names
`wayfinder-exec` (the dumb executor), `wayfinder-wrap` (the universal CLI-tool
wrapper), and the LLM-backed prose front-ends `wayfinder-do`, `wayfinder-tell`,
`wayfinder-ask`, and `wayfinder-chat` (§8) are the console scripts shipped by
[this repository's Python package](https://github.com/curtcox/wayfinder). Security, key management, and
permission policy are covered in
[wayfinder-security.md](wayfinder-security.md); this guide only flags the places
where they matter.

Runnable copies of major sections live under `examples/` (for example
`examples/02-quickstart/run.sh --scripted`).

---

## 1. The mental model

The wayfinder is a black box that knows (or figures out) *what to do next* to
advance a goal. Conceptually, it is an oracle for next steps; operationally, it
is the `wayfinder` command and protocol. It never touches your system.
Everything that happens is a loop between three parties:

```text
you / executor:  "here is a goal"            -> wayfinder goal create
wayfinder:          "do this next"              -> wayfinder next
you / executor:  run the action (or don't)
you / executor:  "here is what happened"     -> wayfinder update
                 ...repeat until done...
```

Three things make this different from just asking a chatbot for shell commands:

1. **Everything is recorded.** Every goal has an append-only, hash-chained
   event log (`.wayfinder/goals/<goal_id>/events.ndjson`). Nothing is ever edited;
   corrections and redactions are new events. You can always audit exactly what
   was recommended, what ran, and what came back.
2. **The wayfinder never executes anything.** You do, or a deliberately dumb
   executor does. The wayfinder only appends events as the defined result of the
   commands you run.
3. **Structured fields are law, prose is advisory.** A recommendation's
   `summary` or `title` can say anything; only `action.shell.argv` is what
   actually runs. When reviewing an action, always read `argv` and `cwd`, never
   just the summary. (This is a spec requirement for approval UIs, and a good
   habit for humans.)

### Command cheat sheet

```bash
wayfinder capabilities                              # what this wayfinder supports
wayfinder goal create < goal.json                   # start a goal
wayfinder status --goal-id GOAL                     # where things stand
wayfinder next --goal-id GOAL --mode=preview        # peek, no commitment
wayfinder next --goal-id GOAL --mode=issue          # get an executable recommendation
wayfinder update --goal-id GOAL < update.json       # report anything back
wayfinder history --goal-id GOAL --since-seq 0      # the full event log (JSONL)
wayfinder explain --goal-id GOAL --recommendation-id REC   # why it recommended that
wayfinder verify --goal-id GOAL                     # check log + artifact integrity
```

And the LLM-backed prose front-ends (illustrative names, §8), for when you
would rather type a sentence than compose JSON:

```bash
wayfinder-do "…a goal in plain language…"           # create a goal and drive it
wayfinder-tell --goal-id GOAL "…anything…"          # prose -> the right structured update
wayfinder-ask  --goal-id GOAL "…a question…"        # prose answers from the log (read-only)
wayfinder-chat --goal-id GOAL                       # interactive session over the same verbs
```

Every non-history command prints exactly one JSON object on stdout — a
`wip.response/0.1` envelope on success, a `wip.error/0.1` object on failure.
`stderr` is for humans only. Exit code 0 means "the command worked", **not**
"the goal/action succeeded" — success and failure of goals and actions live in
the JSON.

Useful reflex: pipe everything through `jq`.

```bash
wayfinder status --goal-id goal_01 | jq '.result | {goal_status, open_recommendation_id, needs}'
```

---

## 2. Quick start: a complete session by hand

This walkthrough drives one goal to completion manually. In practice you will
usually let `wayfinder-exec` do steps 3–5 (see §4), or skip the JSON entirely and
work in prose (§8) — but doing it by hand once teaches you what those tools do
on your behalf.

### 2.1 Create a goal

Goal creation takes a JSON document on stdin. `create_id` is your idempotency
key: if the command times out and you rerun it with the same body, you get the
same goal back (`"replayed": true`) instead of a duplicate.

```bash
wayfinder goal create --request-id req_create_1 <<'EOF'
{
  "schema": "wip.goal_create/0.1",
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
  directory. The wayfinder store lives at `.wayfinder/` under it by default.
- `actor` is who you are. `authority` matters later: cancelling a goal or
  marking it done/failed requires an *authenticated* `owner` or
  `policy_admin`. (How authentication is established is local policy — see the
  (How authentication is established is local policy — see
  [wayfinder-security.md](wayfinder-security.md).)

Capture the goal ID:

```bash
GOAL=$(wayfinder goal create < goal.json | jq -r '.result.goal.goal_id')
```

### 2.2 Peek before committing: preview

```bash
wayfinder next --goal-id "$GOAL" --mode=preview --explain=summary | jq '.result'
```

Preview appends **nothing** to the log and the returned recommendation is
marked `"executable": false`. Use it to see what the wayfinder is thinking, sanity-
check `argv`, or evaluate policy — then throw it away. A previewed
`recommendation_id` cannot be executed or `explain`ed later; you must issue.

### 2.3 Issue a recommendation

```bash
wayfinder next --goal-id "$GOAL" --mode=issue --explain=structured > next.json
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
wayfinder history --goal-id "$GOAL" --since-seq 0 \
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
wayfinder update --goal-id "$GOAL" <<'EOF'
{
  "schema": "wip.update/0.1",
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
wayfinder update --goal-id "$GOAL" <<'EOF'
{
  "schema": "wip.update/0.1",
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
`action_started` or `action_result`; the wayfinder appends both events
atomically.)

**Report the result** — success here, but the shape is identical for failure;
just change `status` and the process fields:

```bash
wayfinder update --goal-id "$GOAL" <<'EOF'
{
  "schema": "wip.update/0.1",
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
  *successful protocol exchange* — `wayfinder update` exits 0 and the failure
  lives in JSON.
- `changed` records whether the world was mutated (`yes`/`no`/`partial`/
  `unknown`), independently of success. `make test` succeeded but changed
  nothing.
- Once an action has any terminal event, it is **never re-executed**. Retries
  happen only when the wayfinder issues a *new* recommendation.

### 2.5 Loop until done

```bash
wayfinder next --goal-id "$GOAL" --mode=issue | jq '.result'
```

When the wayfinder believes the goal is complete it returns a non-executable
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
wayfinder atomically appends `recommendation.accepted` + `goal.completed`):

```bash
wayfinder update --goal-id "$GOAL" <<'EOF'
{
  "schema": "wip.update/0.1",
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

wayfinder status --goal-id "$GOAL" | jq -r '.result.goal_status'
# succeeded
```

---

## 3. Reading recommendations: the six types

`wayfinder next` returns exactly one of six types. Only `action` is executable;
the other five carry their payload in an object named after the type.

| Type | Meaning | What you do |
|---|---|---|
| `action` | Run this one structured action. | Review `argv`/`risk`, accept, run, report. |
| `question` | The wayfinder needs information. | Answer with a `question_answer` update (§6.1). |
| `wait` | Nothing useful until `wait.until_time`. | Sleep, then `wayfinder next` again. |
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
  `stale_recommendation`. Just run `wayfinder next --mode=issue` again.
- The **first actor to accept claims the lease.** A second actor trying to
  start the same action gets `storage_conflict`. This is how two executors on
  the same store avoid double-running a command.
- Past `expires_at`, execution is refused, but status still shows the
  recommendation as open until an event clears it. Any actor may submit a
  `recommendation_disposition` of `expired` to tidy up (the wayfinder checks its
  own clock before accepting it).
- **Once started, always reportable.** If your action already ran, the wayfinder
  must accept your terminal `action_result` even if the recommendation was
  invalidated mid-flight. Results of things that really happened always land.

### 3.2 Only one open recommendation at a time

v0.1 has no parallelism. If an executable recommendation is open and you ask
for another, `wayfinder next --mode=issue` fails with `storage_conflict`. Your
options:

```bash
# Option A: dispose of the open one (reject it with a reason)
# Option B: atomically supersede it with a fresh one:
wayfinder next --goal-id "$GOAL" --mode=issue --supersede | jq '.result'
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
wayfinder-exec run --goal-id "$GOAL"

# See what it WOULD do without executing anything (uses --mode=preview)
wayfinder-exec dry-run --goal-id "$GOAL"
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

A common working style: run `wayfinder-exec` until it stops, look at
`wayfinder status` and the last few history events to see why, unblock (answer a
question, grant an approval, record an observation, fix the environment), and
run it again.

```bash
wayfinder status --goal-id "$GOAL" | jq '.result | {goal_status, reason_code, needs}'
wayfinder history --goal-id "$GOAL" --since-seq 0 | tail -5 | jq -c '{seq, type}'
```

---

## 5. Watching and auditing: status, history, explain, verify

These four commands are the raw feed. If you would rather ask "why is this
stuck?" in English and get an answer synthesized from them, see `wayfinder-ask`
(§8.3) — it is built on exactly these reads.

### 5.1 Status

```bash
wayfinder status --goal-id "$GOAL" | jq '.result'
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
wayfinder history --goal-id "$GOAL" --since-seq 0

# Incremental polling: remember the last seq you saw
wayfinder history --goal-id "$GOAL" --since-seq 12

# Compact life story of the goal
wayfinder history --goal-id "$GOAL" --since-seq 0 \
  | jq -c '{seq, type, actor: .actor.id, subject}'

# What did action act_03 actually output?
wayfinder history --goal-id "$GOAL" --since-seq 0 \
  | jq 'select(.type | startswith("action.")) | select(.data.action_id == "act_03") | .data.action_result.output'
```

A nonzero exit from `history` means the stream may be incomplete — treat what
you received as a prefix and verify before relying on it.

### 5.3 Explain

```bash
wayfinder explain --goal-id "$GOAL" --recommendation-id rec_04 | jq '.result.explanation'
```

Returns the wayfinder's reasoning (`summary`, `evidence` pointing at event IDs,
`redactions`) for any recommendation that was actually issued. Explanations
are advisory — useful for humans, never a basis for executor behavior.
Preview-only recommendations cannot be explained after the fact.

### 5.4 Verify

```bash
wayfinder verify --goal-id "$GOAL" | jq '.result | {ok, problems}'
```

Checks the hash chain and artifact digests. Run it whenever something smells
off, before archiving a goal, or after any abnormal shutdown. `ok: false`
lists problems (`hash_mismatch`, `truncated_line`, `artifact_missing`, …) with
exit 0; nonzero exit is reserved for the tool itself failing. A corrupt log
stops all automatic execution — recovery is out-of-band and deliberately
conservative (only a partial final line may ever be truncated, after backup).

---

## 6. Talking back to the wayfinder

`wayfinder update` is the single door for everything you want the wayfinder to know.
Beyond dispositions and action results, the update types you will actually use
are below. (Each of these can also be produced from a plain-English sentence
via `wayfinder-tell` — see §8.2 — which composes exactly these documents.)

### 6.1 Answering questions

```json
{
  "schema": "wip.update/0.1",
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
  "schema": "wip.update/0.1",
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
wayfinder history --goal-id "$GOAL" --since-seq 0 \
  | jq 'select(.type=="recommendation.issued") | select(.data.recommendation.recommendation_id=="rec_04")
        | .data.recommendation.action.shell | {argv, cwd, env}'
```

(§8.4 shows how an LLM front-end can make this review faster without taking
the decision away from you.)

### 6.3 Observations: telling the wayfinder what you know

Observations are how new facts enter the wayfinder's world — things you did
outside the loop, things you noticed, artifacts you produced. By default an
observation invalidates the open recommendation (the wayfinder should rethink);
set `invalidates_open_recommendations: false` for purely informational notes.

```json
{
  "schema": "wip.update/0.1",
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
(free-form statement), `artifact` (a `wip.artifact/0.1` reference), and
`message` (text for a `human` or `wayfinder` audience).

### 6.4 Corrections

When the wayfinder believes something wrong, correct it — history is never
edited; the correction is a new event the wayfinder must weigh:

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

Overrides let a human reject, replace, force, or short-circuit the wayfinder's
recommendation. The most useful one is `replace` — "no, run *this* instead":

```json
{
  "schema": "wip.update/0.1",
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

You must supply the same risk and idempotency metadata the wayfinder would have —
the executor refuses replacements without it, and it applies the same policy
checks as always (`force` bypasses the *wayfinder's* judgment, never the
executor's safety policy, unless local policy explicitly says otherwise).

The full decision set: `reject`, `replace`, `defer`, `mark_done`,
`mark_failed`, `mark_blocked`, `force`, `unsafe`. `mark_done` and
`mark_failed` terminate the goal and require an authenticated `owner` or
`policy_admin`.

### 6.6 Cancelling a goal

```json
{
  "schema": "wip.update/0.1",
  "protocol_version": "0.1",
  "update_id": "upd_cancel_01",
  "goal_id": "goal_01",
  "created_at": "2026-07-04T19:30:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner", "authenticated": true },
  "update_type": "goal_cancel",
  "goal_cancel": { "reason": "Requirements changed; abandoning this approach." }
}
```

After cancellation (or success/failure), `wayfinder next` refuses with
`invalid_input` — terminal goals stay terminal.

---

## 7. Wrapping other command-line tools

Every command-line tool can be driven through the wayfinder protocol via a
wrapper. `wayfinder-wrap <tool>` presents a WIP-conformant wayfinder whose expertise
is that one tool: you give it a goal in plain language, it issues concrete,
fully-specified invocations of the tool as `shell` actions, and it interprets
the results.

The wrapped wayfinder speaks exactly the same CLI, so everything in this guide
applies unchanged — same commands, same event log, same executor.

### 7.1 Example: ffmpeg without remembering ffmpeg flags

```bash
mkdir -p /work/media && cd /work/media

wayfinder-wrap ffmpeg goal create <<'EOF'
{
  "schema": "wip.goal_create/0.1",
  "protocol_version": "0.1",
  "create_id": "create_transcode_01",
  "created_at": "2026-07-04T20:00:00Z",
  "actor": { "type": "human", "id": "curt", "authority": "owner" },
  "description": "Transcode input.mov to a 1080p H.264 MP4 named output.mp4.",
  "workspace_uri": "file:/work/media"
}
EOF

# Let the executor drive the wrapped wayfinder
wayfinder-exec run --wayfinder "wayfinder-wrap ffmpeg" --goal-id goal_media_01
```

The wrapper's recommendation is an ordinary `shell` action —
`argv: ["ffmpeg", "-i", "input.mov", ...]` — subject to the same review,
policy, and audit trail as anything else. `wayfinder history` shows you exactly
which ffmpeg invocations ran and what they produced.

### 7.2 Example: tools that make remote requests

Wrappers work for network tools too — `curl`, `gh`, `aws`, `kubectl`, anything.
The difference is entirely in risk metadata and policy, not mechanics:

```bash
wayfinder-wrap curl goal create <<'EOF'
{
  "schema": "wip.goal_create/0.1",
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
   [wayfinder-security.md](wayfinder-security.md), not here.

The same pattern covers mutating remote calls (`gh release create`,
`aws s3 rm`, `kubectl apply`): they arrive marked `network_write` /
`external_side_effect` / possibly `irreversible`, and nothing runs until a
human explicitly says so. Actions needing credentials reference them as
`secret_ref` entries resolved through your local secret store — secret values
never appear in commands, logs, or events. (Key management:
[wayfinder-security.md](wayfinder-security.md).)

---

## 8. Working in prose: LLM front-ends

Everything so far has you (or the executor) reading and writing JSON. A prose
front-end puts an LLM between you and that JSON: you type ordinary — but
precise — English, and the front-end composes the protocol traffic.
`wayfinder-do`, `wayfinder-tell`, `wayfinder-ask`, and `wayfinder-chat` are illustrative
names, like `wayfinder-exec`.

Three facts keep this from being magic:

1. **The front-end is on your side of the boundary, not the wayfinder's.** It is
   just another actor speaking the same protocol: it composes
   `wip.goal_create` and `wip.update` documents, calls `wayfinder` and
   `wayfinder-exec`, and does the bookkeeping that makes hand-written updates
   tedious (`update_id`s, `issued_event_seq`/`event_hash`, timestamps). It has
   no special powers — executor policy gates it exactly as it gates you.
2. **Prose goes in, structure comes out, and only the structure is real.**
   Your sentence is translated into structured fields, and those fields are
   what runs and what the log records. The convenience costs nothing in
   auditability: `wayfinder history` shows exactly what was submitted on your
   behalf, byte for byte.
3. **Submitted immediately, audited after.** These front-ends do not stop to
   show you the derived JSON before sending it — the guardrails that matter
   (executor policy, scoped approvals, the immutable log) are downstream and
   unaffected by phrasing. When a translation misses, you fix it the protocol
   way, with a correction event, not an edit. A confirm-before-submit
   front-end is a legitimate alternative configuration; it changes the feel,
   not the protocol.

### 8.1 Goals from a sentence: wayfinder-do

```bash
wayfinder-do "Make the tests in /home/curt/project pass. Don't modify anything
outside the repo, and don't auto-run anything above low risk."
```

The front-end composes the `wip.goal_create` document — the intent becomes
`description`, the named path becomes an absolute `workspace_uri`, and the
risk limit becomes `policy.max_auto_risk_level: "low"` — then runs the
executor loop and narrates:

```text
goal_01 created  (workspace file:/home/curt/project, max auto risk: low)
rec_01  make test                    ran, exit 2 (3 failures in test_parser.py)
rec_02  patch src/parser.py          ran, changed 1 file
rec_03  make test                    ran, exit 0
rec_04  done: "make test exits 0"    accepted
goal_01 succeeded  (4 recommendations, 2m40s)
```

Notice the split it made. "Make the tests pass" is advisory prose in
`description`, shaping what the wayfinder recommends. "Nothing above low risk" is
a hard limit, so it belongs in `policy`, where it is enforced mechanically.
When your prose contains a hard limit, verify it landed in a structured field:

```bash
wayfinder history --goal-id goal_01 --since-seq 0 \
  | jq 'select(.type=="goal.created") | .data.goal | {description, policy}'
```

Wrapped tools (§7) compose naturally — this is the ffmpeg example with the
JSON boilerplate gone:

```bash
cd /work/media
wayfinder-do --wayfinder "wayfinder-wrap ffmpeg" \
  "Convert every .mov in this directory to a 1080p H.264 .mp4 with the same
   basename. Skip any that already have an .mp4. Keep the originals."
```

Every qualifier in that sentence does work: "same basename" pins the output
naming, "skip any that already have" makes reruns cheap, "keep the originals"
rules out cleanup-happy recommendations. That is what "ordinary albeit
precise" means in practice.

### 8.2 Prose in, updates out: wayfinder-tell

One command for everything in §6. It reads the goal's current state (open
recommendation, open question), classifies your sentence into the right
`update_type`, fills in the envelope, and submits.

```bash
# Open question was "npm or pnpm?"          -> question_answer
wayfinder-tell --goal-id "$GOAL" "pnpm"

# Something you did outside the loop        -> observation (invalidates open rec)
wayfinder-tell --goal-id "$GOAL" "I bumped the version in package.json to 2.4.0 by hand just now."

# Purely informational                      -> observation, invalidates_open_recommendations: false
wayfinder-tell --goal-id "$GOAL" "FYI, CI is red on main for unrelated reasons — this doesn't change what you should do next."

# The wayfinder believes something wrong       -> correction
wayfinder-tell --goal-id "$GOAL" "That parser test isn't failing because of our change; it fails on a clean checkout too."

# "No, run this instead"                    -> override.replace, metadata drafted for you
wayfinder-tell --goal-id "$GOAL" "Don't use npm test — this repo uses pnpm, run pnpm test instead."
```

Each invocation prints one line saying what it recorded:

```text
recorded observation upd_9f2c1a (invalidates rec_06; the wayfinder will rethink)
```

The audit reflex, since nothing was shown before submission — read back what
was actually said on your behalf:

```bash
wayfinder history --goal-id "$GOAL" --since-seq 0 | tail -2 | jq -c '{seq, type, data}'
```

Notes that matter:

- The front-end infers invalidation intent from wording ("FYI", "for context"
  vs. "I changed…"). When the difference matters, say it explicitly, as in the
  third example.
- Authority is not negotiable in prose. `wayfinder-tell "cancel this goal,
  requirements changed"` composes a perfectly good `goal_cancel`, but it lands
  only if you are an authenticated `owner`/`policy_admin` — a front-end cannot
  manufacture authority (§13).
- An override with a replacement action requires the full risk and idempotency
  metadata (§6.5). The front-end drafts it from what it knows about the
  command, and the executor policy-checks the replacement like anything else —
  a drafted `risk.level: "low"` on something that touches the network still
  gets stopped.

### 8.3 Questions about the goal: wayfinder-ask

Pure reads. `wayfinder-ask` synthesizes an answer from `status`, `history`,
`explain`, and `verify` — it never appends events.

```bash
wayfinder-ask --goal-id "$GOAL" "why is this stuck?"
```

```text
Blocked on an approval: rec_04 wants to run
    ["gh", "release", "create", "v2.4.0", "--notes-file", "notes.md"]
in file:/work/release (network_write, requires_approval), issued 19:04 [seq 14].
Nothing has executed since act_03 finished at 19:02 [seq 13].
Your options: approve rec_04, run it yourself and report, or reject it with a reason.
```

```bash
wayfinder-ask --goal-id "$GOAL" "what has actually changed on disk so far?"
wayfinder-ask --goal-id "$GOAL" "summarize the last hour as a timeline"
wayfinder-ask --goal-id "$GOAL" "did anything fail, and was it retried?"
wayfinder-ask "which of my goals are waiting on me right now?"     # store-wide
```

Answers cite event `seq` numbers so every claim is checkable. The standing
rule from §5.3 applies with full force: synthesized prose is advisory, the
log is canonical. When an answer is load-bearing, follow the citation.

### 8.4 Review help before you approve

The hard rule (§13) is: review `argv`, not prose. An LLM does not change who
decides — it changes how fast you can understand what you are deciding about.

```bash
wayfinder-ask --goal-id "$GOAL" --recommendation rec_04 \
  "walk me through exactly what this will do and what the worst case is"
```

```text
rec_04 runs, in file:/work/release:

    gh release create v2.4.0 --notes-file notes.md

• gh release create — creates a PUBLIC release on the repo's origin remote
• v2.4.0            — this tag exists locally but has not been pushed [seq 11]
• --notes-file      — notes.md, 41 lines, last written by act_03 [seq 13]

Risk as declared: network_write, irreversible; requires_approval. Worst case:
publishes to the wrong repository (if origin is not what you think) or with
stale notes. This system cannot undo a published release.
Before approving, check: git remote get-url origin; read notes.md.
```

Then the decision is yours — in prose, if you like:

```bash
wayfinder-tell --goal-id "$GOAL" "approve rec_04 — I read the argv and notes.md; publishing is intended."
```

That becomes an `approval` update with `decision: "granted"` and your sentence
as the `reason`, scoped to rec_04 and perishable as always (§6.2).

The one thing the front-end will not relieve you of: it quotes `argv` verbatim
in every review precisely so that you have seen it. If the explanation and the
`argv` ever seem to disagree, the `argv` is the truth and the explanation is a
bug.

### 8.5 Interactive: wayfinder-chat

The same verbs, held in a conversation with context. Every turn is ordinary
protocol traffic underneath — the transcript is a view; the event log is the
record.

```text
$ wayfinder-chat --goal-id goal_rel_01
chat: goal_rel_01 is blocked — rec_04 needs approval
      (gh release create v2.4.0; network_write, irreversible).

you>  what's in the notes file it wants to publish?
chat: notes.md, 41 lines, written by act_03 [seq 13]. It still contains the
      template placeholder "TODO: highlights" at line 5.

you>  have it fix that first, then ask me about the release again
chat: recorded observation upd_31c9 (invalidates rec_04)              [seq 15]
      rec_05: replace the TODO placeholder in notes.md — low risk, executed
                                                                   [seq 16-18]
      rec_06: gh release create v2.4.0 … — needs approval (fresh
      recommendation, so a fresh approval; the old one died with rec_04)

you>  show me what it changed
chat: act_05 replaced line 5 with three bullets summarizing the changes in
      seq 4-12: parser fixes, the pnpm migration, and the CLI --json flag.

you>  approve it
chat: approval granted on rec_06 [seq 20]. Executing…
      released: https://github.com/acme/thing/releases/tag/v2.4.0
      goal_rel_01 succeeded.
```

Worth noticing: the approval did **not** carry over from rec_04 to rec_06.
Approvals bind to one recommendation and die with it (§6.2); no front-end
changes that, however conversational the surface.

### 8.6 Habits for prose work

- **Precision is still your job.** "Clean up the old releases" and "delete
  releases older than v2.0.0, keeping the latest three" are different goals.
  Ambiguous prose costs a round-trip — the front-end asks, or the wayfinder
  issues a `question` recommendation.
- **Hard limits go in structured fields.** Prose in `description` shapes the
  wayfinder's recommendations; it does not bind them. If you say "never touch
  prod", also make sure it is policy — `wayfinder-do` maps limit-shaped phrases
  to `policy`, and §8.1 shows how to verify that it did.
- **Keep an audit rhythm.** End a prose session with
  `wayfinder-ask "what was submitted on my behalf today?"` — or go straight to
  `wayfinder history | jq` if you would rather not have the LLM grade its own
  homework.
- **Corrections, not edits.** A mistranslated update is repaired the protocol
  way: `wayfinder-tell --goal-id "$GOAL" "that last observation is wrong — I
  bumped the version to 2.4.1, not 2.4.0"` appends a correction event; the
  original stays in the log, as always.
- **The floor does not move.** No phrasing — yours or the front-end's — makes
  the executor auto-run what policy denies. When the conversation stops and
  asks for an approval, that is the system working (§13).

---

## 9. Under the hood: ten machines, one protocol

Nothing in this guide depends on *how* the wayfinder thinks or *how* the executor
runs things. Those are the two replaceable black boxes; the protocol between
them is the fixed part. This section makes that concrete with ten very
different implementations — a to-do list, a classical planner, build graphs,
issue trackers, a behavior tree, a workflow engine, configuration management,
terminal automation, a coding agent, and a remote browser. All tool names are
illustrative, like `wayfinder-exec`. You drive every one of them with exactly the
commands you already know; what changes is what `wayfinder next` is doing when it
decides, and what the executor is doing when it runs.

The recurring pattern to notice: the technology supplies the *judgment* or the
*muscle*, and the protocol supplies everything you have relied on so far — the
log, the leases, the approvals, argv-is-law.

### 9.1 Taskwarrior: urgency math as the wayfinder

A wayfinder does not have to be clever to be useful. `wayfinder-tw` keeps a
Taskwarrior database per goal: creating the goal seeds tasks (with due dates,
priorities, and `depends:` edges parsed from your prose), and `wayfinder next` is
little more than `task ready limit:1` — Taskwarrior's own urgency calculation
over priority, deadlines, dependencies, and age picks the step.

```bash
wayfinder-do --wayfinder wayfinder-tw "Prepare the quarterly compliance evidence:
export the access logs, run the audit script on them, and file the report by
Friday. The audit script can't run until the export exists."
```

```text
goal_q3_01 created (3 tasks seeded; 1 dependency)
rec_01  export access logs      urgency 8.1              ran, exit 0
rec_02  run audit script        (unblocked by rec_01)    ran, exit 0
rec_03  file the report         due:friday, urgency 9.4  needs approval (network_write)
```

Tasks whose annotations carry an `argv` become `shell` actions; tasks with no
runnable annotation surface as `question` recommendations for a human to
handle. `action_result` completes tasks, observations add or modify them, and
`wayfinder explain` prints the urgency arithmetic. The `depends:` edge is why
rec_02 waited for rec_01 — decades of to-do-list pragmatics doing the
scheduling, with the event log and executor policy unchanged around it.

### 9.2 A PDDL planner: next steps you can prove

`wayfinder-plan` compiles the goal into a PDDL problem against a domain file
written by whoever operates the wayfinder, runs a classical planner (Fast
Downward, for instance), and then deals the resulting plan out one step per
`wayfinder next`.

```bash
wayfinder-do --wayfinder "wayfinder-plan --domain cluster-maintenance.pddl" \
  "Upgrade node3 to kernel 6.9 without ever having fewer than two nodes serving."
```

The "never fewer than two serving" clause does not stay prose: it becomes an
invariant in the planner's model, so every plan the wayfinder can issue maintains
it by construction — drain node3 only after node4 is back in rotation, and so
on. `wayfinder explain` returns the plan with each step's preconditions. When an
action fails, or an observation contradicts the model ("node4 is actually down
for a disk swap"), the wayfinder replans from the new state and the next
recommendation comes from the new plan.

The trade is explicit: this wayfinder cannot improvise. Prose that does not map
onto domain predicates gets a `question` or a `blocked` recommendation rather
than a guess. For goals where "provably reaches the goal in the model" beats
"plausibly helpful", that rigidity is the feature.

### 9.3 Make, Bazel, Ninja: the dependency graph decides

"What should happen next" is the question a build system answers on every run,
so `wayfinder-make` barely has to think: the goal is "this target is up to date",
`wayfinder next` walks the out-of-date subgraph and issues the first ready recipe
as a `shell` action, a `completed` result marks the node fresh, and the goal is
done when nothing is stale.

```bash
wayfinder-do --wayfinder "wayfinder-make dist/report.pdf" \
  "Bring the quarterly report up to date."
```

Preview mode maps to a dry run (`make -n`, `ninja -n`, `bazel build --nobuild`)
— the wayfinder can always show you the remaining work without running any of it.
An observation that a source file changed dirties that node and everything
downstream, which is exactly the staleness semantics of §3.1 expressed as
mtimes. Bazel and Ninja give the same shape with better graphs; Bazel's
hermetic, sandboxed actions pair especially honestly with risk metadata, since
`blast_radius: "workspace"` is enforced rather than asserted.

### 9.4 GitHub Issues, Jira, Linear: the goal your team can see

Here the tracker is not the brain — it is a live mirror, so people without a
shell can participate. A bridge process (`wayfinder-bridge gh`, say) maps goal
events onto an issue as they append: `goal.created` opens the issue, each
issued recommendation is a comment, `needs` entries become labels, and
`goal.completed` closes it. Traffic flows the other way too: a comment from an
authorized teammate becomes an `approval` or `observation` update.

```text
#412  Rotate the staging TLS certificates              [wayfinder:goal_cert_01]
  bot>    rec_03 wants to run: certbot renew --cert-name staging.acme.dev
          risk: network_write, requires approval           +label needs-approval
  jsmith> approved — renewal window confirmed with the platform team
  bot>    approval recorded [seq 17]; executed, exit 0, changed: yes
                                                           -label needs-approval
```

Two things keep this honest. Identity: the bridge maps commenter accounts to
actors through an explicit allowlist, so a drive-by comment cannot become an
approval — authority is checked the same way it always is (§6.2). And
precedence: the event log is canonical and the issue is a view; if they ever
disagree, the log wins and the bridge re-renders. Jira and Linear bridges are
the same design with a different API underneath.

### 9.5 A behavior tree engine: standing goals that never finish

Some goals are not "reach a state" but "keep a state" — the shape behavior
trees were built for. `wayfinder-bt` ticks a tree against a blackboard hydrated
from the event log: condition nodes read recent observations and action
results, action leaves emit `action` recommendations, and a node still in
progress comes back as `wait`.

```bash
wayfinder-do --wayfinder "wayfinder-bt --tree staging-health.bt" \
  "Keep the staging environment healthy until further notice."
```

```text
rec_01  wait until 21:10          (all checks green)
rec_02  probe /healthz            ran, exit 7   (condition failed)
rec_03  restart app service       ran, exit 0   (fallback, step 1)
rec_04  probe /healthz            ran, exit 0   (recovered)
rec_05  wait until 21:25
```

The remediation ladder is a fallback node: restart the service, then clear the
cache, then escalate — and "escalate" is just a `question` recommendation
addressed to a human. The tree never reaches `done` on its own; you end the
goal with a `goal_cancel` (§6.6) when the standing order expires. Every tick's
decision is reconstructible from the log, which is more than most monitoring
systems can say.

### 9.6 Temporal: the executor that cannot forget

Everything in §4 about the dumb executor's crash behavior — report before
re-execute, replay updates with the same `update_id` — is a durability
contract, and durability contracts are what Temporal sells. `wayfinder-exec` can
be implemented as a Temporal workflow: each issue/accept/execute/report cycle
is a set of activities, timeouts and retries map onto Temporal's primitives,
and workflow state carries the `update_id`s.

```bash
wayfinder-exec-temporal run --goal-id "$GOAL"    # same loop, durable
```

Kill the worker mid-`make test` and the replacement resumes the workflow,
checks whether the command completed, and submits the pending `action_result`
with byte-identical content — the wayfinder answers `"replayed": true` if the
original landed, and nothing runs twice. The §3.1 rule "once started, always
reportable" stops being a discipline you maintain and becomes a property the
engine enforces.

One caution: this setup has two histories. Temporal's workflow history is
machinery — private, replayable, disposable. The `.wayfinder` event log is the
record. `wayfinder verify` audits the one that matters.

### 9.7 Ansible: idempotency as the native tongue

`wayfinder-wrap ansible` (§7 mechanics, unchanged) issues `ansible-playbook`
invocations — and the fit is unusually good, because Ansible already speaks
this guide's dialect. Check mode is preview:

```bash
wayfinder-do --wayfinder "wayfinder-wrap ansible" \
  "Make sure the three web hosts in inventory/prod.ini run nginx 1.26 with
   the hardened config and logrotate. Show me what would change first."
```

"Show me what would change first" becomes a first recommendation running
`ansible-playbook --check --diff` — a dry run against the real hosts (still
`network_read`, so still policy-gated). The apply run arrives as
`network_write` / `external_side_effect` and waits for approval under default
policy. Ansible's `changed=N` output maps directly onto `action_result.changed`
(`no` when the run reports `changed=0`, `partial` when it changed some hosts
and failed on others), and module idempotency is what lets the wrapper declare
`safe_to_run_if_already_done: true` honestly — a timed-out apply can be
re-recommended and re-run cheaply, because converging twice is a no-op.

### 9.8 Expect / pexpect: commands that talk back

Some commands refuse to be batch: installers that prompt, vendor CLIs whose
login flow has no non-interactive mode. These arrive as actions with
`pty: true`, plus an executor extension the pexpect-based executor
understands — a declared dialogue table:

```json
"shell": {
  "argv": ["vendor-cli", "login"],
  "pty": true,
  "x_expect_dialogue": [
    { "expect": "Username:", "send": "svc-deploy" },
    { "expect": "Password:", "send_secret_ref": "vendor/svc-deploy" },
    { "expect": "Session established", "then": "eof" }
  ]
}
```

The review rule of §13 extends one level down: the dialogue table is part of
the structured action, so read it like you read `argv` — it says exactly what
will be typed at the child process, before it happens. The executor drives the
pty with pexpect, and the full transcript lands as an artifact (§11) so the
interaction is auditable afterwards. The password never appears anywhere: it
is a `secret_ref` resolved at send time, and the transcript artifact is
redacted before it is hashed and submitted, per §13's rules.

### 9.9 Codex: a coding agent with the hands taken off

An agentic coding tool normally proposes commands *and runs them*.
`wayfinder-codex` keeps the judgment and removes the hands: the agent session is
hydrated from the event log, and every command the agent tries to execute is
intercepted and issued as an `action` recommendation instead. The
`action_result` you (or the executor) report back is fed to the agent as if
its tool call had run.

```bash
wayfinder-do --wayfinder wayfinder-codex \
  "Find and fix the memory leak the soak test keeps hitting."
```

The effect is frontier-model judgment under WIP execution discipline. The
agent literally cannot touch the system — its proposals become `argv` that
policy gates, humans approve, and the log records, like anything else. Its
stated reasoning surfaces through `wayfinder explain`.

Note the symmetry with §8: there, the LLM sits on *your* side of the boundary,
turning prose into protocol traffic; here it sits inside the wayfinder box,
deciding what to recommend. The two compose — `wayfinder-do` in front,
`wayfinder-codex` behind — and the protocol between them is what keeps either
LLM from quietly exceeding its station.

### 9.10 Browserbase: actions that click instead of exec

For tasks whose only interface is a web page, `wayfinder-web` issues actions that
drive a remote browser session on Browserbase:

```bash
wayfinder-do --wayfinder wayfinder-web \
  "Download June's invoice PDF from the vendor billing portal into ./invoices."
```

The recommendation's `argv` invokes a browser runner whose input is a
structured step script — navigate, fill selector, click, await download —
carried in the action itself rather than referenced on disk, so the event log
contains exactly what the browser was told to do. Reviewing this action means
reading the steps the way you read `argv`: the same structured-fields-are-law
rule, one level down. Portal credentials are `secret_ref`s resolved into the
remote session, never into the log.

Risk metadata is honest about the medium: `network_read` for pure retrieval,
`external_side_effect` the moment a click mutates anything on the far side —
so under default policy a form submission waits for approval just like
`gh release create` did. Two artifacts come back: the downloaded PDF, and the
Browserbase session recording — a video of what actually happened, attached to
the log.

### 9.11 What varies, what never does

Across all ten: what varied is how "what next" gets computed — urgency math,
plan search, dependency graphs, tree ticks, model inference — and what "run
it" means — a fork/exec, a durable workflow, SSH to a fleet, a pty dialogue, a
remote browser. What never varied: one open recommendation at a time, results
always land, approvals scoped and perishable, structured fields as the only
truth, the log as the record. That is the practical meaning of the protocol
being the product: pick the machinery per problem, keep the habits — and the
audit trail — identical.

---

## 10. Deference: wayfinders delegating to wayfinders

A wayfinder can defer parts of a goal to other wayfinders — other instances of
itself, or wrapped tools — using the same protocol. Because a wayfinder can never
execute anything (invariant 1), deference is visible and mediated by the
executor: **the parent wayfinder recommends actions that drive a sub-wayfinder, and
the executor runs those like any other action.** Delegation is just more
`argv`.

### 10.1 What it looks like

Say the parent goal is "cut and publish release 2.4.0". The parent wayfinder
knows the release notes step is really a `git` problem and the publish step is
really a `gh` problem. Its recommendations come out like:

```json
{
  "recommendation_type": "action",
  "summary": "Delegate changelog generation to the git wrapper wayfinder.",
  "action": {
    "action_id": "act_delegate_git_01",
    "kind": "shell",
    "title": "Run sub-goal: generate changelog since v2.3.0",
    "shell": {
      "argv": [
        "wayfinder-exec", "run",
        "--wayfinder", "wayfinder-wrap git",
        "--store", "/work/release/.wayfinder-sub/git",
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
wayfinder/executor loop. The sub-goal gets its own store, its own event log, its
own recommendations and actions, all independently auditable:

```bash
# The parent's log shows the delegation as a single action
wayfinder history --goal-id goal_release_01 --since-seq 0 | jq -c '{seq, type}'

# The sub-goal's log shows what the git wrapper actually did
wayfinder --store /work/release/.wayfinder-sub/git \
  history --goal-id goal_git_01 --since-seq 0 | jq -c '{seq, type}'
```

Results flow back the ordinary way: the sub-run's output/artifacts land in the
parent's `action_result`, and the parent wayfinder reasons over them to pick its
next step.

### 10.2 Rules of thumb for delegation

- **Risk composes.** A delegated sub-goal's action inherits the risk of what
  the sub-wayfinder might do. A well-behaved parent declares the sub-goal's real
  risk envelope (a `gh` publish sub-goal is `network_write` even though
  `argv[0]` is `wayfinder-exec`). Policy on the *sub*-executor still applies
  independently — the inner loop enforces its own approvals, so a sneaky or
  sloppy parent cannot launder a dangerous action through delegation.
- **Timeouts bound the whole sub-run.** `timeout_seconds` on the delegating
  action caps the entire inner loop.
- **One level at a time when debugging.** If a delegated action fails, read
  the sub-store's history first — the parent only sees the summary that came
  back.
- **Deep composition works** — a sub-wayfinder can itself delegate — but each
  level adds a store, a policy surface, and a place to look when things go
  wrong. Prefer shallow trees.
- The wayfinder may also consult other wayfinders *privately* while thinking (its
  internal state is out of scope). You will see the effects only in
  `explanation.evidence`. Anything that touches the world, though, must
  surface as a recommendation and go through an executor.

---

## 11. Artifacts: large and binary outputs

Command output up to the inline limit (see
`wayfinder capabilities | jq '.result.limits'`) rides inside `action_result.output`.
Anything larger is stored as a content-addressed **artifact** under
`.wayfinder/goals/<goal_id>/artifacts/sha256/…` and referenced from events:

```json
{
  "schema": "wip.artifact/0.1",
  "protocol_version": "0.1",
  "artifact_id": "art_01",
  "uri": "file:.wayfinder/goals/goal_01/artifacts/sha256/ab/abc123...",
  "media_type": "text/plain",
  "sha256": "sha256:abc123...",
  "bytes": 15322,
  "redacted": false,
  "description": "stderr from make test"
}
```

To read one, resolve the relative URI against the workspace root:

```bash
less /home/curt/project/.wayfinder/goals/goal_01/artifacts/sha256/ab/abc123...
```

The executor handles storage, hashing, and truncation automatically. If you
report results by hand and the output is big, store the file at its content
address, verify the digest (`shasum -a 256`), and reference it in
`action_result.artifacts` — the wayfinder re-verifies the digest before accepting.
If an artifact captured a secret, use a `redaction` update with a
`replacement_artifact` to genuinely replace its bytes; event payloads, by
contrast, can never be truly redacted, which is why secrets must never enter
them in the first place.

---

## 12. When things go wrong

Every error is a JSON object with a stable `error.code` — branch on that, not
the exit code.

| `error.code` | What it means | What to do |
|---|---|---|
| `invalid_input` | Malformed/ill-formed request, unknown ID, reused idempotency key with different content, `next` on a terminal goal. | Fix the request. Check required fields against the spec. |
| `storage_conflict` | Open recommendation already exists; lease claimed by another actor; append lock contention. | For issuance: dispose or `--supersede`. For claims: someone else owns the action — leave it. For locks: retry. |
| `stale_recommendation` | The world changed after issuance; execution refused. | `wayfinder next --mode=issue` for a fresh one. |
| `policy_denied` | Actor lacks authority, or executor policy refused the action. | Check `actor.authority`/authentication; grant an approval; or run manually. |
| `temporary_failure` | Transient; `retryable: true`, honor `retry_after_seconds`. | Retry the same request (same `update_id`/`create_id` — replays are safe). |
| `unsupported_capability` | Feature/enum this wayfinder doesn't support, or events from a newer protocol version. | Check `wayfinder capabilities`; upgrade or avoid the feature. |
| `corrupt_event_log` | Hash chain broken, seq gap, truncated line. | Stop automation. `wayfinder verify`. Recover out-of-band per the spec. |
| `artifact_integrity_failed` | Artifact bytes don't match their digest. | Re-store the artifact; never reference unverified content. |
| `internal_error` | Wayfinder bug. | Report it; the log is still your source of truth. |

General habits that keep you out of trouble:

- **Mint idempotency keys deliberately** (`create_id`, `update_id`), keep the
  request bodies around, and retry with identical bytes. `"replayed": true`
  in a response means "this already happened; here is the original result and
  the current status" — it is confirmation, not an error.
- **Use `--request-id`** on scripted calls so you can correlate responses in
  your own logs.
- **Don't parse stderr.** Ever. Protocol state is stdout JSON only.
- **Trust the log over your memory.** `wayfinder history` is canonical; if your
  script's idea of state disagrees with a replay of the events, the events win.

---

## 13. Security notes (the short version)

Full treatment — authentication, key management, secret stores, policy
configuration — lives in [wayfinder-security.md](wayfinder-security.md). The five things every
CLI user must internalize:

1. **Review `argv`, not prose.** `summary`, `title`, and
   `command_for_display` are wayfinder-controlled text and can misdescribe the
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
GOAL=$(wayfinder goal create < goal.json | jq -r '.result.goal.goal_id')

# 2. Automate what's safe
wayfinder-exec run --goal-id "$GOAL"

# 3. See why it stopped
wayfinder status --goal-id "$GOAL" | jq '.result | {goal_status, reason_code, needs}'

# 4. Unblock (whichever applies)
wayfinder update --goal-id "$GOAL" < answer.json     # answer a question
wayfinder update --goal-id "$GOAL" < approval.json   # approve a risky action
wayfinder update --goal-id "$GOAL" < observation.json# tell it what you changed

# 5. Repeat 2-4 until:
wayfinder status --goal-id "$GOAL" | jq -r '.result.goal_status'
# succeeded

# 6. Audit
wayfinder verify  --goal-id "$GOAL" | jq '.result.ok'
wayfinder history --goal-id "$GOAL" --since-seq 0 | jq -c '{seq, type, actor: .actor.id}'
```

The same session in prose (§8) — identical protocol traffic underneath:

```bash
wayfinder-do "Make the tests in /home/curt/project pass; nothing above low risk."  # 1-2
wayfinder-ask  --goal-id "$GOAL" "why did it stop?"                                # 3
wayfinder-tell --goal-id "$GOAL" "pnpm"                                            # 4 (whichever applies)
wayfinder-tell --goal-id "$GOAL" "approve rec_04 — argv reviewed, intended"
wayfinder-tell --goal-id "$GOAL" "I fixed the env var myself; try again"
wayfinder-ask  --goal-id "$GOAL" "what was submitted on my behalf, and did we finish?"  # 5-6
```
