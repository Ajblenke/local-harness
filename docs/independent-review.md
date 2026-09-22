# Independent review of the local-harness plan

Review date: 2026-09-15. This review treats working code, runner-owned artifacts, and reproducible command
output as evidence; an agent report by itself is not evidence.

## What the plan gets right

- The phased, local-first rollout matches the privacy goal and the measured 8 GiB GPU constraint.
- Model selection is based on repository tasks and tool behavior rather than published benchmark claims.
- Human approval remains the boundary for prompts, policy, contracts, model weights, and accepting a patch.
- Context distillation precedes weight tuning, and weight adoption is deferred until a scorecard exists.
- The eval suite includes abstention, argument accuracy, multi-step behavior, and a dead-end case instead of
  measuring only fluent final text.
- The one-attempt control loop keeps work reviewable and prevents an unchecked retry from becoming a new
  mutation with inherited authority.

## Assumptions that did not hold

| Assumption | Independent finding | Implemented correction |
|---|---|---|
| HTTP 200 means an eval turn is usable | llama.cpp can return empty, length-starved, malformed, or parser-degraded output with HTTP 200 | Fatal audited-client findings now fail the case |
| Matching expected argument fields is enough | Extra fields and wrong JSON-schema types could pass | Tool schemas and returned arguments are validated; eval schemas reject extra fields |
| One successful run is a trustworthy score | Model nondeterminism and incomplete repetition records can look perfect once; a caller-supplied digest or unrelated local file is only a claim | The judge derives repetition integrity, hashes the model file, requires the loaded router record to expose that same path, captures evaluator identity before requests, records preset/scenario digests, and reports scenario-group vectors |
| The actuator can report its own success | The same actor can omit changes or claim checks it did not run | Work order, completion claim, and runner observation are separately bound by run ID, base commit, and canonical work-order digest |
| A second container is automatically independent | Candidate tests could rewrite the mounted evidence directory and worktree | The checker has no network, no evidence mount, and a read-only candidate worktree; its Compose definition is manifest-bound |
| Timing out the Docker CLI stops the work | A detached child or model-router dependency can survive the client timeout | Each run gets a unique Compose project and teardown runs in `finally`; failed teardown is a sandbox violation |
| A read-only image can also hold pi's runtime config | pi writes model/auth store files even for simple CLI startup | Image-owned config is copied into a per-container writable tmpfs before pi starts |
| A process-global turn number measures prompt-cache reuse | The first turn of later eval cases was incorrectly treated as a continuation | Cache checks now receive the turn number within the current conversation |
| A reachable-looking status command may exit successfully | Automation could not distinguish an unavailable router | `harness status` returns nonzero when the router is unreachable |
| An unused approval enum is harmless future-proofing | Allowing `controller` in v1 implied authority that no v1 component safely implements | Work-order v1 now accepts only `human`; delegated approval requires a new protocol version |
| A timer can safely reuse a concrete loop config | Reusing its run ID, dirty worktree, and artifact directory would stack mutations and overwrite evidence | Scheduled templates now issue unique work orders/worktrees/evidence, lock out overlap, and require a clean primary checkout |
| JSON syntax makes eval scenarios trustworthy | A misspelled expectation key could parse successfully and then be ignored by the scorer | Scenario structure, tools, expected calls, argument types, and bounds now validate before any model request |

## Trust model now implemented

The agent is allowed to make one change in a disposable worktree. It cannot see the host home, credentials,
Docker socket, or internet. A runner outside the agent container records structured pi events, the Git change
set and kinds, sandbox lifecycle, and check-output digests. Registered checks come from the base commit and
run in a separate, networkless checker. The controller advances only when the agent claim and runner
observation agree and every hard gate passes. `advance` means “present the named next step”; it never means
merge, publish, widen authority, or use a cloud model.

The agent image copies only explicit runtime inputs. Its manifest binds those code/configuration inputs,
both Compose policies, the runner event stream, the exit status, and successful teardown. This detects drift
between launch and observation, although a live Docker pilot is still needed to resolve and pin image content.

The score is intentionally not one weighted number. Correctness, fatal findings, repetitions, tokens, turns,
duration, and per-scenario correctness remain visible. The recommended adoption policy is three complete
repetitions per case, perfect correctness, zero fatal findings, a complete reproducibility manifest, and no
scenario-group regression against an explicitly named baseline.

## Residual limitations and recommended next additions

1. Docker is not installed in the review environment, so image build, GPU access, router readiness, checker
   execution, and teardown still require a live pilot. Do not enable the weekly mutation timer before that
   pilot is reviewed.
2. Image versions are pinned to release tags, but resolved multi-architecture content digests are not yet
   pinned. Record and pin those digests after the first successful pull on the target GPU architecture.
3. The dashboard server cannot bind a socket inside the managed review sandbox. Its snapshot behavior and
   loopback-only policy are tested, but a browser smoke test must run on the host.
4. Write scope is enforced against the final Git change set, not with per-path kernel mounts. Temporary writes
   remain contained in a disposable worktree with no external network or credentials; a future v2 could add
   path-specific writable mounts if transient in-worktree writes become part of the threat model.
5. The deterministic judge proves conformance to the scenarios, not general semantic quality. Expand scenario
   coverage from real failure cards before changing thresholds or adding a model-based judge.
6. Telemetry currently has no retention policy. Establish one after measuring a week of event volume; do not
   silently delete evidence before that decision.
7. The cloud escalation boundary and full historical session auditor remain later plan phases and must not be
   inferred from this control-loop implementation.
