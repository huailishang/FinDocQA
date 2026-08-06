# C3 表行标签证据锚定实验

> Executor 候选结论：固定 10 个 `ROW_LABEL_MATCH` 案例全部由 0 覆盖提升为完整覆盖；Full54 的 BINDING_READY 从 22 提升到 32，坐标覆盖从 70/185 提升到 99/185，文档/表来源召回和原有 BINDING_READY 均无退化。最终项目影响裁决由 Evaluator 独立复核后签发。

## 1. Before / After 身份

- Before：`handoffs/evaluator_executor/FDQA-C3V-ROW-LABEL-EVIDENCE-ANCHOR-V1/evidence/before_report.json`，SHA256 `667b59e00b977287bc91b2849efd469e64f65fbfa196be2943f2624b6a183da6`。
- After：`evaluation_artifacts/c3_row_label_evidence_anchor_v1/after_report.json`，SHA256 `2a8ea24828f1bdddccac2d7c75a0f3be3f226d9c0e8af1efe97199a79ee6abac`。
- Comparison：`evaluation_artifacts/c3_row_label_evidence_anchor_v1/comparison.json`，SHA256 `e027441a68924ecce2d86db3752e00802c34f85a15d6012371ab3f1f941333f5`。
- Before 已证明与 H-06 正式报告逐字节一致。

## 2. Exactly one principal change

```text
question + 当前 page 的 CanonicalTable 行标签
→ 过滤通用问句/量纲词并做确定性词项匹配
→ 仅当存在唯一最高正分、该行含非空数据单元格、全部坐标 span 合法且原文片段唯一时触发
→ candidate.text 替换为 page.text 中覆盖该完整表行全部列的连续原文片段
→ 其余情况全部回退原有 _window 行为
```

本实验只修改 `src/retrieval/canonical_lexical.py`。没有修改 document ranking、query terms、score、top_k、window size、structured table、计算器、solver 或 evaluator。候选排序继续使用旧窗口的 score，变化仅发生在候选证据文本。

## 3. 指标对比

| 指标 | Before | After | Delta | 合同门槛 |
|---|---:|---:|---:|---:|
| Row-label complete | 0/10 | 10/10 | +10 | >=8/10 |
| Row-label coordinates | 0/29 | 29/29 | +29 | >=23/29 |
| All 12 complete | 0/12 | 10/12 | +10 | >=8/12 |
| All 12 coordinates | 5/51 | 34/51 | +29 | >=28/51 |
| Full54 BINDING_READY | 22/54 | 32/54 | +10 | >=30/54 |
| Full54 coordinates | 70/185 | 99/185 | +29 | >=93/185 |
| Document Recall@1/3/5 | 24/34/39 | 24/34/39 | 0/0/0 | exact |
| Table Source Recall@5 | 34/54 | 34/54 | 0 | exact |

After terminal distribution：

```text
{"BINDING_READY": 32, "DOCUMENT_MISS": 15, "MEMBER_RANGE_INCOMPLETE": 2, "TABLE_SOURCE_MISS": 5}
```

## 4. 固定 10-case 产品实测

| Case | Capability | Terminal before → after | Coordinates before → after | Candidate source / rank | Selected row trace | Status |
|---|---|---|---:|---|---|---|
| `AAPL/2008/page_78.pdf-2` | `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/AAPL/2008/page_78.pdf-2` / `5` | r3 `change in unrealized gains on derivative instruments` score=14 terms=['change', 'unrealized', 'gain', 'derivative', 'instrument'] coordinates=4 | `improved` |
| `AAPL/2014/page_38.pdf-1` | `SOURCE_BOUND_TABLE_ARGMAX_LABEL` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/AAPL/2014/page_38.pdf-1` / `3` | r1 `cash cash equivalents and marketable securities` score=11 terms=['cash', 'equivalent', 'marketable', 'securitie'] coordinates=4 | `improved` |
| `AON/2011/page_134.pdf-3` | `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/AON/2011/page_134.pdf-3` / `3` | r4 `total operating segments` score=8 terms=['total', 'operating', 'segment'] coordinates=4 | `improved` |
| `GS/2016/page_77.pdf-2` | `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/GS/2016/page_77.pdf-2` / `5` | r1 `equity securities` score=6 terms=['equity', 'securitie'] coordinates=4 | `improved` |
| `LMT/2015/page_56.pdf-2` | `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/LMT/2015/page_56.pdf-2` / `3` | r1 `net sales` score=3 terms=['net', 'sale'] coordinates=4 | `improved` |
| `LMT/2015/page_56.pdf-4` | `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/LMT/2015/page_56.pdf-4` / `1` | r1 `net sales` score=3 terms=['net', 'sale'] coordinates=4 | `improved` |
| `LMT/2016/page_48.pdf-4` | `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/LMT/2016/page_48.pdf-4` / `4` | r2 `operating profit` score=6 terms=['operating', 'profit'] coordinates=4 | `improved` |
| `MRO/2007/page_134.pdf-3` | `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `finqa://dev/MRO/2007/page_134.pdf-3` / `3` | r3 `expected life in years` score=5 terms=['expected', 'life'] coordinates=4 | `improved` |
| `08ec1b4c-f5dc-4654-b931-4ddd23f81113` | `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/3 → 3/3` | `tatqa://table/e614befa-40ae-43c0-93b1-385899b6b181` / `3` | r6 `Total Product` score=5 terms=['total', 'product'] coordinates=4 | `improved` |
| `9e3c7e60-2851-455f-835d-7ba3e276ffc2` | `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY` | `MEMBER_RANGE_INCOMPLETE → BINDING_READY` | `0/2 → 2/2` | `tatqa://table/008149bc-f997-49d9-a981-f01f19579451` / `2` | r7 `Gross deferred tax assets` score=8 terms=['gross', 'deferred', 'tax', 'asset'] coordinates=3 | `improved` |

10/10 案例均通过同一实现路径改善，没有案例 ID、Gold 坐标、官方答案或单题术语分支。

## 5. 两个 RANGE_EXPANSION 案例

| Case | Before covered | After covered | Status |
|---|---:|---:|---|
| `378b81b7-c7d6-46f9-aca6-58729440a889` | 5 | 5 | `unchanged` |
| `e2665282-60dd-4ef1-b29b-fde6a2628d9d` | 0 | 0 | `unchanged` |

本实验未处理这两个多行/章节范围问题，覆盖未下降。它们继续留在 B-03 的 `RANGE_EXPANSION` 分支。

## 6. 护栏与回归审计

- 原有 BINDING_READY：`22` 题；退化 `0`。
- lost document cases：`0`。
- Provider / legacy / network / Token：`0/0/0/0`。
- 新专项测试：6 类失败关闭行为。
- 相关回归：60 passed。
- 完整离线回归：1311 passed；无新增 skip/xfail 摘要。

## 7. 失败关闭边界

以下任一情况均继续使用原 `_window`：

- 行标签最高分并列；
- 没有正分行；
- 目标行只有标签、没有非空数据单元格；
- `coordinate_spans` 缺失或非法；
- 不能覆盖目标行全部坐标；
- 原文片段不能在 page.text 中唯一定位。

其中“只有标签、无数据值”的限制修复了章节计数题误锚定，保证原有 BINDING_READY 零退化。

## 8. 无 Gold / 通用性审计

运行时输入仅为 question、options、CanonicalPage.text、CanonicalPage.tables、coordinate spans、document hit 和 lineage metadata。Gold 只用于离线 comparison 判定，未进入产品代码。

静态审计要求：

- 产品代码不存在冻结 case ID、Gold coordinate、official answer 或 dataset qid 分支；
- `_DOCUMENT_QUERY_STOPWORDS`、`_document_query_terms`、`_evidence_terms`、`_term_weight`、`_score_text`、`_window` 的原有 AST 不变；
- document ranking、top_k、top_k_per_doc、window chars 和 context flank 默认值不变；
- 只有一个产品模块发生变化。

## 9. Changed files

```text
M  src/retrieval/canonical_lexical.py
A  tests/test_canonical_table_row_anchor.py
A  docs/evaluation/c3-row-label-evidence-anchor.md
A  evaluation_artifacts/c3_row_label_evidence_anchor_v1/after_report.json
A  evaluation_artifacts/c3_row_label_evidence_anchor_v1/comparison.json
```

继承的 `tests/test_c3_question_table_evidence_retrieval.py`、`PROJECT_BOTTLENECK_MAP.md` 和诊断文档未修改，Hash 保持冻结值。

## 10. 下一步

由 Evaluator 接受本报告后，独立重跑 before/after、comparison、专项/相关/全量测试、静态审计与 Git 范围检查，并签发任务 verdict 和项目影响 verdict。
