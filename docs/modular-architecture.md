# FinDocQA 模块化架构蓝图

## 1. 改造目标

FinDocQA 不再围绕某一场比赛的 100 道题组织代码，而是变成一个可替换、可评测、可审计的金融长文档问答框架。

核心原则：

```text
不同输入来源
→ 统一成同一种内部文档格式
→ 后续问答链只依赖统一格式
→ 每个模块都能单独替换
→ 每个模块都有自己的评测
```

因此，后续不要再把“PDF 解析器”和“答题逻辑”绑在一起。

---

## 2. 总体结构：两条业务链 + 一个横切可靠性面

FinDocQA 的本体仍然是金融文档知识库 / 文档问答系统。Evaluation / Reliability 不作为第三条业务链，而是横切所有模块的质量保障面。

```text
                         FinDocQA
              金融文档知识库 / 文档问答系统
                              │
             ┌────────────────┴────────────────┐
             ↓                                 ↓
      Document Production                QA Consumption
          文档生产链                         问答消费链
             │                                 │
             └──────────────┬──────────────────┘
                            ↓
                    Canonical Contracts

────────────────────────────────────────────────────
              Evaluation / Reliability
     Gold / Benchmark / Metric / Oracle / Gate
     Regression / Property / Metamorphic / Mutation
────────────────────────────────────────────────────
```

核心原则是：

```text
先建设知识库 / QA 能力
→ 再用与风险匹配的测试方法证明可靠性
→ Gate 足够后继续下一业务能力
```

不把测试体系本身变成项目主线，也不要求每个模块机械使用同一套测试技术。

### A. 文档生产链（Document Production Pipeline）

```text
输入源
→ Source Adapter
→ Parse / Import
→ Normalize
→ Quality Audit
→ Repair / Fallback
→ Canonical Document Store
```

这条链负责回答：

> 原始资料如何变成后续模块都能稳定使用的标准语料？

### B. 问答消费链（QA Consumption Pipeline）

```text
题目
→ 题型 / 任务理解
→ 文档范围
→ 文档召回
→ 证据检索
→ 精排 / 去重 / 压缩
→ 求解
→ Claim 验证
→ 有界恢复
→ 输出
```

这条链只读取 Canonical Document Store，不关心原始输入来自 PDF、Markdown、HTML、JSON，还是别人已经整理好的 benchmark 数据。

---

## 3. 输入层必须支持多种来源

输入不能再默认等于“原始 PDF”。建议定义四种 Source Adapter。

### M0-A 原始 PDF Adapter

适用：

- 自己收集的财报、合同、保险条款、法规、研报；
- FinanceBench 原始 PDF；
- MMLongBench-Doc 等原始 PDF benchmark。

流程：

```text
PDF
→ MinerU / 其他 Parser
→ 页面 / Block / Table / Formula
→ 标准化
```

这里解析器只是可替换插件：

```text
MinerU
PyMuPDF / PyMuPDF4LLM
Marker
OCR / VLM Parser
未来其他 Parser
```

### M0-B 已解析 PDF Adapter

适用：别人已经完成 PDF → 文本/页面的场景。

例如数据已经提供：

```text
page text
page number
section
source document
```

则不重复跑 MinerU，只做格式映射和质量校验。

### M0-C 结构化文本 Adapter

适用：

- Markdown
- TXT
- HTML
- JSON
- XML
- 已抽取的段落列表

这类输入直接进入 Normalize，不需要 PDF Parser。

### M0-D Benchmark / Dataset Adapter

适用：

- TAT-QA
- MultiHiertt
- DocFinQA
- Loong
- 其他已经整理好 question / context / answer / evidence 的数据集

Adapter 的职责是把外部字段映射为 FinDocQA 的统一合同，而不是为每个数据集修改核心 Solver / Retriever。

---

## 4. Canonical Document：整个系统最重要的边界

所有输入最终都应该变成同一种内部结构。

建议目标合同：

```text
CanonicalDocument
├─ document_id
├─ title
├─ domain
├─ source_type
├─ source_uri / source_path
├─ parser_name
├─ parser_version
├─ metadata
└─ pages[]

CanonicalPage
├─ page_number
├─ text
├─ sections[]
├─ blocks[]
├─ tables[]
├─ formulas[]
├─ figures[]
├─ bbox / layout metadata
├─ quality_flags[]
└─ lineage

CanonicalBlock
├─ block_type
│   ├─ text
│   ├─ heading
│   ├─ table
│   ├─ formula
│   ├─ figure
│   └─ list
├─ content
├─ bbox
├─ reading_order
└─ source lineage
```

关键原则：

> Retriever 以后不应该知道“这是 MinerU HTML 表格”；它只应该知道“这是一个 canonical table block”。

这样才能真正换 Parser 而不重写后面 8 个模块。

---

## 5. 文档生产链拆成 4 个模块

### M1 Source Adapter

职责：识别输入类型并加载原始数据。

输入：

```text
PDF / Markdown / HTML / JSON / Benchmark Dataset
```

输出：

```text
RawDocumentSource
```

当前可复用：

- `src/data/loader.py` 的部分数据加载思想；
- MinerU manifest / 文档 catalog 相关能力。

主要缺口：当前 Question Loader 与比赛字段耦合较多，文档输入还没有独立的一等 Source Adapter 合同。

### M2 Parser / Importer

职责：把 RawDocumentSource 转成页面和结构块。

对 PDF：

```text
MinerU / PyMuPDF / OCR / VLM
```

对已解析数据：

```text
直接 import，不二次 OCR
```

当前可复用：

- `src/structure/mineru_adapter.py`
- `src/structure/parser.py`
- `src/structure/selective_parser_fallback.py`

主要缺口：Parser 输出仍主要围绕 page Markdown / MinerU JSON，尚未统一为完全 parser-agnostic 的 CanonicalDocument。

### M3 Normalizer / Structuralizer

职责：把不同 Parser 的输出统一结构化。

包括：

- 标题层级；
- 阅读顺序；
- 表格表头 / rowspan / colspan；
- 单位；
- 公式；
- caption / footnote；
- page / bbox / source lineage。

当前可复用：

- `src/structure/blocks.py`
- `src/structure/chunks.py`
- `src/evidence/structured_tables.py`
- `src/structure/metadata.py`

主要缺口：表格能力目前部分位于 Evidence 层，长期应向 Canonical Structure 层下沉。

### M4 Parser Quality / Repair

职责：先判断解析质量，再决定是否 fallback。

检测：

- 空页 / 低文本密度；
- reading order 异常；
- 表格结构缺失；
- 表头错位；
- 跨页表；
- 单位不清；
- formula 内容缺失；
- 扫描页；
- 图片 / 图表页。

当前可复用：

- `src/structure/quality_audit.py`
- `src/structure/selective_parser_fallback.py`

当前已经有的风险项包括：

```text
reading_order_suspect
cross_page_table_candidate
numeric_table_unit_unclear
table_row_width_inconsistent
formula_machine_content_missing
scan_like_page
unresolved_visual_asset
```

主要缺口：

```text
MinerU 弱
→ PyMuPDF fallback
```

已有基础，但扫描件 / 图表 / image-table 场景仍缺成熟 OCR/VLM 第三路 fallback。

---

## 6. QA 消费链重新整理为 7 个模块

原 9 步没有废掉，而是合并成更清晰的可替换模块。

### M5 Query Understanding

对应原 STEP01。

职责：

- domain；
- basic question type；
- calculation / cross-doc / negation / temporal 等复合标签；
- Answer Contract；
- 检索策略需求。

当前实现：

- `src/classification/`
- `src/answer_contract.py`

### M6 Document Scope & Document Retrieval

对应原 STEP02 + STEP03。

职责：

```text
问题
→ 哪些文档可能相关
→ 文档级排序 / Recall@K
```

当前实现：

- `src/retrieval/document_scope.py`
- `src/retrieval/document_catalog.py`
- `src/retrieval/hybrid.py`
- `src/retrieval/query_plan.py`

后续可替换：

- lexical
- embedding
- hybrid
- metadata filter
- document reranker

### M7 Evidence Retrieval & Ranking

对应原 STEP04 + STEP05 的前半段。

职责：

```text
已选文档
→ page / section / block retrieval
→ table / formula aware retrieval
→ rerank
→ evidence candidates
```

当前实现：

- `src/retrieval/financial_target_page_locator.py`
- `src/retrieval/focused_exact_pages.py`
- `src/evidence/structure_aware.py`

后续可替换：

- BM25
- Embedding
- Cross-Encoder Reranker
- table-aware retriever
- formula-aware retriever
- visual retrieval

### M8 Evidence Assembly & Context Manager

对应原 STEP05 后半段 + STEP06。

职责：

- 去重；
- 同页 / 相邻页补上下文；
- 跨文档完整性；
- 最小充分证据；
- token budget；
- Solver view 与 Verification view 分离。

当前实现：

- `src/evidence/assembler.py`
- `src/evidence/enhanced_assembler.py`
- `src/evidence/minimal_sufficient_set.py`
- `src/evidence/prompt_budget.py`

### M9 Solver / Reasoner

对应原 STEP06/按题型求解。

职责：

- direct fact;
- multi-choice;
- calculation;
- comparison;
- ranking;
- cross-document reasoning;
- free-form extraction。

当前实现：

- `src/solvers/`
- `src/composite/`

原则：确定性计算尽量 Python 化，LLM 不承担可避免的心算。

### M10 Verification

对应原 STEP07。

职责：

```text
answer
→ claim atoms
→ evidence binding
→ SUPPORT / REFUTE / UNRESOLVED
```

绑定：

- subject；
- metric；
- value；
- time；
- unit；
- condition；
- exception；
- source lineage。

当前实现：`src/verification/`。

### M11 Recovery & Output

对应原 STEP08 + STEP09。

Recovery：

```text
缺证 → 补搜
Binding 错 → 重绑
计算错 → 重算
Parser 弱 → 回到 M4
Unknown → STOP
```

Output：

```text
Answer Contract
+ Evidence / Citation
+ Reasoning summary
+ Cost / Trace
```

长期要求：比赛 CSV 只是一个 `Output Adapter`，不能再作为核心数据合同。

---

## 7. 当前最明显的“比赛遗留耦合”

当前 `src/contracts.py` 和主 workflow 中仍存在大量：

```text
submission_slot_count
submission_slot_contracts
submission_answers
submission_mode
submission_template
```

这些概念过去服务于比赛，但不应继续作为 FinDocQA 核心对象。

目标改造：

```text
核心：AnswerContract / AnswerValue / ResultRecord
                     ↓
Output Adapter
├─ JSON
├─ Markdown
├─ API response
├─ benchmark prediction
└─ competition CSV（仅兼容适配器）
```

因此后续需要把“比赛提交合同”从 Solver / Workflow / Question 核心对象中逐步抽离。

---

## 8. 评测体系：除了最终答案，再增加 3 层评测

最终形成四级评测。

### E1 Parser / Document Quality Evaluation

回答：**文档有没有读对？**

指标：

```text
Text Coverage
Page Alignment
Reading Order Accuracy
Table Structure Accuracy
Table Cell / Header Accuracy
Formula Preservation
Unit Preservation
OCR / Scan Success
Figure / Chart Availability
Lineage Accuracy
```

适合数据：

- 自己人工挑选的 50 页 Parser Gold；
- FinanceBench PDF；
- MMLongBench-Doc PDF。

### E2 Retrieval / Evidence Evaluation

回答：**读对之后，有没有找到正确内容？**

指标：

```text
Required Document Recall@K
Page Recall@K
Evidence Recall / Precision / F1
Cross-document Complete Recall
MRR / nDCG（有排序 gold 时）
Evidence Coverage by Claim
```

适合数据：

- FinanceBench evidence page / evidence text；
- MultiHiertt text/table evidence；
- 自有 Gold evidence；
- Loong 多文档任务。

### E3 Reasoning / Verification Evaluation

回答：**证据已经给对了，系统会不会算错、绑错、误判？**

指标：

```text
Calculation Accuracy
Program / Formula Accuracy
Claim SUPPORT/REFUTE/UNRESOLVED Accuracy
Evidence Binding Accuracy
False Accept Rate
False Reject Rate
Recovery Decision Accuracy
```

适合数据：

- TAT-QA；
- MultiHiertt；
- DocFinQA；
- 自有确定性计算 / 法规 / 保险 Gold。

### E4 End-to-End QA Evaluation

回答：**整条链最后好不好用？**

指标：

```text
Answer Accuracy
Exact Match / Set F1 / Numeric Tolerance
Evidence-backed Answer Rate
Latency
Token / Cost
Failure Rate
```

这一级才是过去比赛里最关注的“答案分”。

---

## 9. 模块与评测对应关系

| 模块 | 主要评测 | 失败时先修什么 |
| --- | --- | --- |
| M1 Source Adapter | contract / completeness | 数据接入 |
| M2 Parser | E1 | Parser / OCR |
| M3 Normalizer | E1 | 表格、公式、布局标准化 |
| M4 Quality / Repair | E1 | fallback / quality gate |
| M5 Query Understanding | classification eval | 题型与策略 |
| M6 Document Scope | E2 | 文档级召回 |
| M7 Evidence Retrieval | E2 | page/block retrieval + rerank |
| M8 Context Manager | E2 + token eval | evidence completeness / compression |
| M9 Solver | E3 | 计算 / 推理 |
| M10 Verification | E3 | Claim / Binding |
| M11 Recovery / Output | E3 + E4 | failure routing / result contract |
| 全链 | E4 | 根据前三层定位根因 |

---

## 10. 后续优化顺序

以后不再说“这个项目下一步加 Rerank 还是 Agent”，而是先看模块评测。

建议顺序：

```text
Phase 1
统一 Canonical Document Contract
+ 把输入适配层独立出来

Phase 2
建立 E1 Parser Benchmark
先量化 MinerU / fallback 到底差在哪里

Phase 3
建立 E2 Retrieval Benchmark
再决定 BM25 / Embedding / Rerank 各自补什么

Phase 4
建立 E3 Reasoning / Verification Benchmark
专测计算、Claim、Binding、Recovery

Phase 5
统一 E4 End-to-End Benchmark
A/B 私有 Gold + 外部公开 benchmark

Phase 6
根据指标逐模块替换，不做 qid 特例
```

---

## 10.1 后续知识组织层（规划，不进入当前主线）

在 Canonical Document 和 QA 消费链之间，后续可以通过两个独立实验验证是否需要增加知识组织层：

```text
Canonical Document Store
        ├─ Document Memory Index
        │   └─ 章节树、表格、图片、坐标和结构导航
        └─ Compiled Wiki
            └─ 来源摘要、概念、实体、规则和跨文档对比
```

对应外部借鉴：

- Knowhere：借鉴 Parser 之后的文档层级重建、结构导航和多模态对象绑定；
- LLM Wiki：借鉴“不可变原始资料 → 可重建派生 Wiki → Schema/维护规则”的知识编译方式。

边界：

```text
原始文档和坐标 = 最终事实来源
Document Memory = 导航索引
Wiki = 可失效、可重建的派生知识
```

规划顺序：

```text
完成当前 B-03 / H-07
→ P-KW1：10～20 份文档的结构导航探针
→ 仅在 E2 指标改善后扩大 Document Memory
→ P-LW1：小规模来源可追溯 Wiki 探针
→ 不直接建设完整知识图谱、桌面应用或新平台
```

详细方案见 `reference/Knowhere与LLM-Wiki借鉴分析.md`。

---

## 11. 一句话目标架构

```text
任何文档来源
      ↓
可替换的输入 / Parser 模块
      ↓
统一 Canonical Document
      ↓
可替换的 Retrieval / Evidence / Solver / Verifier
      ↓
统一 Result
      ↓
不同 Output Adapter
```

FinDocQA 的核心价值不再是“这 100 道题能答多少分”，而是：

> **每一层都知道自己输入什么、输出什么、好坏怎么测，并且能够独立替换。**
