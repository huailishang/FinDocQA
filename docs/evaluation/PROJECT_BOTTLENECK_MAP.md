# FinDocQA Project Bottleneck Map

Map revision: `2026-08-04-r6`

Last reviewed: `2026-08-04`

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

状态：产品影响 `IMPROVED`，但原任务因未授权修改父任务 baseline 测试被 `REJECTED`；当前标记为 `IMPROVED_PENDING_TEST_ISOLATION_REPAIR`。

## Bottleneck register

| ID | Stage | Observable failure | Estimated affected scope | Evidence | Confidence | Status |
|---|---|---|---:|---|---|---|
| B-01 | 来源绑定 request → 正常计算主链 | SUM 能力曾不可达 | 固定 SUM 3 cases | 0/3→3/3；33/33 护栏 | high | CLOSED |
| B-02 | 结构化表格证据供给 | 真实 MinerU 表格和完整行证据曾未知 | 190 文档 | 77 份完整行证据、6071 表、77525 行 | high | CLOSED |
| B-03 | 问题 → 文档/表格/行证据 | 正确文档、表格或完整成员范围未进入 Top5 | 54 官方题 / 46 文档闭集 | H-06 后 15 文档缺失、5 表来源缺失、12 成员范围不完整；Binding 22 | high | IMPROVED_PENDING_REPAIR |
| B-04 | 长尾计算算子 | 剩余 unsupported operator 无通用家族达到 5 条 | 最大合格族 1 | C3 stage-exit report | high | RETIRED |
| B-05 | 复杂表格解析 | 2124 张图像表或复杂 span 表未加载，但问题级影响未知 | 2124 张表 | `empty_or_image_table=2038` 等 | medium（现象）；low（业务影响） | WATCH |

## Active bottleneck

Active bottleneck ID: `B-03`

当前判断：

1. H-06 已把 DOCUMENT_MISS 从 18 降到 15，Document Recall@5 从 36 提升到 39，且原有命中零退化。
2. 三道恢复题中，一道进入 BINDING_READY，两道停在 TABLE_SOURCE_MISS，说明文档排序噪声确实存在，但后续表来源检索仍是独立损失层。
3. 当前最大可行动损失仍包括 15 个 DOCUMENT_MISS 和 12 个 MEMBER_RANGE_INCOMPLETE；不能把停用词过滤视为 B-03 已关闭。
4. 原任务唯一阻断是未授权的冻结 baseline 测试隔离修改；先完成最小 repair，再激活 H-07 结构化表格原子证据实验。
5. B-05 暂不升级；尚无证据证明 2124 张 unsupported 表是当前 54 题的第一损失源。

## Active hypothesis

Hypothesis ID: `H-06`

Falsifiable hypothesis:

> 只在 `CanonicalDocumentRetriever` 的文档级 query terms 中过滤固定、通用的英语停用词，可以减少泛词排序噪声，在不修改 Evidence Retriever、窗口、top_k、评分权重和评测数据的情况下，提高同一 54 题闭集上的 Document Recall，并带来非负的表来源、坐标覆盖和 BINDING_READY 变化。

Evaluator 运行时探索性探针：

```text
Document Recall@1：17 → 24
Document Recall@3：32 → 34
Document Recall@5：36 → 39
Table Source Recall@5：33 → 34
Coordinate Coverage：67 → 70
BINDING_READY：21 → 22
原有 Top5 文档命中退化：0
```

该探针已被正式产品实验和 Evaluator 独立复跑复现。H-06 状态：`SUPPORTED_PENDING_TEST_ISOLATION_REPAIR`。产品指标达到保留门槛，但原任务因测试文件超出冻结 Allowed scope 被拒绝。

## H-06 experiment gates

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

## Candidate experiments

| Priority | Hypothesis | Principal change | Same-baseline comparison | Expected result | Cost/risk |
|---:|---|---|---|---|---|
| 1 | H-06 repair | 冻结 baseline 测试隔离与 Hash 护栏 | 父任务测试 + after 独立重跑 | 关闭唯一合同阻断，不改产品指标 | repair，L0 |
| 2 | H-07（repair 后激活） | 针对成员范围不完整的结构化表格原子证据实验 | 固定 12 个失败案例 + 54 题总护栏 | 提升 Coordinate/Binding，旧命中零退化 | 单变量，L1 |
| 3 | H-03（WATCH） | 只修问题级 Gold 明确命中的复杂表格解析损失 | 问题级相关案例 before/after | unknown | 禁止按表数量优化 |
| 4 | H-05（未激活） | C3-N/C3-O Binder 接线 | 仅在 evidence binding-ready 覆盖成立后定义 | unknown | 暂不展开 |

## Reassessment triggers

- H-06 Document Recall 改善但 Table/Coordinate/Binding 退化；
- H-06 只能依靠金融术语特例或案例专用规则改善；
- H-06 无法稳定复现探索性探针；
- H-06 PASS 后 MEMBER_RANGE_INCOMPLETE 仍为最大可行动损失层；
- 复杂表格解析失败与问题级 Gold 明确重合，B-05 升级为 ACTIVE；
- 引入完整官方语料候选池后，闭集排序结论不再成立；
- 项目目标或评测边界变化。

## Revision log

| Revision | Date | Evidence or reason | Bottleneck change | Hypothesis change |
|---|---|---|---|---|
| 2026-08-03-r1 | 2026-08-03 | C3-P Binder 与来源身份修复 PASS；Factory SUM 0/3 | B-01 ACTIVE；B-04 退出主线 | 激活 H-01 |
| 2026-08-03-r2 | 2026-08-03 | SUM 0/3→3/3，但旧 stage-exit 回归失败 | B-01 改善待修复评测门 | H-01 获得产品改善证据 |
| 2026-08-03-r3 | 2026-08-03 | stage-exit 修复 PASS；全量 1256 passed | B-01 CLOSED；B-02 ACTIVE | 关闭 H-01；激活 H-02 |
| 2026-08-04-r4 | 2026-08-04 | 190 文档基线：77 完整行证据、113 无 table | B-02 CLOSED；B-03 ACTIVE；B-05 WATCH | 关闭 H-02；激活 H-04 |
| 2026-08-04-r5 | 2026-08-04 | 54 题基线 PASS；18/3/12/21 分层；18 个文档缺失均为排名偏低；停用词探针 36→39 | B-03 保持 ACTIVE，定位到文档 query 排序噪声 | 关闭 H-04；激活 H-06 |
| 2026-08-04-r6 | 2026-08-04 | H-06 独立复现：Doc R@5 36→39、Table 33→34、Coordinate 67→70、Binding 21→22、lost=0；原任务因未授权 baseline 测试修改被拒绝 | B-03 改善待测试隔离 repair；15/5/12/22 | H-06 获支持待 repair；H-07 暂缓 |
