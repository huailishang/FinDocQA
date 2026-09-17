# Frozen Task Contract｜Fresh12 Cloudflare Bounded Evidence Workspace Capability V1

Task ID: `FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1`

Workflow: `evaluator-executor-workflow/v2.2`

Task kind: `capability_experiment`

Contract state: `CONTRACT_FROZEN`

Baseline HEAD: `b057674`

Project map path: `docs/evaluation/PROJECT_BOTTLENECK_MAP.md`

Project map revision: `2026-09-16-r84`

Active bottleneck: `B-03`

Hypothesis: `H-60`

Validation plan file: `handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/VALIDATION_PLAN.yaml`

Project map: `docs/evaluation/PROJECT_BOTTLENECK_MAP.md`

Metric baseline: H-58 Fresh12 = 12 cases / 9 document families; lexical Top5 = 4 hit / 8 miss; H-60 baseline = same lexical Top5 + same Cloudflare semantic Top5 → equal-weight RRF60 → fixed Top5.

Estimated affected scope: Exact frozen H-58 Fresh12 cohort, 12 cases across 9 document families, verification-boundary evidence reach only.

Expected project impact: Qualify only if recovered_cases >= 3 AND recovered_document_families >= 2 AND protected_loss = 0 AND workspace_max_unique_pages <= 10.

Rollback condition: If the single frozen H-60 run does not meet primary qualification, stop; do not add a second cohort or change provider/model/query/TopK/RRF weights/threshold/parser/solver/judge/reranker inside H-60.

## Observable objective

On the exact H-58 Fresh12 cohort, test whether delaying lexical/semantic contraction until a bounded union Evidence Workspace improves official evidence reach at the verification boundary while preserving existing protected cases, using the configured Cloudflare embedding profile.

## Strategic basis

H-58 independently established a fresh two-sided cohort: `12 cases / 9 document families`, lexical Top5 `4 hit / 8 miss`, with the 8 misses spanning 7 document families.

H-55/H-56/H-56R1 already established `EvidenceWorkspaceScope`, deterministic page-scope enforcement, and real product-route scope propagation. H-59 attempted the capability test with `Qwen/Qwen3-Embedding-8B` through HF/Scaleway but stopped after `9` successful calls because the provider returned terminal HTTP 402 for exhausted included credits. H-59 therefore produced no capability measurement and does not falsify the workspace hypothesis.

The project already has a separately configured backup profile with a real successful preflight:

```text
provider = cloudflare-workers-ai
model = @cf/qwen/qwen3-embedding-0.6b
embedding_dimension = 1024
preflight status = PASS
```

The Human/Task Owner explicitly chose on `2026-09-16` to switch the embedding route and reopen the experiment as a new task.

B-03 remains the first bottleneck. B-06 remains secondary because freeform answer evaluation sits downstream of substantial evidence loss; B-05 remains WATCH and B-07 remains secondary.

## Hypothesis and principal change

Within H-60, both baseline and candidate consume the exact same lexical Top5 and the exact same Cloudflare semantic Top5. The only causal difference between baseline and candidate is contraction timing:

```text
BASELINE
same lexical Top5 + same Cloudflare semantic Top5
→ equal-weight RRF60
→ fixed Top5
→ verification boundary

CANDIDATE
same lexical Top5 + same Cloudflare semantic Top5
→ deduplicated union workspace(max 10 unique pages)
→ EvidenceWorkspaceScope / deterministic verification
→ late contraction where supported
```

Principal change:

`early_fixed_top5_contraction → bounded_union_workspace_before_verification`

H-60 does **not** test whether Cloudflare 0.6B is better or worse than Qwen3-Embedding-8B. Historical 8B absolute retrieval scores are contextual evidence only and must not be treated as a same-model before/after comparison.

## Frozen cohort and authority

Use exactly the H-58 `FRESH12_COHORT_MANIFEST.json`, byte-identical. No extension, substitution, shrinking, reordering, or outcome-based sample construction is allowed.

Gold/reference evidence pages are scoring-only. They must not influence lexical queries, semantic queries, embeddings, ranking, RRF, union construction, Binding, Sufficiency, or late contraction.

## Frozen retrieval lanes

### Lexical lane

Reuse the H-58 frozen lexical results/config without reranking or query rewrite:

```text
retriever = CanonicalLexicalEvidenceRetriever
top_k_per_doc = 5
window_chars = 1800
context_flank_chars = 600
```

### Semantic lane

Freeze the separate Cloudflare embedding profile:

```text
provider = cloudflare-workers-ai
profile_id = qwen3-0.6b-cloudflare-v1
model = @cf/qwen/qwen3-embedding-0.6b
embedding_dimension = 1024
metric = cosine
document_embedding_batch_size = 16
query_embedding_unit = one query vector per case
top_k_per_doc = 5
```

The semantic query must come from the same runtime question representation for all cases, with no Gold/reference fields.

Baseline and candidate must reuse byte-identical persisted semantic page/query embeddings and byte-identical semantic Top5 outputs.

H-59 4096-dimensional 8B vectors are forbidden H-60 runtime inputs. H-60 must build and persist a separate 1024-dimensional cache/index/profile.

## Primary measurement｜verification-boundary evidence reach

For every case persist:

```text
lexical_top5
semantic_top5
baseline_rrf60_top5
candidate_union_workspace
canonical_gold_pages   # scoring only after runtime outputs freeze
baseline_gold_reach
candidate_gold_reach
```

Definitions:

```text
baseline_gold_reach = canonical_gold_pages intersects baseline_rrf60_top5
candidate_gold_reach = canonical_gold_pages intersects candidate_union_workspace
recovered_case = candidate_gold_reach and not baseline_gold_reach
protected_loss = baseline_gold_reach and not candidate_gold_reach
```

Primary capability qualification requires all:

```text
recovered_cases >= 3
recovered_document_families >= 2
protected_loss = 0
candidate_workspace_max_unique_pages <= 10
```

This metric isolates contraction timing while holding both H-60 retrieval lanes fixed.

## Secondary measurement｜deterministic trusted reach

Run the existing scoped financial evidence path for baseline and candidate where current deterministic ClaimSpec / Binding / FinancialEvidenceSufficiency supports the question/evidence shape.

For each case classify:

```text
VERIFIER_SUPPORTED_TRUSTED
VERIFIER_SUPPORTED_NOT_TRUSTED
VERIFIER_UNSUPPORTED
```

Rules:

1. `VERIFIER_UNSUPPORTED` is a downstream coverage limitation, not a workspace failure or success.
2. Unsupported cases remain in the primary 12-case boundary metric.
3. Candidate trusted losses from baseline `VERIFIER_SUPPORTED_TRUSTED` cases must be `0`.
4. Candidate trusted recoveries are reported separately; no minimum secondary count is required.
5. Any late-contraction output pages must be `<=5` and may not use Gold/reference information.

## External-call authorization and cost envelope

The Human/Task Owner explicitly authorized switching to the alternate embedding route and reopening the experiment on `2026-09-16`.

Authorization is therefore:

```text
authorization_api_call = true
scope = Cloudflare Workers AI embedding-only preflight + H-60 frozen semantic embedding execution
paid spend = not authorized
generative LLM / Judge / Solver / reranker / answer generation = not authorized
```

The prior exact frozen input volume is `1019 page inputs + 12 query inputs = 1031 logical inputs`; prior tokenizer measurement was `819652 input tokens`. The inherited Cloudflare project policy estimates this volume far inside the configured daily free allocation, but H-60 must still stop on quota/payment/billing denial and must not enable paid fallback.

Frozen execution envelope:

```text
preflight_success_target = 1
runtime_successful_call_target = 80
successful_call_ceiling = 81
physical_attempt_ceiling = 162
max_attempts_per_logical_unit = 2
max_parallelism = 2
```

The one-input preflight must verify the exact provider/model profile and a 1024-dimensional embedding before the full batch. It counts in the external-call ledger and budget.

Retry only transient network/timeout/429/5xx/provider-transport failures and only within the frozen per-unit limit. Do not retry deterministic validation, malformed input, model/schema configuration, auth/permission, payment/quota terminal denial, or content errors.

Every external attempt must be recorded in an append-only task-local ledger. Preserve completed units and never rerun completed units merely to simplify bookkeeping.

## Required outputs

- `CLOUDFLARE_PREFLIGHT.json`
- `SEMANTIC_EMBEDDING_MANIFEST.json`
- `SEMANTIC_TOP5.jsonl`
- `LANE_INPUT_HASHES.json`
- `BASELINE_RRF60_RESULTS.jsonl`
- `CANDIDATE_WORKSPACE_RESULTS.jsonl`
- `BOUNDARY_CAPABILITY_SUMMARY.json`
- `DOWNSTREAM_TRUSTED_REACH.jsonl`
- `DOWNSTREAM_TRUSTED_SUMMARY.json`
- `API_ATTEMPT_LEDGER.jsonl`
- `RUN_COST_SUMMARY.json`
- task-local deterministic runner(s)
- `evidence/self-check-round1.json`
- `evidence/self-check-round2.json`
- L2 evidence and `REPORT.md`

`RUN_COST_SUMMARY.json` must expose at least:

```text
successful_calls
physical_attempts
preflight_calls
runtime_embedding_calls
max_attempts_per_successful_call
max_parallelism
unauthorized_generative_calls
judge_calls
solver_calls
reranker_calls
```

## Acceptance criteria

- AC-01 H-58 REVIEW/L3 reproduce `PASS / 8/8`, with fresh12 `4 hit / 8 miss`, `7` miss families, and zero external calls in H-58.
- AC-02 H-58 cohort/source/page-authority artifacts remain byte-identical; exactly 12 frozen cases are used; Gold/reference fields are absent from runtime retrieval/workspace inputs.
- AC-03 Semantic retrieval uses exactly `cloudflare-workers-ai / @cf/qwen/qwen3-embedding-0.6b`, dimension `1024`, batch `16`, one query vector per case, semantic Top5; baseline and candidate consume identical persisted semantic lanes; no H-59 8B vector/cache is reused.
- AC-04 Baseline is exact equal-weight `RRF60 → Top5`; candidate is exact deduplicated lexical Top5 ∪ semantic Top5 with maximum 10 unique pages and no pre-verification score reweighting.
- AC-05 Primary qualification is computed exactly as `recovered_cases>=3 AND recovered_document_families>=2 AND protected_loss=0 AND workspace_max<=10`; runtime outputs freeze before Gold-page scoring.
- AC-06 Secondary measurement distinguishes verifier-supported from `VERIFIER_UNSUPPORTED`; unsupported cases are not counted as workspace failures; candidate loses zero baseline trusted successes among supported cases.
- AC-07 External-call evidence proves only authorized Cloudflare embedding calls occurred; preflight succeeds with dimension `1024`; successful calls `<=81`, physical attempts `<=162`, per-unit attempts `<=2`, max parallelism `<=2`; no paid fallback and no generative/Judge/Solver/reranker call occurs.
- AC-08 H-56R1 accepted product/test hashes remain byte-identical; H-60 introduces no `src/**`, `config/**`, or `tests/**` modification.
- AC-09 REPORT separates task verdict from project-impact verdict and explicitly states that H-60 measures workspace capability under the Cloudflare 0.6B profile, not 0.6B-vs-8B model superiority.

## Stop conditions

Stop and return to Evaluator if any of the following occurs:

- Cloudflare exact model/profile preflight fails;
- provider requires paid spend or returns a terminal quota/payment denial;
- frozen cohort/page authority changes;
- semantic lane cannot complete inside the frozen budget;
- runtime Gold/reference leakage is detected;
- baseline/candidate lane hashes differ;
- H-59 8B vector/cache material enters H-60 runtime inputs;
- candidate requires product behavior changes or a second principal variable;
- primary qualification is not met after the single frozen run;
- continuing would require changing model/provider, TopK, RRF weights, query text, cohort, threshold, Parser, Solver, Judge, or reranker.

No second cohort and no alternate provider/model are allowed inside H-60.

## Allowed scope

Executor may create/modify only:

- `handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/**`
- `handoffs/evaluator_executor/state/CURRENT.md` for normal routing.

Executor may read but not modify H-58 accepted artifacts, H-56R1 accepted hashes, H-59 Cloudflare preflight/policy artifacts, product code, config, and tests.

No `src/**`, `config/**`, or `tests/**` change is authorized.

## Exclusions

- Gold/reference information may not influence runtime retrieval, ranking, workspace construction, Binding, Sufficiency, or late contraction.
- H-59 8B vectors/cache may not be reused as H-60 runtime inputs.
- Provider/model/query/TopK/RRF weights/cohort/threshold/parser/solver/judge/reranker may not be changed inside H-60 to rescue the result.
- No product-code changes under `src/**`, `config/**`, or `tests/**`.
- No paid fallback.
- No generative LLM, Judge, Solver, reranker, or answer-generation calls.

## Authorization

```text
authorization_commit = false
authorization_push = false
authorization_history_rewrite = false
authorization_api_call = true
```
