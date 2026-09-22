# local-harness

Runs pi on local models, measures how they behave, and audits what the agents do.
Cloud models are used only for hard tasks, and they never see logs, transcripts, or the vault.

`PLAN.md` holds the phases, their status, and a log of every decision with the reason behind it.
Read that first if you want to know why something is the way it is.

## How the pieces fit

```
pi (main session) ──┬── subagents (scout, worker, reviewer, ...)
                    │        │
                    └────────┴──> llama-server router, one port (8080), one loaded model
                                     ├── Qwen3.5-4B (default, 128K context)
                                     └── MiniCPM5-2B (comparison, 32K context)
harness eval     ── scores a model and preset against evals/scenarios
harness observe  ── local telemetry, Git changes, and controller decisions
harness contract ── issues and evaluates three-party continuation contracts
harness loop     ── one sandboxed mutation, independent observation, and decision
escalation       ── reviewed copies, then one isolated Gemini process
```

## Serving models

The hardened `llm-serve` source is `scripts/llm-serve`; install or stow it onto
your PATH. It starts one `llama-server` in router mode. Only section ids in
`~/.config/llama-router/models.ini` are accepted as model arguments; a GGUF
existing on disk does not make it loadable.

```
llm-serve doctor                # validate binary, config, GGUFs, pid, and endpoint
llm-serve models                # configured ids (not file paths)
llm-serve start
llm-serve warm qwen3.5-4b
llm-serve status
llm-serve stop
```

The supported baseline loads one model at a time: Qwen at 128K context for
normal work, or MiniCPM5 at 32K for comparison. The earlier 160K Qwen setting
measured 7.4 GiB on an 8 GiB GPU and was too sensitive to desktop VRAM use.
Keep `LLM_MODELS_MAX=1`; compare eval runs before attempting concurrency. The
Qwen3 finetune remains excluded because its agent tool output failed evaluation.

Canonical local-only templates live under `config/`. Keep `contextWindow` in
pi's `models.json` equal to `c / np` in `models.ini`.

Preview and apply the reviewed configuration with:

```sh
scripts/install-local-config
scripts/install-local-config --apply
llm-serve doctor
```

The installer resolves existing stow symlinks, makes private timestamped
backups, replaces only `models.json`, `models.ini`, and `llm-serve`, and only
changes the mode of the existing pi `settings.json`.

## Optional Gemini handoff

Pi has a built-in Google provider. Keep its free-tier key in
`GEMINI_API_KEY`; do not store it in `models.json`, a handoff, or this
repository. Interactive and controlled runs remain local-only, and the model
selector never routes to Gemini automatically.

On Linux with Bubblewrap installed, opt a repository in and create a handoff
containing only explicitly named UTF-8 files:

```sh
touch .escalate-ok
uv run harness handoff create \
  --objective "Review the parser failure and propose a fix" \
  --file harness/parser.py --file tests/test_parser.py \
  --model gemini-2.5-flash
```

Review the generated `brief.md`, then run the exact digest-confirmed command
printed by `handoff create`. Set `GEMINI_API_KEY` in the environment first,
preferably through a password manager or a silent shell prompt rather than a
command that records the key in shell history. The runner starts a fresh Pi
process with no session, extension, skill, context-file, shell, live home, Git
metadata, or local state access. Gemini may edit only the private copies; its
response, errors, and changed copies remain under
`~/.local/state/local-harness/handoffs/` and are never applied automatically.
There is one invocation and no harness retry or fallback. See
`docs/model-selector.md` and `docs/cloud-integration-plan.md`.

## Measuring models

```
uv run harness eval --model <id> --model-file /path/to/model.gguf --preset greedy
uv run harness eval --model <id> --model-file /path/to/model.gguf --preset greedy-no-think --scenario tool_calls
uv run harness runs
uv run harness compare <run-a> <run-b>
uv run harness status
uv run harness judge <candidate-run> --baseline <baseline-run> --minimum-repetitions 3
```

Scenarios live in `evals/scenarios/*.json`, presets in `evals/presets/*.json`.
A preset is the request parameters (temperature, max_tokens, thinking on or off).
Server side settings such as context size and reasoning budget live in `models.ini`, per model.

Every case checks the tool chosen, the argument values that matter, and whether the model abstained when it should.
Multi step cases feed canned tool results back and check the sequence, retries, and the final answer.
Every turn also passes the audited client's checks: reasoning that ate the token budget, a degraded tool parser, no prompt cache reuse.

Runs are saved under `~/.local/state/local-harness/runs/`.
Compare two runs before trusting any change to a model, quantization, or preset.

## Observing the system

The pi extension writes versioned JSONL events. Import and inspect them with:

```sh
uv run harness telemetry sync
uv run harness telemetry snapshot
uv run harness observe                 # http://127.0.0.1:8765
```

The observer binds only to loopback and shows recent events, working-tree changes, and persisted
controller decisions with independently observed paths and check results. Controlled-run telemetry is
imported into the same private dashboard store after the run. Its database and decision index use private
permissions.

## Running the control loop

Issue an immutable, commit-anchored work order:

```sh
uv run harness contract init \
  --objective "one reviewable improvement" \
  --write 'harness/**' --write 'tests/**' \
  --output /private/artifacts/work-order.json
uv run harness sandbox prompt /private/artifacts/work-order.json \
  --memory .agents/memory/local-harness-control-loop.md \
  --output /private/artifacts/prompt.md
```

After installing Docker, set `MODEL_FILE` to the chosen GGUF and use a disposable Git worktree anchored
at the work order's `base_commit`:

```sh
uv run harness sandbox doctor
uv run harness loop run /private/artifacts/work-order.json \
  --worktree /private/disposable-worktree \
  --artifacts /private/artifacts
```

The agent and local llama.cpp router share an internal-only Docker network. The runner independently
derives telemetry counts, loads validation commands from the base commit, re-runs them in a networkless
checker, records the actual Git diff, and emits `advance`, `double_check`, or `halt`. It never commits,
pushes, merges, escalates to cloud, or retries a mutation automatically.

Recurring runs use `harness loop scheduled TEMPLATE`, not a reused concrete loop config. Each invocation
requires a clean primary checkout and creates a unique work order, detached worktree, and evidence root.
See `systemd/README.md`; keep the mutation timer disabled until a manual Docker pilot is reviewed.

See `docs/control-loop.md`, `docs/scorecard.md`, `docs/independent-review.md`, and `sandbox/README.md` for the
trust model, the independent findings, and remaining limits.

## Where data lives

Nothing private is in this repo.
Eval runs, telemetry, reports, and handoff directories go to `~/.local/state/local-harness/`.
pi sessions stay in `~/.pi/agent/sessions/`.
Controlled-run artifacts should be stored outside the disposable worktree and kept private.

## Rules

- Never run pi or a cloud coding agent from `~`. Home is a git repo, so either would treat all of it as the project.
- Cloud escalation needs a brief you have read and a `.escalate-ok` marker in the repo.
- Changes to prompts, roles, and skills go through a branch and an eval comparison, never straight to main.
