# Memory selection, consolidation, and evaluation for a small local coding agent

Context for the report writer: the target system is a Qwen3.5-4B coding agent on llama.cpp (8 GiB GPU), with about 30 one-line lesson bullets in four sections, ranked by a decayed helpful/harmful weight and packed into a frozen 6000-char (roughly 1.5k token) system-prompt snapshot per session, deduplicated by token Jaccard >= 0.8.
Research done 2026-10-27; about 20 tool calls, so some items rely on abstracts or secondary summaries and are flagged as such.

## (a) Relevance-aware and budget-aware selection

### Takeaway
The strongest evidence says fewer, more relevant memories beat more memories: ReasoningBank's best result used a single retrieved memory (k=1), and success fell monotonically as k rose to 4, while Chroma and Anthropic both report that focused context beats full context even when everything relevant is present.
For a frozen per-session snapshot, the practical design is to select once at session start, conditioned on the first user message and project, using a hybrid score (relevance + importance + recency) and a budgeted greedy/MMR selector, then keep that snapshot byte-stable for the rest of the session.

### Cited Findings
- ReasoningBank (Google Cloud AI Research, arXiv 2509.25140, ICLR 2026) embeds the task query with gemini-embedding-001, retrieves by cosine similarity, and injects the retrieved items into the system prompt; default top-k = 1 - [ReasoningBank arXiv](https://arxiv.org/html/2509.25140v1)
- ReasoningBank ablation on WebArena-Shopping with Gemini-2.5-flash: no memory 39.0, k=1 49.7, k=2 46.0, k=3 45.5, k=4 44.4 success rate; the authors attribute the decline to conflicting or noisy memories - [ReasoningBank arXiv](https://arxiv.org/html/2509.25140v1)
- ReasoningBank memory items are {title, one-sentence description, content}, at most three per trajectory, distilled from both successes and failures; adding failure-derived items raised ReasoningBank from 46.5 to 49.7, whereas the same change lowered AWM from 44.4 to 42.2 - [ReasoningBank arXiv](https://arxiv.org/html/2509.25140v1)
- ReasoningBank WebArena overall: Gemini-2.5-flash 40.5 to 48.8, Gemini-2.5-pro 46.7 to 53.9, Claude-3.7-sonnet 41.7 to 46.3; SWE-Bench-Verified resolve rate Gemini-2.5-flash 34.2 to 38.8, Gemini-2.5-pro 54.0 to 57.4 - [ReasoningBank arXiv](https://arxiv.org/html/2509.25140v1)
- "Experience-following" property: high input similarity between the current task and a retrieved memory's task leads to high similarity between their outputs, so retrieved memories steer behavior strongly, including copying errors (error propagation) and "misaligned experience replay" where some records consistently produce poor outputs - [Xiong et al., arXiv 2505.16067, ACL 2026](https://arxiv.org/html/2505.16067v2)
- Anthropic (2025-09-29) frames context as a finite "attention budget" where "every new token introduced depletes this budget", recommends the "smallest possible set of high-signal tokens", and favors a hybrid of some upfront retrieval plus just-in-time exploration - [Anthropic Engineering, Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Chroma's Context Rot report (2025-07-14, 18 models including Qwen3-8B, 194,480 calls): on LongMemEval, focused prompts (~300 tokens) scored significantly higher than full prompts (~113k tokens) for all models; distractors degrade accuracy non-uniformly (1 distractor hurts, 4 hurt more) - [Chroma Context Rot](https://www.trychroma.com/research/context-rot)
- Chroma: performance drops faster with length when needle-question semantic similarity is lower (similarity range 0.445-0.775 on PG essays), which implies lessons phrased far from the task's vocabulary are least likely to be used - [Chroma Context Rot](https://www.trychroma.com/research/context-rot)
- Recent papers formalize token-budgeted context selection as knapsack-constrained monotone submodular maximization: "Budget-Aware Routing for Long Clinical Text" (ACL 2026 Findings) proposes a relevance + coverage + diversity objective and reports MMR-style diversity gives consistent gains for LLM generation at tight budgets - [ACL Anthology 2026.findings-acl.2114](https://aclanthology.org/2026.findings-acl.2114.pdf) (abstract-level only)
- "Recall Is Not Enough" (arXiv 2607.00725, 2026) uses cost-scaled greedy (gain per token) with a Lin-Bilmes singleton fallback, the standard approximation algorithm for budgeted submodular maximization, and argues an answer-in-context diagnostic predicts F1 better than recall - [arXiv 2607.00725](https://arxiv.org/pdf/2607.00725) (abstract-level only)
- AdaGReS (arXiv 2512.25052) is a greedy token-budgeted selector scoring relevance minus intra-set redundancy with a closed-form adaptive trade-off, effectively a tunable MMR lambda - [papers.cool listing](https://papers.cool/arxiv/2512.25052) (abstract-level only)
- PACMS (arXiv 2606.20047, June 2026) applies budget-aware submodular coverage selection to tool-using agent context, including memory items and tool outputs - [Emergent Mind listing](https://www.emergentmind.com/papers/2606.20047) (abstract-level only)
- IFScale finds a primacy bias: later instructions are violated more often than earlier ones (late/early error ratios up to 3.37 for gpt-4.1-mini at 100 instructions), and primacy peaks around 150-200 instructions - [IFScale arXiv 2507.11538](https://arxiv.org/html/2507.11538v1)
- Chroma: in the repeated-words task, accuracy was highest when the unique item was near the start of the sequence - [Chroma Context Rot](https://www.trychroma.com/research/context-rot)
- Classic hybrid memory score (relevance + importance + recency, with exponential recency decay) originates in Generative Agents (Park et al., 2023) - [arXiv 2304.03442](https://arxiv.org/abs/2304.03442)
- Reciprocal rank fusion, score = sum over rankers of 1/(k + rank) with k = 60, outperformed individual rankers and Condorcet fusion - [Cormack, Clarke, Buettcher, SIGIR 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
- Lost in the middle: accuracy is highest when relevant information is at the beginning or end of the context and lowest in the middle - [Liu et al., arXiv 2307.03172, TACL 2024](https://arxiv.org/abs/2307.03172)
- ACE reports that with prompt caching (GPT-5.1) 91.8% of input tokens were served from cache, cutting billed input cost 82.6%, i.e., a stable context prefix is what makes evolving playbooks cheap - [ACE arXiv 2510.04618v3](https://arxiv.org/html/2510.04618v3)

### Inferences
- At ~30 bullets and ~1.5k tokens, the budget is not binding in the knapsack sense; the bigger risk is distraction from irrelevant bullets (ReasoningBank k-curve, Chroma distractors), so selection should be driven by relevance gating rather than by filling the 6000-char budget.
- A session-start selector fits the prompt-cache constraint: compute the snapshot once from (project id, first user message, task type), then freeze it; the cache only breaks across sessions, which already happens.
- Concrete scoring recipe consistent with the sources: rank bullets separately by BM25 against the first message, by embedding cosine, and by the existing decayed helpful/harmful weight, fuse with RRF (k=60), then greedy-select by fused score per character with an MMR redundancy penalty until the budget or a relevance floor is hit; always include a small "core" set (top preferences) regardless of query.
- Ordering: put the highest-value bullets first (primacy evidence from IFScale and Chroma), and keep the section order fixed so the snapshot prefix is stable across sessions with the same project.
- Because of experience-following, a wrongly retrieved bullet is not neutral; a relevance floor (show nothing rather than a weakly related lesson) is justified.

### Gaps
- No source found that directly compares a static global memory snapshot against a query-conditioned one at session start for coding agents; ReasoningBank is the closest (query-conditioned, k=1).
- Full experimental tables for the 2026 budgeted-selection papers were not read; only abstracts or snippets.
- No source found that measures lost-in-the-middle specifically for a 1-2k token block of bullets in a system prompt; the position evidence is from longer contexts.
- No source found on per-project memory scoping evaluations specifically; this is a design inference.

## (b) Consolidation, dedup, and merging

### Takeaway
Production and research systems all use embedding similarity to find candidates, then either a non-LLM merge rule (ACE) or an LLM decision among ADD/UPDATE/DELETE/NOOP (Mem0); published thresholds are rare, and ACE's own ablation shows dedup aggressiveness changes accuracy only mildly.
A bigger lever than dedup is what gets admitted: strict selective addition plus deletion of harmful records gave about +10 points over naive growth, and full-rewrite consolidation can cause "context collapse".

### Cited Findings
- ACE (Stanford, SambaNova, et al., arXiv 2510.04618, ICLR 2026) splits roles into Generator (produces trajectories and flags which bullets helped or misled), Reflector (extracts lessons, up to 5 refinement rounds), and Curator (emits compact delta entries merged by non-LLM logic) - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- ACE bullets carry a unique ID plus helpful/harmful counters; grow-and-refine appends new bullets, updates existing ones in place, and deduplicates by semantic embeddings, either proactively after each delta or lazily when the context limit is exceeded - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- ACE Appendix A.6 dedup threshold ablation on FiNER (DeepSeek-V3.1, base 70.7): 50% gives 77.0, 70% gives 73.9, 90% gives 78.6; the authors call the sensitivity mild; the embedding model and exact similarity metric are not stated - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- ACE pruning trigger (max context length) ablation on FiNER: 10K 78.6, 50K 78.4, 100K 78.3 tokens, i.e., a much smaller playbook loses nothing - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- ACE's grow-and-refine analyzer can filter entries flagged harmful via metadata; pruning "mainly removes stale or harmful fragments while preserving core reusable strategies" - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- Context collapse: in a Dynamic Cheatsheet run on AppWorld, one monolithic rewrite shrank an 18,282-token context to 122 tokens and accuracy fell from 66.7 to 57.1 - [ACE arXiv](https://arxiv.org/pdf/2510.04618)
- Mem0 (arXiv 2504.19413) retrieves the s = 10 most similar existing memories for each candidate fact, then an LLM tool call chooses ADD (no equivalent), UPDATE (complementary; replace only if the new fact has more information), DELETE (contradiction), or NOOP; Mem0g's node-matching threshold t and retrieval threshold are unspecified - [Mem0 arXiv](https://arxiv.org/html/2504.19413)
- A-MEM (arXiv 2502.12110) embeds notes with all-MiniLM-L6-v2, retrieves candidates by cosine similarity, then has the LLM decide links and "evolve" existing notes' attributes; ablation on GPT-4o-mini multi-hop F1: no links/no evolution 24.55, links only 31.24, full 45.85 - [A-MEM arXiv](https://arxiv.org/html/2502.12110v9)
- ReasoningBank uses no dedup or merging at all ("minimal consolidation strategy"), simply appending, and still beats baselines - [ReasoningBank arXiv](https://arxiv.org/html/2509.25140v1)
- Xiong et al.: add-all memory consistently performs worse than a fixed memory baseline due to error accumulation; strict selective addition (checked against ground truth or strict criteria) enables long-term improvement; combined selective addition and deletion yields about +10% absolute over naive growth; coarse LLM-judge evaluators help less because of evaluator inaccuracy - [Xiong et al. arXiv 2505.16067](https://arxiv.org/html/2505.16067v2)
- EmbeddingGemma (308M) is reported state of the art under 500M parameters on MTEB Multilingual v2, English v2, and Code, ranking 8th overall on Multilingual v2 - [EmbeddingGemma arXiv 2509.20354](https://arxiv.org/pdf/2509.20354); [Hugging Face blog](https://huggingface.co/blog/embeddinggemma)
- A third-party comparison lists EmbeddingGemma 64.12 vs BGE-small 61.22 vs E5-small-v2 62.53, but this conflicts with Google's ~61 mean on Multilingual v2 and does not state a benchmark version - [SurrealDB blog](https://surrealdb.com/blog/embedding-models-comparison); contradicted by [DeepMind EmbeddingGemma page](https://deepmind.google/models/gemma/embeddinggemma/)
- nomic-embed-text-v1 was described as the best 100M-class model at its 2024 release - [Nomic Embed arXiv 2402.01613](https://arxiv.org/pdf/2402.01613)

### Inferences
- Token Jaccard >= 0.8 will miss paraphrases ("run tests with -x" vs "stop pytest at first failure"); an embedding pass with EmbeddingGemma (or all-MiniLM-L6-v2, which A-MEM used, much smaller) is a cheap improvement for 30 items, as all pairwise cosines are 435 comparisons.
- Since no source publishes a reliable cosine threshold, calibrate on the agent's own bullets: label 50-100 pairs as duplicate/related/distinct and pick the threshold from that; use embeddings only to propose candidate pairs, then let a deterministic rule or an LLM choose merge vs keep both.
- Follow ACE's delta discipline rather than letting the 4B model rewrite the whole list: per-bullet ADD/UPDATE/DELETE with IDs preserved, so helpful/harmful counters and telemetry history survive merges (merged bullet should sum or max the counters and keep both source IDs as aliases).
- On a 4B model, LLM-based merge is riskier than on DeepSeek-V3.1 (ACE notes it depends on a reasonably strong Reflector); keep both when uncertain, and prefer deterministic merges (keep the higher-weight, longer bullet).
- Admission control (Xiong et al.) probably matters more than dedup for this system: only admit lessons tied to a verified outcome (tests passed, user accepted), and delete bullets whose harmful count dominates.

### Gaps
- ACE's embedding model and the meaning of its 50/70/90% threshold were not stated in the parts read.
- Mem0's open-source default similarity thresholds were not verified.
- No head-to-head MTEB numbers for EmbeddingGemma vs bge-small vs nomic on the same version were found; no study of embedding dedup quality for short (one-line) lesson text specifically.

## (c) Evaluation methodology and benchmarks

### Takeaway
Public memory benchmarks (LoCoMo, LongMemEval, MemoryAgentBench) mostly test conversational fact recall, not procedural lesson memory, and LoCoMo results are contested; ACE's AppWorld/FiNER and ReasoningBank's WebArena/SWE-bench are the closest analogues to "lessons that improve task success".
For a local agent, offline replay with ablations (no memory, full snapshot, leave-one-out or leave-section-out, shuffled order) on held-out sessions is the evaluation that actually answers whether a bullet earns its place.

### Cited Findings
- LoCoMo J-scores reported by Mem0: full context 72.90, Mem0g 68.44, Mem0 66.88, Zep 65.99, best RAG (k=2, 256-token chunks) 60.97, LangMem 58.10, OpenAI memory 52.90, A-Mem 48.38; full context wins on accuracy but costs ~26k tokens and 17.1s p95 vs Mem0 ~7k tokens and 1.44s p95 - [Mem0 arXiv](https://arxiv.org/html/2504.19413)
- Mem0 excluded LoCoMo's adversarial category (missing ground truth) and ran LLM-judge 10 times due to stochasticity - [Mem0 arXiv](https://arxiv.org/html/2504.19413)
- Letta (2025-08-12) reported gpt-4o-mini agents reach 74.0% on LoCoMo by storing history in files with grep/search tools, beating Mem0's reported 68.5%; Mem0's re-run of the same code gave 58.44% +/- 0.20, and critics note LoCoMo conversations (~16-26k tokens) fit in context so full-context baselines (~73%) are hard to beat - [Letta blog](https://www.letta.com/blog/benchmarking-ai-agent-memory); disputed per [Towards AI summary](https://pub.towardsai.net/mem0-vs-zep-vs-letta-a-folder-of-text-files-shouldnt-beat-the-61k-star-memory-layer-9d7e65c5799c)
- Letta Leaderboard (launched 2025-05-29) is a separate benchmark of how well LLMs manage agentic memory, not a LoCoMo leaderboard - [Letta blog index](https://www.letta.com/blog)
- LongMemEval (Wu et al., ICLR 2025): 500 questions over scalable chat histories testing information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention; commercial assistants and long-context LLMs show about a 30% accuracy drop over sustained interactions; proposed fixes include session decomposition, fact-augmented key expansion, and time-aware query expansion - [LongMemEval arXiv 2410.10813](https://arxiv.org/html/2410.10813v2)
- MemoryAgentBench (Hu, Wang, McAuley, UCSD, ICLR 2026) converts long-context datasets into incremental multi-turn "inject once, query multiple times" format covering accurate retrieval, test-time learning, long-range understanding, and conflict resolution (selective forgetting in some versions), with new datasets EventQA and FactConsolidation; no evaluated method masters all four - [MemoryAgentBench arXiv 2507.05257](https://arxiv.org/html/2507.05257v2); [ICLR 2026 poster](https://iclr.cc/virtual/2026/poster/10010781)
- ACE results (DeepSeek-V3.1): AppWorld average base ReAct 42.4, offline ACE 59.4 with labels and 57.2 without labels, online ACE 59.5 (+17.1) vs Dynamic Cheatsheet 51.9; matches IBM CUGA (60.3) on the leaderboard average - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- ACE finance: base 69.1 average; offline ACE 81.9 with labels (FiNER 78.3, Formula 85.5) vs GEPA 72.5; online without labels Dynamic Cheatsheet falls to 65.4 (below base) while ACE reaches 72.9; DDXPlus 75.2 to 90.2; BIRD-SQL 47.8 to 52.9 (GEPA 52.2) - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- ACE ablations: no Reflector and no multi-epoch 55.1, no multi-epoch 56.8, full 59.4; online with offline warmup 59.5 vs 56.1 without; 10 reflection rounds can degrade vs 5 - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- ACE limitation: gains depend on reliable feedback; without ground truth or execution signals both ACE and DC can degrade, and simple tasks (HotPotQA, Game of 24) gain little from rich contexts - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- Xiong et al. evaluate memory policies by tracking long-run performance under add-all, selective addition, and deletion policies, a template for policy-level (not item-level) evaluation - [Xiong et al. arXiv 2505.16067](https://arxiv.org/html/2505.16067v2)
- ManyIFEval/StyleMBPP find LLM-as-judge inflates scores relative to rule-based checks, especially at higher instruction counts, which argues for verifiable outcome metrics (tests pass) over judge-scored ones - [When Instructions Multiply, EMNLP 2025 Findings, arXiv 2509.21051](https://arxiv.org/html/2509.21051v1)

### Inferences
- Item-level metrics computable from existing telemetry: memory precision = cited IDs / shown IDs per session; dead weight = bullets shown in N sessions and never cited; churn = added then dropped within K reflections; harmful rate = harmful / (helpful + harmful).
- Citation is only a proxy (models can use a lesson without citing it, or cite without using it); confirm with counterfactual replay: re-run a fixed set of held-out tasks with the snapshot minus one bullet (or one section) and measure the change in a verifiable outcome (tests pass, turns to success, tool errors).
- Minimum offline protocol: (1) no-memory vs current snapshot vs query-conditioned snapshot on the same replayed first-messages, (2) leave-section-out ablations, (3) shuffled-order control for position effects, (4) inter-session holdout where lessons learned from sessions 1..n are evaluated only on sessions after n, to avoid leakage, (5) several seeds because a 4B model's variance is large.
- LoCoMo/LongMemEval scores are poor proxies for this use case (fact recall over chats); ACE-style task-success deltas are the right target metric.

### Gaps
- MemBench was not researched in the available budget; no figures collected.
- Letta Leaderboard methodology and current standings were not fetched.
- No published study found that validates "cited IDs" against counterfactual usefulness of memories.

## (d) What changes for small models and long prompts

### Takeaway
Small and weak models degrade early and steeply as instruction count rises: in IFScale, gpt-4.1-nano drops from 94% at 10 instructions to 72.8% at 50 and 51.6% at 100, and Llama-4-Scout from 100% to 54.4% at 50; small models also prefer omission (dropping instructions entirely).
For a 4B model, about 30 bullets is already in the zone where some will be ignored, so fewer, query-relevant bullets placed early are likely better than filling the 6000-char budget.

### Cited Findings
- IFScale (Distyl AI, arXiv 2507.11538, NeurIPS 2025): 500 keyword-inclusion instructions, 20 models; best frontier models reach only 68% at 500 instructions - [IFScale arXiv](https://arxiv.org/abs/2507.11538)
- IFScale accuracy at 10 / 50 / 100 / 250 / 500 instructions: gpt-4.1-nano 94.0 / 72.8 / 51.6 / 25.7 / 16.2; gpt-4o-mini 94.0 / 65.6 / 41.8 / 18.3 / 10.4; claude-3.5-haiku 98.0 / 78.0 / 43.4 / 16.6 / 8.5; gpt-4.1-mini 100 / 93.2 / 80.0 / 44.5 / 27.2; llama-4-scout 100 / 54.4 / 27.2 / 9.3 / 6.7; gemini-2.5-flash 100 / 96.0 / 82.0 / 50.7 / 34.2 - [IFScale arXiv](https://arxiv.org/html/2507.11538v1)
- IFScale degradation shapes: threshold decay for reasoning models (gemini-2.5-pro, o3), linear for gpt-4.1 and claude-3.7-sonnet, exponential for claude-3.5-haiku and llama-4-scout, which level off at about 7-15% - [IFScale arXiv](https://arxiv.org/html/2507.11538v1)
- IFScale omission:modification ratio at 500 instructions: llama-4-scout 34.88, claude-3.5-haiku 31.69, gpt-4o-mini 15.24, gpt-4.1-nano 11.59; weaker models drop instructions outright - [IFScale arXiv](https://arxiv.org/html/2507.11538v1)
- IFScale tested no model below about the gpt-4.1-nano class and no small open Qwen/Gemma/Llama-8B model; the Qwen tested is qwen3-235b-a22b (20.9% at 500) - [IFScale arXiv](https://arxiv.org/html/2507.11538v1)
- ManyIFEval (up to 10 simultaneous instructions) and StyleMBPP (code, up to 6): performance drops as instruction count rises across 10 LLMs; prompt-level accuracy (all satisfied) falls much more steeply than instruction-level; a logistic regression on instruction count alone predicts performance with about 10% error - [arXiv 2509.21051, EMNLP 2025 Findings](https://arxiv.org/html/2509.21051v1); the "curse of instructions" label is from an earlier ICLR 2025 submission by Harada et al., confirmed only via a secondary source - [Viblo summary](https://viblo.asia/p/the-curse-of-instructions-khi-llm-ngop-tho-vi-qua-nhieu-yeu-cau-cung-luc-ymJXDDo6Jkq)
- Chroma Context Rot includes Qwen3-8B: performance degrades with input length even on trivial replication; Qwen3-8B had a 4.21% non-attempt rate and produced random output from about 5,000 words; shuffled (incoherent) haystacks surprisingly beat coherent ones across all 18 models - [Chroma Context Rot](https://www.trychroma.com/research/context-rot)
- A-MEM with small models (Qwen2.5-1.5B/3B, Llama-3.2-1B/3B) used top-k = 10 retrieved memories (vs 40-50 for GPT-4o) and still beat full-history baselines by wide margins, e.g., Qwen2.5-3B multi-hop F1 27.59 vs 3.11 for LoCoMo full-context, using about 1.1-1.4k tokens per operation vs about 16.9k - [A-MEM arXiv](https://arxiv.org/html/2502.12110v9)
- ACE's gains were obtained with DeepSeek-V3.1, and the authors warn that a weak Reflector can make the context noisy or harmful - [ACE arXiv](https://arxiv.org/html/2510.04618v3)
- Xiong et al.: coarse LLM-based evaluators limit gains from selective addition because of evaluator inaccuracy - [Xiong et al. arXiv 2505.16067](https://arxiv.org/html/2505.16067v2)

### Inferences
- Extrapolating IFScale's small-model curves (nano/mini/scout lose 6-45 points between 10 and 50 instructions), a 4B model shown 30 lessons plus the base system prompt's own rules is likely already omitting some; a target of roughly 8-15 task-relevant lessons is a defensible starting point to test, not a measured optimum.
- Prompt-level compliance falls faster than item-level (ManyIFEval), so measure per-lesson adherence, not just overall success.
- Small models' omission bias and primacy bias argue for: the most important lessons first, short imperative phrasing, and no near-duplicates competing for attention.
- The 4B model is a weak reflector and judge; tie admission and harmful/helpful updates to verifiable signals (tests, exit codes, user acceptance) rather than to the model's self-assessment.

### Gaps
- No instruction-density study found for 2-8B open models (Qwen3/3.5-4B, Gemma, Llama-8B) specifically; IFScale and Chroma stop at gpt-4.1-nano and Qwen3-8B.
- No source found on Qwen3.5-4B long-context or system-prompt adherence behavior.
- No study found isolating the effect of lesson count in a system prompt on coding-agent success for small models; this should be measured offline with the replay protocol above.
