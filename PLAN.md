# Plan

Goal: pi runs everyday agent work on local models, privately.
Cloud models handle only hard tasks, through a boundary that keeps logs and transcripts local.
The system measures its models, shows what every agent is doing, and improves its own context files from audited failures.

The control-loop design and continuation contract live in `docs/control-loop.md`.

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

- [~] Replace the malformed live `~/.pi/agent/models.json` with the reviewed local-only 128K template; the installer is ready but the managed workspace cannot write the dotfiles repository.
- [x] `settings.json`: `defaultProvider` `llama-cpp` and `defaultModel` `qwen3.5-4b` as separate keys; every local role uses Qwen3.5.
- [x] `~/.pi/agent/extensions/subagent/config.json` symlinked from `pi/extensions/subagent-config.json`: FleetView on, artifacts in the session dir, 10 minute run and 2 minute tool timeouts
- [x] `pi/extensions/telemetry.ts` symlinked as `~/.pi/agent/extensions/local-harness-telemetry.ts`; verified: turns, tool durations, token counts, 17.7K cached tokens per turn
- [x] `harness status` shows loaded models, GPU, active runs
- [x] `harness observe` shows telemetry, Git changes, and controller decisions on loopback
- [x] Extensions for local sessions: pi-subagents, ask-user-question, pi-memory, and pi-fff in override mode (decision 19)
- [x] Scout moved to Qwen3.5 (decision 20); a delegation now completes end to end in 32 seconds and the scout stays in the project directory

### Phase 3: Gemini escalation boundary `[~]`

- [x] Fresh-process Bubblewrap boundary excludes `~/.pi`, subagent temp dirs, state, dotfiles, vault, source Git metadata, and the live home directory
- [x] Generated review brief binds the objective, Gemini model, named files, sizes, and content digests
- [x] Escalation guard: brief required, marker required, named-file validation, explicit digest confirmation
- [x] `harness handoff create/run` copies named UTF-8 files and never applies cloud edits automatically
- [ ] Smoke test a non-sensitive fixture with `GEMINI_API_KEY` and free-tier quota exhaustion

### Phase 4: audit sensor `[ ]`

- [ ] `harness/sessions.py` parses pi sessions and subagent events
- [ ] `harness/metrics.py` scores runs
- [ ] `harness report` lists flagged runs
- [ ] Thresholds set from a week of real use

### Phase 4A: controller contract `[~]`

- [x] Document set point, sensor, controller, actuator, disturbances, and dampener
- [x] Version work-order, agent-completion, and runner-observation schemas
- [x] Version the telemetry envelope and import it incrementally into a private SQLite store
- [x] `harness contract check` returns `advance`, `double_check`, or `halt`
- [x] Add durable human feedback for future maintenance sessions
- [x] Generate runner-owned observations from run-scoped telemetry and Git
- [x] Load checks from the base commit and execute them in a networkless checker container
- [x] Require work-order digests, distinct agent/runner identities, complete telemetry, and sandbox evidence
- [x] Add contract fixtures and document the add-and-migrate rule before introducing v2
- [x] Display contract decisions and evidence in the local dashboard
- [x] Add a manual/schedulable one-attempt workflow; keep final acceptance human-controlled
- [ ] Run and review the first full Docker iteration once Docker is installed

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
| 16 | 2026-09-15 | Continuation uses three independently bound documents and hard gates, not an agent self-score | A work-order digest, runner observation, immutable check registry, and distinct identities make unsupported claims fail closed |
| 17 | 2026-09-15 | Agent and local model run in Docker on an internal-only network; checks run in a separate networkless container | This standardizes tools and limits without mounting home, credentials, or the Docker socket and without granting internet egress |
| 18 | 2026-09-15 | Judge output stays an unweighted vector; adoption starts at three perfect repetitions with no fatal findings | Correctness, reliability, and efficiency are unlike quantities, so one aggregate would hide failures |
| 19 | 2026-09-15 | Local pi sessions load only pi-subagents, ask-user-question, pi-memory, and pi-fff | Dropping pi-web-access, pi-lens, and pi-mcp-adapter saves about 7K prompt tokens per turn; pi-memory stays because the ACE memory plan builds on it; pi-fff came back on 2026-09-15 in override mode, replacing grep and find instead of adding tools |
| 20 | 2026-09-15 | The scout runs on Qwen3.5-4B, not LFM2.5 | The LFM scout treated the working directory as `/` and ran an unbounded `find /`; the Qwen3.5 scout ran `ls` in the project directory |
| 21 | 2026-09-15 | pi settings name the default as `defaultProvider` plus a bare `defaultModel` id | `defaultModel: "llama-cpp/qwen3.5-4b"` matches nothing, and pi silently falls back to the first model in `models.json` (Ornith 9B) |
| 22 | 2026-09-15 | The router serves two models only, and nothing uses Ornith, including the ACE Reflector and Curator: Qwen3.5-4B for the main session, workers, and scouts, and LFM2.5-2.6B for researchers | Ornith 9B, MiniCPM5, and the Qwen3 finetune loaded on demand and pushed other models out or onto the CPU; two fixed models are predictable |
| 23 | 2026-09-15 | Qwen3.5 gets a 64K window and LFM2.5 one 32K slot, every layer forced onto the GPU | A vault session hit a 30K prompt and pi clamped the reply to one token. 64K is the largest Qwen window that fits beside LFM; without forcing layers onto the GPU, llama.cpp quietly moved LFM to the CPU (6 tok/s) |
| 24 | 2026-09-15 | Qwen3.5-4B is the only model; the main session and every subagent use it, and LFM2.5 is removed | Joseph wants the most context for the main agent; one model leaves the whole GPU for its cache |
| 25 | 2026-09-15 | Qwen3.5 runs with a 160K window | 262K, 229K, and 196K fail to load; 160K uses 7.4 of 8 GiB |
| 26 | 2026-09-15 | pi auto compaction is off | Compaction rewrites the conversation and throws away the prompt cache; the cache survives a subagent request taking the slot (29,955 of 30,470 tokens reused), so a long uncompacted session stays fast. The 160K window is now a hard session limit |
| 27 | 2026-09-15 | A session near the context limit hands off to a new session through `/handoff` instead of compacting | The note carries file pointers, decisions, challenges, and next steps, so the new session starts small and keeps what matters; the extension saves the agent's reply because Qwen3.5 skipped the write tool call |
| 22 | 2026-09-15 | The independent checker gets a read-only worktree and no evidence mount | Candidate tests are executable code; a writable mount would let them rewrite the diff or runner artifacts while supposedly validating them |
| 23 | 2026-09-15 | A judgeable eval hashes the exact model file itself and captures the evaluator-source digest before its first request | A caller-supplied digest is still a claim, and a model id or Git commit does not identify changed local weights or an uncommitted evaluator |
| 24 | 2026-09-15 | Each Docker iteration gets a unique Compose project and mandatory teardown evidence | Timing out the Docker client does not prove its containers or router stopped |
| 25 | 2026-09-15 | The sandbox image copies only explicit runtime inputs and manifests their combined digest | `COPY .` needlessly enlarged the image trust boundary, while Compose-only hashes omitted code and lockfile drift |
| 26 | 2026-09-15 | Scheduled runs create a unique work order, detached worktree, and evidence root and refuse dirty or overlapping execution | Reusing a concrete loop config would stack mutations and overwrite the evidence needed for human review |
| 27 | 2026-09-15 | Eval scenarios fail closed on unknown fields and invalid expectation semantics before inference | Valid JSON can still contain a typo that silently removes a scoring requirement |
| 28 | 2026-09-20 | Restore a local-only 128K Qwen baseline; Gemini remains disabled until a reviewed handoff boundary exists | The 160K cache leaves little VRAM headroom, and switching providers inside a live session would disclose its transcript even when a repository has opted in |

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

Delegation with Qwen3.5 as main and scout (2026-09-15, all three kept extensions): the scout ran `ls -lh` in the project directory, wrote its context file, and pi exited in 32 seconds.
The main model relabelled byte sizes as kilobytes (12 bytes reported as 12 KB) after asking the scout for KB.
That is the second failure card candidate: units lost across a handoff.

Two model fit on the RTX 5060 (2026-09-15, Obsidian holding 0.4 GiB of the GPU):

| Qwen3.5 window | LFM2.5 | GPU in use | Result |
|---|---|---|---|
| 131K | two 32K slots | Qwen alone 5.8 GiB | Qwen ran out of memory while LFM was loaded; with LFM loaded second, LFM fell back to the CPU at 6 tok/s |
| 98K | one 32K slot | 6.0 GiB | LFM could not allocate its cache |
| 64K | one 32K slot | 7.4 GiB | Qwen 99 tok/s, LFM 181 tok/s. Adopted. |

Qwen3.5 alone (2026-09-15, one slot, q8_0 cache, every layer on the GPU):

| Window | Result |
|---|---|
| 262K, 229K, 196K | Failed to load |
| 160K | 7.4 of 8 GiB. 30K prompt cold in 9.9 s at 71 tok/s; resent, 30,467 cached and 0.3 s; after an unrelated request took the slot, 29,955 cached and 0.5 s. Adopted. |

Handoff test (2026-09-15, RPC mode, threshold lowered to 12.8K, four step task with pytest missing): the warning fired at 12.9K, the note listed the goal, state, files, the pytest failure, and next steps, and the new session read it, switched to unittest as the note suggested, and finished all four steps without handing off again.
An earlier run with the write tool approach failed because the model printed the note instead of saving it.

Rule for pi test runs: run them in `~/.local/state/local-harness/pi-test/` with `ACE_MEMORY_SKIP=1` set, so test sessions are never queued for ACE reflection and cannot teach the real playbook.

## Open items
- Fixed 2026-09-15, uncommitted: the telemetry extension threw a stale ctx error on `agent_settled` when an RPC client closed stdin mid prompt, because pi disposes the session before the prompt's cleanup emits that event.
  The handler now uses the model id remembered from earlier events.
- Handoff triggers at contextWindow minus 20K. A single turn that adds more than 20K tokens (a huge tool result) can still reach the window before the check runs.

- Prompt cache on hybrid models: LFM2.5 reuses 0 of 98 cached tokens when only the user message differs, and 94 when the request is identical.
  Hybrid models restore state only at checkpoints, so a stripped `<think>` block in the echoed assistant message also breaks reuse on the next turn.
  Qwen3.5 is hybrid as well.
  Phase 1 must measure both and decide whether pi should send `reasoning_content` back.
- Qwen3.5 with a q8_0 KV cache is untested; its hybrid attention may behave differently.
- pi-subagents' Claude Code adapter targets CLI 2.1.150; installed is 2.1.270.
- pi swallows a provider error in print mode: exit 0 with no output when the server rejects the request, and it hangs when the base URL is unreachable or JSON mode is used with this provider. Worth an upstream report.
- pi clamps `max_completion_tokens` down to 1 instead of failing when the prompt exceeds the configured context window. Also worth an upstream report.
- pi's extension set costs about 15K prompt tokens per turn on every local model. Decide which extensions a local session actually needs.
- `pi-memory` appears to write an exit summary with the session model after the answer: the router kept generating on Ornith after pi printed its reply.
  With Ornith 9B at 2.8 tok/s this looked like a hang; with Qwen3.5 as the default the same delegation exits in 32 seconds.
  Scripted runs keep a timeout until this is measured over more runs.
- One pi run with no flags stalled before starting a session and sent nothing to the router, right after a killed run.
  It did not reproduce on four later runs; watch for it.
