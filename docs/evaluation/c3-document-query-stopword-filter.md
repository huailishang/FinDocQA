# C3 文档 Query 停用词过滤实验

## 结论

本轮只修改 `CanonicalDocumentRetriever` 的文档级 query terms：过滤冻结的通用英语停用词。Evidence Retriever、评分公式、TopK、窗口、官方数据和评测器均保持不变。

在同一套 54 题、46 文档闭集上：

```text
Document Recall@1：17 → 24
Document Recall@3：32 → 34
Document Recall@5：36 → 39
Table Source Recall@5：33 → 34
Gold Coordinate Coverage@5：67/185 → 70/185
BINDING_READY：21 → 22
lost_document_cases：0
```

该结果只代表冻结的 46 文档闭集，不代表完整 FinQA/TAT-QA 或本地 190 文档全库召回率。

## 单一产品变量

```text
src/retrieval/canonical_lexical.py
```

新增：

```text
_DOCUMENT_QUERY_STOPWORDS
_document_query_terms()
```

调用关系：

```text
_question_terms()
→ 保持原始完整 terms

_document_query_terms()
→ 仅过滤文档排序停用词
→ 过滤后为空则回退 _question_terms()

CanonicalDocumentRetriever
→ 使用 _document_query_terms()

CanonicalLexicalEvidenceRetriever
→ 继续使用 _evidence_terms()
```

## 冻结停用词

```text
a, an, the, of, in, on, for, to, and, or,
is, are, was, were, be, been, being,
what, which, how, during,
this, that, these, those,
from, by, with, at, as
```

只做完整 token 精确匹配。金融词、年份、数字、百分比、单位、中文词和带下划线 token 不受影响。

## 恢复案例

```text
AAPL/2005/page_83.pdf-2：DOCUMENT_MISS → BINDING_READY
GS/2014/page_136.pdf-2：DOCUMENT_MISS → TABLE_SOURCE_MISS
IP/2009/page_37.pdf-4：DOCUMENT_MISS → TABLE_SOURCE_MISS
```

没有任何原有 Top5 文档命中退化。

## 冻结基线测试隔离

父任务测试原先通过实时调用当前产品检索器生成报告，同时硬编码旧基线指标。产品检索改善后，这会把正确变化误判为回归。

修复后：

```text
tests/test_c3_question_table_evidence_retrieval.py
→ 冻结基线断言读取冻结 report.json

after_report.json
→ 继续通过独立 before/after 比较脚本验证
```

未修改基线报告、评测器实现、manifest 或 Gold scorer。

## 产物

```text
evaluation_artifacts/c3_document_query_stopword_filter_v1/after_report.json
SHA256 = 667b59e00b977287bc91b2849efd469e64f65fbfa196be2943f2624b6a183da6
```

## 验证

```text
专项测试：19 passed
相关回归：54 passed
完整离线回归：1305 passed
Provider / legacy / network / Token：0 / 0 / 0 / 0
```

最终是否保留该产品改动，由 Evaluator 独立裁决。
