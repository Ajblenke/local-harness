# local-harness

Runs pi on local models, measures how they behave, and audits what the agents do.
Cloud models are used only for hard tasks, and they never see logs, transcripts, or the vault.

`PLAN.md` holds the phases, their status, and a log of every decision with the reason behind it.
Read that first if you want to know why something is the way it is.

## How the pieces fit

```
pi (main session) ──┬── subagents (scout, worker, reviewer, ...)
                    │        │
                    └────────┴──> llama-server router, one port (8080)
                                     ├── main model      (Qwen3.5-4B or MiniCPM5-2B, chosen by eval)
                                     ├── scout model     (LFM2.5-2.6B)
                                     └── oracle model    (Ornith-1.5-9B, runs alone)
harness eval   ── scores a model and preset against evals/scenarios
harness report ── reads pi sessions and subagent events, flags bad runs   (phase 4)
escalation     ── brief you approve, then Claude Code in an opted in repo  (phase 3)
```

## Serving models

`llm-serve` lives in `~/dotfiles/llm` and is on your PATH.
It starts one `llama-server` in router mode.
Every model in `~/.config/llama-router/models.ini` is served on the same port, and a request picks one by its `model` field.

```
llm-serve start                 # start the router in the background
llm-serve warm <model> <model>  # load models one at a time
llm-serve status                # which models are loaded
llm-serve stop
```

Two rules the GPU imposes:

- Load models with `warm`, one after the other. Two models loading at the same instant fail with out of memory on 8 GiB.
- The 9B model runs alone. Two 4B class models run well together.

To add a model, add a section to `models.ini` with `model = /path/to/file.gguf`.
The section name is the id pi and `harness` use.

## Measuring models

```
uv run harness eval --model <id> --preset greedy
uv run harness eval --model <id> --preset greedy-no-think --scenario tool_calls
uv run harness runs
uv run harness compare <run-a> <run-b>
uv run harness status
```

Scenarios live in `evals/scenarios/*.json`, presets in `evals/presets/*.json`.
A preset is the request parameters (temperature, max_tokens, thinking on or off).
Server side settings such as context size and reasoning budget live in `models.ini`, per model.

Every case checks the tool chosen, the argument values that matter, and whether the model abstained when it should.
Multi step cases feed canned tool results back and check the sequence, retries, and the final answer.
Every turn also passes the audited client's checks: reasoning that ate the token budget, a degraded tool parser, no prompt cache reuse.

Runs are saved under `~/.local/state/local-harness/runs/`.
Compare two runs before trusting any change to a model, quantization, or preset.

## Where data lives

Nothing private is in this repo.
Eval runs, telemetry, reports, and handoff directories go to `~/.local/state/local-harness/`.
pi sessions stay in `~/.pi/agent/sessions/`.

## Rules

- Never run pi or Claude Code from `~`. Home is a git repo, so both would treat all of it as the project.
- Cloud escalation needs a brief you have read and a `.escalate-ok` marker in the repo.
- Changes to prompts, roles, and skills go through a branch and an eval comparison, never straight to main.
