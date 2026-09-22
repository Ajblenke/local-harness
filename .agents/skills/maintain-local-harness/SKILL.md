---
name: maintain-local-harness
description: Maintain the local-harness control loop, contracts, telemetry, judging, or sandbox while preserving evidence and privacy boundaries.
---

# Maintain Local Harness

Use this skill for changes to the control loop, evaluator, telemetry, contracts, or sandbox.
The source of truth is `docs/control-loop.md` plus the versioned schemas in `contracts/`.

## Requirements

- Read `.agents/memory/local-harness-control-loop.md` before proposing a change.
- Treat agent reports as claims; derive acceptance evidence from the runner or deterministic checks.
- Keep existing contract versions immutable. Add a version and migration when semantics change.
- Make one reviewable increment within the issued work-order scope.
- Never broaden cloud, filesystem, network, or approval authority implicitly.
- Run `uv run ruff check .` and `uv run pytest -q` before requesting continuation.

## Workflow

1. Read the work order and verify its scope, limits, checks, and approval policy.
2. Inspect the live implementation, relevant tests, control-loop design, and standing memory.
3. Make the smallest change that satisfies the objective.
4. Run the required checks without substituting self-reported results for runner evidence.
5. Produce a completion envelope conforming to `contracts/completion-v1.schema.json`.
6. Request `double_check` when evidence is missing or uncertainty remains; never claim advance.

Completion means the change is within scope, validation has run, and every completion claim
references evidence the independent observation can supply.

