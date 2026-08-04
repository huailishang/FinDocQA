# C3 问题到表格证据检索基线

## 结论

本轮不增加算子，也不修改检索产品代码，只回答一个问题：

> 给出真实金融表格问题后，现有检索器能否找到正确文档、正确表格，以及完整的成员坐标？

冻结 54 道 FinQA / TAT-QA 官方题，并仅在这些题对应的 46 份文档闭集中排名，使用现有：

```text
CanonicalDocumentRetriever(top_k=5)
→ CanonicalLexicalEvidenceRetriever(top_k_per_doc=5)
```

结果：

```text
54 题
→ 21 题达到 BINDING_READY
→ 18 题卡在 DOCUMENT_MISS
→ 3 题卡在 TABLE_SOURCE_MISS
→ 12 题卡在 MEMBER_RANGE_INCOMPLETE
```

当前主要矛盾仍在“问题 → 文档 / 表格 / 成员范围”的证据链，不在新增确定性算子。

## 1. 冻结评测集

| 项目 | 数量 |
|---|---:|
| 总题数 | 54 |
| FinQA | 34 |
| TAT-QA | 20 |
| 唯一文档 | 46 |
| 适配后的真实来源对象 | 98 |
| Gold 成员坐标 | 185 |

能力分布：

| 候选能力 | 题数 |
|---|---:|
| 数值序列聚合 | 33 |
| 表格谓词计数 | 16 |
| 表格区段计数 | 3 |
| Argmax 标签 | 1 |
| 缺失值计数 | 1 |

冻结输入：

```text
case_manifest SHA256
= 9ab30f6b0fd960cb35b8821784cd7e256abd46851364fb7057a60e398894951b

source taxonomy SHA256
= 10a406d714f8aea1c2f06b1fe594334459c3335fd97fae3e55d68ca5a7788722
```

## 2. 为什么有 98 个来源对象

34 道 FinQA 题来自 27 个 PDF 页面文件，但这些文件在官方开发集中共有 79 个表格问答来源对象。

本轮没有只保留 34 个 Gold 对象，而是保留同一文件下全部 79 个对象，防止人为删除同文档干扰项。TAT-QA 有 19 个唯一表格文档，因此总来源对象为：

```text
FinQA 79
+ TAT-QA 19
= 98
```

每个来源对象只进入 `CanonicalDocumentStore` 一次，Gold 只在检索结束后用于评分。

## 3. 防止 Gold 泄漏

送入检索器的内容只有：

```text
官方 question 文本
+ 空 options
+ 空 doc_ids
+ 空 candidate_doc_ids
+ 空 raw
+ 通用 DEFAULT 分类
```

没有送入：

```text
官方 document_id / filename / document_index
Gold source object ID / table UID
Gold row / column coordinate
candidate_capability
answer / program / derivation
```

适配后的文档文本只来自官方：

```text
FinQA：pre_text + table + post_text
TAT-QA：paragraphs + table
```

每次文档检索调用均由 spy 保存输入哈希和结构化字段；54 题全部通过 Gold-scope 审计。

## 4. 总体指标

| 指标 | 结果 |
|---|---:|
| 必需文档 Recall@1 | 17 / 54 = 31.48% |
| 必需文档 Recall@3 | 32 / 54 = 59.26% |
| 必需文档 Recall@5 | 36 / 54 = 66.67% |
| Gold 表来源 Recall@1 | 11 / 54 = 20.37% |
| Gold 表来源 Recall@3 | 25 / 54 = 46.30% |
| Gold 表来源 Recall@5 | 33 / 54 = 61.11% |
| Gold 表来源 Recall@15 | 36 / 54 = 66.67% |
| Gold 表来源 Recall@25 | 36 / 54 = 66.67% |
| Gold 成员坐标微平均覆盖@5 | 67 / 185 = 36.22% |
| 成员坐标完整覆盖题数@5 | 21 / 54 = 38.89% |
| BINDING_READY | 21 / 54 = 38.89% |

表来源 Recall@15 / @25 没有超过文档 Recall@5，是因为证据检索只在文档 Top5 内继续检索。文档未进入 Top5，后续扩大表格候选数量也救不回来。

## 5. 损失层

终态按首个失败层互斥归类：

| 终态 | 题数 | 含义 |
|---|---:|---|
| DOCUMENT_MISS | 18 | 正确文档未进入 Top5 |
| TABLE_SOURCE_MISS | 3 | 文档命中，但正确来源对象未进入证据 Top5 |
| MEMBER_RANGE_INCOMPLETE | 12 | 正确表进入 Top5，但窗口没有完整覆盖 Gold 成员坐标 |
| BINDING_READY | 21 | 文档、来源对象和全部 Gold 坐标均可用 |

没有 `SOURCE_ADAPTER_ERROR`，54 题全部成功映射到官方来源对象与坐标。

## 6. 数据集差异

| 数据集 | 文档 Recall@5 | 表来源 Recall@5 | 坐标覆盖@5 | BINDING_READY |
|---|---:|---:|---:|---:|
| FinQA | 26 / 34 = 76.47% | 23 / 34 = 67.65% | 44 / 101 = 43.56% | 15 / 34 |
| TAT-QA | 10 / 20 = 50.00% | 10 / 20 = 50.00% | 23 / 84 = 27.38% | 6 / 20 |

TAT-QA 的文档区分和完整成员范围更弱；FinQA 的主要额外损失发生在同一文件内多个表格来源对象之间的排序，以及 1800 字符窗口没有覆盖完整表格成员范围。

## 7. 按能力观察

| 能力 | 题数 | 文档 Recall@5 | 表来源 Recall@5 | 坐标覆盖@5 | BINDING_READY |
|---|---:|---:|---:|---:|---:|
| 数值序列聚合 | 33 | 25 | 22 | 44 / 98 | 15 |
| 表格谓词计数 | 16 | 7 | 7 | 15 / 55 | 4 |
| 表格区段计数 | 3 | 3 | 3 | 8 / 15 | 2 |
| Argmax 标签 | 1 | 1 | 1 | 0 / 3 | 0 |
| 缺失值计数 | 1 | 0 | 0 | 0 / 14 | 0 |

样本数为 1 的能力只作为案例，不应外推总体能力高低。

## 8. 下一步含义

本轮只建立测量基线，不直接决定具体实现。结果支持评估者把下一假设优先放在以下三个可归因层之一：

```text
文档召回：18 题
→ 表来源排序：3 题
→ 完整成员范围提取：12 题
```

优先级不能只看数量，还要考虑同一改动是否可复用、是否会损害已有检索，以及是否能用同一 54 题基线做前后对照。

## 9. 边界

本报告不证明：

```text
本地 190 文档语料的真实问题召回率
真实用户无标准题干场景的表现
最终答案正确率
C3-N / C3-O 已接入正常主链
BINDING_READY 后一定能构造并执行正确 request
```

`BINDING_READY` 只表示当前证据窗口已经覆盖 Gold 文档、Gold 来源对象和完整 Gold 坐标。

## 10. 产物

```text
适配与测量：
src/evaluation/external_benchmarks/table_evidence_retrieval.py

命令行入口：
scripts/evaluate_c3_question_table_evidence_retrieval.py

专项测试：
tests/test_c3_question_table_evidence_retrieval.py

机器报告：
evaluation_artifacts/c3_question_table_evidence_retrieval_baseline_v1/report.json

机器报告 SHA256：
33edc54487162e6b2f5cd7ed30c82c7087002bae0e2cdaf5d3fa7086f0539998
```
