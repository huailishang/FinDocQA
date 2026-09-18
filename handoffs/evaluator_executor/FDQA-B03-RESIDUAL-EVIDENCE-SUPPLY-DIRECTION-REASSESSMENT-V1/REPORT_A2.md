# Executor Report A2｜H-61 指标定义修复

Task ID: FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1  
Executor status: SUBMITTED_FOR_REVIEW  
Baseline HEAD: 849ff3bda52481c7ecb999d5967a851910624265  
Implementation commit: NONE  
Active contract: `CONTRACT_A2.md`  
Workflow: `evaluator-executor-workflow/v2.2`  
Project-impact verdict 建议: **NOT_APPLICABLE**  
End-to-end improvement: **NOT_MEASURED**

本 A2 不是新能力实验，而是修复 A1 的 evaluator-owned（评估者设计）测量矛盾。原 `REPORT.md`、A1 L2、四个机器产物、原 replay 全部保留；不重跑 Retrieval（检索）、不调用模型/API、不安装依赖、不修改产品代码。

## What A2 fixes

A1 把两种不同指标混在同一个 AC-02：

- historical any-Gold：只要 canonical Gold pages 中任意一页被命中，就算 reach；
- all-Gold-pages：该题所有 canonical Gold pages 全部到齐，才算 reach。

A2 已把两者明确分开，并由 `check_packet_a2.py` 独立从冻结原始页集合重新计算。

结果：

```text
historical any-Gold
baseline = 7/12
candidate = 9/12
recovered = 2
lost = 0

A2 all-Gold
baseline = 7/12
candidate = 7/12
recovered = 0
lost = 0
```

两个 historical any-Gold recovery（历史任一页恢复）实际是 partial multi-page coverage（多页 Gold 的部分覆盖）：

- `financebench_id_02024`：Gold=[63,94]，candidate 命中 94，仍缺 63；
- `financebench_id_03856`：Gold=[57,61]，candidate 命中 61，仍缺 57。

所以 H-60 原来的 7→9/+2 不被推翻，但它的准确含义是“**至少一个 Gold 页变得可达**”，不能再描述成“完整证据页都到齐”。

## Cost / candidate-volume clarification

A2 继续复用原 `CASE_FUNNEL.jsonl` 的同源 page-text 统计：

```text
pages:      60 → 104   (+44, +73.33%)
text chars: 250530 → 434584 (+184054, ≈+73.47%)
```

这些只代表候选页/文本量代理，不是 token、延迟或价格。

同时：

- any-Gold：+2；
- all-Gold：+0；
- fact sufficiency（事实充分性）：NOT_MEASURED；
- verifier execution（验证器执行）：0 observed；
- final answer correctness（最终答案正确性）：NOT_MEASURED。

因此不能再用“+2”暗示完整证据、事实充分或最终答案提升。

## Residual and downstream conclusion

A2 不改变 A1 已完成的残余和下游审计：

- H-60 历史 any-Gold miss 仍是 3；
- H-50 `BOTH_MISS` 仍是 18；
- 原 3+18 residual set（残余集合）不修改；
- A2 额外明确 2 条 partial multi-page cases（部分多页覆盖），但不把它们伪装成历史 miss；
- Retrieval direction 仍是 `NO_SINGLE_DIRECTION`；
- `VERIFIER_UNSUPPORTED` 仍是 `STATIC_INPUT_APPLICABILITY`；
- candidate claim availability = 0/12；
- observed verifier executions = 0；
- observed answer evaluations = 0。

所以本轮修复的是**测量解释**，不是主瓶颈关闭。

## Revised next-action decision

`DECISION_A2.json` 保持：

```text
retrieval_direction = NO_SINGLE_DIRECTION
next_action = DOWNSTREAM_CONTRACT_FIRST
historical_verdicts_unchanged = true
default_enable_authorized = false
product_readiness_proven = false
end_to_end_improvement = NOT_MEASURED
```

同时修正原 DECISION 的另一个过度泛化：不再把“>=3 recovered qids / >=2 families”机械继承给所有未来实验。

现在统一写成：

```text
future_thresholds_status = TO_BE_FROZEN_PER_EXPERIMENT
```

也就是每个未来实验在执行前分别冻结自己的：

- 样本/场景；
- 成功指标；
- 最小效果门槛；
- 成本护栏；
- 回归护栏；
- stop/failure condition（停止/失败条件）。

## Revised next-task readiness

`NEXT_TASK_DRAFT_A2.md` 仍只有一个 DRAFT_ONLY：

```text
DOWNSTREAM_CONTRACT_FIRST
Readiness = NOT_READY_FOR_REAL_EXECUTION
```

这里把两件事拆开：

1. **可以先做的**：零 API / 零模型的 consumer contract（消费契约）设计、字段定义、fail-closed 规则和 synthetic interface fixtures（模拟接口样例）。
2. **现在不能冒充已就绪的**：真实 H-60 case 上的 candidate assertion → verifier execution coverage，因为合法的 non-Gold candidate answer source 尚未核实。

模拟正例只能证明“接口接得通”，不能证明真实题能力。

## Workspace snapshot

- Initial `git status --short`: `M docs/evaluation/PROJECT_BOTTLENECK_MAP.md`；`M handoffs/evaluator_executor/state/CURRENT.md`。A2 evaluator 设计文件已存在于任务目录并由 `A2_REVIEW_SNAPSHOT.json` 冻结。
- Final tracked product status: 不修改 `src/**`、`config/**`、`tests/**`；不修改 H-60/H-50 历史包。
- Preserved artifacts: A1 `REPORT.md`、`DECISION.json`、`NEXT_TASK_DRAFT.md`、四个 machine outputs、`evidence/L2-GATE.json` 与 `evidence/replay2/**`。
- Saved diff: NOT_APPLICABLE；本轮没有 implementation diff。
- Diff SHA-256: NOT_APPLICABLE。

## Changed files

| File | Action | Factual change |
|---|---|---|
| `DECISION_A2.json` | created | 保留原方向，加入双口径解释和 `TO_BE_FROZEN_PER_EXPERIMENT` |
| `NEXT_TASK_DRAFT_A2.md` | created | 加入 `NOT_READY_FOR_REAL_EXECUTION`，拆分契约设计与真实执行测量 |
| `REPORT_A2.md` | created | 记录 A2 指标纠错、成本、就绪边界和新 L2 |
| `evidence/a2-l2/**` | to be created by frozen runner | A2 七项机械门禁证据 |

A2 evaluator-owned 的 `CONTRACT_A2.md`、`VALIDATION_PLAN_A2.yaml`、`check_packet_a2.py`、`AMENDMENT-02-METRIC-DEFINITION-CORRECTION.md`、`A2_REVIEW_SNAPSHOT.json` 均保持只读。

## L2 Task Gate

- Gate result: PASS
- Gate summary: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/L2-GATE.json
- Validation plan: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/VALIDATION_PLAN_A2.yaml
- Mandatory failures: 0

A2 VP-06 不重新运行 `run_analysis.py`；Executor 模式只验证原 A1 L2=PASS 且四个 machine outputs 与 `evidence/replay2/**` 逐字节一致。

## EV-01

- AC: AC-01
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-01.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-01.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-01.stderr.log

## EV-02

- AC: AC-02
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-02.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-02.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-02.stderr.log

## EV-03

- AC: AC-03
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-03.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-03.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-03.stderr.log

## EV-04

- AC: AC-04
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-04.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-04.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-04.stderr.log

## EV-05

- AC: AC-05
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-05.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-05.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-05.stderr.log

## EV-06

- AC: AC-06
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-06.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-06.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-06.stderr.log

## EV-07

- AC: AC-07
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-07.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-07.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-l2/EV-07.stderr.log

## Impact comparison

- Measurement evidence: `check_packet_a2.py --check funnel` + frozen `CASE_FUNNEL.jsonl` + `DECISION_A2.json`
- Before: A1 合同把 historical any-Gold 7→9/+2 与 all-Gold-pages 写成同一 AC-02 语义，导致机械 L2 可通过但语义矛盾。
- After: A2 明确 historical any-Gold=7→9/+2/0 loss；all-Gold=7→7/+0/0 loss；并固定两条 partial cases 及 60→104 页、250530→434584 字符。
- Delta: 产品能力变化 = NOT_APPLICABLE；测量定义由混淆变为双口径显式区分；H-60 历史 verdict 不变。
- Guardrail result: 0 API、0 model、0 dependency install、0 `src/config/tests` 修改；原四机器产物与 replay 保持字节一致。
- Scope caveat: any-Gold 只代表至少一个 Gold 页可达；all-Gold 只代表标注页完整到齐；二者都不能外推 fact sufficiency 或 answer correctness，后两者仍为 NOT_MEASURED。

## Deviations and unresolved items

- Contract deviation: 无。A2 正式修订 A1 矛盾，A1 原件和原 BLOCKED 报告均保留。
- Checks not run and reason: L3 未运行，由 Evaluator 独立执行；没有模型/API/Solver/Judge 调用，因为 A2 明确禁止。
- Known unresolved issue: 主瓶颈 B-03 未关闭；non-Gold candidate answer source 尚未核实，因此下一草案标记 `NOT_READY_FOR_REAL_EXECUTION`。
- Human or external dependency: 若未来真实生成候选答案需要模型/API，则必须新包显式授权；当前 A2 不需要外部依赖。
- Out-of-scope finding: 不把 B-06 直接升为第一瓶颈，不实施 Parser/Judge/Provider/Runtime 产品改造。

## Executor conclusion

A2 已把真正的问题修在**测量契约层**：

```text
historical any-Gold = 7→9 / +2
A2 all-Gold = 7→7 / +0
partial cases = 2
historical miss set = 3 (unchanged)
H-50 BOTH_MISS = 18 (unchanged)
next action = DOWNSTREAM_CONTRACT_FIRST
real execution readiness = NOT_READY
end-to-end improvement = NOT_MEASURED
Project-impact verdict suggestion = NOT_APPLICABLE
```

Executor 不修改 H-60 历史判决，不把 partial reach 冒充完整证据，不把 synthetic interface test 冒充真实题能力，也不 commit/push。
