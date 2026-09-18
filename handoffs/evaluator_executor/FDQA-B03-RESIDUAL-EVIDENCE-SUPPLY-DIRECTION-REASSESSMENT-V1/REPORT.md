# Executor Report｜H-61 检索收口与证据消费边界审计

Task ID: `FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1`  
Executor status: BLOCKED
Baseline HEAD: 849ff3bda52481c7ecb999d5967a851910624265
Implementation commit: NONE
Active contract: `CONTRACT_A1.md`  
Workflow: `evaluator-executor-workflow/v2.2`  
Project-impact verdict 建议: **NOT_APPLICABLE**  
End-to-end improvement: **NOT_MEASURED**

阻塞说明：规定产物和 L2 mechanical gate(机械门禁)均已完成，但冻结 AC-02 同时要求 historical any-Gold 7→9 与 new all-Gold-pages 7→9；冻结事实证明两者不是同一口径，因此 Executor 不能自签可提交。

## 1. 全局链路与当前任务位置

FinDocQA 当前链路是：

```text
Question(问题)
→ parser/source identity(解析/来源身份)
→ lexical/semantic retrieval(词法/语义检索)
→ bounded Evidence Workspace(有界证据工作区)
→ candidate assertion(候选断言)
→ Claim-Fact Binding(声明-事实绑定)
→ Evidence Sufficiency / Verification(证据充分性/验证)
→ answer evaluation(答案评价)
→ deliverable answer(可交付答案)
```

已经建立的主要能力：

- parser/source lineage(解析/来源追溯)、结构化事实与多类 deterministic verification(确定性验证)能力已有历史验证；
- H-56R1 已关闭 workspace scope/product-route reachability(工作区范围/产品路由可达性)缺口；
- H-60 证明 bounded workspace(有界工作区)对历史 **any-Gold-page(任一 Gold 页命中)** 指标存在有限正收益，但方向未达到资格线；
- 本 H-61 不再调 TopK、RRF 权重、邻页半径或 provider/model，而是一次把 Retrieval(检索)残余和 downstream consumer contract(下游消费契约)边界查清。

当前项目第一已测瓶颈仍是 B-03 evidence supply(证据供给)，但“有漏题”不等于继续调检索最值得；B-06 freeform scoring(自由文本评分)、B-05 complex table parsing(复杂表格解析)、B-07 provider/output reliability(供应商/输出可靠性)均保持次级/观察状态。本轮的核心价值是判断下一笔有限预算应该花在哪里。

## 2. 输入与执行边界

冻结输入经 `check_packet.py --check authority --mode executor` 校验：

```text
authority=PASS
```

实际输入与 SHA256 记录见 `INPUT_MANIFEST.json`。执行环境：

- Executor Python: Conda `agent`, Python 3.12.13；
- shared runner(共享验证 runner)启动 Python: system Python 3.12.3 + PyYAML 6.0.3；
- external API calls = 0；
- model calls = 0；
- dependency install attempts = 0；
- `src/**`、`config/**`、`tests/**` 未修改。

候选 retrieval mechanism(检索机制)在产出前冻结为 `null`，见 `MECHANISM_FREEZE.json`。原因不是“残余不存在”，而是没有新的 Gold-free(无 Gold)共同机制能越过 H-43/H-53/H-50 历史反证门槛。

## 3. Work unit 1｜12 题 reach/cost funnel(命中/成本漏斗)

机器产物：`CASE_FUNNEL.jsonl`，12 行。

### 3.1 新 A1 口径：all-Gold-pages(全部 Gold 页到齐)

按 `CONTRACT_A1.md` work unit 1 的文字要求，逐题用：

```text
set(canonical_gold_pages) ⊆ selected_pages
```

重新计算：

| 指标 | lexical Top5 | semantic Top5 | baseline RRF60 Top5 | candidate workspace |
|---|---:|---:|---:|---:|
| all-Gold-pages reach | 4/12 | 7/12 | 7/12 | 7/12 |

candidate workspace(候选工作区)相对 baseline：

- 页面总数：60 → 104，+44，约 **+73.33%**；
- 同一 `H60_RUNTIME_INPUTS.jsonl` page-text 来源字符数：250,530 → 434,584，+184,054，约 **+73.47%**；
- 平均页数：5.00 → 8.67；
- 平均字符数：20,877.5 → 36,215.3；
- all-Gold full-case recovery(全部 Gold 页完整恢复)：**0**；
- all-Gold protected loss(原完整命中被破坏)：**0**。

字符数只是 text-volume proxy(文本量代理)，不是 token、延迟或金额成本。

### 3.2 历史 H-60 口径：any-Gold-page(任一 Gold 页命中)

H-60 正式历史结论仍保持：

```text
baseline reach = 7/12
candidate reach = 9/12
recovered = 2
protected_loss = 0
PASS / IMPROVED / SWITCH / NOT_QUALIFIED
```

原因已从冻结代码确认：`run_h60_cloudflare_workspace.py:908-909` 使用：

```python
bool(set(canonical_gold_pages).intersection(selected_pages))
```

也就是 **任一 Gold 页出现就计 reach**，不是 all-Gold-pages。

两条历史 recovery：

- `financebench_id_02024`：Gold = [63, 94]，candidate 只有 94；
- `financebench_id_03856`：Gold = [57, 61]，candidate 只有 61。

所以：

```text
历史 any-Gold：7 → 9，恢复 2
A1 all-Gold：  7 → 7，恢复 0
```

这不是重写 H-60 历史判决。H-60 当时按自己的冻结口径成立；H-61 只是证明**不能把 H-60 的 9/12 直接叫作 all-Gold-pages 9/12**。

## 4. Work unit 2｜Residual(残余)一次收口

机器产物：`RESIDUAL_CASES.jsonl`，严格保留合同规定的：

- H-60 历史 union miss(并集仍漏) = 3；
- H-50 `BOTH_MISS` = 18；
- 合计 21 个 `(cohort, qid)` observation(观察)。

H-60 三条历史 residual：

- `financebench_id_00521` / ULTA 2023；
- `financebench_id_04735` / ADOBE 2015；
- `financebench_id_03882` / AMCOR 2020。

H-50 的 18 条 `BOTH_MISS` 覆盖多文档族，lexical/semantic lane agreement(词法/语义通道一致性)形态不一致：有大量 Top5 零重叠/低重叠，也有少量 shared distractors(共同干扰页)。这说明 evidence-supply loss(证据供给损失)跨批次真实存在，但本轮没有发现一个新机制能同时解释并可操作地解决这些 case。

历史反证继续有效：

- H-43 fresh target-guided planner(新样本目标引导规划)：0/7 recovery，`NO_MEASURABLE_GAIN`；
- H-53 fixed ±2 neighbor(固定 ±2 邻页)：fresh12 只恢复 2/12，`NOT_QUALIFIED` 且新增页成本高；
- H-60 局部 gap-fill(间隙补页)信号拿到 H-50 18 `BOTH_MISS` 上，`max_gap=2/3/4` 均 0/18。

因此 `DECISION.json` 的 retrieval direction(检索方向)为：

```text
NO_SINGLE_DIRECTION
```

这表示“没有新的合格共同机制”，不是“检索问题已经消失”。

## 5. Work unit 3｜Downstream consumer audit(下游消费审计)

机器产物：`DOWNSTREAM_AUDIT.json`。

最关键的纠偏已经确认：

```text
h60_status_origin = STATIC_INPUT_APPLICABILITY
observed_verifier_executions = 0
observed_answer_evaluations = 0
runtime_claim_source = null
```

H-60 的 `downstream_support_measurement()` 在 `run_h60_cloudflare_workspace.py:964-1002` 中直接根据输入条件写 `VERIFIER_UNSUPPORTED`。它记录：

- required runtime input = `candidate_answer_claim`；
- available runtime input = `freeform_question_only`；
- Gold reference answer 不允许；
- generative solver(生成式求解器)不允许。

因此 12 条 `VERIFIER_UNSUPPORTED` 的正确解释是：

> 当前 H-60 measurement boundary(测量边界)没有合法 candidate assertion(候选断言)，所以验证器没有逐题执行。

不能解释为：

> 验证器执行了 12 次并失败 12 次。

现有下游能力本身并非空白：

- `parse_financial_claim` 负责把 assertion/option text(断言/选项文本)解析成 `FinancialClaimSpec`；
- `ClaimFactBinding` 要求 entity/metric/period/unit/document/source lineage(实体/指标/期间/单位/文档/来源追溯)正确绑定；
- `FinancialEvidenceSufficiency` 还要求 required atoms(必需原子)、formula(公式)、document boundary(文档边界)、binding safety(绑定安全性)完整；
- `EvidenceWorkspaceScope` 对允许页面做 fail-closed(失败关闭)约束；
- H-56R1 已证明 product route(产品路由)可传播 workspace scope。

真正缺失的是 H-60 这条 freeform 链上的前置 consumer contract(消费契约)：

```text
freeform question + workspace
→ non-Gold candidate answer/assertion   ← 当前未观测
→ claim parser
→ binding
→ sufficiency/verifier
→ final answer evaluation               ← 当前也未观测
```

所以本轮：

- candidate claim availability = 0/12；
- verifier executed = 0/12；
- fact sufficiency = `NOT_MEASURED`；
- answer correctness = `NOT_MEASURED`。

“0 次验证执行”不是“0%验证能力”。

## 6. Work unit 4｜方向比较与唯一下一任务

`DECISION.json` 给出的唯一 next action(下一动作)：

```text
DOWNSTREAM_CONTRACT_FIRST
```

对应草案：`NEXT_TASK_DRAFT.md`，状态明确为 **DRAFT_ONLY**，不会自动执行。

比较逻辑：

- **B-03 Retrieval(检索)**：残余损失证据强，但没有新 qualifying Gold-free mechanism(合格无 Gold 机制)；继续调 TopK/邻页/RRF 属于重复消耗。
- **B-05 complex table parsing(复杂表格解析)**：2124 张复杂/image/span 表是现象证据，但题目级影响还是低置信。
- **B-06 freeform scoring(自由文本评分)**：known-wrong Judge 有历史证据，但本轮连 candidate assertion→verifier 都没有真实执行，直接扩 Judge 会跨过更前置测量边界。
- **B-07 provider/output gate(供应商/输出门禁)**：历史 cohort 没有 common failure family(共同失败族)达到通用修复门槛，本轮也没有新增证据。

下一草案只冻结：

```text
freeform question
+ bounded workspace
+ non-Gold candidate answer
→ candidate assertion envelope
→ existing verifier consumer contract
```

如果仓库没有可复用的同 qid、非 Gold candidate answer artifact，而要真实生成候选答案，后续必须由 Human/Task Owner 另行明确授权有限 API/model 调用；H-61 本身不具备该授权。

部署依赖三档见 `DEPLOYMENT_PROFILES.md`：P0 无模型词法/结构基线、P1 批准内网语义服务、P2 允许的外部服务。三档共享 lineage/scope/fail-closed 边界，但不声称效果相同，也不把缓存回放冒充在线能力。

## 7. Impact comparison

Task kind 是 `evaluator_design`，因此 Project-impact verdict 建议 **NOT_APPLICABLE**；本轮不宣称产品 capability gain(能力提升)。

本轮新增的项目级信息是：

1. historical any-Gold 7→9 与 A1 all-Gold 7→7 是两个不同 measurement definition(测量定义)；
2. H-60 的 12 条 `VERIFIER_UNSUPPORTED` 是静态 applicability(适用性)结果，不是 verifier execution(验证器执行)结果；
3. 当前没有新的检索共同机制值得立刻投入下一轮；
4. 一个更前置、可测且边界清晰的未知点是 freeform candidate assertion→existing verifier 的 consumer contract；
5. end-to-end answer correctness 仍为 **NOT_MEASURED**。

## 8. Frozen AC-02 semantic blocker(冻结 AC-02 语义阻塞)

`CONTRACT_A1.md` 同时要求：

1. work unit 1 “统一用 all-Gold-pages 命中口径”；
2. AC-02 “7→9 / 恢复2 / 损失0复现”。

冻结 H-60 代码证明 7→9 / recovery2 来自 **any-Gold intersection(任一 Gold 页交集)**。因此两条要求无法在同一 all-Gold measurement(全部 Gold 测量)下同时成立。

机械 checker(检查器)目前把两部分分开检查：

- `authority()` 只验证历史 summary 仍为 7/9/2/0；
- `funnel()` 逐题验证新输出使用 `gold.issubset(pages)` 的 all-Gold 逻辑；

所以两者都可以机械 PASS，但这并不能消除冻结 AC-02 的语义矛盾。

Executor 不修改 evaluator-owned(评估者所有)的 `CONTRACT_A1.md`、`VALIDATION_PLAN_A1.yaml` 或 `check_packet.py` 来“让结果好看”。根据 stop condition(停止条件)里的 “plan not applicable(计划不适用)” 条款，本报告状态保持 **BLOCKED**，等待 Evaluator 在 L3/修订中明确 AC-02 的权威口径。

建议最小修订方向只有二选一：

```text
A. AC-02 明确分开：
   historical any-Gold 7→9 / recovery2（仅历史复核）
   + H-61 all-Gold 7→7 / recovery0（新审计）

或

B. 若 Evaluator 坚持 AC-02 必须 all-Gold 7→9，
   则当前冻结事实直接使 AC-02 不通过。
```

H-60 历史 verdict 不需要因本问题被回写或翻案。

## 9. Validation evidence references

本报告预先绑定 frozen L2(冻结 L2) 的权威证据位置；runner 执行后这些路径为最终机械 gate authority(门禁权威)：

- `evidence/L2-GATE.json`
- `evidence/L2-GATE.md`
- `evidence/EV-01.meta.json` / stdout / stderr — AC-01 authority
- `evidence/EV-02.meta.json` / stdout / stderr — AC-02 funnel
- `evidence/EV-03.meta.json` / stdout / stderr — AC-03 residual
- `evidence/EV-04.meta.json` / stdout / stderr — AC-04 downstream
- `evidence/EV-05.meta.json` / stdout / stderr — AC-05 decision
- `evidence/EV-06.meta.json` / stdout / stderr — AC-06 replay
- `evidence/EV-07.meta.json` / stdout / stderr — AC-07 scope/report

两份 Executor self-check(自检)：

- `evidence/self-check-round1.json`
- `evidence/self-check-round2.json`

即使机械 L2 全通过，本报告也**不会把 runner PASS 当作 task PASS**；最终语义裁决属于 Evaluator，且 AC-02 的 frozen-contract contradiction(冻结合同矛盾)必须在接受快照前被处理。

## 10. Executor conclusion

当前可确认：

```text
H-61 required artifacts: 已产出
Retrieval direction: NO_SINGLE_DIRECTION
Next-action draft: DOWNSTREAM_CONTRACT_FIRST
API/model calls: 0
dependency installs: 0
product implementation changes: 0
historical H-60 verdict: unchanged
end-to-end improvement: NOT_MEASURED
Project-impact verdict suggestion: NOT_APPLICABLE
Executor status: BLOCKED
blocking reason: AC-02 mixes historical any-Gold 7→9 with new all-Gold-pages requirement
```

Executor 不自签 `PASS`，不把下一 DRAFT_ONLY 包自动执行，也不 commit/push。

## Workspace snapshot

- Initial `git status --short`: `M docs/evaluation/PROJECT_BOTTLENECK_MAP.md`；`M handoffs/evaluator_executor/state/CURRENT.md`。两项均在 Executor 产出任务文件前已存在，其中地图为 Evaluator 设计变更。
- Final `git status --short`: 仍只显示上述两个 tracked change；本任务目录产物受仓库现有 ignore/跟踪策略影响未出现在简短状态中，但均已在本地任务目录生成并由 L2 读取。
- Saved diff: 未另存产品 diff；本任务不修改 `src/**`、`config/**`、`tests/**`。
- Diff SHA-256: NOT_APPLICABLE；本任务没有 implementation commit，且报告/任务证据自身不是产品 diff。

## Changed files

| File | Action | SHA-256 / identity | Factual change |
|---|---|---|---|
| `docs/evaluation/PROJECT_BOTTLENECK_MAP.md` | preserved | `24cad5d8e17e08fcf02b822a2dc88d7b4339456705d0ecaf7648ee0eb56008cd` | Executor 未修改；保留 Evaluator 预先存在的 r86 设计变更 |
| `handoffs/evaluator_executor/state/CURRENT.md` | updated | current local state | 仅按冻结路由从 `CONTRACT_FROZEN` 进入 `EXECUTING`，角色仍为 Executor |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/MECHANISM_FREEZE.json` | created | `eb71489661587e7ad13e12c3375406ebae9f2b46c23e917cc03cf3a0202c8de0` | 在分析前冻结“无候选共同检索机制” |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/INPUT_MANIFEST.json` | created/corrected | `45bbf9e21e717f5fc668d8be50a46c02c3e1b98fbec965205c9bd54ce5fe467e` | 记录冻结输入与零调用/零安装；一次 SHA 转录错误已修正 |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/run_analysis.py` | created | `eb0ec3fa7e16bb6a4b8c9427a679f0ca754896f807096f92a4237372ea6863a2` | 零 API 的确定性四产物生成入口 |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/CASE_FUNNEL.jsonl` | created | `21f8bc811f4b9cc51f801b07b8732307a6bf6ea2c9e7ab1999247bb72ec434a3` | 12 题 all-Gold reach 与页/字符成本 |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/RESIDUAL_CASES.jsonl` | created | `33507358e916cba12ae279332ef12b36f656d620885d57fd928a674f7a283863` | H-60 3 + H-50 18 残余观察 |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/DOWNSTREAM_AUDIT.json` | created | `77413e4e33daa98c4fdaf5368b386e6dfd75b78c93d09c1ed8c8fc383bd9ca5c` | producer→consumer 与缺失契约审计 |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/DECISION.json` | created | `6b333d57451011f9f603b9251beff475d870367aea66ed23b32b286c6fd9f2fb` | `NO_SINGLE_DIRECTION / DOWNSTREAM_CONTRACT_FIRST` |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/NEXT_TASK_DRAFT.md` | created | `717249d2b66adcd7c8ff61f06bc8817a21e33c085a6e44c7dbaa64edaf8efaf2` | 恰好一个 DRAFT_ONLY 后续任务 |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/DEPLOYMENT_PROFILES.md` | created | `7371abec405be2d59cdf10ec87ac62b2a47fed394d60e175514006560a028415` | P0/P1/P2 依赖与 fail-closed 边界 |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/**` | created | indexed below | self-check、两次 L2 尝试、最终 PASS gate 与 replay evidence |
| `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/REPORT.md` | created | self-referential; final hash changes with this index | 本 Executor 报告 |

## L2 Task Gate

- Gate result: PASS
- Gate summary: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/L2-GATE.json
- Validation plan: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/VALIDATION_PLAN_A1.yaml
- Mandatory failures: 0

第一次 L2 的 VP-07 因 `INPUT_MANIFEST.json` 手工转录 `financial_report_claims.py` SHA256 时漏掉最后一个字符 `e` 而失败；该次证据保存在 `evidence/l2-attempt1/`。修正为 `DESIGN_INPUT_MANIFEST.json` 的原冻结值后，按合同允许的一次 implementation-error correction replay(实现错误修正重放)重新执行，最终 7/7 PASS；产品行为、分析逻辑与四个机器产物均未改变。

## EV-01

- AC: AC-01
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-01.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-01.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-01.stderr.log

## EV-02

- AC: AC-02
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-02.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-02.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-02.stderr.log

## EV-03

- AC: AC-03
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-03.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-03.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-03.stderr.log

## EV-04

- AC: AC-04
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-04.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-04.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-04.stderr.log

## EV-05

- AC: AC-05
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-05.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-05.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-05.stderr.log

## EV-06

- AC: AC-06
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-06.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-06.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-06.stderr.log

## EV-07

- AC: AC-07
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-07.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-07.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/EV-07.stderr.log

## Impact comparison

- Measurement evidence: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/CASE_FUNNEL.jsonl; handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/DECISION.json
- Before: historical H-60 any-Gold baseline = 7/12；A1 all-Gold baseline = 7/12；baseline pages/chars = 60 / 250530。
- After: historical H-60 any-Gold candidate = 9/12；A1 all-Gold candidate = 7/12；candidate pages/chars = 104 / 434584。
- Delta: historical any-Gold = +2 cases；A1 all-Gold full-case = +0；页面 +44（+73.33%），字符 +184054（约 +73.47%）。
- Guardrail result: historical H-60 verdict unchanged；historical protected loss = 0；external API/model/install = 0；`src/**`/`config/**`/`tests/**` 变更 = 0。
- Scope caveat: historical any-Gold 与 A1 all-Gold 是不同 measurement definition(测量定义)；页面 reach 不能外推 fact sufficiency、verifier result 或 final answer correctness，后三者在本包分别为未执行/NOT_MEASURED。

## Deviations and unresolved items

- Contract deviation: 无主动偏离。执行者严格保留冻结合同和检查器；发现的是冻结 AC-02 自身把 historical any-Gold 7→9 与 new all-Gold-pages 要求写在同一 AC 中的语义冲突。
- Checks not run and reason: 未运行 L3，因为 L3 和语义裁决属于 Evaluator；未运行 API/model/Solver/Judge，因为本包明确禁止且无授权。
- Known unresolved issue: AC-02 的权威口径需要 Evaluator 明确。机械 VP-02 PASS 只能证明 all-Gold 文件计算正确，不能证明 all-Gold 也存在 7→9。
- Human or external dependency: 当前不需要 Human 才能保存本包；若后续草案需要真实生成 non-Gold candidate answer，则必须重新取得明确 API/model 授权。当前最直接依赖是 Evaluator 对 AC-02 做语义裁决/最小修订。
- Out-of-scope finding: B-05/B-06/B-07 仅比较证据优先级，本包没有实施解析器、Judge、Provider 或产品 Runtime 改造。

