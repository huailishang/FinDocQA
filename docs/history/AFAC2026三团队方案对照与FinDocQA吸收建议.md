# AFAC2026 金融长文本赛题：三团队方案对照与 FinDocQA 吸收建议

> 整理日期：2026-10-09
> 来源边界：AAADogs、Lucky、过目不忘三份获奖团队方案介绍由用户提供；文中机制属于团队自述，未对三套完整代码、数据与实验做独立复现。
> 定位：结论级历史参考，不是当前 Executor 合同、Product 默认配置或自动改造授权。

## 0. 一句话结论

三个方案互补，而非三套可直接照搬的框架：

- **AAADogs：失败模式 → 通用代码改进 → 定向/全量回归 → 冻结运行。**
- **Lucky：显式 Requirement → 绑定 Evidence ID → 维护覆盖状态 → 定向补证 → 保护性压缩。**
- **过目不忘：最小两级检索与按选项预算 → 模型单次复核 → 缺证时才扩张；失败驱动小步演进，反对无证据堆复杂 Harness。**

FinDocQA 应将三者吸收为：**受来源约束的证据供给、可检查的需求覆盖、最小充分工作记忆、独立可靠性门禁**。不改变 Enterprise Knowledge / Evidence 能力提供者的定位，也不接管 Control Plane 的通用 Runtime/Harness。

## 1. 分方案提炼

| 维度 | AAADogs | Lucky | 过目不忘 |
| --- | --- | --- | --- |
| 主问题 | 如何让工程补丁跨题复用且可复现 | 如何知道每项判断需要哪些证据、缺什么 | 如何控制检索/推理/复核复杂度和 Token |
| 检索 | 身份规则、中文 BM25、查询/选项分路、词项权重、来源平衡 | 两级稀疏检索，缺口驱动补搜 | 文档级→片段级多路稀疏检索 |
| Memory | 长期证据原文保持不变；运行工作窗口按题压缩 | Requirement—Evidence 关联，ready/partial/missing 状态 | 按题型/难度/选项分配动态预算 |
| 证据压缩 | 核心命中保护、来源/选项保底、条件不闭合时回退 | 计算输入/门槛证据保护；抽取式保留原句、表头、单位、短 Evidence ID | 为每个选项保底，按需扩大证据范围 |
| 求解/校验 | 确定性合并/计算与 Qwen 语义分工，同流水线审计 | 类型化代码、Decimal 与 Qwen 分工；结构化引用及一致性校验 | Qwen 在同次调用内作答、自检，代码格式检查 |
| 演化 | Code Agent 研发期改通用模块；冻结后只执行 | 从反馈归纳经验，形成规则/公式候选并验收回滚 | Codex 错误归因、ACE 经验、OpenEvolve 候选代码、Humanize 独立 review，单变量验收 |

### 1.1 AAADogs：吸收研发机制，不抄赛事权重

值得吸收：
1. **失败根因必须落到可复用模块**：路由、检索、证据预算、来源绑定、计算、回退、审计之一。
2. **固定长期来源与动态工作视图分离**：压缩只能影响当前题可见的证据窗口，不可改写 Canonical 原文/页码/事实权威。
3. **一次改一个因素、定向回归后全量/独立回归、冻结与复跑**。
4. **Prompt/模型调用/输出/来源/Token 统一 trace**，不允许仅用答对率代替审计。

不直接吸收：BM25 的单字双字和数值权重、与数据集强相关的词表；应以现有 Canonical 检索合同做公平 A/B，防止 Gold 泄漏和 qid 特例。

### 1.2 Lucky：吸收 Requirement—Evidence 状态机

最具增量价值的是把 **Requirement 作为可检查的数据对象**。建议未来的影子实验至少区分：
- requirement_id、claim_or_option_id、subject、metric、period、unit、comparison/condition；
- evidence_ids（仅是候选引用，必须可回到原始文档/页/表格/单元格）；
- coverage = ready / partial / missing，另记录 conflict/ambiguous、原因码、缺口与检查依据；
- 每轮补搜前后的覆盖变化、补搜目标、消耗预算和终止原因。

压缩步骤应是 **保护 → 去重 → 抽取 → 装填**，而不是“先摘要再判断”。表格至少保护表头、行列轴、单位、统计口径和引用页；否定/例外/跨页/不同年度作为明确完整性约束。

风险：状态 ready 不能由检索相关性分数直接产生，必须由来源绑定/条件闭合的 checker 证明；否则只是换了名字的幻觉。

### 1.3 过目不忘：吸收最小复杂度与单变量演化

值得吸收：
1. 文档级先限域，域内按题干和选项分路找证据；给多选题的每个选项保留机会。
2. **不因为可用就增加 Agent/模型调用**；简单机制与高复杂度机制须同基线比较答案、证据、Token、延迟和失败率。
3. Codex/ACE/OpenEvolve/Humanize 提供启发：错误分类 → 经验卡片 → 候选单模块 patch → 独立验证 → 无增益回滚。FinDocQA 已有 Evaluator/Executor，不再并行搭第二套 Harness。
4. 同次自检可作便宜的启发式过滤，但**不等于独立验证**；涉及数值、原始来源和最终 Claim 时仍由代码 Checker/独立评测裁决。

开源参考（只看思想和接口，当前不安装、不引入依赖）：
- ACE: https://github.com/ace-agent/ace
- OpenEvolve: https://github.com/algorithmicsuperintelligence/openevolve
- Humanize: https://github.com/PolyArch/humanize

## 2. 与 FinDocQA 当前能力对照（核对日期 2026-10-09）

已存在的可复用底座：
- **原文与血缘**：Canonical Document、PDF/MinerU 页面结构、表格与 lineage。
- **检索**：Canonical lexical、QueryPlan、可插拔 embedding/结构信号；**中文财报页级排序仍是实测首要瓶颈**。
- **工作记忆**：src/evidence/prompt_evidence_selection.py 中的全局选证/文档保底；src/evidence/minimal_sufficient_set.py 的 shadow-only 证据缩减；src/evidence/bounded_workspace_admission.py 的限页准入（工作区尚有未提交改动，不把工作区文件出现等同于正式验收/发布）。
- **计算/验证**：Claim Fact Binding、来源绑定数值计算、SUPPORT/REFUTE/UNRESOLVED 及失败关闭路径。
- **工程闭环**：冻结任务契约、Executor L2、Evaluator 独立 L3、Gold/回归/历史反例、审计指标。

仍需证据，而不是先写“已实现”：
- Requirement 的**逐 Claim/选项显式覆盖状态**能否真正在来源事实校验后可靠闭合；
- 关键页/跨页证据是否在检索排序与工作区准入两层被错误丢弃；
- 按缺口补搜能否实际把 partial/missing 变为有来源依据的 ready，且不会循环或误闭合；
- 文本压缩是否减少真实 Token，同时保留所有必需条件、表格列身份和来源；
- 真实自然语言自由问答的端到端准确率、成本与失败关闭表现。

## 3. 三团队共同的工程原则

**证据可信度 > 压缩率；可复现收益 > 新机制数量；最早首损 > 下游盲目修补。**

通用目标链：
1. 源文件及来源权威；
2. Document Scope 与 page/section/table 召回；
3. Evidence Workspace 准入及 Requirement 覆盖；
4. Claim/Fact Binding 和确定性计算；
5. 答案与引用校验；
6. 缺口驱动有界恢复；
7. 可复现 trace 与独立评估。

固定长期证据是事实权威；query-time 的 Evidence Workspace 才是动态压缩对象。任何 Compiled Knowledge 或工作视图不能替代最终来源。

## 4. 下一阶段：按依赖排序的四步候选工作

### P0：先执行已冻结的 H-79，不改变其输入与验收

当前 CURRENT.md 仍是 FDQA-CN-CANONICAL-PAGE-RANKING-EXPERIMENT-V1、H-79、CONTRACT_FROZEN，首损 B-CN-01。固定七题页级 Top5 any-Gold 为 3/7，是开发诊断，不是生产准确率。H-79 只测已有术语窗口的 IDF unique-term coverage shadow scorer。

- H-79 是否改善页级排序，交由 L2/L3 裁决；参考方案不构成更改其公式、边界、Gold 或发布授权。
- H-79 若失败，按照同一独立评估程序定位误召回/误排序，再决定新的单变量候选。**不直接引入 Lucky 工作区或 BM25 组合掩盖前级首损。**

### P1：Evidence Requirement Coverage Probe（条件成立后由 Evaluator 冻结）

当召回与工作区准入拥有足够的可信样本时，以现有 evidence/verification seam 做小范围离线 shadow：
- A 组：现有 GlobalPromptEvidenceSelector / workspace；
- B 组：保留同一 Retriever 候选，先构造 Requirement，检查覆盖及来源，再执行保护性选证/去重。
- 变量单一：**是否加入显式需求覆盖约束**；不得同时换检索器、embedding、parser、模型、数据集或 Gold。
- 必须报告每个 Requirement 的源证据绑定、选项覆盖、首损、冲突、不确定性、压缩前后上下文大小和 Token（估算与真实分开）。
- 晋级的必要门槛：不牺牲既有已闭合证据/来源血缘，不把 partial/missing 误报 ready，在独立保留切片上存在可归因覆盖改善；成本收益另行判断。

### P2：Gap-Directed Retrieval + Extractive Memory（P1 有正证据后再做）

只对 missing/partial 的确定原因补搜，记录 query/source/page 变化和剩余预算。对一份已被证明可用的候选源，压缩仅保留需要的原句、必要相邻上下文、表格表头/行列/单位和 Evidence ID。

- 补搜最多次数和 Token/latency 预算由未来 Evaluator 冻结；超过上限 fail closed。
- 与静态固定压缩作 A/B：Required Evidence Recall、Claim Binding、错误闭合、恢复成功率、实际 Token、延迟、确定性重复运行和原始来源回溯。
- Qwen 未注册公式的兜底计算必须标注非确定性结果，**不得自动获得验证通过身份**。
- 当前不搭独立 runtime、复杂多 Agent、自动联机自修改；对外只规划证据服务能力。

### P3：失败经验与候选补丁自动化（后置研究）

按 AAADogs/过目不忘的共同思想，复用现有 Evaluator/Executor/Harness：
- 存 **Failure Family**：哪个 stage 的何种可复现缺口；
- 存 **Experience Card**：成功/失败边界、适用域、关联 Gold/非 Gold 证据；
- 生成单一候选代码改动，独立回归与 Holdout、回滚；
- 只在具备真实失败轨迹、明确可推广假设和独立验收后考虑 ACE/OpenEvolve 类自动探索。

**不要把三套开源框架嵌套成“三层自进化系统”；先证明现有流程的单变量修复有可靠收益。**

## 5. 不吸收与风险项

| 候选 | 处理 | 理由 |
| --- | --- | --- |
| Requirement→Evidence 显式绑定 | ABSORB_AS_PROBE | 与当前 Claim Binding 和 Evidence Workspace 有直接衔接 |
| 关键计算数值/表格/否定/例外保护 | ABSORB_AS_PROBE | 需检验无必需证据损失 |
| AAADogs 研发冻结/跨题回归 | ABSORB_INTO_EXISTING_PROCESS | 已有 Evaluator/Executor，可补过程指标 |
| 按选项预算保底 | REFERENCE_ONLY_PENDING_AB | 当前已有文档保底，按选项保底要证明收益 |
| 特定 BM25 加权、词表 | REFERENCE_ONLY | 不能复制比赛域权重，需独立验证 |
| 单次 Qwen 自我复核 | REFERENCE_ONLY | 不可替代独立 Checker |
| ACE/OpenEvolve/Humanize 整套引入 | DEFER | 现有 Harness 已有相似职责，复杂度与依赖成本暂不成立 |
| 没有证据就新增多 Agent/大 Context | REJECT | 增加成本、注意力干扰和归因复杂度 |
| qid 答案特判、Gold 进入在线排序 | REJECT | 违反通用性、独立回归和数据泄漏防线 |

## 6. 文档与任务边界

- 当前任务唯一入口：handoffs/evaluator_executor/state/CURRENT.md。
- H-79 CONTRACT.md / VALIDATION_PLAN.yaml 优先于本文的后续实验设想。
- 本文不代表 H-79 已完成，不要求现在改 Product，也不授权安装依赖、调用付费模型、提交或推送。
- 下一轮如果需要落实，由 Evaluator 在 P0 结束后先确认首损和独立数据，再决定是 P1 还是另一项单变量 Retrieval 修复。
- 原始来源及项目身份不泄露到公共代码：不复制参赛 PDF、提交答题明细或未许可数据。

## 7. 资料出处与验证边界

- AAADogs、Lucky、过目不忘三团队方案：2026-10-09 用户提供的 AFAC 获奖方案全文；属于团队自述，而非已经通过我方 L3 的证据。
- 中国人民大学新闻对“过目不忘”方案路线的公开介绍：https://se-office.ruc.edu.cn/xwdt/ab008b896d264ab1937246f21a685a93.htm
- ACE 项目仓库：https://github.com/ace-agent/ace
- OpenEvolve 项目仓库：https://github.com/algorithmicsuperintelligence/openevolve
- Humanize 项目仓库：https://github.com/PolyArch/humanize
- 项目内部现状依据：AGENTS.md、README.md、docs/architecture.md、docs/evaluation/PROJECT_BOTTLENECK_MAP.md、handoffs/evaluator_executor/state/CURRENT.md 及上述 src/evidence 代码快照。

后续若获得团队源码/复现材料，需单独验收源码许可证、运行成本、数据可用性与复现声明；文章中的指标不可直接迁移为 FinDocQA 的验收指标。
