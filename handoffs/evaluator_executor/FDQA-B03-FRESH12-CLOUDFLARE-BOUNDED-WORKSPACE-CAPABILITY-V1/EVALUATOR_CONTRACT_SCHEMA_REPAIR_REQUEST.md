# Evaluator Contract Schema Repair Request｜H-60

Task ID: `FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1`

Owner needed: Evaluator

Reason: H-60 execution is complete and L2 is `9/9 PASS`, but shared v2.2 `validate_workflow.py` blocks handoff because the frozen `CONTRACT.md` is missing six explicit schema fields/headings. Executor does not modify Evaluator-owned frozen contract semantics.

## Current execution facts

```text
Phase 0 runtime-text gate = PASS
Cloudflare exact-profile preflight = PASS
runtime embedding units = 80/80
successful calls = 81
physical attempts = 81
retryable failures = 0
terminal failures = 0
baseline_gold_reach_cases = 7
candidate_gold_reach_cases = 9
recovered_cases = 2
recovered_document_families = 2
protected_loss = 0
workspace_max_unique_pages = 10
primary_qualified = false
L2 = 9/9 PASS
```

No external calls need to be repeated for this repair.

## Validator-only blockers

The shared validator reports exactly:

1. missing `Exclusions` heading/section;
2. missing explicit `Project map:` field;
3. missing explicit `Metric baseline:` field;
4. missing explicit `Estimated affected scope:` field;
5. missing explicit `Expected project impact:` field;
6. missing explicit `Rollback condition:` field.

These are schema-normalization issues. The underlying semantics already exist in the frozen contract and/or Amendment 01.

## Suggested no-semantic-change normalization

Evaluator may add explicit fields equivalent to the already frozen meaning, for example:

```text
Project map: docs/evaluation/PROJECT_BOTTLENECK_MAP.md
Metric baseline: H-58 Fresh12 = 12 cases / 9 document families; lexical Top5 = 4 hit / 8 miss; H-60 comparison baseline = same lexical Top5 + same Cloudflare semantic Top5 → equal-weight RRF60 → fixed Top5
Estimated affected scope: exact frozen H-58 Fresh12 cohort, 12 cases across 9 document families, verification-boundary evidence reach only
Expected project impact: qualify only if recovered_cases >= 3 AND recovered_document_families >= 2 AND protected_loss = 0 AND workspace_max_unique_pages <= 10
Rollback condition: if the single frozen H-60 run does not meet primary qualification, stop; do not add a second cohort or change provider/model/query/TopK/RRF weights/threshold/parser/solver/judge/reranker inside H-60
```

Add an explicit `## Exclusions` section restating already-frozen exclusions, such as:

- no Gold/reference influence on runtime retrieval/ranking/workspace construction;
- no H-59 8B vector/cache reuse;
- no provider/model/query/TopK/RRF/threshold changes inside H-60;
- no product-code changes under `src/**`, `config/**`, `tests/**`;
- no paid fallback;
- no generative LLM/Judge/Solver/reranker calls.

## Required follow-up after evaluator repair

1. rerun shared `validate_workflow.py` against `CURRENT.md`;
2. if validator becomes `OK`, accept the unchanged submitted execution snapshot;
3. route to `READY_FOR_REVIEW / Evaluator`;
4. run frozen L3 independent gate without repeating Cloudflare live calls unless separately authorized; persisted embedding evidence and offline replay are sufficient for deterministic recheck;
5. issue final task verdict / project-impact verdict / continuation decision.

Executor has not modified `CONTRACT.md`.