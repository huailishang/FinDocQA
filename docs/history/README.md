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

## 当前原则

- docs 只保存稳定架构、接口、评测方法、长期参考和结论级历史。
- 当前执行任务只看 `handoffs/evaluator_executor/state/CURRENT.md`。
- 历史 bug 可以进入 Regression Corpus，但历史任务文件不继续堆在正式 docs。
- 不在公开项目材料中记录排行榜分数、名次或真实提交明细。
