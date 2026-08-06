# FinDocQA 核心瓶颈重新梳理

日期：`2026-08-06`

## 结论

当前第一瓶颈不再是：

```text
15 个 DOCUMENT_MISS
+ 5 个 TABLE_SOURCE_MISS
+ 2 个 MEMBER_RANGE_INCOMPLETE
```

这些是当前 54 题、46 文档闭集里的 E2 局部损失，继续逐题拆分或调参，已经不能证明 FinDocQA 更接近“可用的金融文档问答系统”。

当前第一瓶颈是：

```text
缺少可信、冻结、可重复运行的 E4 端到端 Gold 基线
```

更具体地说：

```text
已有 100 道五领域问题
+ 已有 30 道私有 Gold 种子候选
+ 已有 E4 指标与 Answer A/B 代码

但是：

没有逐题冻结的 Gold manifest
没有完整的文档 / 页码 / 证据 / Claim / 公式记录
没有两轮复核状态
没有 Gold-Core / Shadow 的正式划分
没有当前产品全链端到端基线结果
```

## 为什么这是第一瓶颈

当前已经有不少模块级证据：

```text
E1 文档供给
→ 190 文档、6071 张已加载表、77525 行

E2 结构化表格检索
→ 54 题闭集、Document / Table / Coordinate / Binding 指标

E3 确定性计算
→ FinQA / TAT-QA Oracle-program 大规模基线
```

但这些都不能回答：

```text
用户直接问一个自然语言问题
→ 系统是否找到正确文档和证据
→ 是否得到正确答案
→ 是否引用正确来源
→ 错误答案是否被错误放行
→ 正确答案是否被错误阻断
→ 成本和稳定性是否可接受
```

因此项目现在存在“模块指标越来越细，但最终产品好坏仍不可判定”的问题。

## 已有可用基础

仓库已经具备启动 E4 的材料，不需要重新从零设计：

1. `data/raw_dataset/questions/group_a/`：五个领域各 20 题，共 100 题。
2. `evaluation_artifacts/private_history/legacy_afac/LOCAL_GOLD_SEED_CANDIDATES_20260726.md`：30 道私有种子候选，领域分布较均衡。
3. `evaluation_artifacts/private_history/legacy_afac/LOCAL_GOLD_B_SEED_CANDIDATES_20260726.md`：补充确定性计算和逐项证据候选。
4. `docs/evaluation/local-benchmark.md`：已经定义 Gold-Core、Shadow、证据闭环和分层指标。
5. `src/evaluation/layers/answer_quality.py`、`scripts/evaluate_answer_ab.py`：已有 E4 指标和 A/B 执行框架。

缺的不是更多设计，而是把候选题真正变成可运行、可审计的 Gold 资产。

## 暂停项

以下工作先暂停，不再作为当前主线：

```text
22-case 统一残余损失账本
H-08 specificity 继续调参
H-09 页面合并 / 来源分组继续细修
剩余 2 个 range-expansion 小样本优化
按单个失败案例继续新增规则
```

它们可以作为后续 Failure-Regression，但不能继续决定项目主方向。

Knowhere / LLM Wiki 也先保留在规划层。只有 E4 证明“跨文档导航或知识组织”是主要失败层，才进入对应实验。

## 新的主线

```text
30 道私有 Gold 候选
→ 自动生成逐题证据包
→ Evaluator 独立复核
→ 冻结 Gold-Core / Holdout-Shadow
→ 运行当前系统 E4 基线
→ 按最终答案、证据、门禁和成本定位第一真实瓶颈
→ 再决定修 Parser、Retrieval、Solver、Verifier 还是 Recovery
```

## 第一阶段完成标准

不是要求 30 题一次全部变成 Gold，而是：

```text
30/30 都有机器可读候选记录
30/30 都有证据定位结果或明确缺口
每题都有答案、文档、页码、证据、Claim / 公式和复核状态
不得把历史答案直接当 Gold
不得因凑数量把争议题强行放入 Gold
最终形成可运行的 Gold-Core 和 Shadow manifest
```

只有完成这一步，后续“项目有没有变好”才有统一答案。
