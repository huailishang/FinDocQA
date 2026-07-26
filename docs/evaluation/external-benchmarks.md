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
