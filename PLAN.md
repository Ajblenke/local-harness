# Plan

Goal: pi runs everyday agent work on local models, privately.
Cloud models handle only hard tasks, through a boundary that keeps logs and transcripts local.
The system measures its models, shows what every agent is doing, and improves its own context files from audited failures.

Status marks: `[ ]` not started, `[~]` in progress, `[x]` done.

## Phases

### Phase 0: bootstrap `[x]`

- [x] Repo created, `harness` package, eval runner, scenarios, presets
- [x] `llm-serve warm` loads models one at a time
- [x] `llm` package committed in dotfiles (branch `feat/herdr-agents`), `feat/bootstrap` here

### Phase 1: pick the main model `[x]`

- [x] Download Qwen3.5-4B (UD-Q4_K_XL) and MiniCPM5-2B (Q8_0) to `~/models/`
- [x] Add both to `models.ini` with reasoning and sampling settings
- [x] Run `tool_calls` and `multi_step` with thinking on and off, on both
- [x] Run each through pi itself on a scratch repo: both create, read back, and report the file with every extension loaded (Qwen3.5 20s, MiniCPM5 28s, 17.6K prompt tokens)
- [x] Winner: Qwen3.5-4B (decision 15). MiniCPM5-2B stays as a preset for comparison runs.

### Phase 2: roles, pi config, telemetry `[~]`

- [x] `~/.pi/agent/models.json` matches the presets (32K); Spark entries removed. The file now lives in `~/dotfiles/pi` and is stowed. Model ids have no slashes (`qwen3.5-4b`, `lfm2.5-2.6b`, `ornith-1.5-9b`, `minicpm5-2b`).
- [x] `settings.json`: default model `llama-cpp/qwen3.5-4b`; scout and researcher on `lfm2.5-2.6b`; oracle on `ornith-1.5-9b`
- [x] `~/.pi/agent/extensions/subagent/config.json` symlinked from `pi/extensions/subagent-config.json`: FleetView on, artifacts in the session dir, 10 minute run and 2 minute tool timeouts
- [x] `pi/extensions/telemetry.ts` symlinked as `~/.pi/agent/extensions/local-harness-telemetry.ts`; verified: turns, tool durations, token counts, 17.7K cached tokens per turn
- [x] `harness status` shows loaded models, GPU, active runs
- [ ] Decide which pi extensions a local session keeps (see the prompt cost table) and whether `pi-memory` stays
- [ ] Verify a delegation end to end once the scout stops leaving the project directory

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
| 14 | 2026-09-14 | Context is 32K per model with a q8_0 KV cache (was 8K) | pi's prompt with extensions is 14K to 17K tokens before any conversation; Qwen3.5, MiniCPM5, and LFM2.5 have small per token caches so 32K is affordable |
| 15 | 2026-09-14 | Qwen3.5-4B is the main model | 11/11 on both scenario groups with thinking on and off, completed a real pi tool task, and keeps its prompt cache across turns; MiniCPM5 looped on the dead end case with thinking on |

## Measurements

Recorded as they happen.
Each entry names the run file under `~/.local/state/local-harness/runs/`.

| Date | Model | Preset | Score | Notes |
|---|---|---|---|---|
| 2026-09-14 | LFM2.5-2.6B Q4_K_M | greedy | 10/11 | `20260914T101006_LiquidAI-LFM2.5-2.6B-GGUF_greedy_smoke.json`. Failed the dead end case: searched five paths and never answered. 170 tok/s. |
| 2026-09-14 | Qwen3.5-4B UD-Q4_K_XL | greedy-no-think | 11/11 | `20260914T101238_qwen3.5-4b_greedy-no-think.json`. 93 tok/s. |
| 2026-09-14 | Qwen3.5-4B UD-Q4_K_XL | greedy-think | 11/11 | `20260914T101247_qwen3.5-4b_greedy-think.json`. 93 tok/s, 190 to 630 chars of thinking per case. |
| 2026-09-14 | MiniCPM5-2B Q8_0 | greedy-no-think | 10/11 | `20260914T101307_minicpm5-2b_greedy-no-think.json`. Answered the dead end case correctly ("doesn't exist"); the check wanted the word "not" and was loosened afterwards. 92 tok/s. |
| 2026-09-14 | MiniCPM5-2B Q8_0 | greedy-think | 10/11 | `20260914T101312_minicpm5-2b_greedy-think.json`. Real failure: after the dead end it searched three more times, once with an empty pattern, and never answered. |

Prompt cache with a shared system and tools prefix and a different user message: LFM2.5 reuses 0 of 98 tokens, Qwen3.5 reuses 279 of 295.

pi through the router (tools on, scratch directory):

| Configuration | Prompt tokens | Result |
|---|---|---|
| pi with all extensions and skills, 8K context | 14,433 to 17,590 | Rejected by the server. pi prints nothing and exits 0, or hangs in JSON mode. |
| pi without extensions, with skills | 1,687 | Works |
| pi without extensions or skills | 1,545 | Qwen3.5 created the file, read it back, reported it correctly |
| pi with all extensions, 32K context, `models.json` still 8K | 17,590 | Model returns one token. pi clamps `max_completion_tokens` to `contextWindow` minus its prompt estimate, which bottomed out at 1. Found by proxying the request. |

Rule that follows: `contextWindow` in `~/.pi/agent/models.json` must equal the `c` value in `models.ini`, or pi silently starves the model of output tokens.

First delegation through pi-subagents (main Qwen3.5, scout LFM2.5, task "list the files in the current directory and report their sizes"):
the scout ran on the LFM instance's second slot as intended, but it treated the current directory as `/`, listed the filesystem root, then ran `find / -type f -exec ls -l {} \;` with no bound.
The run never finished and the `find` outlived the killed pi process.
This is the first failure card candidate for phase 5 and the reason subagents now have `toolTimeoutMs` set.

Prompt cost per extension, on top of the 1,545 baseline:

| Extension | Added tokens |
|---|---|
| pi-subagents | 5,400 |
| pi-web-access | 3,250 |
| pi-lens | 3,070 |
| @ff-labs/pi-fff | 1,080 |
| pi-mcp-adapter | 1,040 |
| @juicesharp/rpiv-ask-user-question | under 100 |

## Open items

- Prompt cache on hybrid models: LFM2.5 reuses 0 of 98 cached tokens when only the user message differs, and 94 when the request is identical.
  Hybrid models restore state only at checkpoints, so a stripped `<think>` block in the echoed assistant message also breaks reuse on the next turn.
  Qwen3.5 is hybrid as well.
  Phase 1 must measure both and decide whether pi should send `reasoning_content` back.
- Qwen3.5 with a q8_0 KV cache is untested; its hybrid attention may behave differently.
- pi-subagents' Claude Code adapter targets CLI 2.1.150; installed is 2.1.270.
- pi swallows a provider error in print mode: exit 0 with no output when the server rejects the request, and it hangs when the base URL is unreachable or JSON mode is used with this provider. Worth an upstream report.
- pi clamps `max_completion_tokens` down to 1 instead of failing when the prompt exceeds the configured context window. Also worth an upstream report.
- pi's extension set costs about 15K prompt tokens per turn on every local model. Decide which extensions a local session actually needs.
- `pi-memory` keeps `pi -p` alive after any run that used tools: the task finishes in about 40 seconds and the process sits until killed.
  Every other extension, alone or together, exits cleanly.
  Until that is fixed or the package is removed, scripted runs need a timeout and delegation tests are unreliable.
