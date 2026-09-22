# User-level schedules

The telemetry timer is sensor-only and safe to enable immediately. The loop timer runs one mutation
attempt per week and should remain disabled until a manual Docker iteration has been reviewed.

Install the telemetry units with `install -Dm644`, run `systemctl --user daemon-reload`, then enable
`local-harness-telemetry.timer`.

The services resolve `uv` from `~/.local/bin`, `/usr/local/bin`, or `/usr/bin`, so they work with either
a user install or the system package used on this host.

For a controlled loop named `maintenance`, create
`~/.config/local-harness/maintenance.json`:

```json
{
  "schedule_id": "maintenance",
  "objective": "make one small, reviewable maintenance improvement",
  "write": ["harness/**", "tests/**", "docs/**"],
  "checks": ["ruff", "pytest"],
  "memory": "/absolute/repo/.agents/memory/local-harness-control-loop.md"
}
```

Put `MODEL_FILE=/absolute/model.gguf` in `~/.config/local-harness/model.env`, mode `0600`. After the manual
pilot passes, install the templated units and enable `local-harness-loop@maintenance.timer`. The service
refuses a dirty primary checkout and overlapping invocation. Every tick creates a unique work order,
detached worktree, and evidence directory below `~/.local/state/local-harness/scheduled/`; it never reuses
or overwrites an earlier run. It does not merge, push, remove the review worktree, or open a PR; an
`advance` decision means the verified disposable worktree is ready for human review. This preserves the
project's explicit human-approval policy.
