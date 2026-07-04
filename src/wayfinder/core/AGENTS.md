# Core Protocol Notes

This directory contains the implemented WIP core library. Keep changes small and
backed by focused tests; most behavior here encodes protocol invariants from
`wayfinder-interaction-protocol-v0.1.md`.

## Module Map

- `canonical.py`: RFC 8785 canonical JSON helpers and transport-field stripping.
- `hash_chain.py`: event hash calculation and hash stamping.
- `event_log.py`: append/read behavior for JSONL event logs.
- `reducer.py`: deterministic replay from events to status.
- `updates.py`: update-to-event mapping and recommendation lifecycle handling.
- `freshness.py`: lease, issued-event, and freshness checks.
- `idempotency.py`: create/update replay protection using canonical hashes.
- `goal_store.py`: higher-level API for a single goal store.
- `artifacts.py`: content-addressed artifact storage.
- `lock.py`: append lock primitive.
- `validation.py`: JSON Schema loading and validation.
- `types.py`: shared protocol constants.

## Invariants Agents Should Preserve

- Compare protocol payloads by canonical bytes, not formatted JSON text.
- Strip only transport-only `request_id` before canonical comparison.
- Do not make reducer output depend on wall-clock time.
- Keep event log replay sufficient to reconstruct visible status.
- Treat schemas in `schemas/` as source contracts.
- Add or update tests beside any changed invariant.
