# FinDocQA Project Bottleneck Map

Map revision: `2026-08-12-r41`

Last reviewed: `2026-08-12`

Map owner: Evaluator

Status: `ACTIVE`

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
| B-03 | 问题 → 文档/表格/行证据 | 正确文档、表格或完整成员范围未进入 Top5 | 54 官方题 / 46 文档闭集 + FinanceBench 3-doc/8-QA slice | Real E4 semantic review：冻结 Retrieval miss 6/6 最终语义错误；Retrieval hit 2/2 均生成语义正确答案。H-13 仍显示 miss 根因=alias 2 / derived 2 / causal 2，无单一 patch >=4 | high（局部外部端到端测量） | REOPEN_CANDIDATE |
| B-04 | 长尾计算算子 | 剩余 unsupported operator 无通用家族达到 5 条 | 最大合格族 1 | C3 stage-exit report | high | RETIRED |
| B-05 | 复杂表格解析 | 2124 张图像表或复杂 span 表未加载，但问题级影响未知 | 2124 张表 | `empty_or_image_table=2038` 等 | medium（现象）；low（业务影响） | WATCH |
| B-06 | E4 Gold 与端到端结果度量 | 无法可信自动判断 freeform 最终答案语义正确性并稳定统计 paid-run 成本 | 项目全链；本地 100 题 + 外部公开 benchmark | FinanceBench 8-case real E4 已完成；H-19~H-22 已冻结并 smoke-tested L2 Judge 规则/离线 harness，但 independent freeform Judge 泛化尚未完成 | high | SECONDARY_BLOCKED_BY_B-07 |
| B-07 | Solver/Provider → 输出门禁 → 可交付答案 | 新 independent freeform cohort 中 Provider 执行失败与输出门禁使最终可交付答案严重不足 | AMD_2022_10K 7-case independent cohort；最大单一失败族 4 cases | H-25：7/7 bounded attempts，Provider COMPLETED 3 / ERROR 4，prediction 3/7，blocked 6/7，final accepted 1/7；4 个 ERROR ledger 均缺 failure_category/error_type/http_status | high（该 cohort）；medium（全局外推） | ACTIVE |

## Active bottleneck

Active bottleneck ID: `B-07`

当前判断：

1. H-25 已完成 current8 之外 `AMD_2022_10K` 完整 7-case 的真实 bounded product generation：7/7 各尝试一次，Gold runtime leakage=0，Judge 未运行。
2. 这 7 个独立案例中，最大单一失败族不是 Retrieval，也不是 Judge，而是 `Provider ERROR = 4/7`；它达到“通用问题覆盖至少 4 个独立案例才进入下一实验”的门槛。
3. 另有 2 个 Provider 已完成输出被 `freeform_answer_too_long` / `truncation_risk` 拦截，但只有 2 个案例，暂不单独进入输出门禁调参。
4. 最终 product-gate accepted 仅 1/7，因此当前先解决 product-generation deliverability；在稳定拿到足够真实 prediction 之前，继续做 Judge 泛化测量的信息增益较低。
5. B-06 的 Judge 规则、离线 harness、shadow preflight 已经建立，但 independent freeform Judge 泛化仍未完成；当前降为 `SECONDARY_BLOCKED_BY_B-07`，不是关闭。
6. Evaluator 独立检查发现 4 个 Provider ERROR 的 ledger 只有通用 `ERROR`/时间戳，没有 `failure_category / error_type / http_status`，现有证据无法区分 429、5xx、连接、超时或响应格式失败。
7. 所以不能直接加 retry、换 Provider、换模型或调 Prompt；下一步先用零 Provider repair 把 Provider failure 变成可诊断证据。
8. B-03 Retrieval 继续 `REOPEN_CANDIDATE`；Knowhere / LLM Wiki 与 B-05 Parser 继续等待新的 E4 失败证据，不回到局部题目微调。

## Active hypothesis

Hypothesis ID: `H-26`

Falsifiable hypothesis:

> H-25 的最大失败族是 4/7 Provider ERROR，但当前 `TokenAttempt` / `finalize_provider_attempt` 以及 `llm_client` 异常路径没有持久化 sanitized failure metadata。若先只增加 `failure_category / error_type / http_status` 三类诊断字段，并保持 Provider/model/retry/fallback/timeout/prompt/output-gate 行为完全不变，那么 synthetic HTTPError / Timeout / URLError / invalid JSON / invalid response 路径应能稳定产生可归因 ledger，同时成功调用语义保持不变。若为了获得这些字段必须改变 Provider 行为或保存 raw response body，则 H-26 失败并停止。

当前测量事实：

```text
H-25 L2/L3 = 8/8 PASS
AMD independent freeform cohort = 7 cases
Provider attempts = 7/7
Provider COMPLETED / ERROR = 3 / 4
prediction present = 3/7
product blocked = 6/7
final product-gate accepted = 1/7
largest failure family = Provider ERROR 4/7
ERROR rows with failure_category = 0/4
ERROR rows with error_type = 0/4
ERROR rows with http_status = 0/4
Judge = NOT_RUN
B-06 = SECONDARY_BLOCKED_BY_B-07
B-07 = ACTIVE
```

H-26 只修 Provider error ledger 可诊断性，零 Provider/API 调用，不改变 retry/fallback/provider/model/timeout/prompt/output gate。修复独立通过后，再决定是否需要一次单独授权的 bounded canary 去重新捕获真实 Provider failure 类型。

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
