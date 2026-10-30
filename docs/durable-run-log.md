# Durable Run Log and Crash Recovery

Before this change, a scheduled run that died mid-iteration (OOM, reboot, `systemctl stop`,
SIGKILL) left a worktree and a partial evidence directory with no record of how far it got.
Its Compose project could keep running, nothing marked the run as failed, and the actuator's
event stream was lost because it was buffered in memory until the process exited.

This change makes every scheduled run's progress durable and lets the next invocation finish
or close any run a crash left open. It is the first step from `docs/control-loop.md` toward
durable execution. It does not change the trust model: nothing is retried, committed, or
deleted automatically.

## Run log

Each scheduled run root (`$XDG_STATE_HOME/local-harness/scheduled/SCHEDULE/RUN_ID/`) now has
`run-log.jsonl`. It is append-only, mode `0600`, and opened with `O_APPEND | O_NOFOLLOW`.
Each record is one JSON line:

```json
{"schema":"local-harness/run-event/v1","seq":3,"ts":1767225600000,"run_id":"...","event":"actuator_exited","data":{"exit_code":0}}
```

The file is fsynced after every append, and its directory is fsynced when the log is
created. Events are written in this order:

| Event | Written | Data |
| --- | --- | --- |
| `issued` | before the work order is persisted | `base_commit`, `work_order_sha256` |
| `worktree_created` | after `git worktree add` | |
| `actuator_started` | immediately before the container is launched | `compose_project` |
| `actuator_exited` | after the actuator returns and its project is torn down | `exit_code` |
| `observed` | after independent observation | `observation_sha256` |
| `decided` | after `decision.json` and the contract record are written | `outcome` |
| `completed` | after `schedule-result.json` is written (terminal) | `outcome`, optionally `recovered` |
| `failed` | after the runner raised and `schedule-result.json` was written (terminal) | `reason` |
| `abandoned` | written by recovery for an interrupted run (terminal) | `after`, `reason`, `teardown_exit_code` |

`issued` comes first. `actuator_started` is written just before the only effect that can
outlive the runner process. Every other record is written after the effect it names has become
durable, so the last valid record bounds what may have happened. Readers ignore a torn final line, and the next append
first terminates it with a newline. No record can follow a terminal event.

`harness loop run` and `harness loop config` behave as before; they only journal when
`run_iteration(..., run_log=...)` is given a run root.

## Recovery

`run_scheduled` takes the schedule's non-blocking `flock` and then calls
`recover_schedule()` before it touches Git. Holding the lock proves that no other invocation
owns a run in that schedule, so any run whose log has not ended was interrupted. For each such
run:

1. **Live actuator.** If `actuator_started` was logged without `actuator_exited`, it runs
   `docker compose --project-name local-harness-… down --remove-orphans` for that run's
   project and records the exit code.
2. **Roll forward.** If the last event is `decided` and `evidence/decision.json` exists,
   only the schedule summary is missing. It writes `schedule-result.json` from the persisted
   decision with `recovered: true` and logs `completed`.
3. **Otherwise** it writes a `double_check` `schedule-result.json` (unless one exists) that
   names the last event reached, and logs `abandoned`.

Recovery never deletes or edits a candidate worktree and never re-runs a mutation, so the
policy that every candidate is preserved for review is unchanged. Run directories created
before this change are handled too: one with `work-order.json` but no `schedule-result.json`
is marked `abandoned`, and one with a result is left alone.

To recover by hand, or from a boot-time unit, without issuing new work:

```bash
uv run harness loop recover                       # every schedule; skips ones currently locked
uv run harness loop recover --schedule-id nightly
```

## Streaming actuator output

`sandbox.run_agent` now launches the actuator with `subprocess.Popen` and points stdout and
stderr straight at `runner-events.jsonl` and `agent-stderr.txt` (created `0600`,
`O_NOFOLLOW`, append mode). Events reach disk while the agent runs, so a crash keeps every event
emitted so far, and recovery records how many bytes survived. On timeout the client is killed
and reaped before Compose teardown. Both files are fsynced before the manifest hashes the event
stream, so `runner_event_stats` checks the same bytes it did before.

`secureio.write_private` now also fsyncs the parent directory after its atomic rename, so a
completed evidence file survives power loss together with its directory entry.

## Known limits

- The checker container started by `observe` uses Compose's default project name, not a
  per-run one, so recovery cannot target it. It is bounded by its own timeout.
- When the runner dies after `actuator_started`, recovery tears down the run's project but cannot tell whether
  the agent had already mutated the worktree. That is why recovery marks the run
  `double_check` and does not retry.
- Recovery runs only when the same schedule next fires, or on `harness loop recover`. A
  boot-time `systemd` oneshot that runs `harness loop recover` would close the gap after a
  reboot.

## Next steps (from the durable-loop review)

These are ordered roughly by value for the cost:

1. **Live budget enforcement.** Tail the streamed `runner-events.jsonl` while the actuator
   runs and stop the container when `max_tool_calls` is exceeded, instead of only judging the
   count afterwards.
2. **Heartbeat.** Append an `actuator_heartbeat` event (or touch a file) on an interval so
   recovery and the dashboard can tell a hung run from a slow one, and a watchdog can enforce
   liveness separately from wall time.
3. **Step-level checkpoints.** Journal observation sub-steps (checker runs, diff capture) so
   a recovered run can resume observation without repeating the mutation, with idempotency
   keys derived from `run_id` + step for any step that has external effects.
4. **Boot-time recovery unit.** Add `local-harness-recover.service` (oneshot,
   `After=docker.service`) that runs `harness loop recover`.
5. **Telemetry from the run log.** Sync run-log events into the telemetry DB so the
   dashboard shows run state (`in progress / completed / failed / abandoned`) and recovered
   runs.
6. **Fault-injection tests.** Kill the runner at each event boundary (a pytest fixture that
   raises at a chosen `append_event`) and assert that recovery reaches the expected terminal
   state.
7. **Atelier.** Carry the same event-log and recovery contract into atelier's session loop
   so both projects share one run-event schema.
8. **Retention.** Add an explicit, human-invoked prune for old terminal runs. Recovery should
   keep its no-deletion policy.
