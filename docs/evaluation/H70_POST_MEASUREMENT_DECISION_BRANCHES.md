# H-70 评估后双路线决策规则

用途：把 H-70 完成后的两条候选路线显式记录下来，让 Evaluator 根据 Product 实测结果选择，而不是提前承诺某个实现。本文件不修改 H-70 的 CONTRACT、VALIDATION_PLAN、验收条件或执行范围。

## 决策点

H-69 已正式验收为可复用的 bounded evidence admission 能力。H-70 需要先把 H-69 实际选出的页面继续跑完整漏斗：

`evidence → AST → binding → calculation → verdict`

只有 H-70 L2/L3 确认新的 Product first-loss 后，才决定下一能力任务。

## 路线 A：Admission 已够用，红点继续下移

选择条件：H-70 显示 admitted workspace 基本保留了可用证据，主要且可复现的首损已经移动到 AST、binding、calculation 或 verdict。

后续动作：

- 保留 H-69 bounded admission，不再继续调 admission；
- 后续在单独 integration package 获授权后，把已验收 admission 正式接入正常 Product 主链；
- 针对最早的真实下游失败阶段开启下一单变量任务；
- 没有新证据前，不增加 RRF、rerank、embedding、diversity heuristic 等 evidence-selection 复杂度。

含义：对当前测量边界，H-69 已经足够解决 workspace admission 问题，下一份研发预算应该投向真实测出来的下游瓶颈。

## 路线 B：Workspace 合法，但 10 页选择丢失所需证据

选择条件：至少两个独立 DEV_SEED 问题出现可复现的 evidence-stage loss，并且审计能证明：需要/可用的证据原本存在于冻结的 H-68 Retriever candidate set 中，但被 H-69 的 <=10 页 admission 排除。

后续动作：

- 保持 H-69 的 10 页合同和 fail-closed 语义；
- 不先修 AST / binding / judge，而是开启 Evidence Selection V2；
- V2 先按低复杂度、可审计原则验证现有信号：
  1. 对有证据支持的 focused / financial-target page 提供保留机制；
  2. 保持 required-document coverage，并改善跨文档 balance / diversity；
  3. 现有 Retriever score / order 作为后续填充信号；
  4. 继续保留 PageKey lineage、provenance 和明确 selection reason；
- 只有简单、确定性的 V2 仍出现实测 evidence-preservation failure 时，再考虑 RRF、semantic fusion、rerank 等更重机制。

含义：这时问题已经不是“能否构造合法 workspace”，而是“固定上下文预算下是否保留了对的证据”。

## 不强行二选一

如果 H-70 结果混合，或当前 3 个 DEV_SEED 样本不足以支持路线 A/B 中任意一条，Evaluator 应记录 `INSUFFICIENT_COVERAGE` 并先扩充测量，不为了推进任务而强行选择。

## 边界

- 不改变 H-70 的测量语义。
- 不隐式恢复暂停中的 H-67。
- H-68 Oracle 页面只能作为比较上界，不能作为 Product evidence。
- 3 个 DEV_SEED 的结论不能外推整体中文准确率。
- WeKnora 等外部项目中的 RRF / rerank / semantic retrieval 只进入候选机制池；内部测量未证明需要前，不直接吸收进产品。
