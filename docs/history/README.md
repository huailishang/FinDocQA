# FinDocQA 能力演进摘要

这里仅保留对后续研发仍有价值的结论，不保存逐轮执行过程。

## 2026-07-25：从比赛工程收敛为通用项目

项目停止围绕单次榜单结果继续堆补丁，明确转向可复用的金融长文档 QA 工程。历史提交、逐轮任务、Provider 原始响应和运行 checkpoint 不进入正式文档。

## 2026-07-26：确定模块化方向

核心变化：

```text
原始 PDF / 已解析文档 / TXT / MD
        ↓
统一 Canonical Document
        ↓
Document Scope
        ↓
Evidence Retrieval
        ↓
Solver / Verification / Output
```

同时确定模块必须可以独立替换、独立评测；PDF 解析不再和后续检索、回答逻辑绑死。

## 2026-07-27：本地评测与检索主线形成

建立固定本地 Gold 和模块级评测，重点从“最终答案是否对”扩展为：

```text
找对文档
→ 找对页面/证据
→ 证据绑定正确
→ 求解正确
→ 输出合同正确
```

Document Scope 和 Canonical Retrieval 经过一轮系统修复；embedding / reranker 保持可插拔，不作为当前主线的硬依赖。

## 2026-07-28：进入 C3 确定性计算

C3 主线拆为：

```text
C3-A Deterministic Calculation
→ C3-B Formula Context Recovery
→ C3-C Semantic Variable Binder
→ C3-D CalculationSolver 接入
→ C3-E 独立局部评测
```

随后发现仅靠“发现一个反例、修一个反例”无法证明高风险模块可靠，因此新增统一可靠性测试方向：Specification / Invariant、Input-Space、Decision Table、组合测试、Property-Based、Metamorphic、Mutation 和 Regression Corpus。

## 2026-08-02：C3 外部基线与来源绑定能力阶段收口

C3 外部 Oracle 基线已从“能执行普通公式”扩展到两类来源绑定能力：

```text
C3-M：来源绑定数值序列聚合
SUM / AVERAGE / MINIMUM / MAXIMUM

C3-N：来源绑定表格谓词计数
严格大于 / 严格小于
```

C3-N 的关键结论不是比较和计数本身，而是建立了完整的来源边界验证：

```text
proof 内部自洽
≠
来源范围真实完整

必须再依据官方表格独立核对：
行列轴、区段标题、完整明细范围、Total 汇总边界
```

经过轴字段篡改、成员跨行跨列、范围与成员同步截短、遗漏中间成员、错误区段标题和 Total 边界等反例验证，正常 16 条请求保持不变。FinQA 与 TAT-QA 完整开发集 Oracle 结果为 1599 条可表示、1597 条正确、2 条错误、0 条 C3 执行异常，剩余 23 条不支持运算。

下一项 C3-O 已冻结为“来源绑定表格区段或整表实体行成员计数”，只回答完整成员数量，不做数值比较、文本枚举、缺失值判断或复合计数。截至本次整理尚未开始实现；继续工作时仍以本地 `handoffs/evaluator_executor/state/CURRENT.md` 为唯一入口。

## 当前原则

- docs 只保存稳定架构、接口、评测方法、长期参考和结论级历史。
- 当前执行任务只看 `handoffs/evaluator_executor/state/CURRENT.md`。
- 历史 bug 可以进入 Regression Corpus，但历史任务文件不继续堆在正式 docs。
- 不在公开项目材料中记录排行榜分数、名次或真实提交明细。
