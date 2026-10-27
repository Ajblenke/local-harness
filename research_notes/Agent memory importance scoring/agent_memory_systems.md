# Importance, retention, and forgetting scoring in LLM agent memory systems (2023 to Oct 2026)

Context for the reader: the user's system ("pi", Qwen3.5-4B) uses an ACE-style playbook with weight = (1 + helpful - harmful) * 0.5^(days_since_seen / 60), 14-day probation, 6000-char budget, Jaccard 0.8 dedup, and archive rules (stale: 180 days and weight < 0.25; harmful: harmful >= 3 and harmful > helpful).
Every number below was read from the primary source in this session unless marked otherwise.

## Q1. Generative Agents (Park et al. 2023): exact retrieval score, reflection trigger, ablations

### Takeaway
The paper scores each memory as recency + importance + relevance, with all three weights set to 1 and min-max normalisation, recency decay 0.995 per game hour, and LLM-rated poignancy on a 1 to 10 scale; reflection fires when summed importance passes 150.
The released code does not match the paper: it decays by recency rank at 0.99 and weights relevance 3, importance 2, recency 0.5.
The ablations remove whole modules (reflection, planning, memory), not individual score terms, so there is no evidence on what the importance term adds by itself.

### Cited Findings
- Paper (arXiv 2304.03442, UIST 2023): "The retrieval function scores all memories as a weighted combination of the three elements": score = α_recency·recency + α_importance·importance + α_relevance·relevance. All α are 1, and each component is min-max normalised to [0,1] - [arXiv 2304.03442](https://arxiv.org/html/2304.03442)
- Recency is "an exponential decay function over the number of sandbox game hours since the memory was last retrieved". "Our decay factor is 0.995." - [arXiv 2304.03442](https://arxiv.org/html/2304.03442)
- Relevance is the cosine similarity between the memory embedding and the query embedding - [arXiv 2304.03442](https://arxiv.org/html/2304.03442)
- Importance prompt: "On the scale of 1 to 10, where 1 is purely mundane ... and 10 is extremely poignant", returning an integer. The paper's examples are 2 for cleaning up a room and 8 for asking a crush out on a date. It is scored once, at creation - [arXiv 2304.03442](https://arxiv.org/html/2304.03442)
- Reflection trigger: reflection runs when "the sum of the importance scores for the latest events perceived by the agents exceeds a threshold (150 in our implementation)", which works out to about 2 to 3 reflections per game day. Each reflection takes the 100 most recent records, asks for the "3 most salient high-level questions", then extracts "5 high-level insights" with citations to the evidence records - [arXiv 2304.03442](https://arxiv.org/html/2304.03442)
- Ablation (TrueSkill μ/σ, 100 human raters): full 29.89/0.72; no reflection 26.88/0.69; no reflection and no planning 25.64/0.68; crowdworker-authored 22.95/0.69; no memory, planning, or reflection 21.21/0.70. Kruskal-Wallis H(4)=150.29, p<0.001, and all pairs differ except crowdworker vs fully ablated - [arXiv 2304.03442](https://arxiv.org/html/2304.03442)
- Released code (`reverie/backend_server/persona/memory_structures/scratch.py`): `recency_w = 1`, `relevance_w = 1`, `importance_w = 1`, `recency_decay = 0.99`, `importance_trigger_max = 150` - [GitHub joonspk-research/generative_agents](https://github.com/joonspk-research/generative_agents/blob/main/reverie/backend_server/persona/memory_structures/scratch.py)
- Released code (`cognitive_modules/retrieve.py`, lines 242-249): the global weights are hard-coded as `gw = [0.5, 3, 2]` for recency, relevance, and importance, with `[1,1,1]` and `[1,2,1]` left commented out. Recency is computed as `recency_decay ** i` over memories ordered by last access, so it decays by rank rather than by elapsed hours - [GitHub retrieve.py](https://github.com/joonspk-research/generative_agents/blob/main/reverie/backend_server/persona/cognitive_modules/retrieve.py)

### Inferences
- In the code that actually ran, relevance carries 6 times the weight of recency and importance carries 4 times, so it is effectively a relevance-first ranker. Anyone reproducing the paper's formula should state which version they use.
- The user's weight multiplies a usage-derived value (helpful - harmful) by a time decay. Generative Agents instead adds a one-shot LLM importance to recency and relevance. The two are structurally different, and the user's version has no query-relevance term in the snapshot.
- Rank-based decay (0.99^rank) depends on how many memories exist, not on wall-clock time. The user's 60-day half-life on wall-clock days is the more interpretable choice for a store that is used intermittently.

### Gaps
- No study found that ablates the importance term alone in Generative Agents.
- The paper does not report how consistent the 1 to 10 importance ratings were.

## Q2. Catalogue of scoring, retention, and forgetting mechanisms by system

### Takeaway
Systems fall into five families:
- Hand-tuned decay with reinforcement on recall: MemoryBank, LUFY, FadeMem, MemoryOS heat.
- LLM-decided CRUD at write time with no decay at all: Mem0, A-MEM, MIRIX, and the original ACE.
- Capacity-triggered eviction and summarisation: MemGPT/Letta, MemoryOS, MIRIX core rewrite.
- Temporal invalidation in a graph: Zep/Graphiti, Mem0g.
- Write and delete policies learned with RL: Memory-R1, Mem-α.
Graph centrality (HippoRAG PPR) is used for ranking at retrieval time, not for retention.

### Cited Findings
**MemoryBank (Zhong et al., arXiv 2305.10250, May 2023; later AAAI 2024 according to LUFY's citation "Zhong et al., 2024", not verified here)**
- Retention follows the Ebbinghaus curve, "R = e^(-t/S)", where t is the time elapsed and S is memory strength. "We model S as a discrete value and initialize it with 1 upon its first mention." On recall, "We increase S by 1 and reset t to 0." The authors call it "an exploratory and highly simplified memory updating model" - [MemoryBank PDF](https://arxiv.org/pdf/2305.10250)
- The forgetting target is "less important memory pieces that are long time ago and have not been recalled much" - [MemoryBank PDF](https://arxiv.org/pdf/2305.10250)

**LUFY (Sumida, Inoue, Kawahara; Dialogue & Discourse 16(2), 2025, pp. 74-110; arXiv 2409.12524)**
- Strength: S = wA·A + wP·P + wL·L + wR1·R1 − wR2·R2, where A is emotional arousal (RoBERTa fine-tuned on EmoBank), P is surprise (GPT-2-Large perplexity of the user utterance given the system turn, capped at 160), L is LLM-estimated importance, R1 counts how often the memory was retrieved first, and R2 how often it was retrieved second (retrieval-induced forgetting). Importance = e^(−Δt/S), where Δt is the time since last access - [LUFY PDF](https://arxiv.org/pdf/2409.12524)
- Weights were fitted with Levenberg-Marquardt to human labels, with 10% of utterances labelled important to mimic the finding that people recall about 10% of a conversation. Fitted values: wA 2.76, wP −0.28, wL 0.44, wR1 1.02, wR2 −0.012. "Arousal has the highest weight." Note that the LLM-estimated importance weight is about 1/6 of the arousal weight - [LUFY PDF](https://arxiv.org/pdf/2409.12524)
- Retrieval uses a cosine threshold of 0.8, and forgetting runs after each conversation. In a human study (17 participants, ten 30-minute sessions over 4 days), "prioritizing emotionally engaging memories while forgetting most of the conversation significantly enhanced user experience", and LUFY improved retrieval "by over 17% compared to the naive RAG system with no forgetting mechanism" - [LUFY PDF](https://arxiv.org/pdf/2409.12524)

**FadeMem (Wei et al., arXiv 2601.18642, Jan 2026)**
- Importance: I_i(t) = α·rel(c_i, Q_t) + β·f_i/(1+f_i) + γ·exp(−δ(t−τ_i)), where frequency is a time-decayed access rate f̃_i = Σ_j exp(−κ(t−t_j)) - [arXiv 2601.18642](https://arxiv.org/html/2601.18642v2)
- Decay: v_i(t) = v_i(0)·exp(−λ_i·(t−τ_i)^β_i), with λ_i = λ_base·exp(−μ·I_i), λ_base = 0.1, β = 0.8 in the long-term layer (sub-linear) and 1.2 in the short-term layer (super-linear). At I = 0 the half-life is about 11.25 days (long-term) or 5.02 days (short-term) - [arXiv 2601.18642](https://arxiv.org/html/2601.18642v2)
- Layers use hysteresis: θ_promote = 0.7 and θ_demote = 0.3. Access reinforcement has diminishing returns: v ← v + Δv·(1−v)·exp(−n_i/N). Memories are pruned below ε_prune or when dormant longer than T_max (values not given). Capacity is 1000 long-term and 500 short-term - [arXiv 2601.18642](https://arxiv.org/html/2601.18642v2)
- Results: 45% storage reduction and 82.1% critical-fact retention. Ablations on LoCoMo multi-hop F1 (full 29.43): without fusion 13.63, without the dual layer 19.45, without conflict resolution 22.88. No "without decay" ablation is reported - [arXiv 2601.18642](https://arxiv.org/html/2601.18642v2)

**MemoryOS (Kang et al., arXiv 2506.06326, Jun 2025; EMNLP 2025 per the authors' repo, not verified here)**
- Mid-term segments carry Heat = α·N_visit + β·L_interaction + γ·R_recency, where R_recency = exp(−Δt/μ), Δt is in seconds, α = β = γ = 1, and μ = 1e7 s (about 116 days). Segments with heat above τ = 5 are promoted to long-term persona memory, after which L_interaction is reset to 0. When the store is full (200 segments), the lowest-heat segment is evicted. Short-term memory is a FIFO queue of 7 pages - [arXiv 2506.06326](https://arxiv.org/html/2506.06326)
- A page joins a segment when cos(e_s, e_p) + Jaccard(K_s, K_p) > θ = 0.6, where K are LLM-extracted keyword sets. The user KB and agent traits are FIFO with 100 entries each - [arXiv 2506.06326](https://arxiv.org/html/2506.06326)
- Reported LoCoMo average gains are +49.11% F1 and +46.18% BLEU-1 over baselines (GPT-4o-mini), with the largest gain on temporal questions (+118.8% F1) - [arXiv 2506.06326](https://arxiv.org/html/2506.06326)

**MemGPT / Letta (Packer et al., arXiv 2310.08560, Oct 2023)**
- No importance score is used. The queue manager inserts a memory-pressure warning when prompt tokens exceed a "warning token count (e.g. 70% of the context window)". At the "flush token count (e.g. 100%)" it evicts messages "(e.g. 50% of the context window)" into a recursive summary, and evicted messages stay searchable in recall storage. Working context is a fixed-size block "writeable only via MemGPT function calls", so the LLM itself decides what to keep - [arXiv 2310.08560](https://arxiv.org/html/2310.08560)
- DMR accuracy: GPT-4 32.1% rising to 92.5% with MemGPT; GPT-4 Turbo 35.3% rising to 93.4% - [arXiv 2310.08560](https://arxiv.org/html/2310.08560)

**Mem0 / Mem0g (Chhikara et al., arXiv 2504.19413, Apr 2025)**
- For each message pair, an LLM extracts candidate facts using the summary plus the last m = 10 messages. Each fact is compared with the top s = 10 similar memories, and the LLM chooses ADD (no equivalent memory), UPDATE (augments one; replaced only if the new fact has more information), DELETE (contradicts one), or NOOP. All operations use GPT-4o-mini. There is no decay or importance score - [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- Mem0g: obsolete relationships are "marked invalid rather than physically removed", which enables temporal reasoning - [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- LoCoMo J scores: full-context 72.90, Mem0g 68.44, Mem0 66.88, Zep 65.99, LangMem 58.10, OpenAI 52.90, A-Mem 48.38. p95 latency is 1.44 s for Mem0 vs 17.12 s for full context. Tokens retrieved per query are 1764 (Mem0) vs 26031 (full context) - [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- Caveat: these are vendor-run comparisons. The MIRIX paper reports Zep at 79.09 J with gpt-4.1-mini, which suggests baseline numbers vary widely with backbone and setup - [arXiv 2507.07957](https://arxiv.org/html/2507.07957)

**A-MEM (Xu et al., arXiv 2502.12110, Feb 2025; v11 Oct 2025; NeurIPS checklist attached)**
- A note holds content, a timestamp, LLM keywords, tags, a context description, an embedding (all-MiniLM-L6-v2), and links. Links are chosen by the LLM from the top-k cosine neighbours. "Memory evolution" lets the LLM rewrite the neighbours' context, keywords, and tags. The paper describes no forgetting mechanism, and the "prune" action appears only in a prompt template - [arXiv 2502.12110](https://arxiv.org/html/2502.12110)
- Ablation (LoCoMo F1, GPT-4o-mini), shown as no linking or evolution / no evolution / full: multi-hop 9.65/21.35/27.02; temporal 24.55/31.24/45.85; single-hop 13.28/39.17/44.65. Memory operations cost about 1.2k to 2.5k tokens vs 16.9k for full context - [arXiv 2502.12110](https://arxiv.org/html/2502.12110)

**Zep / Graphiti (Rasmussen et al., arXiv 2501.13956, Jan 2025)**
- Bi-temporal edges: t_valid/t_invalid record when a fact held, and t'_created/t'_expired record when the system learned or retired it. An LLM compares a new edge with semantically related edges, and on "temporally overlapping contradictions" sets the old edge's t_invalid to the new edge's t_valid. Nothing is deleted - [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- Ranking uses RRF, MMR, episode-mentions frequency, node distance from a centroid, and a cross-encoder. Communities are built by label propagation - [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- DMR: 94.8% vs MemGPT 93.4%. LongMemEval (gpt-4o): 71.2% vs full context 60.2%, with latency 2.58 s vs 28.9 s - [arXiv 2501.13956](https://arxiv.org/html/2501.13956)

**HippoRAG 2 (Jiménez Gutiérrez et al., arXiv 2502.14802, Feb 2025, ICML template)**
- Ranking is Personalized PageRank (damping 0.5). Phrase seeds are weighted by the average score of the LLM-filtered top-5 triples. Passage seeds are weighted by embedding similarity × 0.05. If the filter keeps no triples, it falls back to dense retrieval. There is no forgetting or deletion - [arXiv 2502.14802](https://arxiv.org/html/2502.14802)
- Average F1 is 59.8 vs 57.0 for NV-Embed-v2, and 2Wiki F1 is 71.0 vs 61.5 - [arXiv 2502.14802](https://arxiv.org/html/2502.14802)

**MemOS (arXiv 2507.03724, v4 Dec 2025)**
- MemCube metadata includes Lifespan Policy ("TTL or decay rules"), Priority Level, Access Patterns ("frequency and recency"), and a Version Chain. Frequently recalled plaintext is promoted to KV cache, and "underutilized KV entries may be downgraded ... and archived to cold storage." The scheduler weighs "contextual similarity, access frequency, temporal decay, and priority tags", but the paper gives no formula or weights - [arXiv 2507.03724](https://arxiv.org/html/2507.03724)

**MIRIX (Wang and Chen, arXiv 2507.07957, Jul 2025)**
- Six stores: core, episodic, semantic, procedural, resource, and knowledge vault. Core memory is rewritten when it passes 90% of capacity. There is no decay or importance scoring. LoCoMo J is 85.38 vs full context 87.52 and Zep 79.09 (gpt-4.1-mini) - [arXiv 2507.07957](https://arxiv.org/html/2507.07957)

**Memory-R1 (arXiv 2508.19828, Aug 2025, v5 Jan 2026)**
- An RL-trained (PPO/GRPO) memory manager chooses ADD/UPDATE/DELETE/NOOP. Its reward is exact-match correctness of a frozen answer agent, with no labels for individual operations. Training uses 152 QA pairs. The answer agent distils 60 retrieved memories - [arXiv 2508.19828](https://arxiv.org/html/2508.19828)
- LoCoMo with LLaMA-3.1-8B: Mem0 F1/B1/J 30.41/22.22/45.68 vs Memory-R1-GRPO 45.02/37.51/62.74. With Qwen-2.5-7B: Mem0 J 53.30 vs GRPO 61.51. Removing RL from the memory manager drops PPO J from 57.5 to 49.0 - [arXiv 2508.19828](https://arxiv.org/html/2508.19828)

**Mem-α (arXiv 2509.25911, Sep 2025)**
- Trained on Qwen3-4B with RL over core memory (512 tokens, update only), semantic memory, and episodic memory (insert/update/delete). Reward is r = r1 (QA correctness) + r2 (tool-call format) + β·r3 (compression, β = 0.05) + γ·r4 (operation validity judged by Qwen3-32B). The paper is inconsistent about γ, giving both 1 and 0.1 - [arXiv 2509.25911](https://arxiv.org/html/2509.25911)
- Trained on at most 30k tokens, it generalises to over 400k (474K) tokens. MemoryAgentBench score: Mem-α 0.592 vs long-context 0.461 and RAG-top2 0.502. The untrained Qwen3-4B in the same framework scored 0.389 on validation (vs 0.642 after training) - [arXiv 2509.25911](https://arxiv.org/html/2509.25911)

**ACE (Zhang et al., arXiv 2510.04618, Oct 2025, v3 Mar 2026; the user's base design)**
- Bullets carry an id plus helpful/harmful counters. "Grow-and-refine" appends new bullets, updates counters in place, and de-duplicates by semantic embeddings, either after each delta or lazily when the context window is exceeded. Merging is non-LLM deterministic logic - [arXiv 2510.04618](https://arxiv.org/html/2510.04618)
- Ablation (AppWorld, DeepSeek-V3.1, ReAct baseline 42.4): full ACE 59.4; no multi-epoch 56.8; no reflector and no multi-epoch 55.1; online with offline warmup 59.5 vs 56.1 without - [arXiv 2510.04618](https://arxiv.org/html/2510.04618)

**A-MAC (arXiv 2603.04549, Mar 2026): admission rather than retention**
- S(m) = w1·U + w2·C + w3·N + w4·R + w5·T, admitted if S ≥ θ* = 0.55, with weights grid-searched by 5-fold CV. U (utility) is the only LLM-rated term. C is ROUGE-L grounding, N is 1 − max cosine to existing memories, R = exp(−0.01·hours), and T is a rule-based content-type prior - [arXiv 2603.04549](https://arxiv.org/html/2603.04549)

### Inferences
- The user's design sits closest to MemoryBank, LUFY, and FadeMem (exponential decay with usage reinforcement) combined with ACE counters. The parameter scales are comparable: the user's half-life is 60 days, FadeMem's is about 5 to 11 days at zero importance, MemoryOS's time constant μ ≈ 116 days, and A-MAC's half-life is about 69 hours.
- A common pattern the user lacks is hysteresis between promotion and demotion thresholds (FadeMem 0.7/0.3). It prevents bullets from flickering in and out of the 6000-char snapshot.
- FadeMem's diminishing-returns reinforcement (1−v)·exp(−n/N) bounds rich-get-richer effects. The user's linear (1 + helpful) term is unbounded.

### Gaps
- Not fetched or verified this session: LangMem, Cognee, RecallM, SCM (Self-Controlled Memory), ChatDB, MemoryLLM, Larimar, and Letta's current source defaults (for example sleep-time agents).
- None of these appear to use per-item importance decay in their papers, but this was not confirmed here.
- MemOS LoCoMo numbers were not extractable.

## Q3. LLM-rated importance vs computed signals; how reliable are LLM ratings, especially from small models?

### Takeaway
LLM-rated importance is used by Generative Agents (sole importance signal), LUFY (one of five features), A-MAC (utility), and implicitly by the CRUD systems (Mem0, A-MEM, Memory-R1).
Computed signals dominate where weights were fitted or ablated.
In LUFY's fitted weights, arousal (2.76) and top-retrieval count (1.02) outweigh LLM importance (0.44).
In A-MAC, the rule-based type prior matters most and LLM utility is a minor contributor.
No source directly measured inter-rater consistency of 1 to 10 importance scores from small models.
The closest evidence is that vanilla-LLM evaluators deciding what to add or delete can make memory worse than a fixed memory.

### Cited Findings
- LUFY's fitted weights: arousal 2.76, perplexity −0.28, LLM importance 0.44, R1 1.02, R2 −0.012 - [LUFY PDF](https://arxiv.org/pdf/2409.12524)
- A-MAC ablation (F1, full 0.583): without type prior 0.476 (−0.107); without novelty 0.555; without LLM utility 0.560 (−0.023); without confidence 0.568; without recency 0.570. Using one LLM call instead of A-MEM's several cut latency from 3831 to 2644 ms. The paper "does not measure the reliability" of LLM ratings - [arXiv 2603.04549](https://arxiv.org/html/2603.04549)
- Xiong et al. (arXiv 2505.16067, May 2025) on add-all vs selective addition, final performance:

  | Strategy | RegAgent SR | EHRAgent acc | AgentDriver SR | CIC-IoT acc |
  |---|---|---|---|---|
  | Fixed memory | 67.53 | 16.75 | 40.11 | 71.50 |
  | Add-all | 55.48 | 13.05 | 32.32 | 59.90 |
  | Strict (oracle) evaluator | 70.95 | 38.50 | 51.00 | 85.40 |

  "Directly applying a vanilla LLM as a trajectory evaluator ... may lead to a more severe negative impact", while an evaluator fine-tuned on only 300 trajectories gave strong gains - [arXiv 2505.16067](https://arxiv.org/html/2505.16067)
- In the same paper, LLM deletion evaluators separate good from bad records only weakly. On EHRAgent, deleted vs retained records were correct 36.3% vs 44.1% (GPT-4o-mini) and 32.1% vs 49.1% (GPT-4.1-mini). A GPT-4o-mini history-based deleter improved EHRAgent but "leads to degraded performance on AgentDriver" - [arXiv 2505.16067](https://arxiv.org/html/2505.16067)
- Small-model evidence: Mem-α's untrained Qwen3-4B memory constructor scored 0.389 vs 0.642 after RL, and the authors dropped Qwen3-8B for instruction-following problems - [arXiv 2509.25911](https://arxiv.org/html/2509.25911)
- Memory-R1: an untrained 8B memory manager (same pipeline, no RL) gets J 49.0 vs 57.5 with PPO - [arXiv 2508.19828](https://arxiv.org/html/2508.19828)
- TRUSTMEM (arXiv 2606.25161) adds a verifier against omission, corruption, and hallucination in memory updates. Removing it dropped the score from 66.3 to 58.7 (search snippet only, page not fetched) - [arXiv 2606.25161](https://arxiv.org/pdf/2606.25161)
- A learned 22K-parameter MLP scorer, trained on whether messages were later re-accessed, reports 0.786 recall at 77% token savings. This is an SSRN single-author preprint, seen only as a search snippet - [SSRN 6576151](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6576151)

### Inferences
- The user's design already avoids LLM-rated importance by deriving weight from outcome feedback (helpful/harmful). The evidence above supports that choice for a 4B model.
- The weak link is the 4B Reflector deciding what to ADD and assigning helpful/harmful. Xiong et al. show that unfiltered or weakly filtered additions accumulate errors (add-all fell below fixed memory on all four agents). This argues for keeping probation plus harmful-based archiving, and possibly adding a cheap computed admission check such as novelty or grounding, in the style of A-MAC.
- Counters that come from task outcomes, as in Memory-R1's correctness reward, are a more reliable signal than LLM self-judgement.

### Gaps
- No paper found that measures test-retest or inter-model agreement of 1 to 10 importance ratings, or how that agreement varies with model size.

## Q4. Handling conflicting or outdated memories: invalidation vs decay vs deletion

### Takeaway
There are three strategies:
- Hard delete or overwrite, decided by an LLM: Mem0 DELETE/UPDATE, Memory-R1, A-MEM evolution (overwrites neighbours).
- Soft invalidation with history kept: Zep bi-temporal t_invalid, Mem0g "marked invalid", MemOS version chains and archive states.
- Decay-based suppression: FadeMem multiplies the older memory's strength down, and MemoryBank and LUFY let unrecalled items fade.
The temporal-question results favour invalidation or graph approaches.

### Cited Findings
- Zep sets the old edge's t_invalid to the new edge's t_valid on contradiction, keeping both timelines for audit - [arXiv 2501.13956](https://arxiv.org/html/2501.13956)
- On LoCoMo temporal J, Mem0g (graph invalidation) scored 58.13 vs Mem0 55.51, OpenAI memory 21.71, and LangMem 23.43 - [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- FadeMem has an LLM label each pair as compatible, contradictory, subsumes, or subsumed. Compatible pairs have importance scaled by (1 − ω·sim). For contradictory pairs, the old memory is suppressed as v·exp(−ρ·clip(age gap/W_age, 0, 1)). Removing conflict resolution cut multi-hop F1 from 29.43 to 22.88 - [arXiv 2601.18642](https://arxiv.org/html/2601.18642v2)
- MemOS lists the lifecycle states Generated, Activated, Merged, Archived, Expired (and also mentions "Frozen", inconsistently) with a version chain - [arXiv 2507.03724](https://arxiv.org/html/2507.03724)
- History-based deletion (Xiong et al.) removes a record once it has been retrieved more than n times with average utility ≤ β. With a strict evaluator, EHRAgent rose from 38.67 to 42.06 and CIC-IoT from 85.40 to 89.60 - [arXiv 2505.16067](https://arxiv.org/html/2505.16067)

### Inferences
- The user's "harmful" archive rule (harmful ≥ 3 and harmful > helpful) is close to Xiong et al.'s history-based deletion, which is supported by evidence.
- The user's ADD-only Curator has no way to supersede a contradicted bullet. Two contradictory bullets would both survive until counters or decay separate them. A Zep-style "superseded_by / invalid_at" field in the archive is a cheap analogue that keeps history.

### Gaps
- No head-to-head study isolating invalidation vs deletion vs decay on the same benchmark was found.

## Q5. Reported failure modes: bloat, context collapse, rich-get-richer, stale memories

### Takeaway
The documented failure modes are:
- Context collapse and brevity bias from monolithic LLM rewrites (ACE).
- Error propagation and misaligned experience replay from over-eager addition (Xiong et al.).
- Storage bloat, for example Zep using over 600k tokens per conversation vs about 7k for Mem0.
- Security and availability risks from badly tuned eviction.
Rich-get-richer is not measured anywhere directly.
The mitigations that exist are diminishing-returns reinforcement (FadeMem) and LUFY's retrieval-induced-forgetting term.

### Cited Findings
- Context collapse: in AppWorld, a full rewrite at step 60 shrank context from 18,282 to 122 tokens and accuracy from 66.7 to 57.1, below the 63.7 unadapted baseline. Brevity bias is defined as "the tendency of optimization to collapse toward short, generic prompts" - [arXiv 2510.04618](https://arxiv.org/html/2510.04618)
- Error propagation: "inaccuracies in past experiences compound and degrade future performance". Misaligned replay: "some seemingly correct executions can provide limited or even misleading value as experiences" - [arXiv 2505.16067](https://arxiv.org/html/2505.16067)
- Bloat: periodic deletion cut RegAgent memory from 3511 to 1012 records at a small cost (SR 63.18 to 60.88). Combined deletion cut memory by about 70 to 79% with mixed accuracy effects - [arXiv 2505.16067](https://arxiv.org/html/2505.16067)
- Memory footprint per conversation: Mem0 about 7k tokens, Mem0g about 14k, Zep "in excess of 600k" - [arXiv 2504.19413](https://arxiv.org/html/2504.19413)
- Stale or contradictory memories are the motivating problem for Zep's invalidation and FadeMem's contradiction suppression (see Q4)
- LUFY's R2 term down-weights memories that are repeatedly retrieved second, following retrieval-induced forgetting. Its fitted weight (−0.012) is tiny, so in practice it does little - [LUFY PDF](https://arxiv.org/pdf/2409.12524)
- FadeMem reinforces with Δv·(1−v)·exp(−n_i/N), which saturates with repeated access in a window - [arXiv 2601.18642](https://arxiv.org/html/2601.18642v2)
- Security survey: poorly tuned eviction enables denial-of-retrieval or "perpetual retention of sensitive entries" (search snippet) - [arXiv 2604.16548](https://arxiv.org/html/2604.16548)
- Always-On Agents survey: most work applies decay uniformly despite heterogeneous state (search snippet) - [arXiv 2606.30306](https://arxiv.org/pdf/2606.30306)
- The 2026 "second half" survey says forgetting is mostly heuristic (recency decay, importance thresholds), with learned retention emerging (search snippet) - [arXiv 2602.06052](https://arxiv.org/html/2602.06052v4)

### Inferences
- The user's (1 + helpful − harmful) multiplier gives bullets that are shown more often more chances to be marked helpful. That is a rich-get-richer loop, and 60-day decay only partly checks it, because "seen" resets the clock.
- Possible mitigations, by analogy rather than from any study:
  - Saturate the helpful term, for example log1p(helpful) or helpful/(1+helpful) as in FadeMem's f/(1+f).
  - Normalise helpful by times shown.
  - Decay from last helpful rather than last seen.
- ADD-only delta updates with deterministic merging, as in the user's design, are exactly ACE's defence against context collapse. The 6000-char cap brings back a selection problem that ACE handles with lazy dedup.

### Gaps
- No empirical study of rich-get-richer or popularity bias in agent memory retrieval was found.
- "Control-Plane Placement Shapes Forgetting" (arXiv 2606.15903) and "Selective Forgetting" (arXiv 2608.28978) were seen only as titles, so their results are unknown.
