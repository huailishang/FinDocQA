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

现有接口继续保留，但逐步改为读取 Canonical Document Store。问题输入先统一经过 C0/C1，这样比赛结构化题和真实自然语言问题共用同一条后续链。

```text
C0 QuestionAdapter.adapt(payload)
    -> Question

C1 QueryUnderstanding.understand(question)
    -> domain / base_type / answer_shape / traits / confidence

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

### Question 输入兼容规则

```text
AFAC / Benchmark
题干 + options + type + answer_format + 可选 doc_ids
        ↓
QuestionAdapter

真实使用
一句自然语言问题
        ↓
QuestionAdapter
        ↓
Query Understanding 自动补足可推断元数据
```

长期约束：`question/text` 是唯一必需的业务内容；`options`、`domain`、`answer_format`、`doc_ids` 都允许由 Adapter/C1 缺省或推断。显式元数据优先于推断，推断结果必须保留原因与置信度，不允许伪装成数据集真值。

C2 不只依赖题面关键词。自然语言比较题如果 C1 无法确定是否跨文档，允许 Document Scope 的多个高置信实体覆盖槽位在检索后把路由提升为 `cross_doc`。这样“比较同一家公司两个年份”不会仅因出现“比较”被误判，而“比较比亚迪和宁德时代”可在两个实体文档槽位被确认后进入跨文档求解。

Open QA 的 freeform 合同与比赛多槽位 freeform 分开：真实单问默认一个 `answer_value`，Production Integrity 仍要求非空、格式有效、有检索证据、无截断/Provider 错误；AFAC/B 的权威槽位、公式与绑定门禁保持原规则，不因通用问答兼容而放宽。

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

## 7. C3 Deterministic Calculation 接口

计算能力拆成“公式证据 → 变量绑定 → 受限程序 → 确定性执行”，避免把材料公式直接交给 LLM 心算或任意 Python。

```text
BuiltinFormulaRegistry.detect(question)
    -> growth_rate / difference / ratio / percentage_point_change / ranking_asc / ranking_desc / None

MaterialFormulaExtractor.extract_from_candidate(evidence_candidate)
    -> Sequence[FormulaEvidence]

LocalContextVariableBinder.bind(formula_evidence)
    -> Mapping[str, BoundVariable]

FormulaEvidenceGate.evaluate(formula_evidence, bindings)
    -> PASS / REVIEW / FAIL

FormulaContextRecovery(document_store).recover(formula_evidence)
    -> FormulaRecoveryResult
    -> recovered_evidence + recovered_source_refs + recovery_steps + reasons + gate_result

SafeFormulaCompiler.compile(formula_evidence, bindings)
    -> FormulaProgram

DeterministicCalculationEngine.execute_program(program, bindings)
    -> CalculationExecutionResult + trace
```

当前 L2 只自动执行“局部显式公式 + 局部显式变量 + Gate PASS”的安全子集。C3-B V1-R1 新增了保守 Context Recovery：只允许在同一 `CanonicalDocument` 内恢复有限同页上下文、显式 linked table/表格 footnote，以及由公式 anchor 自身文本或 anchor/唯一 `CanonicalFormula` 显式 continuation metadata 授权的相邻页 continuation；普通 same-page 邻块里的“见下页/见上页”不能授权跨页。Recovery 本身不执行计算，恢复后仍必须重新经过 `LocalContextVariableBinder + FormulaEvidenceGate`。公式级显式 footnote dependency 在当前 canonical contract 尚不支持时必须 REVIEW；没有结构 linkage、来源不唯一、跨文档或 lineage 不完整时保持 REVIEW / FAIL。

R2 后 Gate 进一步要求：Formula source lineage 有效；所有被公式引用的 `BoundVariable` 具有有效 lineage；同一变量出现多个不同归一化值时进入 REVIEW；存在业务上限/下限时，最终表达式根必须是 governing `min/max`，且约束 target 必须是根调用的直接变量参数，否则 REVIEW。排序只有显式“升序/从低到高”或“降序/从高到低”才进入确定性 `ranking_asc / ranking_desc`；方向不明确时不猜测。

`FormulaProgram` 只允许白名单算子；当前通用内核不执行 `eval` / `exec` / 任意 subprocess。旧 `CalculationSolver` 的历史执行路径暂保留，后续逐步迁移到通用 C3 内核，避免一次性改变比赛兼容行为。

---

## 8. 重要约束

1. Source Adapter / Parser / Normalizer 不读取 qid 特例；
2. Document Scope 和 Retriever / Assembler / Solver / Verifier 使用同一有效文档范围；
3. EvidenceCandidate 必须携带 doc_id、page、source 和 lineage；
4. Parser fallback 不得跨实体、跨文档自动补证；
5. Claim 验证不能只靠关键词重叠；数值必须与指标、主体、时间和单位局部绑定；
6. Unknown failure 不自动映射为 missing evidence；
7. Benchmark Dataset Adapter 不得把 Gold answer / evidence 泄露给待测模块；
8. 第三方 benchmark 的数据许可证与 FinDocQA 代码许可证分开处理。
