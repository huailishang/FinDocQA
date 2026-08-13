# 文档导航

FinDocQA 的正式文档只保留四类：项目架构、模块接口、评测规范、长期参考。阶段性执行清单和“明日继续”类工作台文档不进入这里。

## 1. 项目与架构

- `project-overview-cn.md`：项目目标、主要能力和当前定位。
- `architecture.md`：整体问答链路和关键工程原则。
- `modular-architecture.md`：文档生产链与 QA 消费链的模块化架构。
- `module-interfaces.md`：模块输入输出、公共合同和可替换边界。

## 2. 当前 C3 确定性计算主线

- `C3确定性计算与材料公式实现方案_20260728.md`：C3-A/C3-B 的计算、公式证据与恢复方案。
- `模块可靠性测试框架与C3B落地计划_20260728.md`：C3-B 收口后采用的 Invariant、Decision Table、组合测试、Property-Based、Metamorphic、Mutation 与 Reliability Gate 方法。

当前执行状态不写入 docs；以本地 `handoffs/evaluator_executor/state/CURRENT.md` 为唯一执行入口。

## 3. 评测与可靠性

见 `evaluation/`：

- `reliability-architecture.md`：Evaluation / Reliability 通用横切架构、公共合同、ReliabilityProfile / Gate 与现有 `src/evaluation/` 迁移方向。
- `local-benchmark.md`：本地固定 Gold / 模块评测方法。
- `external-benchmarks.md`：外部评测集与可借鉴方案。

## 4. 长期参考

见 `reference/`：

- `对比计划.md`：长期能力地图与差距分析。
- `提升教程-进阶优化.md`：金融长文档问答进阶方法总结。
- `Knowhere与LLM-Wiki借鉴分析.md`：Document Memory 与知识编译层的边界、风险和后续最小实验。
- `KDDCup2026冠军方案对FinDocQA吸收分析.md`：将冠军 Data Agent Runtime 与 Knowhere 文档导航结合，整理 Soft Evidence Workspace、Explore Before Solve、受限工具面等可吸收机制及验证优先级。

## 5. 历史

`history/README.md` 只保留结论级能力演进摘要，不保留逐轮执行包、Provider 原始响应、排行榜细节、运行 checkpoint 或真实提交文件。
