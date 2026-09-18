# Evaluator Review｜H-61 A2正式收口

Task ID: FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1
Workflow: evaluator-executor-workflow/v2.2
Task kind: evaluator_design
Baseline HEAD: 849ff3bda52481c7ecb999d5967a851910624265
Contract: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/CONTRACT_A2.md
Reviewed report: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/REPORT_A2.md

## 评估前检查

全链路为解析/来源 → 检索 → 工作区 → 断言/绑定/计算/验证 → 答案。解析、部分确定性计算、工作区范围路由已有历史验证；B-03仍是已测主损失，B-06评分、B-05复杂表格、B-07输出可靠性仍为次级/观察项。H-61位于检索与消费的交界，目标是收口诊断和澄清测量，不是新增能力。

接受未改动的A2提交，原A1合同/检查器/报告/机器产物/重放保持。提交前工作流验证器OK。A1指标矛盾属于评估者设计错误；执行者停在BLOCKED正确，A2已消除此语义阻断。随后按READY_FOR_REVIEW / Evaluator运行独立L3，无API或依赖安装。

## L3 Independent Gate

- Gate result: PASS
- Gate summary: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/L3-GATE.json
- Validation plan: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/VALIDATION_PLAN_A2.yaml

7/7 PASS，mandatory failures=0。RV-EV-06在独立目录重放原分析，四个机器产物逐字节一致，未覆盖执行者产物。复核A2新决策/草案的语义修订，而非把旧DECISION的重放当成A2全部正确的证明。

## Acceptance matrix

| AC | 裁决 | 独立依据 |
|---|---|---|
| AC-01 | 通过 | 原输入、设计文件及A2快照hash一致；历史判决保持。 |
| AC-02 | 通过 | 原排名独立复算any-Gold 7→9、all-Gold 7→7；两条部分覆盖、页数60→104、字符250530→434584全部一致。 |
| AC-03 | 通过 | 残余精确3+18条，按qid保持独立身份；机制冻结为null。H-43/H-53反证得到讨论，没有新增参数搜索。无候选结论仅限本轮证据，不声称不存在其他通用方法。 |
| AC-04 | 通过 | H-60脚本直接生成静态不支持声明；parse_financial_claim需要断言文本，绑定/充分性需要事实及来源；产品路由确有workspace_scope传播。没有把静态审计算动态失败。 |
| AC-05 | 通过 | DECISION_A2采用逐实验冻结门槛；唯一草案拆分零调用设计与真实测量，后者NOT_READY_FOR_REAL_EXECUTION。部署三档明确非等效、非已实现。 |
| AC-06 | 通过 | 新鲜独立重放一致；runner仅本地JSON/文本读取写出，无网络/模型/子进程调用；src/config/tests无改动。 |
| AC-07 | 通过 | 报告区分历史测量、静态审计和未测结果；A2 L2及独立L3均7/7，提交角色正确。 |

## Independent semantic review

- 原H-60 reach为集合交集，不是所有标注页到齐。02024候选到94缺63；03856到61缺57。+2只证明部分标注页可达改善，不能推断事实充分；all-Gold到齐也不等价于答案正确。
- 候选字符量增加约73.47%，完整标注页覆盖没有增加。这约束继续扩页的投入理由，但没有实测token、延迟或价格，不能据此计算真实成本收益。
- 12个VERIFIER_UNSUPPORTED没有触发实际验证器；0个trusted loss在支持样本为0时没有保护性证据。
- 下游审计通过代码核对：financial_claim_ast.py的parse_financial_claim面向选项断言，financial_report_claims.py遍历question.options；evidence_sufficiency.py依赖已绑定事实、公式与来源。工作区范围传播已有接口及历史证据，本轮不应重开scope修复。
- 机制为null，没有运行时trigger/action需要Gold扰动验证。残余分类只作描述，不被晋级为已证实根因或恢复策略；来源文档相同/历史重复不得算新独立泛化样本。
- 下一草案目前只可作为设计方向。现有解析器含中文金融语义和选项结构假设，不能称为通用英文自由问答验证器。下一合同必须限定语言、断言类型、字段及拒绝范围；可以使用带SIMULATED_INTERFACE_ONLY标记的模拟数据测接口，不能用其证明真实题收益。此项是未来任务约束，不是本诊断新增阻断。
- REPORT_A2变更表中的“evidence/a2-l2待生成”是非阻断性旧措辞；实际文件及L2已独立核验。不为此再开修复轮或改写执行者报告。

## Project impact verdict

Impact verdict: NOT_APPLICABLE

这是设计/诊断任务。新增的是可信的双口径解释、部分覆盖与候选量代价、静态消费输入边界，不是新增答案能力。B-03没有关闭，B-06也未被宣布成为第一瓶颈。

## Continuation

Continuation: SWITCH

下一方向为DOWNSTREAM_CONTRACT_FIRST，下一草案路径：
handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/NEXT_TASK_DRAFT_A2.md

Proposed next task ID: FDQA-FREEFORM-CANDIDATE-ASSERTION-CONSUMER-CONTRACT-V1
Next task state: DRAFT_ONLY / NOT_READY_FOR_REAL_EXECUTION

本次只接受方向建议，不自动运行新任务、模型或产品实现。下一包应先冻结最小消费契约与支持范围，核查真实非Gold候选来源，再决定是否值得授权测量；不继续凑检索第三题，不扩Judge来替代前置输入缺口。

L3在地图r86冻结身份下完成，原地图另存evidence/a2-review/MAP_R86_SNAPSHOT.md。正式收口后地图更新为r87是治理变更，不改变本次输入。以后复现H-61应在隔离的r86快照环境使用该地图版本，不能以修改冻结manifest消除live map版本差异。

## RV-EV-01

- AC: AC-01
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-01.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-01.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-01.stderr.log

## RV-EV-02

- AC: AC-02
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-02.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-02.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-02.stderr.log

## RV-EV-03

- AC: AC-03
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-03.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-03.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-03.stderr.log

## RV-EV-04

- AC: AC-04
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-04.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-04.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-04.stderr.log

## RV-EV-05

- AC: AC-05
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-05.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-05.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-05.stderr.log

## RV-EV-06

- AC: AC-06
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-06.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-06.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-06.stderr.log

## RV-EV-07

- AC: AC-07
- Meta: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-07.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-07.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/evidence/a2-review/RV-EV-07.stderr.log

## Final verdict

PASS
