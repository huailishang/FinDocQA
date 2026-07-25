# Module Interfaces

## Shared Contracts

核心数据合同位于 src/contracts.py，主要对象包括 Question、ClassificationResult、EvidenceCandidate、EvidenceBundle、SolverResult、VerificationResult 和 PipelineResult。

## 主接口

QuestionLoader.load → Sequence[Question]

QuestionClassifier.classify(question) → ClassificationResult

DocumentScopeResolver.resolve(question, classification) → candidate_doc_ids + retrieval audit

EvidenceRetriever.retrieve(question, classification) → Sequence[EvidenceCandidate]

EvidenceAssembler.assemble(question, classification, candidates) → EvidenceBundle

Solver.solve(bundle) → SolverResult

Verifier.verify(bundle, result) → VerificationResult

SubmissionWriter.write(results) → output

## 重要约束

1. Document Scope 和后续 Retriever / Assembler / Solver / Verifier 使用同一有效文档范围。
2. EvidenceCandidate 必须尽量携带 doc_id、page、source 和必要的匹配元数据。
3. 解析器 fallback 不得跨实体、跨文档自动补证。
4. Claim 验证不能只靠关键词重叠；数值必须与指标、主体、时间和单位局部绑定。
5. Unknown failure 不自动映射为 missing evidence。
6. Provider 调用和 Token 使用必须可审计；密钥只通过环境变量注入。
