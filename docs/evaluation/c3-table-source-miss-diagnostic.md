# C3 表来源缺失诊断

任务：`FDQA-C3X-TABLE-SOURCE-MISS-DIAGNOSTIC-REPAIR-V1`

修复状态：父任务的排名测量和 `CROSS_DOCUMENT_PAGE_DILUTION = 5/5` 保留；父任务的 `PROMOTE_SINGLE_VARIABLE_EXPERIMENT` 已撤销。

最终裁决：

```text
NO_SINGLE_VARIABLE
```

诊断边界：固定 5 个 `TABLE_SOURCE_MISS`，只修复晋级门和精确来源语义；不修改产品、测试、评测器、manifest、项目地图，不搜索新候选。

## 1. 保留的冻结测量

| 指标 | 结果 |
|---|---:|
| TABLE_SOURCE_MISS | 5 / 54 |
| Required document rank | 1 / 5 / 2 / 1 / 1 |
| 原始 Gold source rank | 7 / 9 / 11 / 11 / 10 |
| Evidence Top5 全部来自 required document 之外 | 5 / 5 |
| Gold coordinates | 15 |
| Provider / legacy / network / token | 0 / 0 / 0 / 0 |

两次 Full54 输出逐字节一致：

```text
SHA256 = 2a8ea24828f1bdddccac2d7c75a0f3be3f226d9c0e8af1efe97199a79ee6abac
```

保留的排名现象：

```text
正确文档已进入 Document Top5
→ 其他文档重复贡献多个同分、同 matched terms 页面
→ Evidence Top5 被跨文档页面占据
→ 精确 Gold source 原始 rank 为 7 / 9 / 11 / 11 / 10
```

主观察仍为：

```text
CROSS_DOCUMENT_PAGE_DILUTION = 5 / 5
```

## 2. 本次修复：分组排名不等于精确来源排名

父任务冻结的分组键：

```text
(document_id, round(current score, 12), tuple(matched_terms))
```

现有 `EvidenceCandidate` 只有一个主来源身份，因此每组只能由原排名中的首个候选作为代表。组内其他 `source_object_id` 即使写入 metadata，也不会被未修改的 Gold source scorer 计为精确来源命中。

| Case | 原始 Gold rank | Gold group size | Group rank | Group representative | Unchanged scorer 下 exact Gold rank | Gold group Top5 | Exact Gold Top5 |
|---|---:|---:|---:|---|---:|---|---|
| `APD/2013/page_40.pdf-1` | 7 | 1 | 3 | `finqa://dev/APD/2013/page_40.pdf-1` | 3 | 是 | 是 |
| `GS/2014/page_136.pdf-2` | 9 | 2 | 4 | `finqa://dev/GS/2014/page_136.pdf-1` | None | 是 | 否 |
| `IP/2009/page_37.pdf-4` | 11 | 4 | 4 | `finqa://dev/IP/2009/page_37.pdf-2` | None | 是 | 否 |
| `LMT/2016/page_48.pdf-1` | 11 | 4 | 4 | `finqa://dev/LMT/2016/page_48.pdf-4` | None | 是 | 否 |
| `MRO/2004/page_36.pdf-2` | 10 | 2 | 4 | `finqa://dev/MRO/2004/page_36.pdf-2` | 4 | 是 | 是 |

汇总：

```text
Gold 所在分组进入 Top5 = 5 / 5
精确 Gold source 进入 Top5 = 2 / 5
Gold 被非 Gold 代表隐藏 = 3 / 5
隐藏案例 = GS/2014/page_136.pdf-2 / IP/2009/page_37.pdf-4 / LMT/2016/page_48.pdf-1
```

因此不能把：

```text
Gold group enters Top5 = true
```

等同于：

```text
exact Gold lineage.source_path enters Top5 = true
```

## 3. 五题 group / exact-source 明细

### APD/2013/page_40.pdf-1

- 冻结 grouping key：`finqa::APD/2013/page_40.pdf + 80.6 + ['the', '2011', '2013', 'is', 'capital', 'expenditure', 'on', 'a']`
- 原始精确 Gold rank：`7`
- Gold group rank：`3`
- Group size：`1`
- Group representative：`finqa://dev/APD/2013/page_40.pdf-1`
- Exact Gold source：`finqa://dev/APD/2013/page_40.pdf-1`
- Unchanged scorer 下 exact Gold rank：`3`
- Gold group 进入 Top5：`true`
- Exact Gold source 进入 Top5：`true`
- Group members：
- `finqa://dev/APD/2013/page_40.pdf-1`

### GS/2014/page_136.pdf-2

- 冻结 grouping key：`finqa::GS/2014/page_136.pdf + 103.5 + ['in', 'and', 'the', 'of', 'interest', 'rate', 'hedges', 'n']`
- 原始精确 Gold rank：`9`
- Gold group rank：`4`
- Group size：`2`
- Group representative：`finqa://dev/GS/2014/page_136.pdf-1`
- Exact Gold source：`finqa://dev/GS/2014/page_136.pdf-2`
- Unchanged scorer 下 exact Gold rank：`None`
- Gold group 进入 Top5：`true`
- Exact Gold source 进入 Top5：`false`
- Group members：
- `finqa://dev/GS/2014/page_136.pdf-1`
- `finqa://dev/GS/2014/page_136.pdf-2`

### IP/2009/page_37.pdf-4

- 冻结 grouping key：`finqa::IP/2009/page_37.pdf + 42.0 + ['the', 'average', 'for', 'sales']`
- 原始精确 Gold rank：`11`
- Gold group rank：`4`
- Group size：`4`
- Group representative：`finqa://dev/IP/2009/page_37.pdf-2`
- Exact Gold source：`finqa://dev/IP/2009/page_37.pdf-4`
- Unchanged scorer 下 exact Gold rank：`None`
- Gold group 进入 Top5：`true`
- Exact Gold source 进入 Top5：`false`
- Group members：
- `finqa://dev/IP/2009/page_37.pdf-2`
- `finqa://dev/IP/2009/page_37.pdf-1`
- `finqa://dev/IP/2009/page_37.pdf-3`
- `finqa://dev/IP/2009/page_37.pdf-4`

### LMT/2016/page_48.pdf-1

- 冻结 grouping key：`finqa::LMT/2016/page_48.pdf + 80.3 + ['net', 'for', 'aeronautics', 'in', '2014', 'and', '2016']`
- 原始精确 Gold rank：`11`
- Gold group rank：`4`
- Group size：`4`
- Group representative：`finqa://dev/LMT/2016/page_48.pdf-4`
- Exact Gold source：`finqa://dev/LMT/2016/page_48.pdf-1`
- Unchanged scorer 下 exact Gold rank：`None`
- Gold group 进入 Top5：`true`
- Exact Gold source 进入 Top5：`false`
- Group members：
- `finqa://dev/LMT/2016/page_48.pdf-4`
- `finqa://dev/LMT/2016/page_48.pdf-1`
- `finqa://dev/LMT/2016/page_48.pdf-3`
- `finqa://dev/LMT/2016/page_48.pdf-2`

### MRO/2004/page_36.pdf-2

- 冻结 grouping key：`finqa::MRO/2004/page_36.pdf + 67.5 + ['were', 'total', 'fuel', 'oil', 'sales', 'in', 'for', 'the', 'year']`
- 原始精确 Gold rank：`10`
- Gold group rank：`4`
- Group size：`2`
- Group representative：`finqa://dev/MRO/2004/page_36.pdf-2`
- Exact Gold source：`finqa://dev/MRO/2004/page_36.pdf-2`
- Unchanged scorer 下 exact Gold rank：`4`
- Gold group 进入 Top5：`true`
- Exact Gold source 进入 Top5：`true`
- Group members：
- `finqa://dev/MRO/2004/page_36.pdf-2`
- `finqa://dev/MRO/2004/page_36.pdf-1`


## 4. 单一来源契约与正式 scorer

静态审计结果：

```text
EvidenceCandidate.source 为单一 str = true
来源提取只读取 lineage.source_path = true
_source_hit_ranks 使用单一来源 ID 比较 = true
来源提取读取 member_source_object_ids / member_lineages = []
Gold scorer 读取 member_source_object_ids / member_lineages = []
未修改 scorer 支持多来源组命中 = false
```

对应实现边界：

```text
EvidenceCandidate.source = 单一值
_candidate_source_object(candidate) = lineage.source_path
_source_hit_ranks = 用单一 source_object_id 与 Gold source ID 比较
```

要让一个 group 代表多个精确来源，必须修改 `EvidenceCandidate` 来源契约、Gold source scorer 或下游来源解析。若保持这些不变，则还需要新增“组内选哪个来源”的第二个变量。

## 5. 修正后的 promotion gate

| Gate | 结果 |
|---|---|
| observed pattern = 5/5 | `true` |
| Gold group Top5 count | `5` |
| Exact Gold source Top5 count | `2` |
| Hidden Gold member count | `3` |
| Exact Gold source ≥ 4/5 | `false` |
| Unchanged Gold scorer 支持多来源组 | `false` |
| 不需要组内 member selection | `false` |
| Exactly one implementable principal change | `false` |
| Parent candidate promotion | `false` |

冻结门槛要求精确 Gold source 至少覆盖 `4/5`，实际只有 `2/5`，因此父页面合并候选不能晋级。

## 6. 最终裁决

```text
NO_SINGLE_VARIABLE
```

原因：

1. `5/5 cross-document dilution` 是排名现象，不等于一个候选能修复 5/5。
2. 父页面合并候选能让 Gold 所在分组 `5/5` 进入 Top5，但现有 scorer 只能看到精确 Gold 来源 `2/5`。
3. 让多来源 metadata 计为命中，需要修改来源契约或 Gold scorer。
4. 在组内选择正确页面，需要再增加一个 source/member selection 变量。
5. 因此不存在父任务所声明的“一个 Evidence Retriever final-selection 变量即可覆盖至少 4/5”。

```text
product_change_authorized = false
```

## 7. 下一分支

本 repair 不提出新产品候选，也不继续搜索 grouping key、权重、top_k 或组内选择机制。

下一步返回 Evaluator，基于 `B-03 / H-09` 重新判断：

```text
是否先补测来源身份契约问题
或切换到其他已测量损失层
```

机器证据：

- `handoffs/evaluator_executor/FDQA-C3X-TABLE-SOURCE-MISS-DIAGNOSTIC-REPAIR-V1/evidence/exact_source_repair.json`
- `handoffs/evaluator_executor/FDQA-C3X-TABLE-SOURCE-MISS-DIAGNOSTIC-REPAIR-V1/evidence/source_contract_audit.json`
- 父任务两次 Full54 与原始得分分解仍保留在 `FDQA-C3X-TABLE-SOURCE-MISS-DIAGNOSTIC-V1/evidence/`。
