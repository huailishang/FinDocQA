# DRAFT_ONLY｜DOWNSTREAM_CONTRACT_FIRST

Proposed task ID: `FDQA-FREEFORM-CANDIDATE-ASSERTION-CONSUMER-CONTRACT-V1`

Readiness: **NOT_READY_FOR_REAL_EXECUTION**

本草案仍然只有一个下一任务，不自动执行。A2 纠正的是测量和就绪条件：**接口设计可以先做，但真实题上的 verifier execution coverage（验证器执行覆盖率）还不能启动，因为合法的 non-Gold candidate answer（非 Gold 候选答案）来源尚未核实。**

## Why this task

H-61 A2 已确认两件事：

1. Retrieval（检索）还有真实残余，但当前没有一个新的 Gold-free（无 Gold）共同机制值得立刻再开一轮调参/扩页实验。
2. H-60 的 12 条 `VERIFIER_UNSUPPORTED` 是静态输入适用性声明。H-60 有 freeform question（自由问答问题）和 Evidence Workspace（证据工作区），但没有合法的 non-Gold candidate assertion（非 Gold 候选断言），所以现有 `FinancialClaimSpec → ClaimFactBinding → FinancialEvidenceSufficiency` 没有被这些 12 题真正执行。

A2 同时纠正历史收益解释：

- historical any-Gold：7/12 → 9/12，+2；
- all-Gold：7/12 → 7/12，+0；
- 两个历史“恢复题”都只是多页 Gold 的部分覆盖，不是完整证据页到齐。

因此下一步最有价值的是先把**消费契约本身设计清楚**，而不是假设候选答案已经存在，更不能直接把模拟正例当作真实题能力。

## Phase 0｜Contract design only

Phase 0 可以在零 API、零模型、零真实候选答案的情况下先做：

```text
freeform question
+ bounded evidence workspace
+ candidate-answer envelope（接口字段定义）
→ assertion adapter contract
→ existing claim parser / binder / sufficiency interfaces
```

输出建议：

- `CANDIDATE_ASSERTION_CONTRACT.md/json`：字段、类型、producer identity、qid/doc identity、workspace scope identity、lineage、fail-closed 规则；
- `CONSUMER_CHAIN_CONTRACT.md/json`：producer → adapter → claim parser → binding → sufficiency/verifier 的字段映射；
- `CONTRACT_FIXTURES.jsonl`：只用于接口测试的 synthetic positive/negative fixtures（模拟正/反例），明确标记 `SIMULATED_INTERFACE_ONLY`；
- `READINESS_CHECK.json`：检查真实 non-Gold candidate-answer source 是否存在、是否可追溯、是否与 Gold 隔离；
- 独立 Validation Plan（验证计划）和 stop conditions（停止条件）。

**Phase 0 的模拟正例只能证明接口能接，不能证明 FinanceBench/H-60 真实题上验证器能跑，也不能计入任何答案正确率或能力提升。**

## Real-execution readiness gate

只有以下条件全部满足，才允许从“契约设计”进入“真实执行覆盖测量”：

1. 找到并核实同一真实 qid 的 non-Gold candidate answer / solver artifact；
2. 该 artifact 有 producer provenance（生产来源）、时间/版本、qid/doc identity；
3. candidate answer 不是从 reference answer / Gold answer 改写或派生；
4. 能绑定到同一 Evidence Workspace scope；
5. 若来源需要新的模型/API调用，Human/Task Owner 已显式授权，并冻结 provider/model/case 数/总调用上限/失败处理/成本记录；
6. Evaluator 已冻结该次真实执行的样本、指标、效果门槛、成本护栏和停止条件。

在 readiness gate 通过前：

```text
verifier execution coverage on real H-60 cases = NOT_READY
fact sufficiency on real H-60 cases = NOT_MEASURED
final answer correctness = NOT_MEASURED
```

## Real-execution outputs（仅 readiness 通过后）

若未来独立包获得授权，可测：

- `ASSERTION_ADAPTER_EVAL.jsonl`：逐题 assertion available、adapter status、reason；
- `CONSUMER_CHAIN_TRACE.jsonl`：producer→adapter→binding→sufficiency 的 lineage；
- verifier executed / unsupported / unresolved / contradicted / trusted 等真实观测；
- fact sufficiency；
- final answer correctness 仅在另有独立 scoring authority（评分权威）时测量，否则继续 `NOT_MEASURED`。

## Required counterexamples

契约设计至少覆盖：

1. 无 candidate answer → `ASSERTION_UNAVAILABLE`，不得记作 verifier failure；
2. candidate answer 有文本但无 provenance → fail closed；
3. qid/doc identity 不匹配 → fail closed；
4. assertion 缺 entity/metric/period/value 必需槽位 → unresolved；
5. assertion 完整但事实越出 workspace scope → untrusted/fail closed；
6. assertion + facts + lineage 完整 → 模拟层允许验证接口路径，但标记 `SIMULATED_INTERFACE_ONLY`；
7. Gold/reference answer 泄漏 → 整轮失败。

## Measurement policy

未来任何真实实验的门槛都必须单独冻结：

```text
future_thresholds_status = TO_BE_FROZEN_PER_EXPERIMENT
```

不继承一个项目级的“必须恢复 >=3 题 / >=2 文档族”机械规则。不同任务应按自己的目标冻结：

- 样本/场景；
- success metric（成功指标）；
- minimum effect / acceptance threshold（最小效果/验收线）；
- cost guardrail（成本护栏）；
- regression guardrail（退化护栏）；
- stop/failure condition（停止/失败条件）。

## Scope and authorization

当前草案继续保持：

```text
authorization_commit = false
authorization_push = false
authorization_history_rewrite = false
authorization_api_call = false
```

Phase 0 只做设计和离线 synthetic fixtures，不实施产品代码。若未来需要通用产品实现，应另立工程包，把逻辑落入 `src/verification/**` 并配置离线测试，不能把 task-local script 当长期产品模块。

## Why this remains the single next direction

- **B-03 Retrieval**：残余真实，但 A2 没有发现新的共同机制；H-60 +2 现在只能解释成部分 Gold 页可达改善。
- **B-05 complex tables**：现象量大，题目级影响仍未建立。
- **B-06 freeform Judge**：candidate assertion→existing verifier 还没有真实执行，直接扩 Judge 仍跨过更前置测量边界。
- **B-07 provider/output gate**：没有新的 common failure family 达到优先修复门槛。

因此仍只保留 **DOWNSTREAM_CONTRACT_FIRST**，但把“契约设计”与“真实执行覆盖测量”明确分开。
