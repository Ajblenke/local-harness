# Agent Memory: Local Harness Control Loop

Standing feedback for future maintenance sessions. Keep durable policy and known
false-positive guidance here; keep individual run evidence in the local state directory.

## Guidance

- An agent completion report is a claim, not independent evidence.
- Missing or inconsistent evidence moves the controller to `double_check`.
- Scope, sandbox, identity, or required-check failures move it to `halt`.
- Contract versions are immutable. Add and migrate; do not reinterpret old records.
- Prefer deterministic gates and score vectors before model-based or aggregate judging.
- Cloud agents never receive telemetry, sessions, the vault, or the live home directory.

