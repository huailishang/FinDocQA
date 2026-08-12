# E4 L2 Judge Shadow Preflight

Preflight version: `v1.0`

Prompt/rubric version: `L2_SHADOW_RUBRIC_V1`

Status: `ZERO_CALL_PREFLIGHT / NOT_AUTHORIZED`

Applies to: FinDocQA E4 future L2 semantic-judge shadow run.

## 1. Purpose

This preflight freezes the runtime contract **before** any model call. It does not select a Provider/model, implement a client, or run a judge.

Current state:

```text
provider = UNSELECTED
model = UNSELECTED
authorization_api_call = false
max_calls = 0
max_prompt_tokens = 0
max_completion_tokens = 0
max_total_tokens = 0
max_cost = 0
model judge = NOT_CALIBRATED / SHADOW_ONLY
real shadow run = NOT_AUTHORIZED
```

Any later task that selects a concrete provider/model or raises a call/token/cost budget above zero requires a separate Human-authorized run contract.

## 2. Frozen input authority

The judge prompt may use only these semantic-review inputs:

```text
question
Gold/reference answer
authoritative Gold evidence / official evidence page when available
predicted answer
frozen answer-shape / task-type metadata when available
```

The following are forbidden as judge authority:

```text
Retriever rank/score
Solver route
Verifier verdict
Provider self-confidence
existing deterministic final verdict
qid/case-specific allowlist
```

Retriever, Solver, Verifier and product delivery signals may exist in separate diagnostic ledgers, but they must not be injected as correctness authority into the shadow judge.

## 3. Frozen provider-agnostic judge prompt contract

The future runtime must instantiate exactly this logical prompt contract without adding case-specific rules:

```text
SYSTEM / ROLE
You are an independent semantic correctness reviewer for financial-document QA.
Judge only whether the predicted answer is semantically supported by the supplied Gold/reference answer and authoritative Gold evidence.
Do not use product retrieval, solver, verifier, provider confidence, deterministic verdicts, case IDs or allowlists as correctness authority.

INPUT
question: <question>
Gold/reference answer: <gold_reference_answer>
authoritative Gold evidence / official evidence page: <gold_evidence_if_available>
predicted answer: <predicted_answer>
frozen answer-shape / task-type metadata: <metadata_if_available>

RUBRIC
1. Check the core conclusion first.
2. Check required values, units, identifiers, direction and comparison relations.
3. Check contradiction, correction, negation, revised values or statements that change the conclusion.
4. Check multipart coverage: all required parts must be addressed.
5. Extra information is negative only when it conflicts with Gold/evidence or changes the conclusion.
6. If Gold/reference/evidence is insufficient to determine correctness reliably, return CANNOT_ASSESS.

OUTPUT
Return one JSON-compatible structured object only. Do not add authoritative free-form text outside the structured result.
```

This is a semantic rubric, not a lexical-similarity rule and not a deterministic cue/regex extension.

## 4. Frozen structured output schema

Allowed `decision` values are exactly:

```text
CORRECT
INCORRECT
CANNOT_ASSESS
```

Required JSON-compatible shape:

```json
{
  "decision": "CORRECT | INCORRECT | CANNOT_ASSESS",
  "reason_code": "<structured_reason_code>",
  "short_rationale": "<short audit rationale>",
  "required_gold_elements_covered": "<structured value representing coverage>",
  "contradiction_found": false,
  "uncertainty_reason": "<required explanation when CANNOT_ASSESS; otherwise may be empty>"
}
```

No free-form answer outside the structured result may be treated as authoritative. Model-generated rationale is audit evidence only; it is not Gold truth.

## 5. Shadow input strata

Machine-readable source registration is frozen in:

`handoffs/evaluator_executor/FDQA-E4-L2-JUDGE-SHADOW-PREFLIGHT-V1/SHADOW_INPUT_MANIFEST.json`

### 5.1 REAL_CALIBRATION

The first real slice is referenced, not copied:

```text
evaluation_artifacts/external_benchmarks/financebench/e4_real_v1_repair1/answer_ab_checkpoint.jsonl
sha256 = 7dcc622cccbfda44b3b58fec7993ece4ab52263a5dde68833aa94644e0048b2d
count = 8
visibility = DEVELOPMENT_VISIBLE
role = FIRST_CALIBRATION_SLICE_ONLY
```

These 8 real outputs are necessary first calibration evidence but are not sufficient for generalization or authority promotion. They remain development-visible.

### 5.2 TRUST_TEST

TRUST_TEST is a separate synthetic/adversarial stratum. It registers failure families and source pointers only; it does not duplicate FinanceBench real rows and it never enters any REAL_CALIBRATION denominator.

Frozen minimum failure families:

```text
contradiction_correction
paraphrase_semantic_equivalence
ordinary_cannot_modality
explicit_answerability_refusal
benign_extra_context
```

Primary accepted source pointer for these families:

`tests/test_freeform_scoring_policy.py`

The reusable preflight document does not copy benchmark answer rows or trust-test rows.

## 6. Zero-call Provider/model and budget gate

The only valid state for this task is:

```text
provider = UNSELECTED
model = UNSELECTED
authorization_api_call = false
max_calls = 0
max_prompt_tokens = 0
max_completion_tokens = 0
max_total_tokens = 0
max_cost = 0
```

This preflight does not imply permission to choose a convenient model. A later Human/API authorization must explicitly freeze the selected Provider/model and non-zero limits before a bounded shadow run can start.

## 7. Checkpoint / resume / provider-ledger semantics

Future shadow execution must preserve the following bookkeeping rules.

### 7.1 Stable identity

Each attempted judge call must have a stable identity derived from the frozen run contract and record at least:

```text
run_id
stratum
case_id
prompt/rubric version
provider
model
attempt_index
```

A record cannot silently move between `REAL_CALIBRATION` and `TRUST_TEST`.

### 7.2 Checkpoint

A checkpoint must preserve completed results and the immutable identity needed to resume them. It must not rewrite previously completed outputs merely because execution restarts.

### 7.3 Resume

On resume:

```text
completed → preserved and skipped
failed / invalid → handled only according to the frozen run contract
missing → eligible for a new attempt within the authorized budget
```

Completed results are not called again unless a new run contract explicitly changes the prompt/rubric version, provider/model, source snapshot, or other frozen run identity.

### 7.4 Provider ledger

The provider ledger must distinguish:

```text
attempted
completed
failed
invalid
```

Every attempted call is counted once under the current run identity.

### 7.5 Incremental accounting

Token and cost accounting must be **incremental** to the current authorized run:

```text
current-run prompt tokens
current-run completion tokens
current-run total tokens
current-run cost
```

Inherited checkpoint rows may be used for resume state, but their historical calls/tokens/cost must not be added again to the current-run totals.

Every result must preserve its prompt/rubric version and provider/model identity so later meta-eval can reproduce which judge configuration produced it.

Partial runs remain non-authoritative and shadow-only.

## 8. Shadow-only side-effect boundary

Frozen authority boundary:

```text
judge output → evaluator evidence only
```

Forbidden paths:

```text
judge output → answer generation rewrite
judge output → Retriever rerank
judge output → Solver route change
judge output → Verifier override
judge output → automatic authority promotion
```

A shadow judge cannot mutate the answer under evaluation, product retrieval/routing, verification, or delivery behavior. If judge failures later motivate a product experiment, that must become a separate bottleneck/hypothesis/task.

## 9. Future shadow-run result accounting

When a later authorized run exists, its structured records must be evaluated with the accepted offline harness and keep the two strata separate:

```text
REAL_CALIBRATION
→ real_total / judge_decided / judge_abstain / judge_coverage
→ agreement / false_accept / false_reject / cannot_assess / unresolved_rate

TRUST_TEST
→ trust_total / trust_pass / trust_fail / trust_abstain
```

TRUST_TEST results must never increase or decrease real agreement/accuracy denominators.

Even a perfect result on the current 8 development-visible outputs does not grant authority. An independent real-output slice beyond the current 8 remains required before any authority-promotion review.

## 10. End-state and Human/API authorization gate

This task must end in exactly this state:

```text
model judge = NOT_CALIBRATED / SHADOW_ONLY
provider = UNSELECTED
model = UNSELECTED
real shadow run = NOT_AUTHORIZED
authorization_api_call = false
all call/token/cost budgets = 0
next required authority = Human/API authorization
```

### Human/API authorization gate

A future bounded shadow run may proceed only after Human authorization creates a new frozen run contract that explicitly supplies all of the following:

```text
selected provider
selected model
source manifest/hash
prompt/rubric version
max_calls
max_prompt_tokens
max_completion_tokens
max_total_tokens
max_cost
checkpoint/resume semantics
provider-ledger destination
```

Until that later authorization exists, no Provider/model/API/network call is allowed. Passing this preflight does **not** itself authorize execution or authority promotion.
