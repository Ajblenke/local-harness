# Plan

Goal: pi runs everyday agent work on local models, privately.
Cloud models handle only hard tasks, through a boundary that keeps logs and transcripts local.
The system measures its models, shows what every agent is doing, and improves its own context files from audited failures.

Status marks: `[ ]` not started, `[~]` in progress, `[x]` done.

## Phases

### Phase 0: bootstrap `[~]`

- [x] Repo created, `harness` package, eval runner, scenarios, presets
- [x] `llm-serve warm` loads models one at a time
- [ ] Merge `feat/llm-router` in dotfiles and `feat/bootstrap` here

### Phase 1: pick the main model `[ ]`

- [ ] Download Qwen3.5-4B (UD-Q4_K_XL) and MiniCPM5-2B (Q8_0) to `~/models/`
- [ ] Add both to `models.ini` with reasoning and sampling settings
- [ ] Run `tool_calls` and `multi_step` with thinking on and off, on both
- [ ] Run each through pi itself on a scratch repo
- [ ] Record the winner and the numbers below

### Phase 2: roles, pi config, telemetry `[ ]`

- [ ] `~/.pi/agent/models.json` matches the presets; Spark entries removed
- [ ] `settings.json`: default model, scout and researcher on LFM, oracle on Ornith
- [ ] `~/.pi/agent/extensions/subagent/config.json`: FleetView on, artifacts in session dir
- [ ] `pi/extensions/telemetry.ts` writes one line per turn and tool call
- [ ] `harness status` shows loaded models, GPU, active runs

### Phase 3: escalation boundary `[ ]`

- [ ] Claude Code deny rules for `~/.pi`, subagent temp dirs, state dir, vault
- [ ] Brief template in `briefs/`
- [ ] `pi/extensions/escalation-guard.ts`: brief required, marker required, scan, confirm
- [ ] `harness handoff <files...>` for vault work
- [ ] Smoke test the Claude Code adapters against CLI 2.1.270

### Phase 4: audit sensor `[ ]`

- [ ] `harness/sessions.py` parses pi sessions and subagent events
- [ ] `harness/metrics.py` scores runs
- [ ] `harness report` lists flagged runs
- [ ] Thresholds set from a week of real use

### Phase 5: context distillation loop `[ ]`

- [ ] `auditor` role drafts failure cards with placeholders
- [ ] Approved cards go to the cloud through the escalation path
- [ ] `harness propose <card>` writes a scenario, applies the change on a branch, compares evals
- [ ] Nightly `harness report` via a systemd user timer

### Phase 6: weight distillation `[ ]`

Starts only when phases 1 through 5 run and a scoreboard exists.
Synthetic trajectories only.
A finetune is adopted only if it scores equal or better on every scenario group.

## Decision log

| # | Date | Decision | Why |
|---|---|---|---|
| 1 | 2026-09-14 | One llama-server router on port 8080 serves every model | Single endpoint for pi, models load on demand, `LLM_MODELS_MAX` bounds VRAM |
| 2 | 2026-09-14 | The bundled `~/.llama-app/llama serve` is the server | The unsloth build needs CUDA 13 libraries that are not installed and silently runs on the CPU |
| 3 | 2026-09-14 | Roles: main session, worker, and reviewer on the strongest small model; scout and researcher on LFM2.5-2.6B; the 9B and cloud for hard tasks | Two small models fit the 8 GiB GPU together (6 GiB measured); the 9B drops to 2 tok/s next to another model |
| 4 | 2026-09-14 | Project lives here, absorbing the earlier agent-audit scripts; dotfiles keeps only `llm-serve` | Eval scripts and a decision log need a real repo |
| 5 | 2026-09-14 | Main model chosen by measurement: Qwen3.5-4B vs MiniCPM5-2B on the same scenarios | Published benchmarks disagree (BFCL v4: 50.3 vs 66.6) and MiniCPM5's tool format is untested in llama.cpp |
| 6 | 2026-09-14 | Self improvement proposes diffs; Joseph approves every one | Trust has to be built before any automation |
| 7 | 2026-09-14 | Auditing is local only; no cloud model ever reads logs, sessions, or the vault | Privacy is the reason for the project |
| 8 | 2026-09-14 | Escalation: hard deny list in Claude Code settings, a brief Joseph approves, whole repo for opted in code projects, copied named files for vault work | The boundary is enforced by where the cloud model runs and what it is denied, not by trusting the brief |
| 9 | 2026-09-14 | Repos opt in to escalation with a `.escalate-ok` marker | Unknown repos stay local by default |
| 10 | 2026-09-14 | Distillation goes into context first (failure cards), into weights later and only if evals say so | 4B failures are mostly process failures; a finetune without evals cannot be judged |
| 11 | 2026-09-14 | Spark-X2.5 is excluded | Its `spark2_5` architecture is unknown to llama.cpp |
| 12 | 2026-09-14 | The qwen finetune in `~/models/qwen3-4B` is not used for agent work | It answers with `[pi.status()]` style output |
| 13 | 2026-09-14 | Scenarios are JSON files, one per group, with canned tool results for multi step cases | A cloud proposal in phase 5 can add a scenario without touching Python |
| 14 | 2026-09-14 | Context is 8K per model with a q8_0 KV cache | At 16K fp16, Qwen3-4B's cache alone was 3.6 GiB and a second model failed to load |

## Measurements

Recorded as they happen.
Each entry names the run file under `~/.local/state/local-harness/runs/`.

| Date | Model | Preset | Score | Notes |
|---|---|---|---|---|
| 2026-09-14 | LFM2.5-2.6B Q4_K_M | greedy | 10/11 | `20260914T101006_LiquidAI-LFM2.5-2.6B-GGUF_greedy_smoke.json`. Failed the dead end case: searched five paths and never answered. 170 tok/s. |

## Open items

- Prompt cache on hybrid models: LFM2.5 reuses 0 of 98 cached tokens when only the user message differs, and 94 when the request is identical.
  Hybrid models restore state only at checkpoints, so a stripped `<think>` block in the echoed assistant message also breaks reuse on the next turn.
  Qwen3.5 is hybrid as well.
  Phase 1 must measure both and decide whether pi should send `reasoning_content` back.
- Qwen3.5 with a q8_0 KV cache is untested; its hybrid attention may behave differently.
- pi-subagents' Claude Code adapter targets CLI 2.1.150; installed is 2.1.270.
- pi's `models.json` still lists 128K context for models the preset serves at 8K.
- Two Spark entries in `models.json` will error until phase 2 removes them.
