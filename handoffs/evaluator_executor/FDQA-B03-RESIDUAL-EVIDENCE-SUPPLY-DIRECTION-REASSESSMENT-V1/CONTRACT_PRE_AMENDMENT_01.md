# Frozen Task Contract｜Residual Evidence Supply Direction Reassessment V1

Task ID: `FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1`

Workflow: `evaluator-executor-workflow/v2.2`

Task kind: `evaluator_design`

Contract state: `CONTRACT_FROZEN`

Baseline HEAD: `b057674`

Project map: `docs/evaluation/PROJECT_BOTTLENECK_MAP.md`

Map revision: `2026-09-17-r85`

Active bottleneck: `B-03`

Hypothesis: `H-61`

Validation plan file: `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/VALIDATION_PLAN.yaml`

## Strategic basis

H-60 completed a valid fresh capability experiment with L2/L3 `9/9 PASS`. Under byte-identical lexical and Cloudflare semantic lanes, verification-boundary Gold reach improved from baseline RRF60 Top5 `7/12` to bounded workspace `9/12`, recovering `2 cases / 2 document families` with `protected_loss=0`. The frozen capability gate required `>=3` recovered cases, so H-60 is `PASS / IMPROVED / SWITCH` with direction decision `NOT_QUALIFIED`.

The remaining H-60 losses are `3/12` cases where Gold is absent from lexical Top5 ∪ semantic Top5. Historical H-50 also contains `18/46 BOTH_MISS`. H-53 fixed ±2 neighbor expansion failed fresh qualification, and H-43 fresh target-guided planning produced no measurable recovery. Evaluator additionally checked the H-60 local internal-gap signal against H-50 BOTH_MISS: `max_gap=2/3/4` all produced `0/18` recovery. Therefore no next product/capability mechanism is currently justified.

H-61 is a zero-API direction reassessment, not a product experiment. It must decide whether existing frozen evidence supports one new Gold-free, falsifiable evidence-supply/selective-exploration mechanism, or whether B-03 must return `NO_SINGLE_DIRECTION` and be re-ranked against B-06/B-05.

## Observable objective

Using only persisted H-60/H-50/H-53/H-43 artifacts, produce a reproducible residual evidence-supply diagnosis and one of exactly two decisions:

```text
CANDIDATE_DIRECTION
NO_SINGLE_DIRECTION
```

No fresh cohort is consumed and no model/provider call is allowed.

## Frozen input authority

Required read-only inputs include at least:

- H-60 `REVIEW.md`, `BOUNDARY_CAPABILITY_SUMMARY.json`, `SEMANTIC_TOP5.jsonl`, `LANE_INPUT_HASHES.json`, `RUNTIME_OUTPUT_FREEZE.json`, persisted embedding cache/metadata as needed;
- H-50 `LANE_OUTCOME_MATRIX.jsonl`, `LANE_SIGNAL_TRACE.jsonl`, `REVIEW.md`;
- H-53 `REVIEW.md` and frozen result artifacts;
- H-43 `REVIEW.md` and frozen result artifacts;
- project map revision `2026-09-17-r85`.

Persist SHA256 identities for all concrete files actually used.

The H-60 residual set must be derived mechanically as `candidate_gold_reach=false` from the frozen H-60 boundary summary and must equal exactly:

```text
financebench_id_00521 / ULTABEAUTY_2023_10K
financebench_id_04735 / ADOBE_2015_10K
financebench_id_03882 / AMCOR_2020_10K
```

## Diagnostic axes

The executor may compute descriptive diagnostics from frozen traces/embeddings, including:

- lexical/semantic union geometry and page-distance structure;
- full semantic Gold rank from already persisted H-60 vectors, if deterministically reproducible without external calls;
- lane overlap/agreement and semantic score-shape signals already available in frozen artifacts;
- document/page locality and structure-locality descriptors;
- whether a proposed action is an already-rejected historical mechanism or materially distinct.

Gold/reference information may be used only after a candidate signal/action definition is frozen for diagnostic labeling. Gold may not be used to construct a trigger, choose per-qid parameters, or tune thresholds until a case is recovered.

## Direction admission gate

`CANDIDATE_DIRECTION` is allowed only if all are true:

1. the same Gold-free trigger/action mechanism covers `>=3 independent cases`;
2. those cases span `>=2 document families`;
3. the mechanism has one principal change and is not qid/document hardcoding;
4. it is materially distinct from mechanisms already fresh-rejected by H-43/H-53, or new evidence explains why the old failure does not apply;
5. it can be validated on a future untouched cohort with an explicit failure condition written before that cohort is inspected;
6. no product/API change was required to discover the direction.

Otherwise decision must be `NO_SINGLE_DIRECTION`.

Two-case signals may be recorded but may not be promoted.

## Required outputs

- `INPUT_MANIFEST.json`
- `RESIDUAL_CASES.jsonl`
- `FAILURE_SIGNAL_MATRIX.jsonl`
- `CROSS_COHORT_MECHANISM_AUDIT.json`
- `DIRECTION_DECISION.json`
- task-local deterministic analysis runner(s)
- `evidence/self-check-round1.json`
- `evidence/self-check-round2.json`
- L2 evidence
- `REPORT.md`

`DIRECTION_DECISION.json` must expose at least:

```text
decision
candidate_mechanism
independent_case_count
document_family_count
gold_free_trigger_defined
principal_change
historical_rejection_conflict
fresh_validation_failure_condition
next_bottleneck_reassessment_required
```

If `decision=NO_SINGLE_DIRECTION`, then:

```text
candidate_mechanism = null
next_bottleneck_reassessment_required = true
```

## Acceptance criteria

- AC-01 H-60 final REVIEW is `PASS`, L3 is `9/9 PASS`, H-60 direction is `NOT_QUALIFIED`, and its frozen boundary summary remains `7/12 → 9/12`, recovered=2, families=2, protected_loss=0.
- AC-02 H-61 input manifest hashes every concrete frozen artifact used and introduces zero network/model/provider calls.
- AC-03 Residual set is derived mechanically from H-60 and equals exactly the three frozen union-miss cases; no case is added/removed based on diagnostic outcome.
- AC-04 Failure-signal matrix separates Gold-free trigger features from scoring-only Gold labels and contains no qid-specific threshold or document hardcoding.
- AC-05 Cross-cohort audit explicitly checks H-43/H-53 historical fresh failures and the H-50 18 BOTH_MISS evidence; fixed-neighbor/internal-gap mechanisms may not be promoted merely from the two H-60 local signals.
- AC-06 Direction decision follows the frozen gate exactly: candidate requires >=3 independent cases, >=2 families, same mechanism, Gold-free trigger, single principal change, and pre-writable fresh failure condition; otherwise `NO_SINGLE_DIRECTION`.
- AC-07 No `src/**`, `config/**`, `tests/**` modification; no API/embedding/generative/Judge/Solver/reranker calls; no new dependency installation/download.
- AC-08 REPORT separates diagnostic task verdict from project-impact/bottleneck recommendation and does not claim capability improvement from a diagnostic-only run.

## Stop conditions

Stop and return to Evaluator if:

- any required frozen input identity is unavailable or inconsistent;
- reproducing a diagnostic would require a provider/model/API call;
- analysis requires product-code modification or dependency installation;
- a proposed mechanism depends on Gold to construct its runtime trigger;
- the only support is fewer than 3 independent cases or fewer than 2 document families;
- the proposed mechanism is merely fixed-neighbor expansion, simple internal gap-fill, unchanged H-43 target planning, TopK expansion, RRF reweighting, or another previously rejected mechanism without materially new cross-cohort evidence.

Do not consume a new untouched cohort inside H-61.

## Allowed scope

Executor may create/modify only:

- `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/**`
- `handoffs/evaluator_executor/state/CURRENT.md` for normal routing.

All H-60/H-50/H-53/H-43 artifacts and product code are read-only.

## Exclusions

- No `src/**`, `config/**`, `tests/**` changes.
- No external API, embedding, generation, Judge, Solver, reranker, or paid call.
- No dependency install/download/bootstrap.
- No fresh cohort selection or outcome-based sample extension.
- No TopK/RRF/model/provider/query/threshold tuning.
- No productization decision from fewer than the frozen direction-admission minimum.

## Authorization

```text
authorization_commit = false
authorization_push = false
authorization_history_rewrite = false
authorization_api_call = false
```
