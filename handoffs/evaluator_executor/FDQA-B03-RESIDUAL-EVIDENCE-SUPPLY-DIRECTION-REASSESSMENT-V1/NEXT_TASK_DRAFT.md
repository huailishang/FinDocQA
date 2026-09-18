# DRAFT_ONLY｜DOWNSTREAM_CONTRACT_FIRST

Proposed task ID: `FDQA-FREEFORM-CANDIDATE-ASSERTION-CONSUMER-CONTRACT-V1`

Status: **DRAFT_ONLY**。本草案不是已冻结任务，不自动执行。

## Why this task

H-61 没有找到可晋级的新检索机制；H-43、H-53 和 H-50 gap-fill 复核都已有反证。与此同时，H-60 的 12 条 `VERIFIER_UNSUPPORTED` 是静态输入适用性声明，不是 12 次 verifier(验证器)执行失败。H-60 只有 freeform question(自由问答问题)和 Evidence Workspace(证据工作区)，没有 non-Gold candidate assertion(非 Gold 候选断言)，因此现有 `FinancialClaimSpec → ClaimFactBinding → FinancialEvidenceSufficiency` 链没有被这些 12 题真正调用。

下一步优先补的不是“更强验证器”，而是先把**自由问答候选答案如何变成可验证断言，并携带来源/工作区身份进入现有验证链**的最小契约冻结下来。

## Observable objective

建立并离线验证一个最小 consumer contract(消费契约)：

```text
freeform question
+ bounded evidence workspace
+ non-Gold candidate answer artifact
→ candidate assertion envelope
→ existing deterministic verifier inputs
→ executed / unsupported / unresolved / trusted 等可审计结果
```

本任务只证明接口可执行、边界可审计；不宣称答案正确率提升，不扩 Judge(裁判模型)，不自动启用生产路径。

## Inputs → outputs

输入：

- H-61 的 `DOWNSTREAM_AUDIT.json`、`CASE_FUNNEL.jsonl`；
- 已冻结的 H-60 12-case question/workspace artifacts；
- 现有 `FinancialClaimSpec`、`ClaimFactBinding`、`FinancialEvidenceSufficiency`、`EvidenceWorkspaceScope` 接口；
- 若仓库中存在**同 qid、非 Gold、带来源身份的既有 candidate answer/solver artifact**，可只读复用。
- 不得把 reference answer / Gold answer 改写成 candidate assertion。

输出：

- `CANDIDATE_ASSERTION_CONTRACT.md/json`：字段、来源身份、scope identity、fail-closed 规则；
- `ASSERTION_ADAPTER_EVAL.jsonl`：逐题记录 assertion available、adapter result、verifier executed、reason；
- `CONSUMER_CHAIN_TRACE.jsonl`：producer→adapter→binding→sufficiency 的逐步 lineage；
- 一份冻结的 offline Validation Plan(离线验证计划)和独立反例集。

## Reusable module boundary

若后续确认需要产品实现，通用逻辑应进入 `src/verification/**`，而不是写进 task script：

- candidate answer → typed/assertion envelope adapter；
- assertion envelope → existing claim parser/binder/sufficiency 接口；
- workspace/source lineage 透传和 fail-closed 校验。

本 DRAFT_ONLY 阶段不实现上述产品代码。

## Counterexamples / measurements

必须至少覆盖：

1. 无 candidate answer：明确 `ASSERTION_UNAVAILABLE`，不得冒充 verifier failure；
2. candidate answer 有文本但无 provenance：fail closed；
3. assertion 可解析但缺 entity/metric/period/value 必需槽位：unresolved；
4. assertion 完整但事实越出 workspace scope：untrusted/fail closed；
5. assertion + facts + lineage 完整：验证器确实执行，并记录 deterministic result；
6. Gold/reference answer 泄漏探针：一旦 candidate assertion 来源依赖 Gold，整轮失败。

核心计数分开：

- candidate assertion availability；
- verifier execution coverage；
- verifier supported/unresolved/contradicted；
- fact sufficiency；
- final answer correctness（若无独立评分权威则继续 `NOT_MEASURED`）。

## Stop conditions

以下任一发生即停止并回 Evaluator：

- 同 qid 没有可合法复用的非 Gold candidate answer，且没有新的 API/model 授权；
- 必须修改 Gold、参考答案或冻结 H-60 artifacts 才能继续；
- 需要扩 Judge/B-06 才能证明本接口；
- scope/lineage 无法确定或 adapter 只能靠 qid hardcode；
- 继续执行需要产品实现，但实现合同尚未单独冻结。

## Authorization needs

当前建议先保持：

```text
authorization_commit = false
authorization_push = false
authorization_history_rewrite = false
authorization_api_call = false
```

如果不存在可复用的非 Gold candidate answer artifacts，而下一阶段要真实生成候选答案，则必须由 Human/Task Owner **单独显式授权**有限模型/API 调用，并冻结 provider、case 数、总调用上限、失败处理、成本记录和 Gold 隔离规则后才能执行。

## Why this outranks the alternatives

- **B-03 Retrieval**：残余损失真实，但当前没有新的 qualifying Gold-free mechanism(合格的无 Gold 机制)；继续调 TopK/邻页/权重属于重复消耗。
- **B-05 complex tables**：现象量大，但题目级影响仍未知。
- **B-06 freeform Judge**：目前连 H-60 的 candidate assertion→verifier 都没有真实执行，直接扩 Judge 会跨过一个更前置的测量缺口。
- **B-07 provider/output gate**：历史上没有新的 common failure family(共同失败族)达到优先修复门槛。

因此本草案只选 **DOWNSTREAM_CONTRACT_FIRST**，不并行发第二个任务。
