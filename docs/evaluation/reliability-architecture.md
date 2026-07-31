# FinDocQA Evaluation / Reliability 横切架构

日期：2026-07-31
状态：Evaluation Core V1 已 PASS；C3-B ReliabilityProfile V1-R1 已完成 Executor 验证，待 Evaluator 复评

## 1. 定位

FinDocQA 的产品主旨是：

> 面向金融长文档的知识库 / 文档问答系统，把原始资料可靠地转成可检索知识，再基于证据完成问答、计算、验证和输出。

Evaluation / Reliability 不是第三条业务流水线，而是一个横切质量保障模块：

```text
文档生产链 ────────────────┐
                           │
问答消费链 ────────────────┼──→ Evaluation / Reliability
                           │      判断“做得是否可靠”
模块实现 / 替换 ───────────┘
```

业务模块负责“做事”；评测模块负责：

```text
定义怎么测
→ 运行测试/评测
→ 计算指标
→ 判断 Gate
→ 生成报告
→ 把历史缺陷沉淀为回归资产
```

因此它必须满足两个边界：

1. **不侵入业务逻辑**：Parser / Retriever / Solver 不读取 Gold，不依赖 benchmark，不写 qid 特例；
2. **能覆盖所有模块**：同一套评测框架允许不同模块使用不同 Gold、Metric、Oracle 和测试技术。

---

## 2. 为什么要做成“横切模块”，而不是继续写独立脚本

当前项目已经有 E1～E4 的基础：

```text
src/evaluation/layers/parser_quality.py
src/evaluation/layers/retrieval_quality.py
src/evaluation/layers/reasoning_quality.py
src/evaluation/layers/answer_quality.py
```

这证明“模块级评测”方向已经成立。

但现有 `src/evaluation/` 还混合了多类历史职责：

```text
真正的通用质量评测
+ A/B 实验
+ 比赛候选决策
+ Submission Writer
+ Token / Provider / Formal Route
+ 历史领域 truth adapter
```

如果继续往里面直接增加 C3 Property / Mutation / Combinatorial，最终会再次变成一个“大杂烩”。

下一步不是重写所有评测，而是先建立一个稳定的横切内核，再让现有能力逐步挂到这个内核上。

---

## 3. 总体架构

```text
                       业务模块
Parser / Retriever / Context / Solver / Verifier / Recovery
                          │
                          │ 标准业务输出
                          ↓
                  Evaluation Adapter
                          │
                          ↓
                  Evaluation Case
          input + expected + metadata + slice
                          │
          ┌───────────────┼────────────────┐
          ↓               ↓                ↓
       Metric           Oracle          Technique
 Recall/F1/...      Gold/Invariant     Gold Benchmark
 Accuracy/...       Relation/...       Property
 False Accept       Decision Table     Metamorphic
 Cost/Latency                          Combinatorial
                                      Mutation/Stateful
          └───────────────┼────────────────┘
                          ↓
                  Evaluation Result
                          ↓
                   Reliability Gate
                 PASS / REVIEW / FAIL
                          ↓
             Report + Regression Corpus
```

关键点：**横切不等于把测试代码插进每一次生产请求。**

第一阶段 Evaluation / Reliability 主要是离线能力：

```text
固定输入 / 生成输入
→ 调业务模块
→ Adapter 抽取标准 Observation
→ Evaluator 计算结果
```

未来如需生产观测，只复用统一 Observation / Metric 合同，不让 Gold / Test Generator 进入线上主链。

---

## 4. 通用对象合同

### 4.1 EvaluationCase

统一描述“测什么”，不绑定具体模块。

建议最小字段：

```text
EvaluationCase
├─ case_id
├─ module_id
├─ input
├─ expected / oracle_ref
├─ tags[]
├─ risk_tags[]
├─ slice
└─ provenance
```

例如：

```text
Parser Case
→ input = PDF/page
→ expected = text/table/formula Gold

Retrieval Case
→ input = question + corpus scope
→ expected = doc/page/evidence Gold

C3-B Case
→ input = CanonicalDocument + FormulaEvidence
→ expected = invariant / decision-table outcome
```

### 4.2 EvaluationObservation

统一描述“业务模块实际做了什么”。

```text
EvaluationObservation
├─ module_id
├─ case_id
├─ output
├─ status
├─ trace
├─ lineage
├─ latency_ms
├─ token_usage
├─ cost
└─ failure
```

业务模块不需要返回完全一样的对象，由 `EvaluationAdapter` 映射。

### 4.3 MetricResult

统一描述一个可比较指标。

```text
MetricResult
├─ metric_name
├─ value
├─ threshold
├─ passed
├─ severity
└─ details
```

既支持连续指标：

```text
Recall@10 = 0.92
```

也支持安全不变量：

```text
ambiguous_formula_must_not_execute = PASS
```

### 4.4 EvaluationResult

聚合一次 case 的所有指标：

```text
EvaluationResult
├─ case_id
├─ module_id
├─ metrics[]
├─ violations[]
├─ gate_status
└─ diagnostics
```

### 4.5 ReliabilityProfile

这是横切架构里最重要的对象。

**不是每个模块使用全部测试方法，而是每个模块拥有自己的可靠性配置。**

```text
ReliabilityProfile
├─ module_id
├─ risk_level
├─ failure_modes[]
├─ required_metrics[]
├─ required_invariants[]
├─ test_techniques[]
├─ dataset / generator refs
└─ gate_policy
```

---

## 5. 按风险选测试技术，而不是全项目统一套模板

第一版建议：

| 模块 | 核心风险 | 首选技术 |
|---|---|---|
| Source Adapter | 输入丢失、边界格式异常 | Contract / Boundary / Property / Fuzz |
| Parser / Importer | 文本、表格、公式、布局解析错误 | Gold / Fuzz / Property / Metamorphic |
| Normalizer | 结构映射和 lineage 破坏 | Invariant / Property / Metamorphic |
| Quality / Repair | 错误 fallback、错误放行 | Decision Table / Combinatorial / Mutation |
| Query Understanding | 错分类、错路由 | Gold Classification / Boundary / Metamorphic |
| Document Scope | 漏文档 | Recall@K / Slice / Hard Negative |
| Evidence Retrieval | 漏证据、排序错误 | Recall / MRR / nDCG / Hard Negative / Metamorphic |
| Context Manager | 证据污染、压缩丢关键证据 | Invariant / Property / Combinatorial |
| Solver / Calculation | 算错、错误执行 | Oracle / Invariant / Property / Metamorphic |
| Verification | False Accept / False Reject | Decision Table / Hard Negative / Mutation |
| Recovery | 错误恢复、循环恢复 | State / Sequence / Property |
| End-to-End | 最终答案、证据、成本与失败率 | Fixed Gold / Regression / Slice / Cost / Latency |

结论：

> 测试技术是一套工具箱，`ReliabilityProfile` 决定某个模块从工具箱中取什么。

---

## 6. E1～E4 与横切架构的关系

E1～E4 保留，但它们描述的是**质量观察层级**，不是代码目录必须一一对应，也不是测试方法。

```text
E1  文档有没有读对
E2  正确知识有没有找到
E3  有证据以后有没有算对 / 绑对 / 验对
E4  整条链最终回答是否正确、稳定、可接受
```

同一个模块可以影响多个层级，例如 Context Manager 同时影响 E2 和 E3。

因此未来推荐结构是：

```text
ReliabilityProfile（按模块组织）
        ↓
Metric / Oracle（可跨模块复用）
        ↓
最终汇总到 E1 / E2 / E3 / E4 视图
```

而不是：

```text
所有测试代码都硬塞进 E1/E2/E3/E4 四个大文件
```

---

## 7. 推荐代码结构

第一阶段不要求立即大迁移，目标结构建议为：

```text
src/evaluation/
├─ contracts.py                 # EvaluationCase / Observation / Result
├─ profiles.py                  # ReliabilityProfile
├─ runner.py                    # 通用离线 runner
├─ registry.py                  # metric / oracle / adapter 注册
│
├─ adapters/                    # 业务输出 → Observation
│  ├─ parser.py
│  ├─ retrieval.py
│  ├─ calculation.py
│  └─ verification.py
│
├─ metrics/                     # 纯指标函数
│  ├─ parser.py
│  ├─ retrieval.py
│  ├─ reasoning.py
│  ├─ answer.py
│  └─ reliability.py
│
├─ oracles/                     # Gold / Invariant / Decision Table / Relation
│  ├─ gold.py
│  ├─ invariant.py
│  ├─ decision_table.py
│  └─ metamorphic.py
│
├─ techniques/                  # 生成/攻击方式
│  ├─ combinatorial.py
│  ├─ property_based.py
│  ├─ metamorphic.py
│  ├─ mutation.py
│  └─ stateful.py
│
├─ gates/                       # PASS / REVIEW / FAIL
│  └─ reliability_gate.py
│
├─ reports/                     # 报告对象/序列化，不放运行产物
│  └─ summary.py
│
└─ layers/                      # 现有 E1-E4，过渡期兼容
```

注意：`evaluation_artifacts/` 仍然是运行产物目录，不应与 `src/evaluation/reports/` 的“报告代码”混为一谈。

---

## 8. 现有 `src/evaluation/` 怎么处理

### 8.1 建议保留并逐步纳入通用内核

```text
layers/parser_quality.py
layers/retrieval_quality.py
layers/reasoning_quality.py
layers/answer_quality.py
layers/retrieval_benchmark.py
answer_ab.py
retrieval_ab.py
```

其中前五个已经接近通用 Metric / Benchmark 能力，可以作为迁移起点。

### 8.2 需要重新归类，但本阶段不急着移动

现有：

```text
formal_submission.py
writer.py
token_accounting.py
provider_health.py
preview_cost_guard.py
```

它们更像：

```text
Output / Runtime / Provider / Experiment Infrastructure
```

不应长期作为“通用评测核心”。

### 8.3 历史比赛 / Candidate 决策能力

例如：

```text
unified_candidate_decision_chain.py
dynamic_candidate_funnel.py
exact_doc_coverage.py
multislot_recovery.py
domain_adapters/*
```

需要逐个判断：

- 真正可泛化的能力 → 提炼为 Metric / Oracle / Adapter；
- 数据集专用兼容 → 放到 benchmark / compatibility adapter；
- 纯比赛历史逻辑 → 后续迁入历史兼容区，不继续扩展。

本阶段不做大规模文件搬迁，避免影响现有 700+ 回归测试。

---

## 9. C3-B 作为第一个 ReliabilityProfile 样板

C3-B 当前最适合验证横切架构，因为它的主要风险很清晰：**False Accept**。

建议 Profile：

```text
module_id: c3_formula_context_recovery
risk_level: HIGH

failure_modes:
- ambiguous_formula_accepted
- unresolved_dependency_accepted
- cross_document_recovery
- physical_neighbor_as_binding
- incomplete_lineage_accepted

required_invariants:
- ambiguity_must_not_execute
- unresolved_dependency_must_not_pass
- cross_document_recovery_forbidden
- unrelated_evidence_must_not_change_decision

techniques:
- decision_table
- combinatorial_2way
- selected_3way
- property_based
- metamorphic
- mutation_for_critical_gates

release_gate:
- critical_invariant_violations == 0
- incorrect_but_accepted == 0
- historical_regressions == 0
```

这样 C3-B 的测试不再是一堆散落 case，而成为横切评测框架的第一个模块配置。

---

## 10. 通用 Gate 的边界

`ReliabilityGate` 只负责读取评测结果和 Profile，不执行业务逻辑。

```text
EvaluationResult[]
+ ReliabilityProfile
        ↓
ReliabilityGate
        ↓
PASS / REVIEW / FAIL
```

示例：

```text
Retrieval
→ Recall@10 >= 0.95
→ critical slice recall >= 0.90
→ PASS

C3-B
→ 任一 Critical Invariant violation
→ FAIL

Parser
→ overall text coverage 高
但 formula critical slice 未达标
→ REVIEW / FAIL（由 profile 决定）
```

这样不同模块有不同门槛，但 Gate 机制统一。

---

## 11. 数据资产也要统一，而不仅是代码

横切评测最终依赖四类测试资产：

```text
Gold Corpus
历史 Regression Corpus
Generated / Adversarial Cases
External Benchmark Adapter
```

建议未来统一为：

```text
evaluation/
├─ gold/
├─ regression/
├─ generated/
└─ external/
```

实际大文件、第三方数据许可证和 `.gitignore` 规则另行处理；核心代码只保存合同和轻量 fixture。

历史 Bug 必须逐步从“某次聊天/某份报告”变成 Regression Corpus 中的长期资产。

---

## 12. 实施顺序

### Phase 0｜当前：先冻结架构边界

```text
FinDocQA 主线 = 知识库 / QA
Evaluation = 横切保障
C3-B = 第一个可靠性样板
```

### Phase 1｜通用评测内核 V1

先只实现最小公共合同：

```text
EvaluationCase
EvaluationObservation
MetricResult
EvaluationResult
ReliabilityProfile
ReliabilityGate
```

并用 Adapter 包装现有 E1～E4，不大迁移旧代码。

2026-07-29 V1 已按该边界落地：

```text
src/evaluation/contracts.py
→ EvaluationCase / EvaluationObservation / MetricResult / EvaluationResult

src/evaluation/profiles.py
→ ReliabilityProfile

src/evaluation/gates/reliability_gate.py
→ ReliabilityGate / ReliabilityGateDecision

src/evaluation/adapters/layered.py
→ 现有 E1～E4 result.to_dict() 的薄兼容适配
```

当前 Gate 只读取统一 Metric / Invariant 与 Profile：

- required critical invariant 缺失或违反时 fail-closed；
- 普通 required metric 未达标时由 Profile 决定 `REVIEW / FAIL`；
- 不允许把 required invariant 的缺失降级成 `REVIEW`；
- Gate 内不包含 Parser / Retrieval / C3-B 等模块硬编码；
- 现有 `src/evaluation/layers/` 保持原语义和原入口。

2026-07-29 V1 首轮复评发现 3 类 False Pass，V1-R1 已做安全收口：

```text
1. required metric / invariant 改为逐 case 检查，避免一个 case 掩盖另一个 case 的缺失；
2. MetricResult 新增 MetricKind（METRIC / INVARIANT），同名普通 Metric 不得冒充 required invariant；
3. threshold metric 与 invariant 在构造阶段校验 value / threshold / comparison / passed 自洽，矛盾对象直接 ValueError。
```

R1 不改变 E1～E4 旧指标语义，也不增加任何模块专用 Gate 分支。

### Phase 2｜C3-B Reliability Gate

按照现有计划接入：

```text
Safety Contract
Input Space
Decision Table
2-way / selected 3-way
Property
Metamorphic
Mutation
Regression Corpus
```

验证通用切面是否真的能服务一个高风险模块。

2026-07-29，C3-B ReliabilityProfile V1 已完成第一批真实接线：

```text
src/evaluation/c3b_profile.py
→ HIGH risk Profile + failure modes + required invariants

src/evaluation/adapters/calculation.py
→ FormulaRecoveryResult → EvaluationResult

src/evaluation/oracles/c3b.py
→ C3-B safety invariants
→ Decision Table V1
→ 12-row constrained pairwise baseline
→ selected 3-way safety cases
```

第一批 required invariants：

```text
ambiguity_must_not_execute
unresolved_dependency_must_not_pass
cross_document_recovery_forbidden
unrelated_evidence_must_not_change_decision
incomplete_lineage_must_not_execute
```

其中前四条来自既定 C3-B Safety Contract；第五条直接覆盖 Profile 已声明的 `incomplete_lineage_accepted` 高风险 failure mode，避免“风险有名字但没有 Gate 合同”。

当前仍只实现有界 Decision Table / 组合样板；`property_based / metamorphic / mutation_for_critical_gates` 已声明在 Profile 中，但通用 runner 尚未实现。

### Phase 3｜复制到下一类模块

优先建议：

```text
C3-B
→ Retrieval
→ Parser Quality / Repair
→ Recovery State Machine
```

每复制一个模块，都只增加其需要的 Profile / Adapter / Metric，不复制整套测试技术。

### Phase 4｜整理历史 `src/evaluation/`

在通用内核稳定后再迁移：

```text
Submission / Writer
Provider / Token
Competition Candidate Logic
```

避免现在为了“目录漂亮”制造无业务收益的大返工。

---

## 13. 当前结论

FinDocQA 的正确关系应固定为：

```text
                   产品主线
        金融文档知识库 / 文档 QA
                      ↓
                可替换业务模块
                      ↓
          Evaluation / Reliability 横切
                      ↓
           证明模块是否足够可靠
                      ↓
               继续开发下一能力
```

因此下一步不是继续无限扩写测试方法，而是：

> **先做一个足够小的通用 Evaluation Core，再用 C3-B 验证它。**

验证成立以后，再让 Parser、Retrieval、Verification、Recovery 逐步接入同一个横切框架。
