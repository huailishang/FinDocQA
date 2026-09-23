# FinDocQA Project Bottleneck Map

Map revision: `2026-09-22-r98`

Last reviewed: `2026-09-22`

Map owner: Evaluator

Status: `ACTIVE`

## Latest review — H-72 (2026-09-22)

H-72 为 REJECTED / NOT_APPLICABLE / CONTINUE。正式独立 L3 2/2 PASS，两份完整诊断输出 SHA-256 均为 `a62676507e349db977851aa9245fa685681d86caf02849009d1290634c670f54`。上轮 kg 单位反例、输出保全和预算问题已关闭；但额外零构造器调用反例证明 null/NaN 分子仍被当作完整分量，同身份 100/200 冲突会任取首组合，AC-01 未满足。

第一产品瓶颈 B-CN-01 仍为证据首损可信归因；分类仍为 NO_SINGLE_PAGE_WITNESS=1 / UNKNOWN=4，零排除正见证不能证明准入充分。B-CN-02 覆盖不足不变。下一包 H-73 `FDQA-CN-COMPONENT-VALUE-CONFLICT-REPAIR-V1` 只修诊断数值有效性与冲突失败关闭，使用 H-72 已独立复现的完整快照重放归因，零新证据构造。H-67 仍暂停。本节覆盖下方 H-71 节中的下一任务指向。

## Latest review — H-71 (2026-09-22)

H-71 复核为 REJECTED / NOT_APPLICABLE / CONTINUE。只读独立审计确认冻结输入、93 页留存记录与 12 个基线阶段状态自洽，提交分类为 NO_SINGLE_PAGE_WITNESS=1 / UNKNOWN=4；但将真实研发费用单位从 CNY 改为 kg 后，诊断器仍接受为匹配分量，未满足单位失败关闭。另有两次非环境归因修订重跑，四次完整预算已耗尽，正式 L3 未执行；不能把结构验证和 L2 PASS 当最终验收。

第一瓶颈 B-CN-01 不改判：H-70 的五个证据首损仍缺可信归因。当前无路线 A/B 新结论；B-CN-02 独立中文覆盖不足保持。下一包 H-72 `FDQA-CN-RETENTION-AUDIT-VALIDITY-REPAIR-V1` 只修任务内诊断有效性和独立输出保全，Executor L2 / Evaluator L3 各预留一次完整测量。H-71 所有原提交数据保留，H-67 继续暂停。本节覆盖下方 H-70 节中的“下一任务 H-71”。

## Latest review — H-70 (2026-09-22)

H-70 已独立复核 PASS / NOT_APPLICABLE / CONTINUE。冻结 L3 为 1/1 mandatory PASS：3/3 工作区各 10 页，12 个选项首损为 evidence=5 / binding=5 / NONE=2。L2 为 GBK、L3 为 UTF-8，正确解码后 JSON 完全相同；不能声称原始日志字节一致。额外核验 25 个结构化文档文件哈希无漂移，继续使用 pre-H67 隔离源码。

B-CN-01 从“准入后首损未测”推进为“证据首损已测、但根因未分清”：三个问题均有证据失败，五个绑定失败是后续已观察瓶颈。测量通过不等于产品改进，也不等于两个选项答案已通过 Gold 正确性验证。B-CN-02 独立中文覆盖不足保持不变，holdout=0。

路线裁决为 INSUFFICIENT_COVERAGE：既未证明准入基本保留充分证据，也未证明至少两个独立问题的相关可用证据在候选中存在却被准入排除。下一任务 H-71 `FDQA-CN-CANDIDATE-EVIDENCE-RETENTION-AUDIT-V1` 对五个证据失败作候选页/准入页来源事实审计；不修产品，不恢复 H-67，不把单页诊断算成产品表现。H-70 正式结论见其 REVIEW.md。本节覆盖下方 r95 历史状态中的“尚未正式测量”。

## Current priority — Chinese financial documents (2026-09-21)

用户明确暂缓英文评测，回到中文金融文档。H-66英文兼容性与H-67外币绑定均PAUSED_BY_USER；H67未验收实现保留/归档，不继续正式验收、不自动作为中文基线。H65既有接口验收结论保留。

第一瓶颈B-CN-01已再次前移：H-69 正式 L2/L3 均 3/3 mandatory PASS，同一 H-68 Retriever 输入仍为 fin_a_004/014/020 = 24/38/31 个唯一页，但 bounded evidence admission（受限证据准入）后 3/3 均可构造合法 <=10 页 EvidenceWorkspaceScope，required-document coverage 为 2/2，四类 fail-closed 控制全部拒绝，Gold/model/API/network 使用均为0。workspace admission 不再是这 3 个 DEV_SEED 的最早阻断点。

当前 B-CN-01 红点移动到“admitted workspace 之后的真实下游首损尚未正式测量”：需要在隔离的 pre-H67 已验收源码上，把 H-69 选出的真实 10 页继续跑 evidence → AST → binding → calculation → verdict 漏斗，再决定 evidence supply 与 binding 哪一层是下一能力瓶颈。不得直接使用当前脏工作区 H-67 binder。

次级B-CN-02保持：独立中文验证覆盖不足。现有本地Gold9题，其中财报3题(fin_a_014/020/004)、12选项、5文档；全部DEV_SEED、holdout=0。任何 H-69/H-70 结论都不得外推整体中文表现。H-68 Oracle 仅作为比较上界，不并入产品准确率。

H-69最终复核：PASS / project impact IMPROVED / CONTINUE。Evaluator L3 重新执行 frozen plan 后 3/3 mandatory PASS，RV-EV-01 与 Executor EV-01 stdout SHA-256 完全一致；受保护 Retriever/workspace/evidence-builder 哈希不变。下一包 H-70 为 evaluator_design，只测 admitted Product workspace 的下游漏斗，不做产品修复。

H-70 评估后的下一步采用显式双路线分流，不提前选择实现：若 admitted workspace 已基本保留可用证据、首损明确下移到 AST / binding / calculation / verdict，则保留 H-69 不再调 admission，后续先完成主链接入并修最早的真实下游瓶颈；若至少两个独立 DEV_SEED 问题可证明“关键/可用证据存在于 H-68 Retriever 候选中、但被 H-69 <=10 页选择排除”，则进入 Evidence Selection V2，先验证 focused/financial-target page 保留、跨文档 coverage/diversity 与现有排序信号，再视测量结果决定是否需要 RRF / semantic fusion / rerank。若证据不足则不强行二选一，先补测量。详细判据见 `docs/evaluation/H70_POST_MEASUREMENT_DECISION_BRANCHES.md`。

## Current closure update — H-65

H-65 已独立验收 PASS / IMPROVED / CONTINUE。七项检查7/7、固定12封装逐字段重放12/12、21字段闭合、原四反例4/4拒绝、异常页码矩阵22/22拒绝，相关回归53 passed / 27 subtests passed。两轮发现的dry-run遗漏、有损页码转换和数字字符串转换异常已关闭。

收益仅为 candidate answer + explicit provenance + workspace lineage → assertion envelope；real_candidate_source_count=0，真实parser/binding/verifier与答案正确性仍未测。B-03证据供给残余不变，B-06仍待真实测量；下一投入为受限英文声明→绑定→验证最小闭环，草案未冻结，不自动授权API。正式证据见 `handoffs/evaluator_executor/FDQA-FREEFORM-CANDIDATE-ASSERTION-ENVELOPE-ADAPTER-V1/REVIEW_FINAL.md`。下文历史任务/候选假设不覆盖本条最新状态。

## Project outcome

- Observable project outcome: 用户提出自然语言问题后，系统能从金融文档中找到可靠证据，生成可追溯、可验证、失败关闭的答案。
- Primary user or business value: 把 FinDocQA 从比赛式答案生成改造成可用于个人与企业知识空间的工业文档问答底座。
- Forbidden failures: 来源、范围、公式或答案形态不确定时伪装成确定答案；错误来源血缘；错误确定性执行；无证据放行。
- Current evaluation boundary: 当前推进“结构化表格证据检索与确定性计算链”。Oracle、文档级供给和 46 文档闭集成绩均不得外推为完整语料检索或端到端答案准确率。

## End-to-end capability chain

```text
自然语言问题
→ Query Understanding / calculation 分类
→ 文档检索与结构化表格证据
→ EvidenceBundle
→ 来源绑定 request 组装
→ C3 确定性执行
→ 答案与来源血缘校验
→ 可交付答案
```

## Evaluator global-view response protocol

每次正式评估 / 复核都必须同时回答“局部任务结果”和“整体项目现在走到哪一步”。

评估前先简要给出：

```text
全链路
→ 已完成能力
→ 第一瓶颈 / 次级瓶颈
→ 当前任务位于哪一段、试图打通什么
```

评估后再简要给出：

```text
本次结论
→ 第一瓶颈是否前移 / 原地阻塞 / 切换方向
→ 新建立了什么能力或证据
→ 剩余最主要未知点
→ 下一任务如何推动整体链路，而不是只修局部 case
```

默认保持简洁，不重复完整历史 ledger；目标是用户每次都能看到“项目全局地图 + 当前红点 + 红点移动方向”。

## Measurement basis

已验证能力：

```text
Factory structured-table SUM：0/3 → 3/3
C3-P Binder 反例：33/33 失败关闭
C3-M 正常主链：ACTIVE
C3-N / C3-O 正常主链：BLOCKED_BY_MISSING_BINDING
完整离线回归：1290 passed
```

190 文档结构化表格供给：

```text
document_count = 190
有 table 元素并加载完整身份行证据 = 77
无 table 元素 = 113
total_tables_seen = 8195
total_tables_loaded = 6071
total_rows_loaded = 77525
unsupported_layout_count = 2124
```

54 题问题级表格证据闭集基线：

```text
候选文档闭集 = 46
来源对象 = 98
Document Recall@1/3/5 = 17 / 32 / 36
Gold Table Source Recall@5 = 33
Gold Coordinate Coverage@5 = 67 / 185
BINDING_READY = 21
Terminal = DOCUMENT_MISS 18 / TABLE_SOURCE_MISS 3 / MEMBER_RANGE_INCOMPLETE 12 / BINDING_READY 21
Provider / legacy / network / Token = 0
```

边界：该 Document Recall 只表示 46 文档闭集内排名，不代表完整 FinQA/TAT-QA 或本地 190 文档全库召回率。

H-06 停用词过滤独立复核结果：

```text
Document Recall@1/3/5 = 24 / 34 / 39
Gold Table Source Recall@5 = 34
Gold Coordinate Coverage@5 = 70 / 185
BINDING_READY = 22
Terminal = DOCUMENT_MISS 15 / TABLE_SOURCE_MISS 5 / MEMBER_RANGE_INCOMPLETE 12 / BINDING_READY 22
旧 Top5 文档命中损失 = 0
完整离线回归 = 1305 passed
```

状态：产品影响 `IMPROVED`。测试隔离 repair 已由 Evaluator 独立复核通过：baseline 21 passed、专项 19 passed、相关 54 passed、全量 1305 passed；动态 after 报告与正式报告逐字节一致，8 个冻结 Hash 不变。H-06 正式保留。

H-07 成员范围诊断独立复核结果：

```text
MEMBER_RANGE_INCOMPLETE = 12
ROW_LABEL_MATCH = 10 / 12
RANGE_EXPANSION = 2 / 12
ROW_LABEL_MATCH 独立问题数 = 9
10/10 目标表均位于当前固定窗口之后
运行时 Gold / case ID / official answer 依赖 = 0
```

状态：诊断任务 `PASS`，项目影响 `NOT_APPLICABLE`。根因判定置信度 high；产品收益仍是模拟上界，必须进入同基线 capability experiment 实测。

H-07 表行标签锚定独立复核结果：

```text
row-label complete = 0/10 → 10/10
row-label coordinates = 0/29 → 29/29
Gold Coordinate Coverage = 70/185 → 99/185
BINDING_READY = 22/54 → 32/54
Terminal = DOCUMENT_MISS 15 / TABLE_SOURCE_MISS 5 / MEMBER_RANGE_INCOMPLETE 2 / BINDING_READY 32
Document Recall@1/3/5 = 24 / 34 / 39（不变）
Table Source Recall@5 = 34 / 54（不变）
已有 BINDING_READY regression = 0/22
完整离线回归 = 1311 passed
```

状态：任务 `PASS`，项目影响 `IMPROVED`，H-07 产品改动正式保留。改善严格限定于冻结的 10 个 row-label 案例；两个 RANGE_EXPANSION 案例不变。

H-08 文档缺失诊断复核结果：

```text
15-case 完整文档排名 = 可重复生成
Gold document 均在 46 文档候选池
相关测试 = 31 passed
执行者候选分布 = 8 generic/numeric + 4 near-duplicate + 3 identity-missing
```

状态：完整排名测量保留，但父任务 `FAIL / REPAIR_REQUIRED`。执行者以 `boost=4.0` 是否恢复案例直接定义 `GENERIC_OR_NUMERIC_TERM_DOMINANCE`，导致 8-case 根因组与 8-case 恢复组循环重合；同一 54 题闭集同时用于选词、选系数、分组和设置验收线。敏感性审计显示 boost 2/3 仅恢复 6 题，boost 5 及以上立即产生 1 个旧 Top5 退化，只有单点 4.0 满足 8/15 且零退化。

H-08 独立归因修复复核结果：

```text
独立根因分布 = generic/numeric 6 / near-duplicate 5 / identity 2 / content gap 1 / unresolved 1
最大共同根因 = 6/15，独立问题数 = 6
boost=3.0：恢复 6/15，Full54 Recall@5=45，旧 Top5 退化 0
boost=4.0：恢复 8/15，Full54 Recall@5=47，旧 Top5 退化 0
boost=5.0：恢复 8/15，Full54 Recall@5=46，旧 Top5 退化 1
相关测试 = 31 passed
```

状态：修复任务 `PASS`，项目影响 `NOT_APPLICABLE`，正式裁决 `NO_SINGLE_VARIABLE`。H-08 当前 specificity 候选不进入产品；文档缺失仍保留为已测量损失层，但没有证据支持继续围绕同一变量调参。

H-09 表来源缺失诊断复核结果：

```text
冻结排名与 5/5 CROSS_DOCUMENT_PAGE_DILUTION = 可重复
两次 Full54 SHA256 = 2a8ea248...6abac
相关测试 = 31 passed
执行者候选 = 等分、同 matched_terms 页面合并
现有 EvidenceCandidate 来源身份 = 单一 source / lineage.source_path
现有 Gold source scorer = 只读取单一 lineage.source_path
候选合并后精确 Gold source 进入 Top5 = 2/5
Gold 隐藏在非首个组成员 = 3/5（GS / IP / LMT）
```

状态：父诊断任务 `REJECTED / REPAIR_REQUIRED`，项目影响 `NOT_APPLICABLE`。5/5 跨文档页面挤占作为测量事实保留，但 `PROMOTE_SINGLE_VARIABLE_EXPERIMENT` 不接受。若只保留每组首个来源，3/5 正确表页仍不可见；若让一个候选携带并计入多个来源，则必须改变来源契约或 Gold scorer，不再满足“单一 Evidence Retriever 变量、Gold scorer 不变”。

H-09 晋级门与精确来源语义 repair 独立复核结果：

```text
Gold 所在分组进入 Top5 = 5/5
精确 Gold source 进入 Top5 = 2/5
Gold 被非 Gold representative 隐藏 = 3/5（GS / IP / LMT）
现有 EvidenceCandidate 来源身份 = 单一 lineage.source_path
现有 Gold scorer = 不读取 member source metadata
相关测试 = 31 passed
```

状态：repair `PASS`，项目影响 `NOT_APPLICABLE`，正式裁决 `NO_SINGLE_VARIABLE`。H-09 页面合并候选关闭，不进入产品；B-03 剩余 `15 / 5 / 2` 保留为 Failure-Regression，已按 Human/Task Owner 指示暂停继续细分和微调。


## Core bottleneck reassessment｜E4 Gold 基线

Human/Task Owner 于 `2026-08-06` 要求暂停 22-case 残余损失账本，不再围绕小案例反复修补。Evaluator 重新对齐项目结果后确认：

```text
E1 已有文档供给测量
E2 已有 54 题闭集检索测量
E3 已有 Oracle-program 计算测量
E4 没有冻结、可信、可运行的 Gold 基线
```

当前仓库已经存在：

```text
五领域问题 = 100 题
私有 Gold 种子候选 = 30 题
E4 Answer Quality / A-B 代码 = 已存在
```

但尚不存在：

```text
逐题 Gold manifest
文档 / 页码 / 证据 / Claim / 公式闭环
两轮复核状态
Gold-Core / Holdout-Shadow 正式划分
当前产品端到端基线结果
```

因此，项目当前第一瓶颈不是某个 Retriever 小参数，而是无法用统一 Gold 判断：最终答案是否正确、来源是否正确、错误是否被放行、正确答案是否被阻断，以及成本是否可接受。

详细依据见 `docs/evaluation/core-bottleneck-reassessment.md`。

## H-11 Gold 证据包 V1 独立复核

```text
30/30 候选与原始问题唯一匹配
30/30 required document identity 可解析
Provider / network / token = 0 / 0 / 0
相关测试 = 42 passed

但：
逐选项 evidence = 151
缺页码 = 108 / 151
逐选项证据全部缺页码的题 = 10
MECHANICAL_CONTRADICTION = 3
独立核对后的误判 = 3 / 3
```

三个误判分别是：法规原文完整支持却被标为冲突；宁德时代现金流原文明确为 `1,332 亿元` 却被标为不同值；美的 2025 营收增长率 `12.11%` 高于 2024 年 `9.44%`，却因无关宏观段落中的“下降”被标为冲突。

状态：任务 `REJECTED / REPAIR_REQUIRED`，项目影响 `NOT_APPLICABLE`。30-case manifest 与来源身份框架可以保留，但当前 `22 partial / 5 missing / 3 contradiction / 0 ready` 不是可信的 Gold 状态分布，不能用于冻结 Core / Shadow，也不能据此判断 30 题都需要人工重做。先修复页级来源血缘和高置信冲突门禁。

## H-11 Gold 证据语义修复独立复核

```text
option evidence = 153
page missing = 0 / 153
全题 option evidence 缺页码 = 0
已知误冲突关闭 = 3 / 3
保留真实反证 = 1（res_a_001 / D）
状态 = 25 PARTIAL / 5 MISSING_REQUIRED_EVIDENCE / 0 MECHANICAL_CONTRADICTION
相关测试 = 63 passed
Provider / network / token = 0 / 0 / 0
```

状态：repair `PASS`，项目影响 `NOT_APPLICABLE`。页级来源和高置信冲突门禁已可信到可以继续人工/Evaluator Gold 裁决；`READY=0` 只说明通用机械闭环不能自动冻结 Gold，不代表 30 道候选都不可用，也不再触发继续扩展 evaluator 规则。

下一步不强制凑满 30 题：从 30 题候选池中形成少量分层复核 dossier，实际能冻结多少由独立证据裁决决定；同时把已有 FinQA / TAT-QA 外部轨道、Failure-Regression 与未来 Shadow 注册为分轨 Evaluation Suite。外部轨道与本地 Gold 分开计分，不混成单一总分。

## H-11 Minimum Gold + Evaluation Suite 独立复核

```text
suite generator 独立重生成 = 字节一致
本地候选 / eligible / dossier = 30 / 17 / 10
分轨 suite = 5 tracks
外部 active reference = 2
combined total score = false
相关测试 = 116 passed
```

结构任务 `PASS`，项目影响 `NOT_APPLICABLE`。但 shortlist 分数偏重“存在页级片段”，不能直接代表证据语义相关。10 道中 4 道所有选项均未机械闭环，等待期、身故保险金、文件要素和部分财务指标的片段存在明显错位。

Evaluator 随后回到原始页级文档独立裁决 10 道 dossier：

```text
GOLD = 5
QUESTION_OR_ANSWER_AMBIGUOUS = 2
DEFERRED_EVIDENCE_GAP = 3
Gold 领域 = 金融合同 1 / 财务报告 2 / 研究报告 2
监管 / 保险 Gold = 0 / 0
```

状态：两波 17 个 machine-eligible 已全部裁决并完成 Evaluation Suite v0.2 物化：机器可读 Gold = 9，ambiguous = 4，deferred = 4；领域分布为金融合同 2 / 财务报告 3 / 保险 1 / 监管 1 / 研究 2，五个本地域均有 Gold。36 个 Gold evidence 文件运行时 Hash 校验通过，六个 v0.2 输出独立双生成字节一致，14 个相关回归通过。当前仍不伪造 Holdout-Shadow，本地轨继续 `FROZEN_SEED_NOT_SCORE_READY`。人工扩题到此暂停，后续优先扩大外部可信测量范围。

## Bottleneck register

| ID | Stage | Observable failure | Estimated affected scope | Evidence | Confidence | Status |
|---|---|---|---:|---|---|---|
| B-01 | 来源绑定 request → 正常计算主链 | SUM 能力曾不可达 | 固定 SUM 3 cases | 0/3→3/3；33/33 护栏 | high | CLOSED |
| B-02 | 结构化表格证据供给 | 真实 MinerU 表格和完整行证据曾未知 | 190 文档 | 77 份完整行证据、6071 表、77525 行 | high | CLOSED |
| B-03 | 问题 → 可靠页面/证据工作区 | bounded multi-lane workspace 已证明可减少一部分 fixed-Top5 lane competition，但 workspace 内仍存在 evidence-supply / selective-exploration 缺口 | 54 官方题 / 46 文档闭集 + FinanceBench 多文档 fresh cohorts | H-50 46-case：lexical 12/46、semantic 25/46、union oracle 28/46，仍有 18 BOTH_MISS；H-53 固定 ±2 邻页 fresh12 仅恢复 2/12，`NOT_QUALIFIED`；H-60 同 lane 比较 RRF60 Top5 `7/12` → bounded workspace `9/12`，恢复 2 case / 2 families、protected loss=0，但低于预声明 >=3 门槛，仍有 3/12 union miss。H-60 的局部 gap-fill 信号在 H-50 18 BOTH_MISS 上 `0/18` 复现 | high（late contraction 有真实正向证据但不足；剩余 supply loss 跨批次存在，尚无新的单一通用机制） | ACTIVE |
| B-04 | 长尾计算算子 | 剩余 unsupported operator 无通用家族达到 5 条 | 最大合格族 1 | C3 stage-exit report | high | RETIRED |
| B-05 | 复杂表格解析 | 2124 张图像表或复杂 span 表未加载，但问题级影响未知 | 2124 张表 | `empty_or_image_table=2038` 等 | medium（现象）；low（业务影响） | WATCH |
| B-06 | E4 Gold 与端到端结果度量 | 无法可信自动判断 freeform 最终答案语义正确性并稳定统计 paid-run 成本 | 项目全链；本地 100 题 + 外部公开 benchmark | H-28 known-wrong Judge 3/3 agreement；H-32 AMEX reference labels=0/7 correct；known-correct 方向仍为空。但 H-33 已证明 6/7 在 Retrieval Top5 前丢失官方 evidence，因此继续扩 Judge 不是当前第一优先级 | high | SECONDARY_BLOCKED_BY_B03 |
| B-07 | Solver/Provider → 输出门禁 → 可交付答案 | independent freeform cohort 曾出现 Provider ERROR 与输出门禁阻断 | AMD_2022_10K 7-case independent cohort | H-25 历史 Provider ERROR=4/7；H-26 补诊断；H-27 对历史 4 ERROR 重跑得到 3 COMPLETED + 1 INVALID_RESPONSE、0 transport retry，决策 `NO_COMMON_FAMILY`，无单一 Provider failure family >=4；另有 2 个 output-gate case 仍低于通用修复门槛 | high（该 cohort）；medium（全局外推） | SECONDARY_NO_SINGLE_COMMON_FAMILY |

## Active bottleneck

Active bottleneck ID: `B-CN-01`

当前决策（2026-09-21 r94）：H-69 已正式 PASS / IMPROVED / CONTINUE。workspace admission 子瓶颈在冻结 3 题上从 0/3 合法工作区推进到 3/3；24/38/31 原始唯一页保持不变，after 均为 10 页，required document 覆盖 2/2，来源审计与 fail-closed 护栏通过。Retriever、`EvidenceWorkspaceScope`、financial evidence builder 均未被 H-69 修改。

B-CN-01 当前红点不是 admission 本身，而是 admission 之后的 Product 下游 first-loss 未正式重新测量。H-68 Oracle 已证明 evidence / AST / binding / verdict 均可能成为后续损失，但 Oracle 不能代表 Product；必须使用 H-69 实际选出的 10 页重新跑真实 Product 漏斗。

次级瓶颈 `B-CN-02` 仍是独立中文 holdout 缺失。当前 3 题只允许做 DEV_SEED 诊断。

## Active hypothesis

Hypothesis ID: `H-70`

H-70：`FDQA-CN-ADMITTED-WORKSPACE-DOWNSTREAM-FUNNEL-V1`，任务类型为 evaluator_design。目标是在 H-68 pre-H67 隔离源码上，仅叠加已验收 H-69 admission 模块，把同一 3 题 / 12 选项重新跑 evidence → AST → binding → calculation → verdict，形成新的 Product first-failure 分布。该任务只测量、不修产品；H-68 Oracle 只读取持久化统计作比较，不把 Oracle 页注入 Product。

当前冻结执行包：`handoffs/evaluator_executor/FDQA-CN-ADMITTED-WORKSPACE-DOWNSTREAM-FUNNEL-V1/CONTRACT.md`。

## Historical r86 bottleneck decision

当前决策（2026-09-18 r86）：B-03仍是第一已测损失，B-06次级、B-05观察、B-07次级。不因检索还有漏题而无限优先修检索，也不把输入缺失视作验证器失败。

链路：问题 → 解析/来源 → 检索 → 证据工作区 → 断言/绑定/计算/验证 → 可消费证据与答案。工作区scope及产品路由已闭环；H-60证明页面可达7/12→9/12，未证明最终答案收益。新增关键事实：H-60脚本因缺少候选答案断言统一写12条VERIFIER_UNSUPPORTED，没有逐题执行验证器。

用户授权同包A1：一次检索收口与消费契约审计，覆盖全12题而非只看3个miss，复算页数/字符成本，区分页面、事实、断言、验证和答案的测量边界。历史H-60原判和阈值不变；零新题集/调参/API/产品实施。

## Historical r86 hypothesis

Hypothesis ID: `H-61`
Task: `FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1`
Task kind: `evaluator_design`
Active contract: `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/CONTRACT_A1.md`

审计检索候选和下游缺失输入/接口，比较B-03/B-05/B-06/B-07，建议恰好一个后续草案。不预设B-06第一。建议为DOWNSTREAM_CONTRACT_FIRST、RETRIEVAL_PROBE_FIRST或必要证据缺失时HOLD_FOR_MISSING_EVIDENCE，由评估者复核。无共同机制是有效收口。

分层准入：观察到的收益保留为实验资产；有限实验需机制明确、成本可控、可证伪；默认启用/可靠交付另需独立样本、成本和下游正确性证据。>=3独立题/2文档族仅作检索候选筛选，不是统计显著性，不阻止接口审计。两题信号不抹掉、不自动晋级。

本轮只建立契约及检查工具，不宣称H-61执行完成或瓶颈关闭。无模型部署采用词法/结构基线，语义按批准的内网/外部服务可选；不承诺三档同等效果，不把缓存回放冒充无模型生产能力。

## Historical r85 bottleneck decision（已由r86替代）

Active bottleneck ID: `B-03`

当前复合判断（2026-09-17 r85）：

1. **B-03 仍是第一瓶颈，但红点已经移动。** H-60 独立 L3=`9/9 PASS`，同一 lexical + Cloudflare semantic lanes 下，bounded workspace 把 verification-boundary Gold reach 从 `7/12` 提升到 `9/12`，恢复 `2 cases / 2 families`、`protected_loss=0`；说明 premature Top5 contraction 确实造成一部分损失，但未达到冻结 `>=3 recovery` 资格线。
2. **H-60 正式结论是 `PASS / IMPROVED / SWITCH`，方向决策 `NOT_QUALIFIED`。** 不继续在 H-60 内改 TopK、RRF 权重、阈值、provider/model 或 cohort 追第 3 个 recovery；H-60 只证明 late contraction 有限有效，不支持产品化晋级，也不支持 0.6B-vs-8B 比较。
3. **剩余第一未知点转为 evidence supply / selective exploration。** H-60 candidate 仍有 `3/12` union miss：ULTA 2023、ADOBE 2015、AMCOR 2020；Gold 均不在 lexical Top5 ∪ semantic Top5。ULTA/AMCOR 有近邻页信号，ADOBE 是更深定位 miss，说明残余并非单一“再多保几页”问题。
4. **不重开固定邻页/简单 gap-fill。** H-53 已证明固定 ±2 邻页 fresh12 仅恢复 `2/12` 且新增页过多；Evaluator 将 H-60 局部 gap-fill 信号拿到 H-50 的 `18 BOTH_MISS` 上复核，`max_gap=2/3/4` 均为 `0/18` recovery。该信号不足以形成新能力方向。
5. **下一步先做零 API residual evidence-supply direction reassessment。** 复用 H-60 persisted embeddings/lanes 与 H-50/H-53/H-43 历史证据，检查是否存在跨 case、跨 family、Gold-free、且未被历史 fresh 失败否决的单一 supply/exploration 机制；若没有，必须返回 `NO_SINGLE_DIRECTION` 并重排 B-03 vs B-06/B-05，而不是继续微调 Retrieval。
6. **B-06 仍是最明确的下游候选瓶颈。** freeform Judge authority 仍缺 known-correct 泛化；但在 B-03 仍有 `3/12` fresh union miss、历史 H-50 仍有 `18/46 BOTH_MISS` 的情况下，暂不把 B-06 提升为第一瓶颈。
7. **B-05 保持 WATCH，B-07 保持 secondary。** 当前还没有“页面已找到但复杂表事实不可用”足够题目级证据把 B-05 前移；B-07 也没有新的 common failure family。
8. **C3 / Binder / workspace scope / product-route reachability 已不是当前阻碍。** H-56R1 与 H-60 共同证明 scope 路由和 bounded workspace 执行边界可用；下一轮只研究 evidence supply 方向，不再重复修 scope。

当前主线：

```text
Question
→ lexical / semantic lanes
→ bounded Evidence Workspace ✅（scope / product-route reachability 已打通）
→ H-60: RRF60 Top5 7/12 → workspace 9/12，+2 cases / 2 families，0 loss；NOT_QUALIFIED
→ remaining: 3/12 union miss + H-50 historical 18/46 BOTH_MISS
→ [当前红点] H-61 residual evidence-supply direction reassessment（zero API）
→ 若找到 >=3 cases / >=2 families 的 Gold-free 单一机制：再冻结 fresh capability validation
→ 若找不到：NO_SINGLE_DIRECTION，重排 B-03 vs B-06/B-05
```

## Historical r85 hypothesis（已由A1替代）

Hypothesis ID: `H-61`

Proposed task: `FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1`

Task kind: `evaluator_design`

Composite basis:

> H-60 已独立证明 late contraction 有真实但不足的收益：同 lane 下 verification-boundary reach `7/12 → 9/12`，恢复 2 case / 2 families、0 protected loss，但低于事前 `>=3` qualification。剩余 3 个 H-60 miss 均为 lexical Top5 ∪ semantic Top5 之外的 evidence-supply loss；历史 H-50 仍有 18/46 BOTH_MISS。H-53 固定邻页与 H-43 fresh target-guided planner 均已出现 fresh failure，因此不能直接重开旧机制。

H-61 只做 zero-API / zero-product-change 的方向重评估：冻结 H-60 的 3 个 residual miss 与 H-50/H-53/H-43 已有证据，描述 full-rank / page-distance / lane-agreement / structure-locality 等 failure signals；任何候选机制必须先定义 Gold-free trigger，再用 Gold 仅作诊断标签。

Direction gate：只有当同一未被历史 fresh 失败直接否决的机制覆盖 `>=3 independent cases + >=2 document families`，且能写出单一 principal change 与新的 fresh validation failure condition，才输出 `CANDIDATE_DIRECTION`。否则输出 `NO_SINGLE_DIRECTION`，停止 B-03 微调并重新排序 B-03 / B-06 / B-05。H-61 不授权任何 embedding、Generative LLM、Judge、Solver、reranker、provider call 或产品代码修改。

## Direction admission policy｜方向准入标准

本项目不再使用“固定 `>=4 case` 才能形成方向”的单一硬门槛。方向发现与能力证明分开：

```text
1 case  = 个例，只记录
2 cases = 信号，继续观察
>=3 independent cases + >=2 document families + 同一机制 + 可证伪
        = CANDIDATE_DIRECTION（候选方向）
```

`CANDIDATE_DIRECTION` 只表示值得冻结一个小实验，不表示能力已经成立。必须同时满足：

1. `independent cases >= 3`；
2. `document families >= 2`（对文档检索类问题）；
3. 三个案例能用同一 mechanism(机制)与同一 principal change(主变量)解释，不能只是同名归类；
4. 机制不得依赖 qid、Gold、reference answer 或单文档硬编码；
5. 能提前写出 fresh validation(新鲜样本验证)的失败条件。

达到候选方向门后，**停止继续在旧 cohort 中凑数量或优化阈值**，冻结机制并转入新的 untouched cohort(未触碰样本集)验证。若 3 个案例全部来自同一文档族，只记为 `LOCAL_CLUSTER(局部簇)`，不升级为跨文档方向。

能力资格仍由后续预声明 fresh cohort 决定，不从“旧题达到 3/4/5 道”直接外推。

## Historical H-53 hypothesis

Hypothesis ID: `H-53`

Task: `FDQA-B03-FRESH12-NEIGHBOR-EXPANSION-GENERALIZATION-V1`

Task kind: `capability_experiment`

Strategic basis:

> H-52 independently passed L3 `8/8` and found one Gold-free candidate direction: fixed same-document `+/-2` physical-page neighbor expansion around the frozen lexical Top5. It reached previously missed Gold in `3/13` diagnostic misses across `2` document families, satisfying the project direction-admission threshold but only at its minimum boundary. H-52 made no product change and has `capability_qualification=false`.

Frozen H-53 principal change:

```text
baseline = unchanged canonical lexical Top5 physical pages
candidate = baseline Top5 UNION same-document non-seed pages at distance 1..2
mechanism = bounded_neighbor_expand_r2
radius = exactly 2
query rewrite = none
qid/document rules = none
model/API calls = 0
product changes = 0
```

Fresh cohort is frozen before retrieval/Gold outcomes using the FinanceBench source revision `cc39aeb4afdf33909ee1412188bf89035950c2eb`:

```text
12 questions / 6 whole document families
ADOBE_2022_10K
AMAZON_2017_10K
AMCOR_2023Q4_EARNINGS
BLOCK_2020_10K
CORNING_2022_10K
GENERALMILLS_2020_10K
```

Selection rule: `qa_count desc + doc_name asc`, excluding every document family already exposed in prior B-03 JSON/JSONL artifacts; take the first six remaining whole documents. No substitution, early stop, extension, or outcome-based selection is allowed.

Fresh capability qualification:

```text
QUALIFIED iff
recovered lexical misses >= 3
AND recovered document families >= 2
AND protected loss = 0
```

Candidate-page expansion cost/noise must be reported. Even `QUALIFIED` proves only fresh generalization of the bounded evidence-reach mechanism; it does not authorize product integration. If the gate fails, stop this mechanism rather than tuning radius or adding a second exploration family.

## Completed H-06 experiment gates

单一主变量：

```text
文档级 query terms 的标准英语停用词过滤
```

不得同时修改：

```text
Evidence Retriever query terms
窗口大小或 flank
top_k
term weight / scoring formula
文档标题或官方适配文本
54-case manifest / Gold scorer
```

保留条件：

```text
Document Recall@1/3/5 均不下降
Document Recall@5 至少 36 → 39
Table Source Recall@5 不下降
Gold Coordinate Coverage 不下降
BINDING_READY 不下降，目标 21 → 22
原有 Top5 文档命中题新增退化 = 0
Provider / legacy / network / Token = 0
完整离线回归通过
```

任一核心护栏失败则回滚，不通过追加金融术语或案例特例继续调参。

## Completed H-07 diagnostic gates

诊断包必须对固定 12 个 `MEMBER_RANGE_INCOMPLETE` 逐题记录：

```text
Gold source Top5 rank
候选表来源与成员证据
已覆盖 / 缺失坐标数量
失败发生在 source selection / row-label match / range expansion / coordinate projection 的哪一层
是否能在不读取 Gold 坐标的情况下，从问题、能力类型和表结构推导目标成员范围
```

晋级产品实验的条件：

```text
至少 8/12 案例归入同一通用根因
可提出 exactly one principal change
不需要修改文档排序、top_k、Gold scorer、manifest 或多个产品模块
能定义 12-case before/after 与 54-case 总护栏
```

否则 H-07 诊断结论为 `NO_SINGLE_VARIABLE`，重新评估 B-03 的 DOCUMENT_MISS / TABLE_SOURCE_MISS 分支。

## Candidate experiments

| Priority | Hypothesis | Principal change | Same-baseline comparison | Expected result | Cost/risk |
|---:|---|---|---|---|---|
| 1 | FinanceBench research-only Adapter | 在 `RESEARCH_ONLY_NONCOMMERCIAL` use scope 下新增 FinanceBench → Canonical Question / Gold Annotation / Document Reference Adapter | 冻结 source snapshot：GitHub/HF 150/150、qid 相等、84/84 PDF tree | 150 条可离线规范化；Gold/evidence 仅用于 evaluator；不改 Retriever/Solver/Verifier | measurement infra，L0/L1 |
| 2 | FinanceBench research PDF acquisition / parse smoke | Adapter 通过后，只为本地非商业研究按冻结 84 个文档引用准备 PDF 输入并做 parser smoke | 同一 source snapshot + research-only use scope | PDF→Canonical Document 可追溯，第三方文件继续 gitignored、不再分发 | bounded download；先小切片再扩 |
| 3 | FinanceBench external E4 baseline | Adapter + PDF smoke 通过后使用固定 research subset 运行现有全链 | 同一公开 Gold、同一模型与配置 | Answer / Evidence / False Accept / False Reject / Cost 外部基线 | Provider 运行仍需单独授权 |
| 4 | FinMRAGBench Adapter / stress track | FinanceBench 路线许可受阻或完成后，再核 FinMRAGBench 的官方 license/体量并接高难金融 QA | 官方 expert-verified QA | 暴露多页、多文档、复杂金融分析损失 | 后续；先核许可证/体量 |
| 5 | FinRAGBench-V / FinDER specialty tracks | FinRAGBench-V 只做视觉/citation 切片；FinDER 只做 research-only Query/Retrieval | 官方 schema/license | 补 Parser/Visual Citation 与真实 Query Retrieval | 202GB / CC-BY-NC-4.0，禁止当前全量接入 |
| 6 | E4 largest-failure capability experiment | 由 Local + 可合法使用的外部 E4 第一失败层冻结一个 principal change | 同一 E4 before/after | 直接改善最终产品指标，核心门禁不退化 | 后续决定 |

## Reassessment triggers

- 30 道种子候选无法从当前原始文档和解析资产定位到可靠来源；
- 候选题与原始问题文件、文档 ID 或历史答案无法一一对应；
- 可形成完整证据闭环的题目不足以覆盖五个领域或主要题型；
- Gold 候选必须依赖排行榜分数、模型多数票或 official answer 才能成立；
- E4 runner 无法读取冻结 manifest 或无法记录答案、证据、门禁和成本四类指标；
- E4 基线显示当前第一损失并非 Retrieval，则 B-03 继续保持 PAUSED；
- E4 基线显示复杂表格、知识导航或来源组织是主要失败层，再分别升级 B-05、P-KW1 或 P-LW1；
- 项目目标、数据授权或可使用模型约束发生变化。

## Revision log

| Revision | Date | Evidence or reason | Bottleneck change | Hypothesis change |
|---|---|---|---|---|
| 2026-09-21-r95 | 2026-09-21 | 用户要求把 H-70 后两条候选路线显式记录给 Evaluator；新增 evaluation decision note，并在当前红点处链接 | 不预设新瓶颈；H-70 后按 Product 实测在“下游首损”与“Evidence Selection V2”之间分流，证据不足则先补测量 | H-70 仍为只测量；不提前激活新 capability hypothesis |
| 2026-09-21-r94 | 2026-09-21 | H-69 Evaluator L3 3/3 mandatory PASS；24/38/31→10/10/10；3/3 合法 workspace；4/4 fail-closed | workspace admission 不再是冻结 3 题最早阻断；B-CN-01 红点移动到 admitted workspace 后的 Product downstream first-loss | H-69 PASS/IMPROVED/CONTINUE；激活 H-70 admitted-workspace downstream funnel measurement |
| 2026-09-20-r93 | 2026-09-20 | H68 corrected R2 + Evaluator L3 2/2 PASS；3题检索唯一页24/38/31均>10；12/12首损=evidence/workspace admission；5/5负控制REJECT；R2/L3核心hash一致 | B-CN-01从中文链路首损未知收敛为bounded evidence admission/selection缺口；B-CN-02独立覆盖仍保留 | H68 PASS/NOT_APPLICABLE/CONTINUE；激活H69单变量bounded evidence admission capability experiment |
| 2026-09-20-r92 | 2026-09-20 | 用户中文优先；H66/H67暂停；本地3财报seed、14证据引用hash有效，问题整文件hash漂移 | B-CN-01中文链路测量/基线，B-CN-02独立覆盖；英文损失不外推中文 | H68 DESIGN_ONLY；先中文逐选项漏斗，不执行模型、不直接改产品 |
| 2026-09-20-r91 | 2026-09-20 | H-65最终独立7/7；12/12逐字段重放；4/4反例和22/22页码矩阵拒绝；53 passed/27 subtests | 封装入口阻塞关闭，证据供给与真实答案验证仍未闭合 | H-65 PASS/IMPROVED/CONTINUE；下一英文声明绑定验证切片仅DRAFT_ONLY |
| 2026-09-19-r90 | 2026-09-19 | H-64 A1 L2=7/7；A2 L3=7/7；fixed12 wiring/producer-consumability/workspace-lineage 均 0/12→12/12；104 pages；outside=0；8/8 fail-closed；4题/3文档家族独立抽查 PASS | bounded workspace→producer input contract 已闭合；B-03 检索残余不变；真实 candidate/verifier 仍未测 | H-64 PASS/IMPROVED/CONTINUE；激活 H-65 candidate assertion/envelope adapter，零 API/模型调用 |
| 2026-09-19-r89 | 2026-09-19 | H-63 L2/L3=7/7；existing producer chain found；fixed12 静态 12/12→Direct；normal runtime qid presence=0/12；H-62 envelope 仍有 7 个 wiring/adapter 缺口 | 下游真实测量的第一前置阻塞收敛到 workspace/query→EvidenceBundle producer input wiring；B-03 检索残余不变 | H-63 PASS/NOT_APPLICABLE/CONTINUE；激活 H-64 frozen-workspace→EvidenceBundle wiring capability experiment，零 API/模型调用 |
| 2026-09-19-r88 | 2026-09-19 | H-62 L2/L3=7/7；固定12题 provenance-valid candidate source=0；readiness=NOT_READY；真实 verifier/fact/answer 均未测 | B-03仍未闭合，但下游真实测量被候选答案生产入口前置阻塞；继续暂停无新机制的检索微调 | H-62 PASS/NOT_APPLICABLE/SWITCH；激活 H-63 candidate-producer preflight，零 API/模型调用 |
| 2026-09-18-r87 | 2026-09-18 | H-61 A2独立L3=7/7，any7→9/all7→7，候选字符+73.47%；静态不支持不等于验证失败 | B-03仍未闭合，停止当前检索微调；下一投入为消费契约设计，B-06未自动提升 | H-61 PASS/NOT_APPLICABLE/SWITCH；下一仅DRAFT_ONLY，真实候选来源未就绪 |
| 2026-09-18-r86 | 2026-09-18 | 用户授权H-61 A1；查明H-60 unsupported为静态输入声明，非验证执行 | B-03主损失保留，开始比较消费契约缺口与检索机会成本；未宣称能力提升 | 同包一次收口、全12题漏斗/成本、消费边界审计；历史阈值不变 |
| 2026-08-03-r1 | 2026-08-03 | C3-P Binder 与来源身份修复 PASS；Factory SUM 0/3 | B-01 ACTIVE；B-04 退出主线 | 激活 H-01 |
| 2026-08-03-r2 | 2026-08-03 | SUM 0/3→3/3，但旧 stage-exit 回归失败 | B-01 改善待修复评测门 | H-01 获得产品改善证据 |
| 2026-08-03-r3 | 2026-08-03 | stage-exit 修复 PASS；全量 1256 passed | B-01 CLOSED；B-02 ACTIVE | 关闭 H-01；激活 H-02 |
| 2026-08-04-r4 | 2026-08-04 | 190 文档基线：77 完整行证据、113 无 table | B-02 CLOSED；B-03 ACTIVE；B-05 WATCH | 关闭 H-02；激活 H-04 |
| 2026-08-04-r5 | 2026-08-04 | 54 题基线 PASS；18/3/12/21 分层；18 个文档缺失均为排名偏低；停用词探针 36→39 | B-03 保持 ACTIVE，定位到文档 query 排序噪声 | 关闭 H-04；激活 H-06 |
| 2026-08-04-r6 | 2026-08-04 | H-06 独立复现：Doc R@5 36→39、Table 33→34、Coordinate 67→70、Binding 21→22、lost=0；原任务因未授权 baseline 测试修改被拒绝 | B-03 改善待测试隔离 repair；15/5/12/22 | H-06 获支持待 repair；H-07 暂缓 |
| 2026-08-05-r7 | 2026-08-05 | H-06 测试隔离 repair PASS；全量 1305 passed；after 报告逐字节一致；12 个成员范围失败中 FinQA 8、TAT-QA 4 | B-03 保持 ACTIVE；15/5/12/22 | H-06 正式保留；激活 H-07 diagnostic |
| 2026-08-05-r8 | 2026-08-05 | H-07 diagnostic PASS：ROW_LABEL_MATCH 10/12、按问题去重 9、RANGE_EXPANSION 2/12、运行时 Gold 依赖 0 | B-03 保持 ACTIVE；成员范围损失拆为单行锚定与范围扩展 | 激活 H-07 capability experiment；范围扩展暂缓 |
| 2026-08-05-r9 | 2026-08-05 | H-07 capability PASS/IMPROVED：row-label 10/10、Coordinate 70→99、Binding 22→32、旧正确案例回归 0、全量 1311 passed | B-03 保持 ACTIVE；当前损失 15/5/2/32，DOCUMENT_MISS 成为最大层 | H-07 正式保留；激活 H-08 document-miss diagnostic；P-KW1/P-LW1 进入后续规划 |
| 2026-08-05-r10 | 2026-08-05 | H-08 完整排名可复现，但根因组由 boost=4.0 恢复结果反向定义；系数敏感性仅单点 4.0 满足 8/15 且零退化，无独立 holdout | B-03 保持 ACTIVE；15-case 排名证据保留，产品实验未授权 | H-08 diagnostic FAIL/REPAIR_REQUIRED；激活独立归因与稳健性修复 |
| 2026-08-06-r11 | 2026-08-06 | H-08 repair PASS：独立最大根因 6/15；boost 3/4/5 分别恢复 6/8/8，Recall@5=45/47/46，boost5 退化1；正式 NO_SINGLE_VARIABLE | B-03 保持 ACTIVE；停止围绕 specificity 闭集调参，转向 5 个 TABLE_SOURCE_MISS | H-08 当前候选关闭；激活 H-09 table-source diagnostic |
| 2026-08-06-r12 | 2026-08-06 | H-09 排名事实与 5/5 cross-document dilution 可复现；但页面合并在 singular lineage + unchanged scorer 下仅让精确 Gold source 2/5 可见，3/5 隐藏在非首个组成员 | B-03 保持 ACTIVE；不进入 capability experiment | H-09 diagnostic REJECTED/REPAIR_REQUIRED；激活 exact-source promotion-gate repair |
| 2026-08-06-r13 | 2026-08-06 | H-09 repair PASS：Gold group Top5=5/5、exact Gold Top5=2/5、hidden=3/5、31 tests；正式 NO_SINGLE_VARIABLE | B-03 保持 ACTIVE；剩余 15/5/2，不继续围绕已否决变量微调 | 关闭 H-09 页面合并候选；激活 H-10 residual-loss reassessment |
| 2026-08-06-r14 | 2026-08-06 | Human 要求暂停 22-case 小问题账本；仓库已有 100 题、30 Gold 候选和 E4 代码，但无冻结 Gold manifest、逐题证据包或 E4 baseline | B-03 转 PAUSED；B-06 E4 Gold / 端到端度量成为 ACTIVE | 关闭 H-10；激活 H-11 Local Gold evidence pack |
| 2026-08-06-r15 | 2026-08-06 | H-11 V1：30/30 映射、42 tests、零调用通过；但 option evidence 108/151 缺页码，10 题全缺页；3 个机械冲突独立核对均为误判 | B-06 保持 ACTIVE；Gold 状态分布尚不可用 | H-11 转 ACTIVE_REPAIR_REQUIRED，先修页级血缘与冲突门禁 |
| 2026-08-06-r16 | 2026-08-06 | H-11 repair PASS：153 条 option evidence 全部页级可追溯，3/3 已知误冲突关闭，真实反证保留，63 tests；状态 25 partial / 5 missing / 0 contradiction | B-06 保持 ACTIVE；证据生成器不再是首要阻断，正式 Gold / E4 baseline 仍缺失 | H-11 转 LOCAL_GOLD_SUITE_ASSEMBLY，不强制 30，组合本地小 Gold 与外部轨道 |
| 2026-08-06-r17 | 2026-08-06 | Suite 组装 PASS：独立重生成一致、5 tracks、116 tests；shortlist 4/10 语义证据弱。Evaluator 独立裁决 10 题：5 Gold / 2 ambiguous / 3 deferred | B-06 保持 ACTIVE；Gold 决策已有 5，道路从候选筛选转为正式 manifest 与后续 E4 准备 | H-11 转 LOCAL_GOLD_MANIFEST_MATERIALIZATION，不补凑题量 |
| 2026-08-07-r18 | 2026-08-07 | Local Gold manifest PASS：9/9 冻结输入、5/5 Gold 精确物化、2 ambiguous + 3 deferred 分离、14 tests；本地轨仍 FROZEN_SEED_NOT_SCORE_READY | B-06 保持 ACTIVE；“无 manifest”缺口关闭，当前缺口收窄为 Gold 覆盖 / Shadow / E4 baseline readiness | H-11 转 WAVE2_GOLD_DOSSIER_PREPARATION，只处理剩余 7 道 machine-eligible 候选 |
| 2026-08-07-r19 | 2026-08-07 | Wave-2 dossier preparation 独立复核通过；7 道裁决为 4 Gold / 2 ambiguous / 1 deferred，累计 9 Gold 首次覆盖五域。外部复核确定 FinanceBench=P1，FinMRAGBench=P2，FinRAGBench-V/FinDER 专项注册 | B-06 保持 ACTIVE；停止主动扩本地 Gold，主缺口转为 suite v0.2 + 外部金融 PDF E4 接入 | H-11 转 EVALUATION_SUITE_V0_2_CONSOLIDATION，随后 FinanceBench Adapter |
| 2026-08-07-r20 | 2026-08-07 | Evaluation Suite v0.2 独立复核通过：9 Gold / 4 ambiguous / 4 deferred，五域覆盖，36 evidence Hash、双生成、14 tests 均通过；同时复核 FinanceBench HF 数据卡为 CC-BY-NC-4.0 | B-06 保持 ACTIVE；本地 suite 收口，外部主缺口先变为 FinanceBench source/license 可复现与用途边界 | H-11 转 FINANCEBENCH_SOURCE_LICENSE_SNAPSHOT；许可明确后才允许 Adapter |
| 2026-08-07-r21 | 2026-08-07 | FinanceBench source snapshot 独立复核通过：GitHub/HF 150/150 qid 全等、core mismatch=0、150/150 doc join、84/84 PDF tree、双生成稳定；Human 明确用途仅限学习/非商业研究 | B-06 保持 ACTIVE；source identity 与项目 use-scope gate 均关闭，下一缺口为 research-only Adapter | H-11 转 FINANCEBENCH_RESEARCH_ADAPTER；第三方数据继续隔离、不得商业化或重新许可 |
| 2026-08-10-r22 | 2026-08-10 | FinanceBench research-only Adapter 独立复核通过：150/150 Canonical compatible、84 docs、50/50/50 types、Gold leakage=0；12 focused + 19 regressions passed；baseline/scope/hash 均独立一致 | B-06 保持 ACTIVE；Adapter 缺口关闭，下一缺口为原始 PDF materialization / Parser evidence-page readiness，E4 baseline 仍为 0 | H-11 转 FINANCEBENCH_PDF_PARSER_SMOKE；先小切片，不直接跑 Provider/E4 |
| 2026-08-10-r23 | 2026-08-10 | 3-doc FinanceBench raw-PDF smoke 独立复核 PASS：3/3 PDF hash/page-count 一致；8 QA、10 evidence annotations、10 unique evidence pages；out-of-range=0；PyMuPDF 10/10 页有文本 | B-06 保持 ACTIVE；原始 PDF/官方页码物理链路不再是首要阻断，下一缺口为项目自身 Canonical ingestion 与 evidence retrieval | 关闭 H-11 基础接入序列；激活 H-12 FINANCEBENCH_CANONICAL_EVIDENCE_SMOKE；仍不跑 Provider/E4 |
| 2026-08-10-r24 | 2026-08-10 | Canonical evidence smoke 独立复核 PASS：3-doc page/Canonical counts 全一致，10/10 page+lineage+token sequence；Canonical lexical all-gold@5=2/8、annotation=2/10、Retrieval loss=6/8。停用词探针 2/8→2/8；6 个 miss Gold worst rank=52/135/109/147/70/81 | B-06 保持 ACTIVE；B-03 由 PAUSED 转 REOPEN_CANDIDATE，但尚未授权产品改动；简单 stopword/top_k 假设被否决 | H-12 关闭为 RETRIEVAL_LAYER_CONFIRMED；激活 H-13 FINANCEBENCH_RETRIEVAL_SEMANTIC_GAP_DIAGNOSTIC，先找 >=4 case 的单一 question-only 机制 |
| 2026-08-10-r25 | 2026-08-10 | H-13 semantic-gap diagnostic 独立复核 PASS：Phase-A 8/8 Gold key leakage=0；Retrieval baseline 再现 2/8、loss=6/8；miss taxonomy=direct alias 2 / derived operand 2 / causal-business 2；无 family >=4，gate=`NO_SINGLE_VARIABLE` | B-03 记录外部弱点后回到 PAUSED，不授权 patch；B-06 保持 ACTIVE，停止 8-QA 微调并转 E4 baseline | H-13 关闭；激活 H-14 FINANCEBENCH_E4_BASELINE_PREFLIGHT；先修正外部 E4 接线与 provider gate，不做真实调用 |
| 2026-08-10-r26 | 2026-08-10 | FinanceBench E4 preflight 独立复核 PASS：8/8 candidate-doc binding、Gold leakage=0、factory scope escape=0/provider=0；factory baseline=2/8、2/10；36 regressions passed；CLI dry-run 0 calls，两个 negative execute gate 均 exit2 before workflow/provider | B-06 保持 ACTIVE；E4 接线缺口关闭，external answer baseline 仍 NOT_RUN；B-03 继续 PAUSED | H-14 关闭为 PREFLIGHT_READY；激活 H-15 FINANCEBENCH_REAL_E4_BASELINE，但状态为 HUMAN_AUTHORIZATION_REQUIRED |
| 2026-08-10-r27 | 2026-08-10 | Human 明确授权 H-15 bounded real Provider run；冻结 8-case/3-doc、ModelScope `Qwen/Qwen3.5-397B-A17B`、单 endpoint、total call budget=8、checkpoint/resume/provider ledger；禁止产品改动 | B-06 保持 ACTIVE；进入首次外部真实 E4 answer baseline；B-03 继续 PAUSED | H-15 转 AUTHORIZED_FOR_BOUNDED_REAL_E4，分发真实 baseline Executor 包 |
| 2026-08-10-r28 | 2026-08-10 | H-15 V1 real run 被评估基础设施阻断：8 actual attempts 中 ModelScope 7 / SiliconFlow 1；5 completions、3 invalid/blocked；两个 Retrieval-hit controls 无有效 E4。resume 0 新调用、产品 diff=0 | B-06 保持 ACTIVE；V1 raw 0/8 禁止晋级；B-03 继续 PAUSED | H-15 V1 REJECTED；激活 H-15R1 REAL_E4_INFRA_REPAIR，只继承 5 有效案例并最多追加 3 次单 ModelScope 调用 |
| 2026-08-10-r29 | 2026-08-10 | H-15R1 PASS：Repair1 单 endpoint audit 通过，新增 3/3 ModelScope completions，8-case runtime 完整；Evaluator 语义裁决=2/8 正确生成、1/8 正确且放行；6/6 Retrieval miss 错、2/2 Retrieval hit 对；01858 正确但被长度门禁误拦；raw exact/value 仍 0/8 | B-03 转 REOPEN_CANDIDATE（端到端支持 Retrieval 为主损失层，但无单一 patch）；B-06 保持 ACTIVE（freeform scorer 不可信） | 关闭 H-15/H-15R1；激活 H-16 FINANCEBENCH_FREEFORM_E4_SCORER_DIAGNOSTIC，零 Provider；provider incremental accounting 独立 maintenance |
| 2026-08-11-r30 | 2026-08-11 | H-16 独立复核：冻结 8-case oracle agreement=8/8，correct=2/8，5/5 frozen counterfactual、双生成、20+8 tests、零 Provider 均通过；额外 adversarial 复核同时复现 protected-anchor 明确否定、冲突修正值、numeric-only 冲突值的 generic false accept | B-06 保持 ACTIVE；冻结 scorer diagnostic 可接受，但更大范围自动评分仍不可信；B-03 不变 | H-16 任务 PASS/NOT_APPLICABLE；拒绝立即 PROMOTE，激活 H-16R1 FREEFORM_SCORER_CONTRADICTION_GUARD_REPAIR |
| 2026-08-11-r31 | 2026-08-11 | H-16R1 冻结修复独立复核：6/6 contradiction negatives、4/4 benign controls、8-case oracle 8/8、22 regressions 均通过；但 8 个未冻结同义 semantic probes 仅 5/8 正确，出现 wrong-figure / actual-figure / numeric-incorrect 三个明确 false accept | B-06 保持 ACTIVE；deterministic anchor scorer 仅保留为窄范围辅助信号，不晋级 unrestricted binary semantic judge；停止 cue/regex 微修 | H-16R1 PASS/NOT_APPLICABLE + HOLD_SCORER；激活 H-17 E4_FREEFORM_SCORING_POLICY_DESIGN，先定义 auto-score / abstain / semantic-review 边界，不做 paid E4 |
| 2026-08-11-r32 | 2026-08-11 | Human 指定 Evaluator 直接基于项目 evidence + 公开参考制定 H-17 policy；最终冻结 `ADOPT_LAYERED_SEMANTIC_REVIEW`：Option A reject，Option B 作为 L1 triage，Option C 作为完整 E4 policy；deterministic 仅对结构化可证明结果有最终权限，其余必须 semantic review；model judge 在 meta-eval 前 shadow-only | B-06 保持 ACTIVE，但架构选择已关闭；当前第一缺口变为 policy 是否能在现有 evidence 上稳定路由且不产生 false AUTO_CORRECT | H-17 evaluator-design 收口；激活 H-18 SCORING_POLICY_SHADOW_REPLAY，零 Provider，先回放 8 real outputs 与 adversarial guardrails |
| 2026-08-11-r33 | 2026-08-11 | H-18 独立复核：固定 8 real outputs 得到 6 AUTO_INCORRECT + 2 REVIEW_REQUIRED，14 个 complex probes 与 4 个 benign controls 均安全 abstain；但额外 4 个金融/合同 `cannot` 语义控制中 3 个被 false AUTO_INCORRECT，根因是 router 复用 inherited scorer 的宽泛 bare-`cannot` refusal signal | B-06 保持 ACTIVE；layered policy 不变，但 L1 自动判错权限尚不可信，禁止进入 L2 judge calibration | H-18 REJECTED/NOT_APPLICABLE；激活 H-18R1 REFUSAL_AUTHORITY_REPAIR，只收紧 answerability refusal，其他边界不动 |
| 2026-08-12-r34 | 2026-08-12 | H-18R1 独立 L3 最终 7/7 PASS：4/4 普通金融/合同 `cannot` 安全 abstain，6/6 明确 answerability-refusal 为 AUTO_INCORRECT，real-8 保持 6+2；首次 L3 的 import-path 环境缺陷经显式 A1 amendment 修正并保留失败证据 | B-06 保持 ACTIVE；L1 refusal authority 前置阻断关闭，不再继续 deterministic refusal/cue 微调；下一缺口转为 L2 semantic judge calibration contract | H-18R1 PASS/NOT_APPLICABLE；激活 H-19 L2_JUDGE_CALIBRATION_DESIGN，零 Provider，先冻结校准输入/输出、real-vs-trust-test 分层、meta-eval 指标与晋级条件 |
| 2026-08-12-r35 | 2026-08-12 | H-19 独立 L3 7/7 PASS：L2 I/O authority、三值输出、REAL_CALIBRATION/TRUST_TEST 分轨、9 个核心 meta-eval 指标、shadow-only 与 independent-real-slice 晋级边界均冻结；zero Provider | B-06 保持 ACTIVE；“如何校准第二层尺子”的规则缺口关闭，下一缺口是把规则变成 provider-agnostic、可机械复现的离线 harness | H-19 PASS/NOT_APPLICABLE；激活 H-20 L2_JUDGE_OFFLINE_HARNESS，优先复用现有 EvaluationCase/Observation/Result，仍零 Provider |
| 2026-08-12-r36 | 2026-08-12 | H-20 独立 L3 7/7 PASS：离线 harness 复用 EvaluationCase/Observation，三值 schema、REAL/TRUST 分轨、固定算术、malformed/missing/duplicate/mismatch fail-closed 均独立复现；zero Provider | B-06 保持 ACTIVE；离线记分基础设施缺口关闭，下一缺口转为真实 model-judge shadow run 的 prompt/rubric/manifest/budget/checkpoint/authorization preflight | H-20 PASS/NOT_APPLICABLE；激活 H-21 L2_JUDGE_SHADOW_PREFLIGHT，仍零 Provider，形成明确 Human/API authorization gate |
| 2026-08-12-r37 | 2026-08-12 | H-21 独立 L3 7/7 PASS：prompt/rubric/schema、8-output REAL source identity、5 类 TRUST families、零调用预算、checkpoint/resume/provider-ledger 与 evaluator-evidence-only 边界均通过；Provider/model 仍 UNSELECTED | B-06 保持 ACTIVE；shadow-run preflight 缺口关闭，下一步唯一阻断变为 Human 对 bounded external model/API shadow-judge calls 的明确授权 | H-21 PASS/NOT_APPLICABLE；激活 H-22 BOUNDED_SHADOW_JUDGE_RUN，但停在 authorization gate，未授权前不分发 Executor 真实调用包 |
| 2026-08-12-r38 | 2026-08-12 | Human 授权 H-22，当前 GPT-5.6 Sol 会话完成 8 REAL + 28 TRUST 的 session-mediated shadow smoke；L2/L3 7/7 PASS，REAL agreement=8/8、TRUST=28/28、0 abstain；但同一会话已看过 real-8 历史 6错2对结论，明确记录为 CONTEXT_CONTAMINATED | B-06 保持 ACTIVE；rubric/protocol 执行已 smoke-tested，但独立 judge 泛化仍未证明，禁止 authority promotion | H-22 session-smoke PASS/NOT_APPLICABLE；激活 H-23 INDEPENDENT_REAL_SLICE_DISCOVERY，先零 Provider 查找 current 8 之外真实输出切片 |
| 2026-08-12-r39 | 2026-08-12 | H-23 独立 L3 5/5 PASS：repository-wide discovery 找到 A 历史 prediction ∩ Local Gold v0.2 的完整 9-case 交集，source/reference Hash、9/9 Gold authority、current8 overlap=0、8 exact/1 mismatch 均独立复现 | B-06 保持 ACTIVE；该 9-case 全为 structured multi/MCQ，L1 exact/set 已足以裁决，只登记为 INDEPENDENT_STRUCTURED_CONTROL_SLICE，不消耗下一次 L2 semantic-judge 实验 | H-23 PASS/NOT_APPLICABLE；激活 H-24 INDEPENDENT_FREEFORM_SLICE_FREEZE，零 Provider 机械冻结 current8 之外完整 FinanceBench document cohort |
| 2026-08-12-r40 | 2026-08-12 | H-24 独立 L3 6/6 PASS：排除 current8 三个 3M 文档后，按 qa_count desc + doc_name asc 机械选择 `AMD_2022_10K` 完整 7-case cohort；7/7 question/reference 非空、0 choice-letter-only、唯一 PDF path、7 个 source-line Hash exact、0 prediction/Judge/API | B-06 保持 ACTIVE；独立 freeform 试卷已经冻结，但还没有 FinDocQA product prediction，因此仍不能做 fresh-context Judge 泛化测量 | H-24 PASS/NOT_APPLICABLE；激活 H-25 PRODUCT_GENERATION_AUTH，停在 Human/API 授权门，前次 H-22 Judge 授权不可复用 |
| 2026-08-12-r41 | 2026-08-12 | Human 授权 H-25 后完成 AMD 7-case bounded product generation；独立 L3 8/8 PASS，7/7 each-one-attempt，Provider COMPLETED/ERROR=3/4，prediction=3/7，blocked=6/7，final accepted=1/7，Gold leak=0，Judge 未运行 | 新建 B-07 并设为 ACTIVE：最大单一失败族 Provider ERROR=4/7 达到通用问题门槛；B-06 降为 SECONDARY_BLOCKED_BY_B-07。4 个 ERROR ledger 均缺 failure_category/error_type/http_status，不能盲目 retry/换模型 | H-25 PASS/NOT_APPLICABLE；激活 H-26 PROVIDER_ERROR_LEDGER_DIAGNOSTICS_REPAIR，零 Provider，只补 sanitized failure diagnostics，不改变调用行为 |
| 2026-08-13-r42 | 2026-08-13 | H-26 经 baseline identity amendment 后 amended L2 7/7 PASS、独立 L3 7/7 PASS；synthetic quota/capability/HTTP/timeout/connection/JSON/response-shape failures 均可落 sanitized diagnostics，success/provider budget/circuit-breaker regressions 通过，零 Provider/API | B-07 保持 ACTIVE；诊断缺口关闭但真实 4-case 根因仍未知。明确把 H-26 作为 observability 止步线，不继续扩展异常 taxonomy；下一步只复现历史 4 个 ERROR case | H-26 PASS/NOT_APPLICABLE；激活 H-27 PROVIDER_FAILURE_REAL_CANARY_AUTH，固定 4 case、同 Provider/model、总调用最多 4，当前停在 Human/API authorization gate |
| 2026-08-13-r43 | 2026-08-13 | H-27 bounded real canary：历史 4 个 Provider ERROR 重跑后 3 COMPLETED + 1 INVALID_RESPONSE，0 transport retry，物理调用 4，L2/L3 均 8/8 PASS，冻结决策 `NO_COMMON_FAMILY`；00222 的 Provider terminal 完整但 product observation 因 task-local checkpoint bug unavailable，明确排除后续 product/Judge 统计 | B-07 降为 SECONDARY_NO_SINGLE_COMMON_FAMILY，停止 Provider subtype 微修；AMD independent cohort 现有 5 个可用 freeform prediction，B-06 恢复 ACTIVE；B-03 保持 REOPEN_CANDIDATE | H-27 PASS/NOT_APPLICABLE；激活 H-28 INDEPENDENT_FREEFORM_JUDGE_5CASE_AUTH，固定 5 个可用 prediction，等待新的 bounded fresh-context Judge/model API 授权 |
| 2026-08-13-r44 | 2026-08-13 | H-27 persistence amendment 纠正 r43：00917/01279 仅保存 `prediction_present=true`，实际 prediction text 未持久化，不能进入 Judge；Judge-ready slice 从名义 5-case 收敛为 H-25 的 00995/01198/00757 三个 persisted freeform outputs。Human 已授权原 5-case 范围内 bounded external Judge，并允许纯网络失败重试 | B-06 保持 ACTIVE；不为凑 5 题重新生成产品答案。H-19 明确禁止任意 sample-count threshold，先用真实可审计 3-case independent slice 测 fresh-context Judge；B-07 继续 SECONDARY_NO_SINGLE_COMMON_FAMILY | H-28 纠正为 INDEPENDENT_FREEFORM_JUDGE_3CASE；冻结 ModelScope `Qwen/Qwen3.5-122B-A10B`，3 primary + 最多 3 transport-only retries，准备路由 Executor |
| 2026-08-13-r45 | 2026-08-13 | H-28 完成：3 primary、0 retry、3/3 structured Judge records；Executor L2 8/8 PASS，Evaluator L3 8/8 PASS。Evaluator 独立参考标签三题均为 INCORRECT，外部 fresh-context Judge 也均判 INCORRECT，agreement=1.0、false_accept=0、abstain=0 | B-06 保持 ACTIVE；当前证据只证明 known-wrong 拒绝方向，缺 independent known-correct freeform 样本，不能用空覆盖的 false_reject=0 推动 Judge authority。KDD Cup champion / Knowhere 吸收作为规划层登记，不改变当前瓶颈排序 | H-28 PASS/NOT_APPLICABLE；激活 H-29 INDEPENDENT_KNOWN_CORRECT_FREEFORM_DISCOVERY，先零 Provider 从已有 artifacts 找可审计 known-correct freeform candidate；若不存在再进入新的 bounded product-generation authorization gate |
| 2026-08-13-r46 | 2026-08-13 | H-29 全仓零 API discovery：73 JSONL、4 个 `predicted_answers` artifacts、23 条 FinanceBench prediction rows；排除 current8 16 rows + H-28 3 rows，并识别 H-27 两条 bool-only observation 后，结构合格剩余 candidate=0。Executor L2 7/7、Evaluator L3 7/7 PASS | B-06 保持 ACTIVE；停止继续翻旧 persisted outputs。下一步先增加独立文档族多样性，机械冻结第二个完整 FinanceBench freeform cohort；冠军 Runtime/Knowhere 研究继续作为规划层，不抢当前瓶颈 | H-29 PASS/NOT_APPLICABLE；激活 H-30 SECOND_INDEPENDENT_FREEFORM_COHORT_FREEZE，按 `qa_count desc + doc_name asc` 排除已使用 3M/AMD 文档后选择 `AMERICANEXPRESS_2022_10K`，零 Provider/API |
| 2026-08-13-r47 | 2026-08-13 | H-30 完成：`AMERICANEXPRESS_2022_10K` 完整 7-case cohort 按 `qa_count desc + doc_name asc` 无偏冻结；Executor L2 6/6、Evaluator L3 6/6 PASS，Evaluator 独立重算确认 AMEX/BOEING 7/7 tie 与 7 个 source-order qid。官方 FinanceBench commit=`cc39aeb...`，AMEX PDF blob=`da116dc...`，Provider/API/Judge/prediction=0 | B-06 保持 ACTIVE；第二独立文档族输入与唯一 PDF identity 已锁死，下一缺口是产生新的 persisted product outputs，再补 known-correct/known-wrong 两方向 Judge 证据；不回到 Retrieval 微修 | H-30 PASS/NOT_APPLICABLE；激活 H-31 AMEX_7CASE_PRODUCT_GENERATION_AUTH，新的 Human/API 授权缺失，历史 H-25/H-27/H-28 authority 不继承 |
| 2026-08-17-r48 | 2026-08-17 | H-31 已完成并独立复核：AMEX 7-case 真实 product generation 7/7 Provider COMPLETED、7/7 persisted predictions、每题恰好 1 次、retry/fallback=0、Gold leakage=0、Judge 未运行；Executor L2 8/8、Evaluator L3 8/8 PASS | B-06 保持 ACTIVE；“缺新产品答案”已关闭，第一缺口改为为这 7 个输出建立 independent reference labels，先绑定官方 reference/evidence，不直接再次跑 Judge，也不回到 Retrieval 微修 | H-31 PASS/NOT_APPLICABLE；激活 H-32 AMEX_7CASE_REFERENCE_DOSSIER，offline、零 Provider/API/Judge，Executor 仅机械绑定，不判 CORRECT/INCORRECT |
| 2026-08-17-r49 | 2026-08-17 | H-32 经 A1 nullable-justification 修订后 Executor L2 8/8、Evaluator L3 8/8 PASS；7-row dossier 与 FinanceBench source-exact，Evaluator 独立 semantic labels = 0/7 CORRECT、7/7 INCORRECT；至少 6/7 产品答案表现为“证据不足/无法确认”，但 H-31 未保存逐题 retrieval trace | B-06 仍 ACTIVE 但暂停继续扩第三文档族/Judge；B-03 保持 REOPEN_CANDIDATE，先用 H-33 零 API 重放同一 lexical retriever 做官方 evidence TopK 归因。只有 >=4 独立 miss 才升 B-03 ACTIVE | H-32 PASS/NOT_APPLICABLE；激活 H-33 AMEX_7CASE_RETRIEVAL_ATTRIBUTION，零 Provider/API/Judge/product-generation，禁止任何产品 patch |
| 2026-08-17-r50 | 2026-08-17 | H-33 严格复用 H-31 canonical lexical Retrieval：AMEX 官方 evidence Top5 hit=1/7、miss=6/7，Executor L2 8/8、Evaluator L3 8/8 PASS，Gold leak/Provider/API/Judge/product change=0；Evaluator stopword-filter 反事实仍 1/7、恢复 0 | B-03 从 REOPEN_CANDIDATE 升为 ACTIVE；B-06 降为 SECONDARY_BLOCKED_BY_B03。当前只证明 Retrieval 是主瓶颈，尚无 exactly-one-variable 修复证据，禁止直接调 TopK/embedding/reranker/scoring/alias | H-33 PASS/NOT_APPLICABLE；激活 H-34 AMEX_6MISS_RETRIEVAL_LOSS_FAMILY_DIAGNOSIS，零 API/零产品修改，先判断是否有同一 query/ranking mechanism 覆盖 >=4 case |
| 2026-08-17-r51 | 2026-08-17 | H-34 在 Executor 开始前因项目级反思被 SUPERSEDED_PRE_EXECUTION：FinanceBench E4 当前使用的是轻量 `canonical_lexical` shadow baseline，而项目默认已有 `lexical_hybrid`，研究层还登记 BM25/semantic/query-planning 等路线。继续只解剖 AMEX 6 个 miss 会过早微调影子基线 | B-03 保持 ACTIVE；先做检索路线横向实验而非逐题修词。冻结 15 QA / 4 docs：原 8 QA + AMEX 7 QA；统一 Top5/evidence-page 指标 | 激活 H-35 FINANCEBENCH_15CASE_RETRIEVAL_BAKEOFF：A canonical_lexical / B lexical_hybrid / C task-local BM25；候选必须保住 baseline 3/3 hit 且额外恢复 >=4/12 miss 才有产品实验资格 |

<!-- r52 evaluator update -->
H-35 independent L3: PASS. `canonical_lexical=3/15`, `lexical_hybrid=1/15`, corrected `BM25=3/15`; both candidates recovered `0/12` baseline misses. Active routing moves to H-36 `FINANCEBENCH_15CASE_QUERY_PLAN_RETRIEVAL_PROBE`; if it fails the same `preserve 3/3 + recover >=4/12 + regress 0` gate, move to bounded semantic embedding retrieval rather than lexical/query-term micro-tuning.

<!-- r53 evaluator update -->
Human/Task Owner 于 `2026-08-18` 要求避免把 semantic retrieval(语义检索)、query planning(查询规划)、Know-where(知道去哪里找)拆成三个连续小包。原 QueryPlanBuilder-only H-36 在执行前 `SUPERSEDED_PRE_EXECUTION`；H-36 改为 `FINANCEBENCH_15CASE_NEXTGEN_RETRIEVAL_BAKEOFF`，三 lane 同基线横向选路，但严格隔离机制、不做融合。

<!-- r54 evaluator design-freeze update -->
H-36 在 Executor 开始前增加 Evaluator Design Freeze：项目已有 semantic retrieval 接口与 QueryPlanBuilder，但没有现成 DocumentMemory/navigation runtime。`docs/reference/B03下一代检索三路线实验设计.md` 已冻结三条最小实现；Know-where 本轮仅测 `DocumentMemoryLite + Structure Probe + Local Evidence Search`，结构供给不足时必须 `BLOCKED_BY_STRUCTURE_SUPPLY`，不得临时重写 Parser 或由 Executor 自定义框架。

<!-- r55 evaluator scope update -->
H-36 暂停 semantic retrieval：即便环境已有 embedding API，公平实验仍需先完成冻结文档的 page-level embedding、向量索引、模型/维度/批次/成本控制。当前先比较 Query Planning 与 Know-where Lite；两条都不达标后再决定是否单独开启语义向量实验。

<!-- r56 evaluator update -->
H-36 L3 PASS：Query Planning 无可测增益；Know-where Lite 因结构供给不足未能有效测量。独立检查显示 3M_2022_10K 的平面 Markdown 实际保留大量 SEC PART/ITEM 标记，因此 H-37 优先做现有文本结构恢复并在同一包重放 Know-where，而不是直接重跑 Parser 或启动 semantic retrieval。

<!-- r57 evaluator update -->
H-37 L3 PASS + NO_MEASURABLE_GAIN：结构供给已恢复，但 Know-where Lite 仅 1/15、恢复 0/12、退化 2/3。H-38 转为 12-miss mechanism-level failure attribution(机制级失败归因)，先确认是否存在 >=4 独立 case 的通用失败族，再决定下一条能力路线；semantic retrieval 继续延期。

<!-- r58 evaluator update -->
H-38 L3 PASS：12 miss 收敛为两个 5-case 通用族。优先选择 5/5 HIGH 的 TABLE_ROW_LOCALIZATION；H-39 只验证 table/header/row retrieval unit 是否能恢复 >=4/5 表格族并保住 baseline 3/3。Derived-formula 与 semantic 路线继续保留为后续候选。

<!-- r59 evaluator update -->
H-39 L3 PASS + NO_MEASURABLE_GAIN：table/header/row retrieval unit 在冻结 15-case 上仍为 3/15，TABLE_ROW_LOCALIZATION 0/5 恢复、controls 3/3 保留。结合 H-38 两个 5-case 族，下一假设上移到共同前置层 Evidence Target Planning(证据目标规划)：先把问题转换为“需要哪些财务事实 / 什么操作 / 什么证据形态”，再决定后续 retrieval/binding；不直接建设完整公式 Solver。H-40 冻结 10-case（5 table + 5 derived），门槛为 >=8/10 TARGET_COMPLETE 且每族 >=4/5，零 API/Gold runtime rule/产品改动。

<!-- r60 evaluator update -->
H-40 L2/L3 8/8 PASS。Evaluator 未继承 Executor 从 4/10 调整到 9/10 的 checker 结论，而是逐题独立复核 EvidenceTargetPlan：最终仍为 9/10 TARGET_COMPLETE，但标签纠正为 TABLE_ROW_LOCALIZATION=5/5、DERIVED_FORMULA_EVIDENCE=4/5；03029 升为 COMPLETE，01351 因缺 FY2022 税率输入降为 PARTIAL。H-40 只证明规划表示成立，不证明 Retrieval 改善。H-41 因此冻结为 EvidenceTargetPlan-guided lexical retrieval 同基线能力实验：10 个历史 miss + 3 controls，要求 controls 3/3、恢复 >=4/10 且两族各 >=2/5，零 semantic/reranker/Know-where/QueryPlanBuilder/Parser/Solver/API/Gold-runtime-rule。

<!-- r61 evaluator update -->
H-41 独立复核 `PASS + IMPROVED`：10 个历史 miss 恢复 `6/10`，两个家族分别 `4/5` 与 `2/5`，controls `3/3`。随后 H-42 将单一主变量上移到 generic EvidenceTargetPlan generation(通用证据目标计划生成)，在 untouched AMD 7-case 上测试 operation-first + structure-aware planner(操作优先 + 结构感知规划器)，冻结 H-41 fusion 不变。

<!-- r62 evaluator update -->
H-42 执行结果中的 AMD `0/7 → 0/7` 被 Evaluator 复核发现使用了错误 FinanceBench page authority(页码权威口径)。项目 H-33 已冻结 `evidence_page_num` zero-indexed → canonical page one-indexed 的 `+1` 映射。独立 evidence-fix L3 `PASS` 后，保持 H-42 Top5 traces 不变的正确重评分为 baseline `2/7`、candidate `4/7`、5 个真实 baseline miss 中恢复 `2`、净 Top5 `+2`。H-42 正式合同因原 denominator(分母)失效保持 `REJECTED / INCONCLUSIVE`，但方向未被证伪。B-03 继续 ACTIVE；激活 H-43 fresh-cohort generalization(新样本泛化)，先冻结 `BOEING_2022_10K` 7-case 的 canonical page supply、正确 authority 与 unchanged lexical baseline，再决定是否进入新的 planner capability experiment。

<!-- r63 evaluator update -->
H-43 fresh Boeing capability experiment(全新 Boeing 能力实验)由 Evaluator 独立重算并跑 L3：baseline `0/7`、candidate `0/7`、recovered `0/7`，controls `3/3`、regression `0`；10/10 plans 与边界检查均成立，唯一失败是预声明 recovery gate。正式 verdict=`REJECTED`、project impact=`NO_MEASURABLE_GAIN`、continuation=`SWITCH`。停止 Boeing/alias/metric-registry 微调；激活 H-44 pure page-level Semantic Retrieval(纯页级语义检索)，回到 H-35 既有 15-case / 4-doc cohort 与原 `preserve 3/3 + recover >=4/12 + regress 0` 门槛。仓库已有 embedding index/retriever 与 SiliconFlow adapter，H-44 等待独立 bounded API-call authorization。

<!-- r64 evaluator authorization update -->
Human/Task Owner 于 `2026-09-05` 明确授权 H-44 最多 `80` 次 HTTP 尝试的 SiliconFlow `/v1/embeddings` 实验；成功 embedding 调用上限 `64`、单逻辑单元最多 `2` 次、并发 `1`。授权仅覆盖 `Qwen/Qwen3-Embedding-8B` embedding，不继承到生成/reranker/Judge/Solver。H-44 合同因此正式冻结并路由 `CONTRACT_FROZEN / Executor`。

<!-- r65 evaluator update -->
H-44 pure page-level Semantic Retrieval(纯页级语义检索)由 Evaluator 完成验证层机械修复后独立 L3 `8/8 PASS`，并额外直接重算结果/Gold 隔离/API 账本：canonical lexical `3/15`→semantic `8/15`，12 个 baseline miss 恢复 `5`，protected hits `3/3`，regression `0`，正式 verdict=`PASS`、project impact=`IMPROVED`、continuation=`CONTINUE`。由于 H-41 历史集提升曾在 H-43 fresh Boeing 失效，本轮不直接 productize semantic；激活 H-45 fresh semantic generalization staging，按 H-30 既有 `qa_count desc + doc_name asc` 规则选下一 untouched `PEPSICO_2022_10K` 5-case，先执行零 API evidence/page-authority/lexical-baseline freeze；baseline misses `>=3` 才进入后续全 5-case semantic capability experiment。

<!-- r66 evaluator packaging update -->
Human/Task Owner 要求减少频繁评估/审查。H-45 因此不再逐文档拆包：`PEPSICO5` 在执行前 `SUPERSEDED_PRE_EXECUTION`，改为一个 v2.2 单任务、多 work-unit 的 `FDQA-B03-FRESH3-SEMANTIC-GENERALIZATION-READINESS-WAVE-V1`。冻结 5 个候选队列，目标一次完成 3 个 fresh family 的 PDF/page/authority/unchanged lexical baseline/readiness aggregation，最多检查 5 个候选；只有 source/evidence-supply 硬阻断可 fallback，禁止按 baseline 难度换样本。本包 model/API=0，最后统一产出 whole-document eligibility 与 future H-44-style embedding 调用预算，之后仅需一次 Evaluator review，再决定一个 bulk semantic wave。

<!-- r67 evaluator update -->
H-45 Fresh3 readiness wave 已通过 L2/L3 `9/9` 并正式 `PASS / NOT_APPLICABLE / CONTINUE`。PepsiCo `0/5`、Amcor `0/4`、Ulta `4/4`。H-46 冻结为 Fresh13 bulk semantic generalization：9 个 recovery cases + 4 个 protected controls，复用 H-44 pure page embedding cosine ranking；资格门槛为 total recover `>=3/9` + PepsiCo `>=1` + Amcor `>=1` + Ulta controls `4/4` + regression `0`。项目同时明确：原比赛路线只是参考基线，embedding semantic ranking 是基于现代 LLM/RAG 架构主动加入、必须靠项目证据证明价值的扩展；learned reranker 暂不加入 H-46。

<!-- r68 evaluator update -->
H-46 Fresh13 pure semantic 已正式 `PASS / REGRESSED / SWITCH`，Evaluator L3 `8/8 PASS`：lexical `4/13`→semantic `6/13`，恢复 `5/9`，但 Ulta protected controls `4/4→1/4`，产生 3 个 regression，因此 pure semantic replacement 不得产品化。Evaluator 零 API 事后诊断发现 lexical+semantic 等权 RRF60 在同一 Fresh13 为 `9/13`、恢复 `5/9`、regression 0，但只作为 hypothesis generator。H-47 冻结 fresh fusion holdout readiness：从 H-45 原机械队列 rank 4 AES / rank 5 BestBuy 开始，必要时依序扩 rank 6–8；只做 lexical baseline，要求 misses>=4 且 hits>=3，model/API/fusion/reranker=0。Human 同时补充原比赛禁止 embedding/reranking model 的历史赛制约束，当前按人工提供事实记录，待后续补官方规则原文。

<!-- r69 evaluator update -->
H-47 经 Evaluator 复核正式 `PASS / NOT_APPLICABLE / CONTINUE`。原 L2 VP-02 的 blank-page 零容忍属于 checker 过约束，已用 evaluator amendment 修正；amended L2/L3 均 `8/8 PASS`，实验事实不变。H-47 机械扩展 rank 4–8 后得到 15 cases / 2 lexical hit / 13 miss，恢复空间充足但 protected-control 仍差 1 个，故禁止把门槛从 3 降到 2。H-48 冻结 rank 9–14 继续 zero-API lexical readiness extension，累计命中达到 3 后按最早完整文档前缀停止；仍不运行 semantic/RRF/reranker。

<!-- r70 evaluator update -->
H-48 经 Evaluator 正式复核为 `PASS / NOT_APPLICABLE / CONTINUE`，L2/L3 均 `8/8 PASS`。rank 9 强生 8-K 为 3/3 lexical hit，使 fresh pool 达到 18 cases / 5 protected hit / 13 recovery miss，并按 earliest full-document prefix 停止。H-49 已冻结为 Fresh18 lexical+semantic equal-weight RRF60 generalization capability experiment：资格门为 recovered>=4/13、至少 2 个 recovery families、protected=5/5、regression=0；pure semantic 同批保留作对照。未来 embedding 预算 68 successful / 85 physical，当前未授权。

<!-- r71 evaluator update -->
H-49 经 Evaluator 正式收口为 `PASS / IMPROVED / SWITCH`。Fresh18 lexical=`5/18`，pure semantic=`11/18`，equal-weight RRF60=`9/18`；RRF 达到预声明 `4/13` recovery / >=2 families / protected 5/5 / regression 0，但并未优于 pure semantic。结合 H-44 protected 3/3、H-46 1/4、H-49 5/5 的跨 cohort 差异，H-50 转为 zero-API lane-arbitration diagnostic，检查 Gold-free agreement/confidence signals 是否足以形成下一 fresh hypothesis；不继续事后调 RRF 权重。

<!-- r72 evaluator update -->
H-50 经 Evaluator 独立 L3 `8/8 PASS`，正式 `PASS / NOT_APPLICABLE / CONTINUE`。46-case 诊断输出 `CANDIDATE_SIGNAL_FOUND`：`ZERO-OVERLAP-ESCALATE` 触发 17/46，跨 H44/H46/H49 三批，观察结果为 7 `SEMANTIC_ONLY` + 10 `BOTH_MISS`、0 `BOTH_HIT`；`TOP1-CONSENSUS` 仅 4/46，跨 H46/H49，4/4 `BOTH_HIT`。两者仍全部是 `HYPOTHESIS_ONLY`，产品路由未改变。下一轮不继续 RRF/阈值微调，优先 H-51 zero-API fresh arbitration holdout readiness：复用 H-48 预声明但尚未执行的 rank 10–14 queue，只做 unchanged lexical Top5 和未来调用预算；若无法得到 misses>=4 且 hits>=3 的双侧 headroom，则停止并转向 Exploration Runtime。

<!-- r73 evaluator update -->
H-51 经 Evaluator 独立 L3 `8/8 PASS`，正式 `PASS / NOT_APPLICABLE / SWITCH`。固定 rank 10–14 的 14 个 untouched FinanceBench case 得到 lexical `1/14 hit`、`13/14 miss`，因此 `two_sided_headroom_ready=false`，按冻结合同停在 `BLOCKED_INSUFFICIENT_TWO_SIDED_HEADROOM`。该结果不证伪 H50-A，但明确否决“继续向后找题凑 3 个 protected hit 再验证仲裁”的路径，因为那会形成 post-outcome sample construction(看结果后构样)。B-03 继续 ACTIVE，但研究对象上移到 Exploration Runtime。激活 H-52 `FDQA-B03-EXPLORATION-RUNTIME-SHADOW-DIAGNOSTIC-V1`：先复用 H-51 的 13 个 observed miss 做零 API、零产品改动的 hypothesis-generation(假设生成)，只有形成 >=4 独立 case 的通用 Gold-free exploration action family 后，才消费新的 untouched cohort 做 capability validation。

<!-- r74 evaluator standard update -->
Human/Task Owner 与 Evaluator 重新校准“方向形成”门槛：不再把固定 `>=4 case` 当作所有 hypothesis-generation(假设生成)任务的硬门槛。新标准为 `>=3 independent cases + >=2 document families + same mechanism/principal change + falsifiable fresh test`；1 个是个例、2 个是信号、3 个但单文档只算 `LOCAL_CLUSTER(局部簇)`。H-51 的双侧 holdout readiness(留出集就绪性)门槛不属于此标准，因此 H-51 verdict 不变。H-52 在执行前原包直接 amended(修订)，不另开碎包：候选方向达到新门后即停止旧 cohort 继续凑数，转向后续 untouched cohort 做 capability validation。

<!-- r75 evaluator update -->
H-52 经 Evaluator 正式复核为 `PASS / NOT_APPLICABLE / CONTINUE`，L2/L3 均 `8/8 PASS`。`bounded_neighbor_expand_r2` 在 13 个 H-51 lexical miss 中恢复 `3` 个、跨 `2` 个文档族，达到候选方向最低门槛；Evaluator 额外敏感性审计显示 radius=1 为 `0`、radius=2/3 为 `3`、radius=4 为 `5`，因此该信号存在但半径敏感，禁止继续在旧 cohort 调半径。激活 H-53 Fresh12：机械冻结此前 B-03 未暴露的下一 6 个完整 FinanceBench 文档 / 12 题，固定 radius=2 做零 API fresh generalization(新样本泛化)；若恢复 `<3` 或跨文档族 `<2`，则停止该机制，不扩样、不调半径。

<!-- r76 evaluator update -->
H-53 经 Evaluator 独立 L3 `8/8 PASS`，任务本身 `PASS`，page-level reach 从 lexical `0/12` 到 fixed `±2` neighbor `2/12`，属于可测但不足的改善；因未达到 frozen `>=3` recovery gate 且平均每题新增 `12.75` 页，fresh decision=`NOT_QUALIFIED`，continuation=`SWITCH`。正式停止邻页半径方向。复合评估将 B-03 上移为 premature Top5 contraction / evidence-workspace composition(过早 Top5 收敛 / 证据工作区组合)瓶颈：H-50 46-case 显示 lexical=`12/46`、semantic=`25/46`、union oracle=`28/46`，存在 `16 SEMANTIC_ONLY + 3 LEXICAL_ONLY`，同时仍有 `18 BOTH_MISS`。激活 H-54 zero-API architecture readiness：验证 bounded multi-lane Evidence Workspace + Evidence Sufficiency + late contraction 是否能形成一个可证伪、单一主变量的新能力实验。

<!-- r77 evaluator update -->
H-54 经复合检查：L2/L3 均 `8/8 PASS`，其 lane complementarity(通道互补)数据结论保留，但 Evaluator 否决 `CANDIDATE_READY`，正式 verdict=`REJECTED / NOT_READY_INTERFACE_CONTRACT_GAP / SWITCH`。原因：`CanonicalLexicalEvidenceRetriever` 输出 page-scoped `EvidenceCandidate`，而 `build_financial_report_option_evidence(question, structured_root)` / `FinancialContext` / `FinancialEvidenceCompletionAdapter` 当前均从完整结构化文档建立 ledger，没有 page-workspace 输入契约；若直接 fresh 验证，workspace 可能只做旁路统计而下游仍读取全量证据，无法隔离“late contraction”主变量。激活 H-55 zero-API interface compatibility：只冻结统一 page scope 如何同时约束 initial + completion evidence，以及 unknown page fail-closed；通过后才允许下一 fresh capability experiment。

<!-- r78 evaluator update -->
H-55 经 Evaluator 独立 L3 `8/8 PASS` 并完成代码级复核，正式 verdict=`PASS / INTERFACE_READY / CONTINUE`。`EvidenceCandidate` 的 one-based canonical page 与 `FinancialFact.source_page` 的 zero-based MinerU page_idx 可通过 `canonical_physical_page = source_page + 1` 确定性对齐；同一 scope 必须同时约束 initial ledger/narrative 与 completion ledger/corrective retrieval，未知页/越界页/身份冲突必须 fail closed。激活 H-56 `repair`：本地实现并贯穿 `EvidenceWorkspaceScope`，用离线反例证明无 scope 越界和 unscoped regression；不调用 semantic/API，不做 fresh 能力结论。

<!-- r79 evaluator update -->
H-56 模块实现 L2/L3 均 `8/8 PASS`，但独立代码复核发现真实 `production_typed_evidence → derived_claim_router → financial_report_claims` 非-TF 路由没有传播 `workspace_scope`。模块能力保留但任务正式 `REJECTED / IMPROVED_BUT_INCOMPLETE / REPAIR_REQUIRED`；B-03 红点缩到 product route reachability(产品路由可达性)，激活 H-56R1 窄修复。

<!-- r80 evaluator update -->
H-56R1 Executor L2=`8/8 PASS`；Evaluator 独立 L3=`8/8 PASS`，product-route scoped tests `2 passed`、相关 regression `51 passed`，范围外金融事实无法形成 trusted evidence，范围内与 unscoped 行为保持，H-56 核心 Hash 不变。正式 verdict=`PASS / NOT_APPLICABLE / CONTINUE`。B-03 的 route reachability blocker 关闭，第一红点前移到 fresh workspace generalization readiness；激活 H-57 zero-API readiness，冻结未触碰 whole-document cohort、page authority、lexical baseline、future semantic budget 与单变量 workspace experiment。

<!-- r81 evaluator update -->
H-57 按 outcome-blind 规则冻结 3 个全新 whole-document families / 6 cases，source/PDF/page authority 与 lexical 双重重放均稳定，但 lexical=`4 hit / 2 miss`，且 2 个 miss 只来自 1 个文档族；冻结 `lexical_misses>=3` mandatory gate 失败，L2=`7/8`，无 L3，正式 verdict=`REJECTED / NOT_APPLICABLE / CONTINUE`，reason=`BLOCKED_BY_READINESS_HEADROOM`。不降低门槛、不扩已观察 cohort。排除 H-57 后仍有 55 families / 58 questions 未暴露；激活 H-58 最后一次 zero-API Fresh12 two-sided readiness wave：按 source metadata 固定排序取最短完整文档前缀直到 >=12 cases，要求 misses>=3、miss families>=2、hits>=3。若仍失败，停止滚动找题并重排 B-03 vs B-06/B-05。

<!-- r82 evaluator update -->
H-58 Executor L2=`8/8 PASS`；Evaluator 独立 L3=`8/8 PASS`。最终 fresh cohort 为 9 families / 12 cases，lexical=`4 hit / 8 miss`，8 miss 跨 7 families，满足 `misses>=3 + miss families>=2 + hits>=3` two-sided readiness。首次 probe 的 exposure-scanner 字段缺口已独立复核为不改变最终 selection 的过程偏差：合同事前固定 >=12-case whole-family prefix，首次看到的 3 families 仅 6 cases，修正后的 exposed set + source metadata 唯一推出最终 9-family prefix。正式 verdict=`PASS / NOT_APPLICABLE / CONTINUE`。B-03 保持第一瓶颈；H-59 fresh capability experiment 已冻结为 `early RRF60 Top5` vs `bounded union workspace(max10)`，Primary 测 verification-boundary evidence reach，Secondary 测 deterministic trusted reach。H-59 需要 semantic embedding 外部调用，当前 `authorization_api_call=false`，保守预算上限 80 successful / 240 physical attempts，等待 Human 明确授权。

<!-- r83 evaluator/provider update -->
Human 授权 H-59 embedding-only 调用后，Evaluator 完成 SiliconFlow / ModelScope / HF / Cloudflare 预检。HF Inference Providers → Scaleway → `Qwen/Qwen3-Embedding-8B` Windows route 实际 probe PASS，4096d；冻结输入 `819652` tokens，成本预检 `PASS_WITH_LOW_MARGIN`。Cloudflare `@cf/qwen/qwen3-embedding-0.6b` 1024d 独立 backup profile 也实际 probe PASS，但因模型/维度不同不得作为 H-59 fallback。H-59 路由 Executor，API=true，其他外部模型调用仍禁用。

<!-- r84 evaluator switch update -->
H-59 执行到第 10 次物理尝试时，HF/Scaleway 返回 terminal HTTP 402；最终 `9 successful + 1 terminal`，L2=`4/8`，Primary=`NOT_MEASURED`，project impact=`INCONCLUSIVE`，没有形成 capability verdict。Human 于 `2026-09-16` 选择“换一个油、重开一个”，不补原 HF 额度。Evaluator 因此保留 H-59 审计与 8B partial cache，正式 `SWITCH` 到 H-60 `FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1`：复用同一 H-58 Fresh12 与同一 contraction-timing 主假设，但冻结 Cloudflare `@cf/qwen/qwen3-embedding-0.6b` 1024d 独立 profile。H-60 不做 0.6B-vs-8B 优劣比较；只在本任务内部比较同一 Cloudflare semantic lane 下 `RRF60 Top5` 与 `bounded union workspace(max10)`。B-03 继续 ACTIVE；B-06 明确为后续第二瓶颈，B-05 WATCH，B-07 secondary。

<!-- r85 evaluator review update -->
H-60 在 Amendment 01 零依赖门禁后完成全部 Cloudflare frozen run：Phase0 PASS，preflight PASS，80/80 runtime embedding units 完成，81 successful / 81 physical attempts，L2=`9/9 PASS`、Evaluator L3=`9/9 PASS`。同 lane 下 baseline RRF60 Top5 Gold reach=`7/12`，bounded workspace=`9/12`，恢复 `2 cases / 2 families`、`protected_loss=0`、workspace max=10；低于预声明 `recovered>=3`，因此任务 verdict=`PASS`、project impact=`IMPROVED`、continuation=`SWITCH`，方向 decision=`NOT_QUALIFIED`。剩余 3/12 为 lexical∪semantic union miss；H-60 两个近邻信号做 H-50 cross-cohort gap-fill sanity check 后，`max_gap=2/3/4` 均为 `0/18` BOTH_MISS recovery，禁止重开 fixed-neighbor/gap-fill 微调。激活 H-61 `FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1`，zero API / zero product change，先判断是否仍存在 >=3 cases + >=2 families 的 Gold-free 单一 evidence-supply/exploration 机制；否则返回 `NO_SINGLE_DIRECTION` 并重排 B-03 vs B-06/B-05。
