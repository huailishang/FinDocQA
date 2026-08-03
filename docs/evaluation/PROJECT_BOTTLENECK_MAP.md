# FinDocQA Project Bottleneck Map

Map revision: `2026-08-03-r3`

Last reviewed: `2026-08-03`

Map owner: Evaluator

Status: `ACTIVE`

## Project outcome

- Observable project outcome: 用户提出自然语言问题后，系统能从金融文档中找到可靠证据，生成可追溯、可验证、失败关闭的答案。
- Primary user or business value: 把 FinDocQA 从比赛式答案生成改造成可用于个人与企业知识空间的工业文档问答底座。
- Forbidden failures: 来源、范围、公式或答案形态不确定时伪装成确定答案；错误来源血缘；错误确定性执行；无证据放行。
- Current evaluation boundary: 当前只推进“结构化表格确定性计算链”。不把 Oracle 覆盖率或固定 fixture 成绩外推为 PDF 解析、检索或端到端问答准确率。

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

## Current measurement basis

已验证能力：

```text
Factory structured-table SUM：0/3 → 3/3
C3-P Binder 反例：33/33 失败关闭
C3-M 正常主链：ACTIVE
C3-N / C3-O 正常主链：BLOCKED_BY_MISSING_BINDING
完整离线回归：1256 passed
```

当前本地语料事实：

```text
data/processed_mineru 下发现 190 个 *content_list_v2.json
```

尚未量化：

```text
190 份文档中有多少包含表格
多少表格能被 loader 解析
多少文档能形成完整行证据
失败主要发生在无表格、解析、来源身份还是认证层
```

## Bottleneck register

| ID | Stage | Observable failure | Estimated affected scope | Evidence | Confidence | Status |
|---|---|---|---:|---|---|---|
| B-01 | 来源绑定 request → 正常计算主链 | SUM 能力曾不可达，且退出门不能表达接线后状态 | 固定 SUM 边界 3 cases | 0/3→3/3；33/33 护栏；R1 后 1256 passed | high | CLOSED |
| B-02 | 结构化表格证据可用性 | 真实 MinerU 语料中，表格与完整行证据的可用比例未知 | 190 份 content_list_v2 文档 | 当前只有 fixture 与若干定向用例，没有全语料覆盖基线 | high（问题存在）；low（规模未知） | ACTIVE |
| B-03 | PDF 解析与检索 | 原始 PDF 到问题相关表格行证据的召回率、跨页/跨表完整性未知 | unknown | 当前还没有问题级真实语料测量 | low | WATCH |
| B-04 | 长尾计算算子 | 剩余 unsupported operator 无通用家族达到 5 条 | 最大合格族 1 | C3 stage-exit report | high | RETIRED |

## Active bottleneck

Active bottleneck ID: `B-02`

为什么现在先做 B-02：

1. B-01 已完成产品接线、失败关闭护栏和可信退出门；继续修接线没有新增信息。
2. 系统是否真正有用，下一关键不是再加算子，而是现实文档能否稳定产出 Binder 所需的结构化行证据。
3. 如果真实语料大部分没有可加载表格，下一步应回到解析或证据生成；如果行证据覆盖已经较高，才值得继续做 C3-N/C3-O Binder 或问题级激活。

数量级判断：

```text
已知语料分母：190 份 content_list_v2 文档
可用表格文档数：unknown
完整行证据文档数：unknown
```

## Active hypothesis

Hypothesis ID: `H-02`

Falsifiable hypothesis:

> 对本地冻结的 190 份 MinerU `content_list_v2` 文档进行全量离线扫描，可以建立稳定、可复现、互斥完备的结构化表格可用性基线，并明确 B-02 的主要损失层级，而不修改任何产品实现。

本轮不假设覆盖率一定高。成功标准是“把未知变成可信数字”，不是制造改善数字。

必须输出的层级：

```text
L0 文档清单完整
L1 content_list 可读取
L2 文档是否包含 table 元素
L3 table_body 是否可解析
L4 StructuredTableRow 是否成功加载
L5 来源身份与基础认证字段是否完整
```

主要指标：

```text
document_count
documents_with_table_elements
documents_with_parsed_tables
documents_with_loaded_rows
total_table_elements
total_loaded_rows
load_failure_count
failure_reason_counts
domain_breakdown
```

护栏：

```text
190 份文档一份不漏、一份不重复
分类互斥且总和等于分母
双跑输出字节一致
Provider / legacy / network / Token = 0
不修改 src/**、data/** 或冻结快照
```

范围限制：

- 这是文档级结构化证据供给基线，不是问题级检索召回率。
- “有可加载行”不等于某个用户问题一定能绑定成功。
- 不代表 C3-N/C3-O 已接入，也不代表 FinDocQA 总体准确率。

## Candidate experiments

| Priority | Hypothesis | Principal change | Same-baseline comparison | Expected result | Cost/risk |
|---:|---|---|---|---|---|
| 1 | H-02 | 新建只读全语料结构化表格覆盖率 evaluator | 同一 190 文档清单双跑 | 获得稳定覆盖率与失败桶 | evaluator-only，L1 |
| 2 | H-03（未激活） | 基于 H-02 结果修复最大解析/认证损失层 | H-02 baseline before/after | unknown | 由最大失败桶决定 |
| 3 | H-04（未激活） | 问题级检索到 structured-table evidence 的覆盖测量 | 固定问题集 | unknown | 需先确认文档级供给 |
| 4 | H-05（未激活） | C3-N/C3-O Binder 接线 | 待 B-02/B-03 结果后定义 | unknown | 暂不展开 |

## Reassessment triggers

- H-02 发现大量 content_list 缺失或不可读；
- 大部分文档没有 table 元素，B-03 解析成为首要瓶颈；
- table 元素很多但行加载率低，结构化 loader/认证成为首要瓶颈；
- 行证据覆盖较高但问题级仍不可用，切换到检索与绑定测量；
- 数据目录或 190 文档清单发生变化；
- 项目目标或评测边界变化。

## Revision log

| Revision | Date | Evidence or reason | Bottleneck change | Hypothesis change |
|---|---|---|---|---|
| 2026-08-03-r1 | 2026-08-03 | C3-P Binder 与来源身份修复 PASS；Factory SUM 基线 0/3；切换 v2.1 | B-01 成为当前瓶颈；B-04 退出主线 | 激活 H-01：SUM 正常链接入 |
| 2026-08-03-r2 | 2026-08-03 | SUM 0/3→3/3，但旧 stage-exit 13 项失败 | B-01 标记为改善待退出门修复 | H-01 获得产品改善证据 |
| 2026-08-03-r3 | 2026-08-03 | R1 PASS；stage-exit 14 passed；联合 240 passed；全量 1256 passed；SUM 与快照护栏保持 | B-01 CLOSED；B-02 ACTIVE | 关闭 H-01；激活 H-02：190 文档结构化表格覆盖率基线 |
