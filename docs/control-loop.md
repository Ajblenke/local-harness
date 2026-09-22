# Local Harness Control Loop

Status: accepted initial design, 2026-09-14.

## Set point

Agent runs are observable, reproducible, private by default, and unable to advance when
their claimed result is not supported by independent evidence. Changes remain small and
reviewable, and Joseph approves changes to prompts, policy, contracts, or model weights.

## Components

- **Sensor:** pi telemetry, sandbox observations, git state, resource measurements, and
  deterministic validation commands. Interactive telemetry may be best effort; evidence
  required by a controlled run must fail closed if it cannot be recorded.
- **Controller:** issues a work order, compares the agent's completion envelope with the
  runner's independent observation, and returns `advance`, `double_check`, or `halt`.
- **Actuator:** a local pi agent. It receives the work order and runs in an isolated Git
  worktree inside the standardized container. A second container performs validation.
- **Disturbances:** concurrent edits, model nondeterminism, dependency changes, stale
  configuration, prompt growth, partial telemetry, and processes that outlive their agent.
- **Dampener:** required checks, scope enforcement, sandbox violations, a changed-file cap,
  and regression comparisons prevent the loop from accepting a worse state.

## Three-party continuation contract

The controller must not treat an agent's self-report as proof. Every controlled run uses
three versioned documents:

1. A **work order**, issued before execution, states the objective, allowed read/write
   scope, denied paths, required checks, limits, and approval policy.
2. A **completion envelope**, written by the agent, states what it believes it changed,
   what it checked, its remaining uncertainty, and the evidence supporting each claim.
3. An **observation**, written by the trusted runner, records the actual changed paths,
   check results, sandbox violations, telemetry reference, and evidence identifiers.

`harness contract check WORK_ORDER COMPLETION OBSERVATION` evaluates the three documents. The work order
is anchored to a base Git commit and hashed canonically; completion and observation must bind to that
same digest. Agent and runner identities must differ.

- `advance`: documents are valid and agree; all hard gates pass.
- `double_check`: evidence is missing, incomplete, uncertain, or inconsistent without a
  demonstrated safety violation. The next action is an independent re-check, not another
  mutation.
- `halt`: scope, sandbox, identity, or required-check failure makes continuation unsafe.
  Human review is required.

An `advance` decision permits only the next step named by the work order. It never implies
permission to merge, publish, escalate to cloud, or widen scope. Work-order v1 always requires human
approval; delegating acceptance to a future controller requires a new contract version and migration.

## Evolution across sessions

The JSON schemas under `contracts/` are the protocol source of truth, including the event envelope used
by the observer. A future session may
add a new version but must not edit the meaning of an existing version. A protocol change
requires:

1. a new schema version and documented migration;
2. fixtures proving old records remain interpretable;
3. controller tests for both versions;
4. a decision-log entry and human approval.

Standing guidance lives in `.agents/memory/local-harness-control-loop.md`. It contains
durable reviewer feedback, not run logs. Run evidence remains append-only in the local
state directory and records its contract version.

## Initial operating policy

- Run locally and manually before adding a timer.
- Use deterministic gates before model-based semantic judgment.
- Keep the score as a vector until real-run data justifies weights.
- Allow only one mutation attempt before an independent double-check.
- Never allow the same agent invocation to both create and independently verify evidence.
- Keep cloud escalation disabled until its separate sandbox and handoff boundary pass
  adversarial tests.

## Runnable flow

1. `harness contract init` issues a work order anchored to `HEAD`.
2. `harness sandbox prompt` combines that order with durable human feedback.
3. `harness loop run` executes pi once in a disposable worktree. The agent and local llama.cpp router
   communicate only over an internal Docker network.
4. The runner imports run-scoped start/tool/settled events, enumerates the Git diff, and loads registered
   checks from the base commit so an agent cannot weaken them. Checks use the image's locked environment
   without syncing candidate dependency declarations.
5. A second checker runs those commands with no network, a read-only candidate worktree, and no evidence
   mount. The controller persists a decision.
6. A human reviews an `advance` worktree. No command in v1 commits, pushes, merges, or retries.

For timers, `harness loop scheduled TEMPLATE` first requires a clean primary checkout, takes a nonblocking
per-schedule lock, and issues a fresh run ID, work order, detached worktree, and evidence directory. It never
reuses the concrete `loop config` paths from a prior run and preserves every candidate worktree for review.

Host-run observations remain useful diagnostics but always produce `double_check`: only complete telemetry,
the standardized Docker sandbox, an enforced workspace read boundary, and isolated checks may advance.

The local dashboard (`harness observe`) shows telemetry, current Git changes, and recent decisions. It binds
to loopback, sends no CORS header, and reads only the private local state store.
