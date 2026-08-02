# External Benchmarks for FinDocQA

## 1. 用途

这些项目不作为 FinDocQA 核心实现依赖，而作为外部评测数据源和 Adapter 目标。

目标不是把所有数据集都下载进仓库，而是记录：

- 输入是什么；
- 是否包含原始 PDF；
- 是否已经完成解析；
- 是否有标准答案；
- 是否有 evidence / page / program；
- 最适合评测 FinDocQA 哪一层。

---

## 2. 重点项目

### FinanceBench

定位：金融开放书问答 benchmark。

公开样本提供：

- 150 道人工标注 QA；
- human-annotated gold answer；
- evidence text；
- evidence page number；
- full-page evidence text；
- 对应金融文档 PDF。

最适合：

```text
E1 Parser
E2 Evidence Retrieval
E4 End-to-End QA
```

价值：可以从原始 PDF 开始跑 FinDocQA 全链，也可以直接用官方 evidence page 做 retrieval gold。

注意：外部数据许可与 FinDocQA Apache-2.0 代码许可分开处理，不把第三方数据直接并入代码许可证。

### MMLongBench-Doc

定位：长 PDF + 多模态文档理解 benchmark。

官方数据包括：

- 135 份 PDF；
- 1091 道问题；
- reference answer；
- evidence_pages；
- evidence_sources；
- evidence 类型覆盖 text / table / chart / image 等；
- 大量跨页问题。

最适合：

```text
E1 Parser / Layout / Multimodal
E2 Page Retrieval
E4 End-to-End
```

价值：专门暴露表格、图表、图片、版面和跨页理解问题，适合作为 Parser 与 visual fallback 的压力集。

### DocFinQA

定位：长上下文金融数值推理。

特点：

- 7,437 道问题；
- 在 FinQA 基础上扩展完整文档上下文；
- 平均上下文约 123k words；
- 重点测试 retrieval + long-context reasoning。

最适合：

```text
E2 Retrieval
E3 Calculation / Reasoning
E4 End-to-End
```

不作为主要 Parser benchmark，因为任务重点是已经准备好的长文档上下文。

### TAT-QA

定位：金融报告中的表格 + 文本混合 QA。

官方数据：

- 16,552 道问题；
- 2,757 个真实财务报告 hybrid contexts；
- 提供 gold answer；
- 数据已经组织成 tabular + textual context。

最适合：

```text
E3 Table Reasoning
E3 Calculation
E4 Answer
```

适合回答“给对表格和文本后，Solver 会不会算错”，不适合单独评价 PDF → table parser。

### MultiHiertt

定位：多层级表格 + 文本的数值推理。

数据字段包括：

- paragraphs；
- tables（HTML）；
- question；
- answer；
- reasoning program；
- text_evidence；
- table_evidence。

最适合：

```text
E2 Evidence Retrieval
E3 Hierarchical Table Reasoning
E3 Program / Formula Accuracy
```

因为表格已经是 HTML，主要测后半段，而不是原始 PDF 解析。

### Loong

定位：长上下文、多文档 QA。

特点：

- 每个实例平均约 11 份文档；
- 覆盖 Financial Reports / Legal Cases / Academic Papers；
- 任务包括 Spotlight Locating、Comparison、Clustering、Chain of Reasoning；
- 包含中英文场景。

最适合：

```text
E2 Document Scope
E2 Multi-document Retrieval
E3 Cross-document Reasoning
E4 End-to-End
```

价值：与 FinDocQA 无 doc_ids / 多文档候选范围场景很接近。

---

## 3. 后续可关注项目

### FinTextQA

长答案金融 QA，强调 source-attributed long-form answers，并使用 RAG、retriever、reranker、generator 的完整设置。

可用于以后测试：

```text
long-form answer
attribution
retriever / reranker
```

### FinLFQA

强调复杂金融问题的长答案与 attribution，评测 supporting evidence、intermediate numerical reasoning 和 domain-specific knowledge。

适合以后扩展 Answer + Citation 质量评测。

### KG-MuLQA

2026 ACL 数据/框架，基于金融 credit agreements 构造 20,139 个 QA，对 multi-hop retrieval、set operations、answer plurality 做分层控制。

和 FinDocQA 的合同/信贷长文档方向高度相关，后续可作为：

```text
E2 multi-hop retrieval
E3 set/comparison reasoning
E4 unseen contract QA
```

的补充 benchmark。

---

## 4. 推荐接入优先级

不要一次接全部。

```text
P1 FinanceBench
   原始 PDF + evidence page + answer
   → 最适合先打通完整 Adapter

P2 MMLongBench-Doc
   PDF + table/chart/image/layout
   → 专门补 Parser / Multimodal 评测

P3 TAT-QA / MultiHiertt
   已结构化表格 + program/evidence
   → 专门测 Solver / Verification

P4 DocFinQA / Loong
   长上下文 / 多文档
   → 测 Retrieval 与跨文档能力

P5 FinLFQA / KG-MuLQA
   attribution / credit agreements / multi-hop
   → 后续能力扩展
```

---

## 5. 接入原则

外部 benchmark 只能通过 Adapter 接入：

```text
External Dataset
→ Dataset Adapter
→ Canonical Document / Canonical Question / Gold Annotation
→ FinDocQA modules
```

禁止为了某个 benchmark 在 Retriever / Solver / Verifier 中写 dataset-specific 或 case-specific 特例。

第三方数据文件默认不直接提交进 FinDocQA 代码仓；优先提供 downloader / adapter / schema mapping，并遵守各数据集自己的许可证。

---

## 6. C3 外部 Oracle 基线 V1（2026-08-02，C3-M 实测结果）

当前使用 FinQA 与 TAT-QA 完整官方 development split，运行模式固定为 `ORACLE_PROGRAM`：

```text
普通算术 program / derivation
→ 评测 Adapter
→ 现有 ExplicitC3Pipeline
→ 现有 Decimal 执行器

FinQA table_average / table_min / table_max / table_sum
→ FinQA 评测 Adapter 绑定官方表格行与单元格
→ 通用 SourceBoundNumericSeries 合同
→ 通用序列聚合编译器
→ 现有 FormulaProgram + BoundVariable
→ 现有 Decimal 执行器
```

这仍然只测程序映射、来源绑定、运算符覆盖、单位处理和确定性执行，不测 PDF 解析、检索、公式发现或端到端问答。

实测结果：

| 指标 | FinQA | TAT-QA | 合计 |
|---|---:|---:|---:|
| 全部开发集题数 | 883 | 1668 | 2551 |
| 数值可评测题 | 873 | 750 | 1623 |
| 当前 C3 可表示 | 871 | 712 | 1583 |
| 支持范围内正确 | 871 | 710 | 1581 |
| 支持范围内错误 | 0 | 2 | 2 |
| C3 执行异常 | 0 | 0 | 0 |
| 支持范围内正确率 | 100.0000% | 99.7191% | 99.8737% |
| 全数值集有效正确率 | 99.7709% | 94.6667% | 97.4122% |

测量有效性：

```text
2551 / 2551 均有唯一终态
连续运行记录字节一致
FinQA 官方评分器与内部等价复算差异 = 0
TAT-QA 官方算术评分语义差异 = 0
各数据集 emitted 数 = native scorer prediction 数
各数据集终态正确数 = native scorer correct 数
Provider / 旧路由 / 网络调用 = 0
Prompt / Completion / Total Token = 0 / 0 / 0
measurement_valid = true
```

当前剩余最大能力损失：

```text
UNSUPPORTED_OPERATOR = 39

FinQA = 2
- 1 条要求返回最大值对应的年份标签
- 1 条存在“预计算总计列 + 分项列”的聚合范围歧义

TAT-QA = 37
- count / cardinality = 32
- 百分比字面量测量适配器问题 = 5
```

结果文件：

```text
evaluation_artifacts/c3_external_oracle_baseline_v1/
c3m_source_bound_numeric_series_aggregation_v1/
```

父目录中的原始 `per_case_records.jsonl` 与 `aggregate_report.json` 继续保留为 C3-L 的冻结输入；C3-M 的实测结果写入独立子目录，避免后续能力实现覆盖历史评估快照。

边界声明：

```text
end_to_end_evidence = false
active_route_authority = false
shadow_promotion_authority = false
production_correctness_authority = false
```

---

## 7. C3 不支持运算能力分诊 V1（C3-L）

C3-L 将原有 72 条 `UNSUPPORTED_OPERATOR` 拆成：

```text
FINQA_TABLE_AGGREGATION      = 35
TATQA_COUNT_CARDINALITY      = 32
TATQA_FUNCTION_DERIVATION    = 5
```

其中 FinQA 的 35 条表聚合进一步分为：

```text
可用通用来源绑定数值序列聚合恢复 = 33
要求 argmax 年份标签输出             = 1
总计列与分项列范围不唯一             = 1
```

因此 C3-L 选择的下一项产品能力是：

```text
SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION
来源绑定的数值序列聚合
```

C3-L 当时给出的理论上限为：

```text
C3 可表示题：1550 → 1583
全数值集有效正确率上限：1581 / 1623 = 97.4122%
```

该理论上限现已由 C3-M 的完整外部基线实测达到。

C3-L 的 5 条百分比字面量问题继续归类为 `MEASUREMENT_ADAPTER_REPAIR`，不属于 FinDocQA 产品能力，也未在 C3-M 中修复。

---

## 8. C3-M 来源绑定数值序列聚合

C3-M 新增的是一个数据集无关的产品合同，而不是 FinQA 专用运算符：

```text
SourceBoundNumericSeries
├─ series_id
├─ metric / entity
├─ source_object_id
├─ binding_status
├─ explicit aggregation range
└─ ordered items
   ├─ Decimal value
   ├─ unit / dimension
   ├─ source reference
   ├─ source coordinate
   └─ header / period label
```

支持的标量选择器：

```text
AVERAGE
MINIMUM
MAXIMUM
SUM
```

编译方式：

```text
SUM      → 稳定 add 链
AVERAGE  → 稳定 add 链 ÷ 精确成员数
MINIMUM  → 现有 min
MAXIMUM  → 现有 max
多选择器 → 复用同一组来源绑定变量
组合输出 → 使用现有 subtract 等下游算术
```

执行前必须同时通过：

```text
来源序列绑定有效
聚合选择器有效
问题与聚合操作一致
```

以下情况全部失败关闭：

```text
空序列
非数值或非有限值
缺少来源血缘
重复单元格坐标
跨文档或跨表混合
单位或维度不一致
聚合范围不明确
总计列与分项列冲突
不支持的聚合选择器
要求返回最大值/最小值对应标签
问题与运算不匹配或未知
```

外部评测结果：

```text
接受的 FinQA 子集 = 33
通用产品 API 成功执行 = 33
正确 = 33
错误 = 0
C3 执行异常 = 0
```

两个明确排除项仍保持失败关闭：

```text
AAPL/2014/page_38.pdf-1 → LABEL_OUTPUT_NOT_SUPPORTED
ABMD/2009/page_56.pdf-1 → AMBIGUOUS_AGGREGATION_RANGE
```

评测 Adapter 可以理解 FinQA 表结构，但 `src/calculation/` 中的产品合同和编译器不包含 FinQA、TAT-QA、case ID、官方答案或预期输出逻辑。

## C3-N 来源绑定表格谓词计数

C3-N 在 C3-M 的来源绑定数值序列之上增加一个独立产品能力：对已经唯一绑定、单位一致且来源坐标完整的数值集合，使用显式阈值执行严格大于或严格小于比较，并返回满足条件的成员数量。

产品边界：

- 只支持 GREATER_THAN 和 LESS_THAN。
- 阈值必须是有限 Decimal。
- 阈值单位和维度必须与集合一致。
- 每个成员必须有完整来源坐标、来源对象和 FormulaSourceRef。
- 输出是非负整数。
- trace 包含每个成员的值、阈值、比较符、比较结果和最终总成员数、命中数。
- 不支持大于等于、小于等于、等于、复合条件、缺失值计数、区段成员直接计数或标签输出。

TAT-QA Oracle 适配只选择冻结分类中满足完整能力证明的 16 条案例：

- 13 条跨期间列的同一指标比较。
- 2 条单期间列的类别比较。
- 1 条明确绑定区段的类别比较。
- 15 条严格大于，1 条严格小于。
- 8 条 million 单位，8 条 thousand 单位。
- 成员数量分布为 5 条两成员、10 条三成员、1 条十五成员。

适配器逐格核对官方表格中的原始单元格、数值、坐标、期间或类别标签、来源对象、阈值与比较符。官方 answer 只在产品执行后参与评分，不参与选路、集合绑定或计数。

C3-N 的来源验证分为两层：

```text
第一层：proof 内部一致
轴字段、成员行列、标签、坐标和值互相一致

第二层：官方表格独立验证
行轴必须覆盖声明行的完整期间列
单期间类别列必须覆盖表头后、唯一 Total 行前的全部连续数值明细
绑定区段必须从唯一标题行开始，并在匹配该区段的 Total 汇总行前结束
```

因此，不能通过同时修改 `start/end` 和成员列表来截短范围。即使被删除成员不影响最终计数，适配器仍会因为官方完整范围不一致而失败关闭。Total 行即使数值可解析，也只作为边界，不进入成员集合。

C3-N 独立快照：

    evaluation_artifacts/c3_external_oracle_baseline_v1/c3n_source_bound_table_predicate_cardinality_v1/

完整 Oracle 结果：

| Dataset | Numeric eligible | Representable | Correct | Incorrect | C3 errors |
|---|---:|---:|---:|---:|---:|
| FinQA | 873 | 871 | 871 | 0 | 0 |
| TAT-QA | 750 | 728 | 726 | 2 | 0 |
| Combined | 1623 | 1599 | 1597 | 2 | 0 |

剩余 UNSUPPORTED_OPERATOR 为 23。两次完整运行的 per_case_records.jsonl、aggregate_report.json 和 aggregate_report.md 字节一致。Provider、旧计算路由、网络调用和 Token 使用均为 0。该结果仍是 Oracle 模式能力覆盖，不代表端到端检索或文档解析准确率。
