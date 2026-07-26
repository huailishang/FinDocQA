# Module Interfaces

## 1. 目标

FinDocQA 的模块之间只能通过稳定数据合同通信。实现可以替换，但上下游不应感知具体 Parser、Retriever、Dataset 或 Output 格式。

当前 `src/contracts.py` 仍主要覆盖 Question / Evidence / Solver / Verification；文档生产链已在 `src/document/` 建立 Canonical Document 合同。下一步是逐步让正式 Retrieval 消费该合同，并把比赛 `submission_*` 字段迁移到 Output Adapter。

---

## 2. 文档生产链目标接口

### SourceAdapter

```text
load(source_config) -> Sequence[RawDocumentSource]
```

输入可以是：

```text
PDF
Markdown / TXT
HTML
JSON / XML
External Benchmark
```

### DocumentParser

```text
parse(raw_source) -> ParsedDocument
```

PDF Parser 可以是 MinerU、PyMuPDF、OCR/VLM 等；非 PDF Importer 也实现同一接口。

### DocumentNormalizer

```text
normalize(parsed_document) -> CanonicalDocument
```

统一标题、页码、Block、表格、公式、Figure、reading order、bbox 和 lineage。

### DocumentQualityAuditor

```text
audit(canonical_document) -> DocumentQualityReport
```

### DocumentRepairPolicy

```text
plan(report) -> Sequence[RepairAction]
apply(action) -> CanonicalPage / CanonicalDocument
```

RepairAction 必须限定文档、页面和问题类型，不允许无边界全量重解析。

---

## 3. Canonical Document 目标合同

后续建议在 `src/contracts.py` 或独立 `src/document/contracts.py` 中增加：

```text
RawDocumentSource
ParsedDocument
CanonicalDocument
CanonicalPage
CanonicalBlock
CanonicalTable
CanonicalFormula
CanonicalFigure
SourceLineage
DocumentQualityReport
```

最重要约束：

1. `CanonicalDocument` 不包含 MinerU 专属字段名；
2. Parser 原始 payload 可以放 metadata，但不能成为下游必需字段；
3. 每个 Block 都必须可回溯到 document/page/source；
4. Table / Formula / Figure 是一等结构，不只是一段 Markdown 字符串；
5. 质量 flag 不能等于“解析失败”，要区分 confirmed anomaly 与 needs review。

---

## 4. QA 消费链主接口

现有接口继续保留，但逐步改为读取 Canonical Document Store。

```text
QuestionLoader.load
    -> Sequence[Question]

QuestionClassifier.classify(question)
    -> ClassificationResult

DocumentScopeResolver.resolve(question, classification)
    -> candidate_doc_ids + audit

DocumentRetriever.retrieve(question, classification, corpus)
    -> ranked documents

EvidenceRetriever.retrieve(question, classification, document_scope)
    -> Sequence[EvidenceCandidate]

EvidenceAssembler.assemble(question, classification, candidates)
    -> EvidenceBundle

Solver.solve(bundle)
    -> SolverResult

Verifier.verify(bundle, result)
    -> VerificationResult

RecoveryPolicy.plan(failure_state)
    -> RecoveryPlan

ResultWriter.write(results)
    -> JSON / Markdown / benchmark prediction / API response
```

---

## 5. Answer 与 Output 要解耦

当前核心对象仍有：

```text
submission_slot_count
submission_slot_contracts
submission_answers
```

这些是历史比赛兼容字段，不应作为长期核心接口。

当前已开始迁移：

```text
Question
└─ question_answer_slot_count() / question_answer_slot_contracts()

PipelineResult
├─ answer_values            ← 新通用字段
└─ submission_answers       ← 旧兼容字段，暂保留

PipelineResult
→ ResultRecord
→ OutputAdapter
   └─ JsonResultWriter      ← 已实现 JSON / JSONL
```

后续目标仍然是：

```text
OutputAdapter
├─ JsonOutputAdapter
├─ MarkdownOutputAdapter
├─ BenchmarkOutputAdapter
└─ LegacyCompetitionCsvAdapter
```

“多槽位答案”本身可以是通用能力，但核心命名统一为 `answer_slots` / `answer_values`，而不是 `submission_*`。旧字段只在兼容层逐步消化。

---

## 6. 评测接口

每个模块实现都应配套 Evaluator。

```text
ParserEvaluator.evaluate(predicted_document, gold_document)
RetrievalEvaluator.evaluate(retrieved, gold_evidence)
ReasoningEvaluator.evaluate(result, gold_reasoning)
EndToEndEvaluator.evaluate(result, gold_answer)
```

模块替换时先跑对应局部评测，再跑 End-to-End；不能只看最终答案涨跌。

---

## 7. 重要约束

1. Source Adapter / Parser / Normalizer 不读取 qid 特例；
2. Document Scope 和 Retriever / Assembler / Solver / Verifier 使用同一有效文档范围；
3. EvidenceCandidate 必须携带 doc_id、page、source 和 lineage；
4. Parser fallback 不得跨实体、跨文档自动补证；
5. Claim 验证不能只靠关键词重叠；数值必须与指标、主体、时间和单位局部绑定；
6. Unknown failure 不自动映射为 missing evidence；
7. Benchmark Dataset Adapter 不得把 Gold answer / evidence 泄露给待测模块；
8. 第三方 benchmark 的数据许可证与 FinDocQA 代码许可证分开处理。
