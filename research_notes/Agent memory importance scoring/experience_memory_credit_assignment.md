# Experience / Procedural Memory for LLM Agents: How Lesson Usefulness Is Scored and Credited (2023 - Oct 2026)

Scope note: Primary sources were fetched on 2026-10-27 (arXiv HTML pages, ACE and ExpeL reference code cloned from GitHub).
ACE code was read at commit `82709de050e1db6e6ef2f07bcb0393560b94992a` (2026-08-24) of https://github.com/ace-agent/ace.
Items marked "(background, not re-fetched)" come from well-known papers whose arXiv IDs I am confident of, but I did not re-read them in this pass; the report writer should treat their details as lower confidence.

## 1. ACE and Dynamic Cheatsheet: exact counter semantics, grow-and-refine, dedup, selection, results, limitations

### Takeaway
In the ACE reference code, helpful/harmful counters move only when the Reflector tags a bullet, and the Reflector is shown only the bullets the Generator cited in its `bullet_ids` output; if the Generator cites nothing, the Reflector sees "(No bullets used by generator)" and no counter can move.
This exactly matches the user's observation (zero citations leads to zero reinforcement), so the "frozen counters" pathology is built into ACE's design, not a bug in the user's port.

### Cited Findings
**Paper (Zhang et al., arXiv 2510.04618, Oct 2025)**
- Context is a set of itemized bullets; each bullet has metadata (unique ID, counters of how often it was marked helpful or harmful) and content (a reusable strategy, domain concept, or common failure mode) — [ACE paper](https://arxiv.org/html/2510.04618)
- Pipeline: the Generator marks which bullets were useful or misleading; the Reflector uses that plus execution traces to extract lessons (optionally over several refinement iterations); the Curator converts lessons into compact "delta entries" merged by non-LLM logic, allowing parallel merges — [ACE paper](https://arxiv.org/html/2510.04618)
- Grow-and-refine: new bullets get new IDs and are appended; existing bullets are updated in place (e.g., counters incremented); de-duplication compares bullets via semantic embeddings; refinement can run proactively after each delta or lazily when the context window is exceeded — [ACE paper](https://arxiv.org/html/2510.04618)
- The paper contains no explicit scoring formula for bullet value; counters are raw integers — [ACE paper](https://arxiv.org/html/2510.04618)
- Context collapse (case study, AppWorld): at step 60 the context held 18,282 tokens at 66.7 accuracy; after a full LLM rewrite it shrank to 122 tokens and accuracy fell to 57.1, below the 63.7 no-adaptation baseline — [ACE paper](https://arxiv.org/html/2510.04618)
- Brevity bias: prompt optimizers collapse toward short, generic instructions (example from Gao et al.: near-identical "Create unit tests to ensure methods behave as expected") — [ACE paper](https://arxiv.org/html/2510.04618)
- Results, DeepSeek-V3.1 base. AppWorld average: ReAct 42.4; offline ACE with GT labels 59.4 (+17.0); offline ACE without GT 57.2 (+14.8); GEPA 46.4; ICL 46.0; online ACE without GT 59.5 (+17.1); online Dynamic Cheatsheet (cumulative) 51.9 — [ACE paper](https://arxiv.org/html/2510.04618)
- FiNER accuracy: base 70.7; offline ACE with GT 78.3, without GT 71.1 (+0.4); online with GT 76.7, online without GT 67.3 (-3.4, i.e. worse than no memory) — [ACE paper](https://arxiv.org/html/2510.04618)
- Formula accuracy: base 67.5; offline with GT 85.5, without GT 83.0; online with GT 76.5, without GT 78.5 — [ACE paper](https://arxiv.org/html/2510.04618)
- Ablations (AppWorld): without Reflector and without multi-epoch 55.1; without multi-epoch 56.8; full offline 59.4; online without GT 56.1, with offline warmup 59.5 — [ACE paper](https://arxiv.org/html/2510.04618)
- Stated limitations: ACE depends on a reasonably strong Reflector, otherwise the context becomes noisy or harmful; without reliable feedback (GT labels or execution outcomes) ACE and DC can degrade (FiNER online); some tasks (HotPotQA, Game of 24) need only a short rule — [ACE paper](https://arxiv.org/html/2510.04618)

**Reference code (github.com/ace-agent/ace)**
- Bullet line format, parsed by regex: `[id] helpful=X harmful=Y :: content` — [ace/playbook_utils.py](https://github.com/ace-agent/ace/blob/main/playbook_utils.py)
- `update_bullet_counts(playbook, bullet_tags)`: for each tag dict `{id, tag}`, `helpful += 1` if tag is "helpful", `harmful += 1` if "harmful", "neutral" makes no change; if no tags, it prints "No valid bullet tags found" and returns the playbook unchanged. There is no decay, normalization, or outcome weighting — [ace/playbook_utils.py](https://github.com/ace-agent/ace/blob/main/playbook_utils.py)
- The Generator prompt asks the model to output a JSON `bullet_ids` list of "all bulletpoints in the playbook that's relevant, helpful for you"; IDs are parsed from JSON with a regex fallback — [ace/ace/prompts/generator.py](https://github.com/ace-agent/ace/blob/main/ace/prompts/generator.py), [ace/ace/core/generator.py](https://github.com/ace-agent/ace/blob/main/ace/core/generator.py)
- `extract_playbook_bullets(playbook, bullet_ids)` returns the literal string "(No bullets used by generator)" when `bullet_ids` is empty, and that string is what the Reflector receives as `bullets_used` — [ace/playbook_utils.py](https://github.com/ace-agent/ace/blob/main/playbook_utils.py), [ace/ace/ace.py](https://github.com/ace-agent/ace/blob/main/ace/ace.py)
- The Reflector runs on both wrong answers (with retry rounds, feedback "Predicted answer does not match ground truth") and correct answers ("still run reflector to tag helpful bullets") — [ace/ace/ace.py](https://github.com/ace-agent/ace/blob/main/ace/ace.py)
- Selection for context: the whole playbook string is formatted into the Generator prompt (`GENERATOR_PROMPT.format(playbook, reflection, question, context)`); the reference code has no retrieval or top-k step — [ace/ace/core/generator.py](https://github.com/ace-agent/ace/blob/main/ace/core/generator.py)
- Curator: only `ADD` is implemented in `apply_curator_operations`; UPDATE, MERGE, CREATE_META and DELETE are commented "TODO: Future operation types (not implemented yet)" — [ace/playbook_utils.py](https://github.com/ace-agent/ace/blob/main/playbook_utils.py)
- `playbook_token_budget` defaults to 80,000 and is only passed into the Curator prompt text; it is not enforced by code — [eval/finance/run.py](https://github.com/ace-agent/ace/blob/main/eval/finance/run.py), [ace/ace/core/curator.py](https://github.com/ace-agent/ace/blob/main/ace/core/curator.py)
- Dedup (`BulletpointAnalyzer`) is off by default (`use_bulletpoint_analyzer=False`); when on, it embeds bullets with `all-mpnet-base-v2`, groups pairs with cosine similarity `>= 0.90`, and asks an LLM to merge each group, summing counters: "Use combined counts: helpful={total_helpful} harmful={total_harmful}" — [ace/ace/core/bulletpoint_analyzer.py](https://github.com/ace-agent/ace/blob/main/ace/core/bulletpoint_analyzer.py), [ace/ace/ace.py](https://github.com/ace-agent/ace/blob/main/ace/ace.py)
- Statistics only (not used for selection): "high_performing" = helpful > 5 and harmful < 2; "problematic" = harmful >= helpful and harmful > 0; "unused" = helpful + harmful = 0 — [ace/playbook_utils.py](https://github.com/ace-agent/ace/blob/main/playbook_utils.py)
- A separate community implementation exists (kayba-ai/agentic-context-engine, pip `ace-framework`); its relation to the official repo is unclear — [search result](https://github.com/lineCode/agentic-context-engine)

**Dynamic Cheatsheet (Suzgun, Yuksekgonul, Bianchi, Jurafsky, Zou; arXiv 2504.07952, Apr 2025)**
- Persistent, self-curated memory of short reusable snippets, with no ground-truth labels or human feedback — [Dynamic Cheatsheet](https://arxiv.org/abs/2504.07952)
- Claude 3.5 Sonnet AIME accuracy "more than doubled"; GPT-4o Game of 24 from about 10% to 99% after reusing a Python solution; GPQA-Diamond +9% and MMLU-Pro +8% for Claude — [Dynamic Cheatsheet](https://arxiv.org/abs/2504.07952)

### Inferences
- In ACE, citation is the sole credit channel: credit is gated by self-report, then judged by the Reflector. With a 4B Generator that never cites, ACE's counters degrade to "count of near-duplicate proposals" exactly as the user sees.
- ACE's reported gains come mostly from the growing playbook content (ADD), not from counters, since the reference code never uses counters for selection or pruning. So frozen counters may cost little in ACE's own regime, but the user loses any pruning signal.
- The FiNER online without-GT result (-3.4) is direct evidence that with weak feedback, an ACE playbook can be net harmful.
- Cheap fix consistent with ACE's design: pass all in-context bullets (or a retrieved top-k) to the Reflector when `bullet_ids` is empty, instead of the empty placeholder string.

### Gaps
- ACE appendix hyperparameters (the paper's dedup threshold, Reflector rounds) were beyond the fetched portion; the 0.90 value is from code defaults.
- Dynamic Cheatsheet DC-Cu vs DC-RS details and any model-size analysis were not in the abstract page.

## 2. Other experience-memory systems: how they score lessons

### Takeaway
Lesson-value schemes fall into three families: LLM-voted importance counts (ExpeL, ACE), outcome-labelled memories with similarity retrieval and no explicit value (ReasoningBank, AWM, Voyager), and learned utility values updated from task reward (Memento, MemRL, Memory-R1).
The value-learned family reports the strongest evidence that scoring improves over similarity-only retrieval.

### Cited Findings
**ExpeL (Zhao et al., arXiv 2308.10144, AAAI 2024; code LeapLabTHU/ExpeL)**
- Rule operations parsed by regex `(REMOVE|EDIT|ADD|AGREE)( \d+)?: text` — [ExpeL agent/expel.py](https://github.com/LeapLabTHU/ExpeL/blob/main/agent/expel.py)
- Exact update rule in `update_rules`: ADD appends with count 2; AGREE +1; EDIT replaces text and +1; REMOVE -1, or -3 when the list is full (`list_full = max_num_rules + 5 <= len(rules)`); an ADD that matches an existing rule is dropped; an EDIT whose text matches an existing rule becomes AGREE; rules with count <= 0 are deleted; rules are sorted by count descending. Ops are applied in order REMOVE, AGREE, EDIT, ADD — [ExpeL agent/expel.py](https://github.com/LeapLabTHU/ExpeL/blob/main/agent/expel.py)
- Votes come from an LLM comparing success/failure trajectory pairs and success sets against the existing rule list, not from per-task usage — [ExpeL agent/expel.py](https://github.com/LeapLabTHU/ExpeL/blob/main/agent/expel.py)
- Few-shot successful trajectories are retrieved by embedding cosine similarity to the task or current thought — [ExpeL agent/expel.py](https://github.com/LeapLabTHU/ExpeL/blob/main/agent/expel.py)

**ReasoningBank (arXiv 2509.25140, v2 Mar 2026)**
- Memory item = title, one-sentence description, content; at most 3 items per trajectory; consolidation is "simple addition", with no dedup or pruning described — [ReasoningBank](https://arxiv.org/html/2509.25140)
- Success/failure is labelled by an LLM judge without ground truth; judge accuracy 72.7% on WebArena-Shopping (Gemini-2.5-flash); simulated judges at 70-90% accuracy performed similarly to a ground-truth judge — [ReasoningBank](https://arxiv.org/html/2509.25140)
- Retrieval: gemini-embedding-001 cosine, default top-k = 1 — [ReasoningBank](https://arxiv.org/html/2509.25140)
- WebArena success +8.3 / +7.2 / +4.6 over no memory (Gemini-2.5-flash, -pro, Claude-3.7-sonnet); SWE-Bench-Verified 34.2 to 38.8 (flash), 54.0 to 57.4 (pro) — [ReasoningBank](https://arxiv.org/html/2509.25140)
- Learning from failures: ReasoningBank 46.5 (successes only) to 49.7 (with failures); Synapse 40.6 to 41.7; AWM 44.4 to 42.2 (failures hurt AWM) — [ReasoningBank](https://arxiv.org/html/2509.25140)

**Training-Free GRPO (arXiv 2510.08191, Oct 2025)**
- Experience library updated per step with Add / Delete / Modify / Keep (prompt figure says merge instead of keep) — [Training-Free GRPO](https://arxiv.org/html/2510.08191)
- "Semantic advantage": G rollouts per query (5 math, 3 web), scored by a reward model, summarized, then compared against the library to extract a natural-language experience; computed only for groups with both successes and failures — [Training-Free GRPO](https://arxiv.org/html/2510.08191)
- Without ground truth (majority vote, self-discrimination, self-reflection): AIME24/25 80.7 / 68.9 vs 82.7 / 73.3 with GT (DeepSeek-V3.1-Terminus, ReAct; baseline 80.0 / 67.9) — [Training-Free GRPO](https://arxiv.org/html/2510.08191)
- Small/weak models gain little or lose: QwQ-32B WebWalkerQA 27.5 to 25.5; Qwen2.5-72B AIME 13.5/9.6 to 14.9/11.4; Qwen3-32B 29.1/19.5 to 33.5/25.4 — [Training-Free GRPO](https://arxiv.org/html/2510.08191)
- Cost: about $18 for 3 steps over 100 samples (38M input, 6.6M output tokens) — [Training-Free GRPO](https://arxiv.org/html/2510.08191)

**MemP (arXiv 2508.06433, v3 Jan 2026)**
- Procedural memory distilled into step-level instructions and script-like abstractions; studies Build, Retrieval and Update strategies; a dynamic regimen "updates, corrects, and deprecates" contents; gains on TravelPlanner and ALFWorld; memory built by a stronger model transfers to a weaker model with substantial gains — [MemP](https://arxiv.org/abs/2508.06433v1)

**Background, not re-fetched**
- Reflexion stores verbal self-reflections in an episodic buffer per task, without cross-task value scoring — [Reflexion](https://arxiv.org/abs/2303.11366)
- Voyager adds a skill (code) to its library only after the environment and a self-verification critic confirm success; skills are retrieved by embedding of their description — [Voyager](https://arxiv.org/abs/2305.16291)
- Agent Workflow Memory induces reusable workflows from (self-judged or GT) successful trajectories — [AWM](https://arxiv.org/abs/2409.07429)
- CLIN keeps a causal-abstraction memory updated across trials — [CLIN](https://arxiv.org/abs/2310.10134); AutoGuide extracts context-conditioned guidelines from offline contrasting trajectories — [AutoGuide](https://arxiv.org/abs/2403.08978); Synapse stores trajectories as exemplars — [Synapse](https://arxiv.org/abs/2306.07863); Agent KB shares cross-agent experience — [Agent KB](https://arxiv.org/abs/2507.06229)

### Inferences
- ExpeL's scheme is the closest lightweight alternative to ACE that does not depend on citations: a periodic LLM pass votes AGREE/REMOVE on rules given success/failure contrasts, with a +2 prior on new rules and harsher removal when full. This works with the user's deterministic grades as the success/failure label.
- ReasoningBank's result that a 70-90% accurate judge is about as good as ground truth suggests the user's deterministic grade (likely better than 70%) is a sufficient label for outcome-weighted updates.
- Training-Free GRPO's semantic advantage needs contrast within a group; a single-rollout harness rarely has it, and weak base models benefit little.

### Gaps
- I did not verify current details for AWM, CLIN, AutoGuide, Synapse, Agent KB, Voyager or Reflexion in this pass.
- ReasoningBank's retrieved-memory count ablation (appendix C.1) and limitations were not in the fetched portion.

## 3. Credit assignment when the agent does not cite: learned utility, counterfactuals, RL policies

### Takeaway
The strongest documented approach is to credit every injected memory with the episode reward through a running-average utility (MemRL, Memento), then rank by a mix of similarity and utility.
Counterfactual (with/without) drafting is the clean but costly alternative, and a 2026 study shows plain outcome credit can be almost uncorrelated with true contribution when outcomes are dominated by shared noise.

### Cited Findings
**MemRL (Zhang et al., arXiv 2601.03192, Jan 2026, code MemTensor/MemRL)**
- Memory = Intent-Experience-Utility triplets; a frozen LLM plus runtime RL over memory utilities — [MemRL abs](https://arxiv.org/abs/2601.03192)
- Two-phase retrieval: Phase A recalls top-k1 by intent cosine similarity above threshold delta (none pass means no memory); Phase B scores `score = (1 - lambda) * z(sim) + lambda * z(Q)` with z-score normalization, injects top-k2 — [MemRL](https://arxiv.org/html/2601.03192v2)
- Q update: Monte Carlo rule `Q_new = Q_old + alpha * (r - Q_old)`, applied to all memories actually injected into context; new experiences start at `Q_init`; reward bounded in [-1, 1] for success/failure — [MemRL](https://arxiv.org/html/2601.03192v2)
- lambda = 0.5 best; lambda = 0 (similarity only) plateaus; lambda near 1 is volatile; k1=5, k2=3 beat 3/1 and 10/5 — [MemRL](https://arxiv.org/html/2601.03192v2)
- Runtime CSR average 0.798 vs MemP 0.760, Mem0 0.712, RAG 0.699; transfer average 0.794 vs MemP 0.766 vs no memory 0.709 — [MemRL](https://arxiv.org/html/2601.03192v2)
- Q-values correlate with empirical success (Pearson r = 0.861); about 12% of top-Q memories are failures ("near misses"); removing z-normalization and similarity gating raises forgetting from 0.041 to 0.073 — [MemRL](https://arxiv.org/html/2601.03192v2)
- Limitation stated by authors: credit ambiguity when multiple experiences are referenced, suggesting Shapley-style attribution or value decomposition; step-wise updates add variance on long horizons — [MemRL](https://arxiv.org/html/2601.03192v2)

**Memento (arXiv 2508.16153, Aug 2025)**
- Case retrieval policy is a softmax over case Q-values with temperature alpha (entropy-regularized optimal policy); soft Q-learning update toward `r + gamma * alpha * log sum exp(Q(s', c') / alpha)` — [Memento](https://arxiv.org/html/2508.16153)
- Parametric variant: planning is single-step so the TD target collapses to immediate reward; reward is binary and MSE is replaced by cross-entropy; read returns top-K by learned Q — [Memento](https://arxiv.org/html/2508.16153)
- Non-parametric: top-K by SimCSE cosine; K = 4 best on DeepResearcher; parametric beat non-parametric in continual learning (85.44 vs 84.85 at iteration 5); GAIA validation 87.88% Pass@3 — [Memento](https://arxiv.org/html/2508.16153)

**Memory-R1 (arXiv 2508.19828, Aug 2025, v5 Jan 2026)**
- A Memory Manager learns ADD / UPDATE / DELETE / NOOP and an Answer Agent pre-selects entries, both trained with outcome-driven PPO and GRPO from 152 QA pairs, at 3B to 14B — [Memory-R1](https://arxiv.org/abs/2508.19828)

**Counterfactual credit**
- MemTrial (Wu, Cai, Wang, arXiv 2610.11732, Oct 2026): contribution `c_j = mean{U(S): j in S} - mean{U(S): j not in S}` over draft subsets, centred across the k retrieved items (Banzhaf main effect); 8 drafts per decision from a 2^(4-1) half-fraction over k = 4 — [MemTrial](https://arxiv.org/html/2610.11732v1)
- MemTrial proves outcome credit (mean utility of drafts that used an item) equals the day's mean utility plus half the contribution; empirically outcome credit correlates +0.91 to +0.97 with the market baseline but about -0.07 to +0.11 with true contribution; retrieved experiences changed utility on only 2-4 of 56 months; cost about 8x a single draft — [MemTrial](https://arxiv.org/html/2610.11732v1)
- ConMem (arXiv 2610.33432) counts a memory helpful only when an attribution auditor finds it was used and quality improved; What Eviction Destroys (arXiv 2609.08279) uses restore-counterfactual tests framed as leave-one-out context attribution; CounterMem (arXiv 2609.31874) scores records by whether they changed the action — [search summary](https://arxiv.org/html/2610.33432), [Eviction](https://arxiv.org/html/2609.08279v1), [CounterMem](https://arxiv.org/html/2609.31874v1) (details from search snippets only, not fetched)
- ContextCite (Cohen-Wang et al., 2024) ablates context sources and fits a sparse linear surrogate to attribute output to sources (background, not re-fetched) — [ContextCite](https://arxiv.org/abs/2409.00729)

### Inferences
- For the user's harness, MemRL-style update is a drop-in replacement for citation-gated counters: for every bullet in context (or top-k2 retrieved), `Q <- Q + alpha*(grade - Q)` using the deterministic session grade mapped to [-1, 1]. It needs no citation and no Reflector judgement.
- Because a whole playbook is in context (ACE style), crediting every bullet gives identical updates; credit only discriminates if bullets are retrieved per task (similarity gate) so different sessions see different subsets. This argues for adding top-k retrieval.
- MemTrial warns that with small samples (26 reflections) outcome credit will mostly track task difficulty; centring rewards per task type (subtract a running baseline) is the cheap partial fix it identifies.
- Occasional randomized omission of a bullet (an A/B exploration step) gives unbiased with/without estimates at low cost; this is a contextual bandit view and is my inference, not a paper result for this setting.

### Gaps
- MemRL numeric `Q_init`, alpha and delta values were not in the fetched portion.
- I found no paper evaluating learned memory utilities on a 4B model in coding agents; Mem-alpha (arXiv 2509.25911) was not checked.
- The 2026 ConMem / CounterMem / Eviction papers were seen only as search snippets.

## 4. Do small models reliably self-report which instructions they used?

### Takeaway
No direct study of playbook-ID citation by 4B models was found, but the citation literature shows self-reported citations are often post-rationalized and small models struggle with citation even in simple cases, so self-report is a weak credit signal; automated detectors are more reliable.

### Cited Findings
- Post-rationalization: models (tested on Command-R+) produce citations that do not reflect what drove the answer; shown by inserting text into irrelevant documents and observing citation changes; authors say broader validation is needed — [Correctness is not Faithfulness](https://arxiv.org/pdf/2412.18004)
- Small LLMs "struggle even for straightforward relations", and all models struggle with multi-document reasoning, in citation generation — [Citation Failure in LLMs](https://arxiv.org/html/2510.20303v1)
- Fine-tuned 7-8B detectors beat zero-shot large models at judging citation faithfulness (Llama-3.1-8B 90.6% test accuracy) — [CiteCheck](https://arxiv.org/pdf/2502.10881)
- Models identify relevant documents reliably (high Doc-F1) but fail to localize evidence spans — [Structured Inline Citation](https://arxiv.org/pdf/2606.07130)
- A widely repeated "57% of RAG citations are post-rationalized" figure could not be traced to a primary source — [blog, unverified](https://tianpan.co/blog/2026-04-23-rag-citations-post-hoc-rationalization)

### Inferences
- Alternatives to citation for the user: (a) applied-detection, matching a bullet's actionable content (command names, file patterns, flags, tool names) against the transcript's tool calls with string or embedding match; (b) an LLM judge asked "was bullet X applied?" per in-context bullet (ConMem's "used and improved" test); (c) outcome credit to all injected bullets (MemRL). (a) is deterministic and cheap and suits the user's deterministic grader.
- The many dropped lessons whose quoted evidence is not found suggest the same small-model weakness at span localization reported in the inline-citation work; fuzzy matching of evidence quotes, rather than exact matching, may be warranted.

### Gaps
- No primary source measured citation compliance for instruction/bullet IDs in agent contexts, or at 4B scale.

## 5. Failure modes and mitigations

### Takeaway
The documented failure modes are context collapse from whole rewrites, brevity bias, noise from weak Reflectors or feedback, and value drift from failure memories; mitigations are incremental deltas, non-LLM merges, outcome gating, and value-aware retrieval.

### Cited Findings
- Context collapse (18,282 to 122 tokens; 66.7 to 57.1) mitigated by itemized delta updates merged by deterministic logic — [ACE paper](https://arxiv.org/html/2510.04618)
- Without reliable feedback, playbooks can degrade below baseline (FiNER online -3.4) — [ACE paper](https://arxiv.org/html/2510.04618)
- Wrong lessons: ExpeL removes rules whose votes reach 0, with -3 per REMOVE when the list is full — [ExpeL code](https://github.com/LeapLabTHU/ExpeL/blob/main/agent/expel.py)
- Failure trajectories helped ReasoningBank but hurt AWM (44.4 to 42.2), so how failures are distilled matters — [ReasoningBank](https://arxiv.org/html/2509.25140)
- Value-aware retrieval reduced forgetting (0.041 vs MemP 0.051), and similarity gating plus z-normalization matter (0.073 without) — [MemRL](https://arxiv.org/html/2601.03192v2)
- Weak base models gain little or degrade from experience libraries (QwQ-32B -2.0 pass@1) — [Training-Free GRPO](https://arxiv.org/html/2510.08191)
- Outcome credit can be confounded by shared environment noise — [MemTrial](https://arxiv.org/html/2610.11732v1)

### Inferences
- For a 4B harness, the user's grade-gating of which lesson kinds may be added is consistent with ACE's and ReasoningBank's evidence that feedback quality governs whether memory helps.
- Recommended stack (inference): keep ACE deltas; replace citation-gated counters with per-injected-bullet utility `Q` updated from the deterministic grade; add similarity-gated top-k retrieval with `(1-lambda) z(sim) + lambda z(Q)`; add a deterministic "applied" detector to sharpen credit; prune ExpeL-style when Q falls below a floor after N exposures.

### Gaps
- No paper found that studies memory poisoning from self-generated wrong lessons specifically in coding agents; adversarial memory-poisoning literature was not covered.
