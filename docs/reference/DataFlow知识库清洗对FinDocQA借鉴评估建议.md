# DataFlow 知识库清洗对 FinDocQA 的借鉴与评估建议（2026-10-17）

> **状态：REFERENCE_ONLY / EVALUATOR_REVIEW_REQUESTED（仅供评估，未冻结任务、未执行实验、未验收）。**
> 本文记录可复用的上游知识生产机制与可证伪实验候选，不授权安装依赖、下载模型、调用外部 API、处理非公开原始金融资料或调整 Product 默认路线。不得覆盖 `handoffs/evaluator_executor/state/CURRENT.md`，不得修改进行中的 H 系列冻结合同。最终取舍由 Evaluator 按当前真实瓶颈、证据与预算裁决。

## 1. 结论与定位

**DataFlow 有价值，适合放在 FinDocQA 的“文档生产链”中，重点借鉴知识清洗与可复现 Operator/Pipeline，而不是替换整套 FinDocQA 或现有 MinerU。**

- **MinerU / Parser**：把 PDF 等非结构化原始资料解析为页、文本、表格、公式和版面结构；我们已有 `src/structure/mineru_adapter.py`、`parser.py`、`selective_parser_fallback.py`。
- **DataFlow 候选处理层**：在既有解析结果之上做噪声治理、规范化、可控分块、质量筛选，按需产生带原文引用的候选 QA；对应项目 M3 Normalizer 与 M4 Quality Audit 的局部扩展。
- **FinDocQA / Evidence Service**：保有权威 Canonical Source、Canonical Document Store、Document Scope、Evidence Retrieval、Claim/计算绑定、Verification、Recovery，不交给通用清洗框架重做。

这里的“前序环节”特指 **相对 Query-time 检索与问答消费链的上游**；并非要求重新引入 PDF Parser。整个流程是：

```text
企业文件 / 金融 PDF / Markdown / HTML / JSON
  -> M1 Source Adapter（来源与访问控制）
  -> M2 Parse / Import（现有 MinerU 或其他 Parser）
  -> M3 Normalize / Structuralize（DataFlow 可插拔候选）
  -> M4 Quality Audit / Repair（现有只读审计 + 受控 fallback）
  -> Canonical Document Store（原文事实权威、来源血缘、版本）
  -> M5~M11 FinDocQA QA Consumption
     Scope -> Evidence -> Binder / Calculation -> Verification -> Output
```

数据质量不能代替最终证据可靠性：即使清洗得可读，也不意味着来源、时间、单位、表格结构、证据范围就正确。

## 2. 公开代码核对的 DataFlow 能力（代码事实与假设分开）

上游主仓：https://github.com/OpenDCAI/DataFlow （Apache-2.0，采用时仍应审阅具体依赖与分发义务）。

| 已见实现 | 代码位置 / 行为 | 我们的判断 |
| --- | --- | --- |
| Knowledge Base Cleaning 示例 | `dataflow/statics/pipelines/api_pipelines/kbcleaning_pipeline.py`：MinerU 转换 → `KBCChunkGenerator` → `KBCTextCleaner` → `Text2MultiHopQAGenerator` | 有可参考的端到端知识加工范例，但不能以示例结果代表金融准确率 |
| `KBCTextCleaner` | `dataflow/operators/knowledge_cleaning/generate/kbc_text_cleaner.py`：对 chunk 调用 LLM 清洗并写 `cleaned_chunk` | **优先评估**：格式、噪声、标签规范化；不直接替换原文 |
| 清洗 Prompt | `dataflow/prompts/kbcleaning.py`：要求数字事实、术语、表格不被修改，并包含隐私脱敏/涉密文本转换示例 | 提示词约束不构成零错误证明；部分脱敏、替换规则会损伤可验证事实，须隔离或禁用 |
| `KBCChunkGenerator` | `dataflow/operators/knowledge_cleaning/generate/kbc_chunk_generator.py`：token/sentence/semantic/recursive 分块；对超长文本有按空格拆词分段的分支 | **选择性参考**：需要保留页、表格、行、单位、标题等边界；中文和跨页表不能照搬通用切分 |
| Operator / Pipeline 机制 | 主仓 README 描述可复用/可替换算子、Pipeline 编排和 JSON/JSONL/CSV 输入 | 借鉴可复现性与单算子评测，不必立即引入完整 WebUI/Agent/Harness |
| QA 合成 | 通过生成算子从清洗后的内容生成多跳 QA | 仅作为候选评测/训练数据；未校验来源与答案时不是独立 Gold |

相关代码入口：
- https://github.com/OpenDCAI/DataFlow/blob/main/dataflow/statics/pipelines/api_pipelines/kbcleaning_pipeline.py
- https://github.com/OpenDCAI/DataFlow/blob/main/dataflow/operators/knowledge_cleaning/generate/kbc_text_cleaner.py
- https://github.com/OpenDCAI/DataFlow/blob/main/dataflow/operators/knowledge_cleaning/generate/kbc_chunk_generator.py
- https://github.com/OpenDCAI/DataFlow/blob/main/dataflow/prompts/kbcleaning.py
- https://github.com/OpenDCAI/DataFlow/blob/main/LICENSE

**未核验事项**：DataFlow 在本项目中文金融语料上的真实精度、速度、Token 成本、依赖兼容性、长表/跨页表表现和可用权限边界。本文不是性能验证报告。

## 3. 与现有项目接口的适配评估

当前项目已明确“文档生产链 + 问答消费链 + 横切 Evaluation”，Canonical Document 至少需有 `document_id`、`page_number`、`section/heading`、`block_type`、结构化表格/公式、`bbox/reading_order`、`quality_flags`、`source lineage`。

| 对接点 | 已有能力 / 代码 | 候选改造边界 | 判断 |
| --- | --- | --- | --- |
| M1 / M2 数据接入与解析 | `src/data/loader.py`、`src/structure/mineru_adapter.py`、`src/structure/parser.py` | 复用现有解析成果，只接受 canonical 输入，不再重复跑 MinerU | **KEEP** |
| M3 规范化和切块 | `src/structure/blocks.py`、`chunks.py`、`metadata.py`、`src/evidence/structured_tables.py` | 试验 `KnowledgeCleaningAdapter`（可开关），输出 `CleanedView` 而不是覆盖原文 | **PROBE** |
| M4 质量验证 | `src/structure/quality_audit.py`、`corpus_validator.py`、`selective_parser_fallback.py` | 在现有审计之外增加清洗前后不变量、偏差报告、回退判据 | **EXTEND ONLY IF PROVEN** |
| Canonical Store | `src/document/contracts.py`、`src/document/store.py` | 存原文/派生视图双轨、来源偏移和内容 hash，不能丢失权威文本 | **MUST PRESERVE** |
| M6~M10 检索/验证 | `src/retrieval/`、`src/evidence/`、`src/verification/` | A/B 下游配置冻结，仅切换是否使用安全派生清洗视图 | **NO DIRECT REWRITE** |

候选接口（设计示意，**不是已经实现的类**）：

```text
KnowledgeCleaningAdapter.run(
  canonical_document_snapshot,
  policy="deterministic_first",
  external_api_authorized=false
)
 -> CleanedView {
      source_doc_id, source_hash, page_key,
      source_block_ids / table_cell_ids,
      cleaned_text,
      transform_rules, model_or_operator_version,
      changed_spans, quality_flags, output_hash
    }
 -> QualityGate: PASS / REVIEW / FAIL
```

未经定位/血缘验证的 chunk 只能作为“辅助导航文本”，**不得升级为 Claim 的原始支持证据**；清洗失败时应保持旧版 Canonical 路径可用，并对该派生视图 fail closed。

## 4. 金融文档特有的不可破坏约束

1. **数字/单位/口径**：金额、万/亿、币种、比例/百分点、基期、年报期、金额区间、正负号、百分号、公式与成员集合必须不发生语义漂移。字面格式允许受控规范化，但需可逆/可比较并通过等值检查。
2. **结构与绑定**：页码、表格的列头和单位、行成员、脚注、标题层次、跨页表关联必须保留。不得把多个不同年度/业务主体的表格行合并成一段无身份文本。
3. **原文权威**：保留 immutable Raw / Canonical；所有 CleanedView / QA 合成内容均为有版本、可重建、可失效的派生层；答案 Claim 必须仍能定位原始页、块或单元格。
4. **安全与访问控制**：源系统权限、密级、范围由企业授权规则决定；模型 Prompt 里“涉密/PII 替换”不能作为唯一安全保障。对外发布的脱敏视图与内部证据原文须隔离，未经授权不调用外部模型 API。
5. **版本有效性**：同名制度新旧版本、修订与废止、适用主体/生效日、引用文件冲突属于企业知识治理，**不等于 DataFlow 的通用文本清洗**。应保留 `effective_date`、`supersedes`、`access_policy`、`source_authority` 等候选字段，是否入 Canonical 合同由后续单独决策。
6. **LLM 清洗输出不能单独作为 Gold**：QA 合成需要人审或可执行验证，训练与评测集合隔离，防止将合成答案回灌到同题评测中形成泄漏。

反例套件至少包括：`CNY` 与 `kg` 单位错配、2024/2025 数值串年、同名文件跨业务、跨页表头错位、脚注定义删除、负号/括号表示丢失、敏感段落被整句替换、源页引用丢失、重复版本混入。

## 5. 与当前瓶颈及已有知识路线的先后关系

依据项目现有证据，FinDocQA 现阶段主攻中文金融文档的证据首损、Evidence Workspace、Binding 和可信验证；B-CN-02 独立中文覆盖仍不足。文档生产质量是重要上游能力，但**尚无“DataFlow 清洗可改善当前第一瓶颈”的直接测量证据**。

- 不将通用上游清洗设为新第一瓶颈，不在本轮重排 H 系列工作，不改变 `CURRENT.md`、受保护实现或检索默认。
- 与 `docs/reference/京东海博AI-Native知识库对FinDocQA借鉴分析.md` 的 Knowledge Compilation **不同**：DataFlow 偏解析后数据加工；Knowledge Compilation 偏实体/规则/主题与可导航知识。两者可串联，但后者不能绕过原文取证。
- 与 `docs/reference/OpenViking-VikingRAG对FinDocQA借鉴分析.md` 的 Know-where **不同**：OpenViking/VikingRAG 重在 query-time 导航、分层读取、缺口补证；DataFlow 偏 ingest-time 清洗与 QA 候选构造。
- 若将来评估新检索/证据记忆/恢复能力，仍应先依 `docs/reference/对比计划.md` 的 AFAC2026 获奖方案优先参考规则比较适用性，不因出现新开源库而覆盖主线。

## 6. 候选 A/B：P-DQC1 Financial Document Cleaning Probe（待评估者冻结）

**目标**：检验在不损害金融事实与来源权威的前提下，DataFlow 式上游清洗是否对现有解析结果产生可归因的知识质量、证据召回或维护收益。

**样本范围（提议而非已建立）**：
- 5~10 份授权可用的非敏感金融文档，覆盖财报表格、金融合同、保险条款、监管制度；含扫描或跨页表时单独分层。
- 20~30 道附原始页/行/表格/Claim 标注的人工可信问题；区分 DEV、独立 holdout；无法建立独立 Gold 则仅允许诊断，不做效果晋级。
- 冻结 `corpus_snapshot` / 文档 hash、Parser 输出、索引器、Retriever/TopK、Solver、Verifier、随机种子、测试口径和评测代码版本。

**实验设计（单一主变量）**：

| Lane | 输入与流程 | 唯一差异 |
| --- | --- | --- |
| A / BASELINE | 既有 MinerU/Canonical → 既有规范化/切块 → 相同 Retrieval/QA | 现有基线 |
| B / CLEANING | 完全相同 Canonical → **选定清洗算子（默认先确定性去噪）** → 相同 Retrieval/QA | 一种清洗机制的开关 |

不要在同一次 A/B 中同时更换 Parser、chunk 策略、Embedding、Rerank 或模型。若先验证 chunk，则另开独立单变量实验。LLM 清洗只能作为下一阶段备选，单独记录 Prompt、Provider 与审计开销，且需明确授权。

**建议 Gate（由 Evaluator 最终冻结阈值）**：

| 维度 | 最小验收建议 | 测量方式 |
| --- | --- | --- |
| 核心事实保真 | 金额、日期、单位、公司/主体、公式等关键字段错误 **0** | 结构化字段快照比对 + 人审反例 |
| Lineage 与权限 | 被放行的可检索内容 100% 绑定 `doc/page/block` 原文来源及其权限 | 逐 chunk provenance 断言，未知来源 fail closed |
| 表格保真 | 不能丢失受测表格行、列头/单位与跨页关系 | Gold table-cell/row 定位与结构一致性 |
| Required Evidence | 独立题集证据召回不下降；出现负迁移不晋级 | 同配置 Recall@K、evidence F1、protected cases |
| 答案与验证 | 不新增错误放行、不降低 Claim 绑定有效性 | 现有 Verification Gate / negative-control 回放 |
| 加工净收益 | 噪声/重复片段有下降或证据定位有可归因改善，且成本可接受 | 有效 chunk 比率、Token、耗时、计算/人审成本 |
| 复现与隔离 | A/B 双轨可重放；不污染主语料/生产索引 | Manifest、hash、operator version、离线日志 |

失败/停止：任一关键数字或来源不符、产生无法溯源的答案事实、损伤证据召回、默认行为变化、未经批准的外部服务依赖、独立证据不足、成本显著高于收益。失败后保留 A 基线，B 仅留实验记录。

**成果形态**：一个候选 `KnowledgeCleaningAdapter` 的合同与最小离线比较报告（不是上线承诺）。若评估者认定当前确无上游数据质量损失样本，则标 `REFERENCE_ONLY / DEFER`，不为试新框架制造任务。

## 7. 需要 Evaluator 明确裁决的问题

1. **归因与优先级**：当前真实首损是否有被 M3 清洗影响的具体证据？若无，是否保持 `DEFER`，待 Parser/E1 或结构化证据质量的真实失败出现再试验？
2. **复用方式**：直接引入 DataFlow 依赖、仅借鉴算法/Prompt，还是实现本项目的轻量 Adapter？比较耦合、安装/模型依赖、许可、稳定性与可维护成本；推荐默认优先轻量/可替换。
3. **事实权威边界**：清洗派生视图能否做到 page/block/table-cell lineage、原文回指、版本失效和权限过滤？不足时仅做导航、不准作为 Claim 原文证据。
4. **样本与独立性**：是否已有可用的带 Gold 清洗负例、中文金融文档覆盖和独立 holdout？没有时应先完成 E1 数据质量切片而非直接跑全量。
5. **单变量与预算**：候选 A/B 的一个主变量、确定性优先、测试 Gate、最大处理页数/Token/人工复核预算、API 与依赖授权如何冻结？
6. **最终判断**：给出 `ABSORB / REFERENCE_ONLY / REJECT` 的分模块裁决，并说明对现有第一瓶颈的帮助证据，避免“整体 DataFlow 值得学”但没有工程动作。

**建议 Evaluator 输出**：一份简短 Decision Record，包含当前项目全链定位 → 本建议处于哪一段 → 各模块裁决/风险 → 是否触发独立 PoC → 若触发则冻结单变量 Gate 和回退条件。**评估者未批准前，执行者不得以本文为新执行包。**

## 8. 交叉资料

- 项目当前结构：`docs/architecture.md`、`docs/modular-architecture.md`、`docs/module-interfaces.md`。
- 当前真实瓶颈与历史测量：`docs/evaluation/PROJECT_BOTTLENECK_MAP.md`；实际任务权威以 `handoffs/evaluator_executor/state/CURRENT.md` 为准。
- 上下游知识路线：`docs/reference/京东海博AI-Native知识库对FinDocQA借鉴分析.md`、`docs/reference/OpenViking-VikingRAG对FinDocQA借鉴分析.md`。
- 外部优先级：`docs/reference/对比计划.md` 第 6 节与 `docs/history/AFAC2026三团队方案对照与FinDocQA吸收建议.md`。

**状态声明**：截至 2026-10-17，已进行代码与接口层参考分析；没有运行 DataFlow 对 FinDocQA 文档进行清洗 A/B，没有产生性能或准确率结论。
