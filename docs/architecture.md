# Architecture

## 总体原则

FinDocQA 的主旨不是“测试平台”，而是一个面向金融长文档的知识库 / 文档问答系统。测试与评测属于横切质量保障能力，用来证明各业务模块是否可靠，而不是替代业务主线。

整体采用“**两条业务链 + 统一合同 + 一个横切可靠性面**”的架构：

```text
                         FinDocQA
              金融文档知识库 / 文档问答系统
                              │
             ┌────────────────┴────────────────┐
             ↓                                 ↓
        文档生产链                         问答消费链
各种输入 → 标准语料              标准语料 + 问题 → 答案与证据
             │                                 │
             └──────────────┬──────────────────┘
                            ↓
                  统一数据 / 结果 / 血缘合同

────────────────────────────────────────────────────
              Evaluation / Reliability 横切面
     模块测试 · Benchmark · Invariant · Regression
     组合/属性/变形测试 · Gate · 运行质量观测
────────────────────────────────────────────────────
```

因此项目始终按下面的优先级组织：

```text
产品能力建设
→ 模块实现可替换
→ 模块可靠性可证明
→ 跨模块集成可验证
→ End-to-End 可评测
```

不能反过来为了建设测试体系而让业务主线长期停滞。每完成一个关键模块，只建设与其风险相匹配的 Reliability Gate，通过后继续下一能力。

详细模块拆分见 `docs/modular-architecture.md`；横切评测设计见 `docs/evaluation/reliability-architecture.md`。

## A. 文档生产链

```text
PDF / Markdown / HTML / JSON / 外部 Benchmark
→ Source Adapter
→ Parser / Importer
→ Normalizer
→ Quality Audit / Repair
→ Canonical Document Store
```

### 输入适配

输入不默认等于 PDF。

- 原始 PDF：运行 MinerU、PyMuPDF、OCR/VLM 等可替换 Parser；
- 已解析 PDF：直接导入页面文本、页码和结构；
- Markdown / HTML / JSON：跳过 PDF Parser，直接标准化；
- 外部 Benchmark：通过 Dataset Adapter 映射为统一文档、问题和 Gold Annotation。

### Canonical Document

后续模块只读取统一文档对象，不依赖具体 Parser。

标准结构至少保留：

```text
document_id
page_number
text
section / heading
block_type
structured table
formula
figure
bbox / reading_order
quality flags
source lineage
```

Parser 的职责止于生成标准结构；Retriever 不应知道上游使用 MinerU 还是其他方案。

### 质量与回退

解析质量异常时按页执行受控 fallback：

```text
主 Parser
→ 质量审计
→ 同文档/同页替代 Parser
→ 必要时 OCR / VLM
```

重点检测复杂表格、公式、阅读顺序、扫描页、图表、单位和跨页结构。

## B. 问答消费链

```text
题目
→ Query Understanding
→ Document Scope / Document Retrieval
→ Evidence Retrieval / Ranking
→ Evidence Assembly / Context Management
→ Solver / Reasoner
→ Claim Verification
→ Recovery
→ Output Adapter
```

### Query Understanding

识别领域、基础题型、复合标签、答案合同和下游策略需求。

### Document Scope / Retrieval

先确定候选文档范围，再执行文档级召回。Embedding、BM25、Hybrid、Reranker 都应作为可替换实现，而不是写进主 workflow。

### Evidence Retrieval / Context

在文档内部定位 page / section / block / table / formula，随后执行精排、去重、跨文档完整性检查和最小充分证据压缩。

### Solver

按题型处理事实查找、多选、计算、比较、排序、跨文档推理和自由文本。可确定的算术优先由 Python 计算。

### Verification

复杂答案拆成 Claim Atom，并绑定主体、指标、时间、值、单位、条件、例外和来源，输出 SUPPORT / REFUTE / UNRESOLVED。

### Recovery

只按已识别根因恢复：

```text
缺证 → 补搜
Binding → 重绑
计算 → 重算
Parser → 回文档生产链修复
Unknown → STOP
```

### Output

核心链输出通用 `ResultRecord`。JSON、Markdown、API Response、Benchmark Prediction、比赛 CSV 都只是不同 Output Adapter。

比赛提交格式不得继续污染核心 Question / Solver / Workflow 合同。

## C. Evaluation / Reliability 横切面

评测不是第三条业务流水线，而是覆盖文档生产链和问答消费链的横切能力。业务模块负责“做事”，Evaluation / Reliability 负责“判断做得是否可靠”。

统一保留四层质量视角：

```text
E1 Parser / Document Quality
E2 Retrieval / Evidence Quality
E3 Reasoning / Verification Quality
E4 End-to-End Answer Quality
```

但具体测试技术不按 E1～E4 机械套用，而由模块风险决定。例如：

```text
Parser              → Gold + Fuzz + Property
Retrieval           → Recall/nDCG + Hard Negative + Metamorphic
Calculation / Gate  → Invariant + Decision Table + Combinatorial + Property + Mutation
Recovery            → State / Sequence + Property
End-to-End          → Fixed Gold + Regression + Cost/Latency + Failure Analysis
```

横切评测层至少负责六类公共能力：

1. **Gold / Case Contract**：统一测试样本、Gold、证据与数据切片描述；
2. **Evaluator Adapter**：不同模块把输出映射到统一评测输入；
3. **Metric / Oracle**：Recall、F1、Accuracy、Invariant、False Accept/Reject 等；
4. **Test Technique Profile**：按风险选择组合、属性、变形、Mutation、Stateful 等技术；
5. **Reliability Gate**：把指标和安全约束转成 PASS / REVIEW / FAIL；
6. **Report / Regression Corpus**：统一保存可复现结果和历史缺陷样本。

这样最终答案下降时，可以继续定位根因来自：

```text
没读对文档
还是
没找到证据
还是
上下文组装不完整
还是
有证据但算/判断错
还是
恢复/输出合同问题
```

评测切面不得反向侵入业务模块：Parser、Retriever、Solver 等不应为了某个 benchmark 写 Gold 特例，也不应依赖具体评测脚本。

详细设计见：

- `docs/evaluation/reliability-architecture.md`
- `docs/evaluation/local-benchmark.md`
- `docs/evaluation/external-benchmarks.md`

## 扩展原则

Embedding、Rerank、图检索、OCR/VLM、动态记忆和 Agent 编排都可以增加，但必须满足：

1. 通过统一合同接入；
2. 不向下游泄露具体 Parser / Dataset 实现；
3. 有独立模块评测；
4. 不引入 qid / dataset 特例；
5. 保留完整文档、页面、证据和运行血缘。
