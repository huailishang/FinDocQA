# Executor Report｜H-60 Cloudflare Bounded Workspace Capability

Task ID: `FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1`

Workflow: `evaluator-executor-workflow/v2.2`

Task kind: `capability_experiment`

Executor status: SUBMITTED_FOR_REVIEW

Baseline HEAD: `b057674`

Primary hypothesis verdict: `NOT_QUALIFIED`

Project-impact verdict: `IMPROVED`

## 本轮执行结果

H-60 已完成冻结的单轮 capability experiment(能力实验)。执行严格遵守 Amendment 01：先通过 Phase 0 runtime-text gate(运行文本门禁)，再执行 Cloudflare exact-profile preflight(精确配置预检)，随后完成全部 80 个 runtime embedding(运行时向量)单元，并冻结 baseline(基线) / candidate(候选) 输出后再进行 Gold scoring(标准答案评分)。

本轮没有安装、下载或升级任何依赖，没有修改 `src/**`、`config/**`、`tests/**`，没有 generative LLM(生成式大模型)、Judge(裁判)、Solver(求解器)或 reranker(重排器)调用，也没有 paid fallback(付费回退)。

## Phase 0｜Runtime text gate(运行文本门禁)

`RUNTIME_TEXT_PREFLIGHT.json = PASS`

```text
no_dependency_install_policy = ENFORCED
dependency_install_attempts = 0
dependency_download_attempts = 0
extractor = PyMuPDF
extractor_version = 1.28.0
financebench_revision = cc39aeb4afdf33909ee1412188bf89035950c2eb
page_count = 1019
query_count = 12
canonical_hash_checks = 15
canonical_hash_mismatches = 0
h59_canonical_refs_checked = 15
h59_canonical_hash_mismatches = 15
h59_runtime_source_accepted = false
runtime_input_sha256 = c0645faf9febe794b9400a30773d7bed46ff39af632c7a1c28ab4d4668fc62d7
```

H-60 使用机器上已经存在的 PyMuPDF `1.28.0` 解释器，从冻结 FinanceBench Git blobs 重新抽取 `1019` 页，并复现 H-58 的 `15/15` canonical page-text hash(标准页文本哈希)。H-59 的旧 runtime text(运行文本)仍因 `15/15` mismatch(不一致)被拒绝。

## Phase 1｜Cloudflare exact-profile preflight(精确配置预检)

`CLOUDFLARE_PREFLIGHT.json = PASS`

```text
provider = cloudflare-workers-ai
model = @cf/qwen/qwen3-embedding-0.6b
profile_id = qwen3-0.6b-cloudflare-v1
embedding_dimension = 1024
paid_fallback_used = false
```

单输入 preflight(预检)返回 HTTP 200，向量维度为冻结的 `1024`。凭证值和原始 provider response(供应商响应)均未持久化。

## Phase 2｜Runtime embedding(运行时向量)

全部冻结单元完成：

```text
page batch calls = 68
query calls = 12
runtime embedding calls = 80
preflight calls = 1
successful calls = 81
physical attempts = 81
retryable failures = 0
terminal failures = 0
max attempts per successful call = 1
max parallelism = 1
```

预算约束全部满足：successful calls `<=81`、physical attempts `<=162`、per-unit attempts `<=2`、max parallelism `<=2`。

## Primary measurement｜Verification-boundary evidence reach(验证边界证据到达)

冻结比较：

```text
BASELINE
same lexical Top5 + same Cloudflare semantic Top5
→ equal-weight RRF60
→ fixed Top5
→ verification boundary

CANDIDATE
same lexical Top5 + same Cloudflare semantic Top5
→ deduplicated union workspace(max10)
→ verification boundary
```

观测结果：

```text
case_count = 12
baseline_gold_reach_cases = 7
candidate_gold_reach_cases = 9
recovered_cases = 2
recovered_document_families = 2
protected_loss = 0
workspace_max_unique_pages = 10
qualified = false
```

恢复的两个 case(样本)来自两个不同文档族：

```text
financebench_id_02024 → VERIZON_2021_10K
financebench_id_03856 → ADOBE_2017_10K
```

冻结 qualification rule(合格规则)要求：

```text
recovered_cases >= 3
recovered_document_families >= 2
protected_loss = 0
workspace_max_unique_pages <= 10
```

本轮满足后三项，但 `recovered_cases = 2 < 3`，因此 H-60 **未达到冻结 qualification threshold(合格阈值)**。

## Secondary measurement｜Deterministic trusted reach(确定性可信到达)

12/12 case 均被当前 deterministic verifier(确定性验证器)分类为 `VERIFIER_UNSUPPORTED(验证器当前不支持)`，原因是现有 claim verifier(声明验证器)需要非 Gold 的 candidate answer claim(候选答案声明)，而 H-60 当前只有 freeform question(自由问答问题)和检索证据边界，没有可合法复用的非 Gold candidate answer。

因此：

```text
verifier_supported_case_count = 0
verifier_unsupported_case_count = 12
unsupported_counted_as_workspace_failure = false
candidate_trusted_losses_from_baseline_supported_trusted = 0
secondary_improvement_claim_supported = false
```

该结果不作为 workspace(证据工作区)成功或失败的二次证据，只记录为 downstream coverage limitation(下游覆盖限制)。

## Offline replay(离线重放)

完成一次只读 offline replay(离线重放)：

```text
api_attempt_count_before = 81
api_attempt_count_after = 81
external_api_calls_during_replay = 0
offline_replay = PASS
```

离线重放重新生成并核对主要输出 hash，结果仍为：

```text
recovered_cases = 2
recovered_document_families = 2
protected_loss = 0
workspace_max_unique_pages = 10
primary_qualified = false
```

说明当前 capability result(能力结果)可由持久化 embedding(向量)离线复现，不依赖再次调用 Cloudflare。

## API / cost guardrail(调用与成本护栏)

`RUN_COST_SUMMARY.json` 证明：

```text
successful_calls = 81
physical_attempts = 81
preflight_calls = 1
runtime_embedding_calls = 80
retryable_failures = 0
terminal_failures = 0
paid_fallback_used = false
unauthorized_generative_calls = 0
judge_calls = 0
solver_calls = 0
reranker_calls = 0
credentials_persisted = false
raw_provider_responses_persisted = false
```

## Impact comparison

Before: H-58 Fresh12 cohort(冻结样本集)提供 `12 cases / 9 document families`，lexical baseline(词法基线)为 `4 hit / 8 miss`。H-60 要验证的是同一 lexical Top5 + 同一 Cloudflare semantic Top5 下，延迟收缩为 bounded union workspace(有界并集证据工作区)能否在 verification boundary(验证边界)显著恢复更多 Gold evidence(标准证据)。

After: baseline RRF60 Top5 到达 `7/12`，candidate bounded workspace 到达 `9/12`，增加 `2` 个 recovered case(恢复样本)，覆盖 `2` 个不同文档族，且 `protected_loss = 0`。

Delta: `+2/12 evidence-reach cases`，但冻结门槛要求至少 `+3 cases`。因此本轮存在有限正向信号，但**未达到事先定义的 capability qualification(能力合格)标准**。

Project-impact verdict: `IMPROVED`。这里仅表示冻结 measurement boundary(测量边界)上 evidence reach(证据到达)从 `7/12` 提升到 `9/12`，存在可测的正向变化；它**不等于** capability qualification(能力合格)、产品就绪或值得继续同方向追加预算。

H-60 只测量 Cloudflare 0.6B profile(配置)下的 contraction timing(收缩时机)效果，**不支持任何 0.6B-vs-8B 或 0.6B vs 8B 模型优劣结论**。

## Stop condition(停止条件)

冻结合同规定：`primary qualification is not met after the single frozen run` 时必须停止并返回 Evaluator(评估者)，不能在 H-60 内追加第二 cohort(样本集)、改阈值、改模型/provider、改 query、改 TopK、改 RRF 权重或继续调参。

因此执行者不再追加实验。

## Required outputs(必需产物)

已生成：

- `RUNTIME_TEXT_PREFLIGHT.json`
- `H60_RUNTIME_INPUTS.jsonl`
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
- `RUNTIME_OUTPUT_FREEZE.json`
- `evidence/OFFLINE_REPLAY.json`

## Workspace snapshot

Workspace snapshot: baseline HEAD=`b057674`；`CURRENT.md` 保持 `EXECUTING / Executor`；H-60 task-local outputs 已完整生成；`src/**`、`config/**`、`tests/**` 无本任务修改。

Changed files: 仅 H-60 task-local `handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/**` 及其 evidence(证据)产物发生变化；未提交 commit、未 push、未 history rewrite(历史改写)。

Deviations and unresolved items: 执行过程中曾出现一次工具层长调用 timeout(超时)，但 task checkpoint(任务断点)显示该波次已完整持久化到 `50/80`，后续从 checkpoint 继续且没有重复已完成 logical unit(逻辑单元)。唯一未解决项是当前 deterministic verifier(确定性验证器)对 12/12 freeform case 均为 `VERIFIER_UNSUPPORTED`；这不影响 primary measurement(主测量)。

Measurement evidence: `RUNTIME_TEXT_PREFLIGHT.json`、`H60_RUNTIME_INPUTS.jsonl`、`CLOUDFLARE_PREFLIGHT.json`、`SEMANTIC_EMBEDDING_MANIFEST.json`、`SEMANTIC_TOP5.jsonl`、`BASELINE_RRF60_RESULTS.jsonl`、`CANDIDATE_WORKSPACE_RESULTS.jsonl`、`BOUNDARY_CAPABILITY_SUMMARY.json`、`DOWNSTREAM_TRUSTED_SUMMARY.json`、`RUN_COST_SUMMARY.json`、`evidence/OFFLINE_REPLAY.json`、`evidence/l2/L2-GATE.json`。

Guardrail result: PASS。零依赖安装/下载；81/81 external calls(外部调用)成功且在冻结预算内；paid fallback=0；Generative LLM/Judge/Solver/reranker=0；Gold 只在 runtime outputs freeze(运行输出冻结)后用于评分；产品代码修改=0。

Scope caveat: H-60 只验证 Cloudflare 0.6B profile 下 `early fixed Top5 contraction → bounded union workspace before verification` 的 evidence-reach(证据到达)效果；不验证 0.6B 与 8B 模型优劣，不测答案质量，也不允许在本任务内更换 provider/model、query、TopK、RRF 权重、cohort 或阈值来“救结果”。

## L2 Task Gate

- Gate result: PASS
- Gate summary: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/L2-GATE.json
- Validation plan: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/VALIDATION_PLAN.yaml

L2 = `9/9 PASS`，mandatory failures(强制失败项)=`0`。这表示执行包、输入权威、同 lane(同通道)比较、Gold 后评分、API 预算、产品代码保护和报告边界均满足冻结机械验收。L2 PASS 不代表 capability qualified(能力合格)，也不代表 Evaluator 最终 PASS。

`CURRENT.md` 按 v2.2 submit(提交)语义继续保持 `EXECUTING / Executor`，等待 Evaluator accept(接收)后再切换 `READY_FOR_REVIEW / Evaluator`。

## EV evidence

## EV-01

- AC: AC-02, AC-07
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-01.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-01.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-01.stderr.log

## EV-02

- AC: AC-01
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-02.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-02.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-02.stderr.log

## EV-03

- AC: AC-02, AC-03
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-03.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-03.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-03.stderr.log

## EV-04

- AC: AC-03, AC-04
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-04.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-04.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-04.stderr.log

## EV-05

- AC: AC-05
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-05.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-05.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-05.stderr.log

## EV-06

- AC: AC-06
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-06.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-06.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-06.stderr.log

## EV-07

- AC: AC-07
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-07.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-07.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-07.stderr.log

## EV-08

- AC: AC-08, AC-09
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-08.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-08.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-08.stderr.log

## EV-09

- AC: AC-07, AC-09
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-09.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-09.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l2/EV-09.stderr.log

## Executor task verdict

`EXECUTION_COMPLETE / PRIMARY_NOT_QUALIFIED / SUBMITTED_FOR_REVIEW`

Executor 不发 `PASS / REJECTED`。H-60 实验执行与 L2 已完成。原 shared v2.2 workflow schema blocker 已由 Evaluator 以无语义变化的 CONTRACT 字段规范化修复，并经 `validate_workflow.py` 复核为 `OK`；本次提交不重复任何 Cloudflare 调用或重跑实验。