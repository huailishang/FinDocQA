# Evaluator ↔ Executor Current State

<!-- evaluator-executor-workflow:v2.2 -->

```yaml
workflow: evaluator-executor-workflow/v2.2
task_id: FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1
task_kind: capability_experiment
state: CONTRACT_FROZEN
current_role: Executor
baseline_commit: e291d55c8f3ed4ca8628904bdc33ea408731826b
project_map_path: docs/evaluation/PROJECT_BOTTLENECK_MAP.md
project_map_revision: 2026-09-19-r90
active_bottleneck_id: B-03
hypothesis_id: H-65
contract_path: handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/CONTRACT.md
executor_report_path: handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/REPORT.md
evaluator_review_path: handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/REVIEW.md
next_artifact_path: handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/REPORT.md
authorization_commit: false
authorization_push: false
authorization_history_rewrite: false
authorization_api_call: false
no_dependency_install: true
```

## Previous task closure

H-64 (`FDQA-FROZEN-WORKSPACE-TO-EVIDENCE-BUNDLE-WIRING-V1`) 已由 Evaluator 正式关闭：

```text
task verdict = PASS
project impact = IMPROVED
continuation = CONTINUE

Executor A1 L2 = 7/7 PASS
Evaluator A2 L3 = 7/7 PASS

fixed12 input wiring = 0/12 → 12/12
bundle solver consumable = 0/12 → 12/12
workspace lineage closed = 0/12 → 12/12
workspace pages = 104
outside pages = 0
mandatory fail-closed = 8/8
real candidate/model/verifier = NOT EXECUTED
```

Final review:

`handoffs/evaluator_executor/FDQA-FROZEN-WORKSPACE-TO-EVIDENCE-BUNDLE-WIRING-V1/REVIEW_A2.md`

说明：

- A1 修复的是 evaluator-owned pytest 环境假设；
- A2 修复的是并行 security-gate commit 导致的 Evaluator L3 baseline compatibility；
- H-64 能力收益只限于 bounded workspace → producer input contract；
- 真实 candidate answer、English claim parser、verifier correctness 仍未测。

## Current execution task

H-65 已冻结并路由给 Executor。

目标：

```text
synthetic candidate SolverResult
+ explicit producer/run/source metadata
+ H-64 EvidenceBundle lineage
        ↓
FreeformCandidateAssertionEnvelopeAdapter
        ↓
H-62 21-field candidate assertion envelope
```

能力目标：

```text
synthetic fixed12 READY_FOR_ADAPTER = 12/12
H-62 required field closure = 21/21 for 12/12
candidate_id deterministic = 12/12
evidence refs in workspace = 12/12
mandatory fail-closed = all blocked
```

Hard boundaries:

- product layer only adds `src/verification/freeform_candidate_assertion_adapter.py`;
- test layer only adds `tests/test_freeform_candidate_assertion_adapter.py`;
- accepted H-64 adapter/test are read-only hash-frozen inputs;
- no external API/model/provider call;
- no dependency install/download;
- no real candidate answer generation;
- no real verifier execution;
- no Gold/reference answer/page use;
- no financial_claim_ast/parser implementation;
- no verifier/solver/classifier/retrieval/run/safe-runner modification;
- no benchmark/qid-specific product hardcode;
- synthetic fixtures are `SIMULATED_INTERFACE_ONLY`;
- invalid decision must have `envelope=null`;
- no commit/push/history rewrite authorization.

H-65 PASS 也必须保持：

```text
real_candidate_source_count = 0
ready_for_human_authorization = false
next_blocker = FREEFORM_CLAIM_PARSER_REQUIRED
```

Executor entry:

`handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/EXECUTOR_START.md`

Frozen contract:

`handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/CONTRACT.md`

Validation plan:

`handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/VALIDATION_PLAN.yaml`
