# FinDocQA 跨项目联动边界

## 1. 项目自己的主线

FinDocQA 仍然是金融文档问答领域项目，不因为 Platform 采用 Capability（能力）架构就提前做通用插件化。

当前端到端链保持：

```text
自然语言问题
→ Query Understanding（查询理解）
→ Retrieval（检索）/ Query Planning（查询规划）/ Know-where（知道去哪里找）
→ EvidenceBundle（证据包）
→ Deterministic Solver（确定性求解）/ LLM Answer
→ Validation（验证）
→ 可追溯答案
```

当前第一优先仍由 `docs/evaluation/PROJECT_BOTTLENECK_MAP.md` 决定。

## 2. 当前映射到 Platform 的一级能力槽位

机器可读清单见 `config/runtime-capability-profile.json`。当前只登记已经能对应到标准一级槽位的实现：

```text
HAR-C 检索器
→ findocqa.retrieval / lexical-hybrid

HAR-C 上下文构建器
→ findocqa.context / evidence-bundle-builder

HAR-E 规划器
→ findocqa.planning / query-plan-builder

HAR-E 执行器
→ findocqa.solver / c3-deterministic-solver

HAR-V 校验器
→ findocqa.validation / answer-evidence-validator
```

Know-where（知道去哪里找）、金融来源血缘模型、金融文档规则继续作为 Domain extension（领域扩展），当前不单独升格为 Platform 一级能力槽位。

## 3. 领域实现与未来 Contract（契约）观察

### 检索器

FinDocQA 已经同时存在 lexical、BM25、hybrid、Query Planning 等路线，是最适合验证“同一能力位多个实现”的项目。

候选公共契约只描述：

```text
input
→ query / scope / corpus handle / options

output
→ ranked evidence candidates
→ source identity
→ score / reason
→ trace metadata
```

金融指标别名、表格坐标、FinanceBench/FinQA 规则等继续留在 FinDocQA。

### Query Planner Contract（查询规划契约）

只在 Query Planning（查询规划）和 Know-where（知道去哪里找）形成稳定输入输出后再考虑公共化。

公共候选只描述：

```text
question + available sources
→ retrieval plan
→ search targets / query variants / stop condition
```

金融领域 ontology（本体）、指标词典、文档目录策略仍留在 FinDocQA。

### Validator Contract（验证契约）

FinDocQA 已有 Evidence / Answer / Source lineage（来源血缘）验证链。未来可与数仓 Agent 的独立校验形成“第二消费者”比较，但公共层只抽：

```text
subject
+ evidence
+ policy / acceptance criteria
→ findings
→ pass / fail / uncertain
→ evidence refs
```

不把金融答案判分规则抽进 Platform。

## 3. 与其他项目的联动

| 联动项目 | 可比较能力 | 当前判断 |
|---|---|---|
| `data-warehouse-agent` | Validator（校验器）、Evidence（证据）、Source lineage（来源血缘） | **最可能形成第二消费者**。先比较 Finding / Evidence / Verdict 结构，不共用业务规则 |
| `agentic-payment-trust-lab` | Trace（轨迹）、Source identity（来源身份）、fail-closed（失败关闭） | 可借鉴证据连续性与来源分层，但支付 Gate 不直接复用到 RAG |
| `agent-runtime-platform` | Registry（注册）、Profile（装配）、Trace hook（轨迹挂点） | FinDocQA 先作为检索能力验证场；未证明跨项目复用前不搬代码 |

## 4. 当前允许抽取的最小公共形状

第一候选不是完整 RAG 插件，而是：

```text
Capability ID
+ implementation ID
+ input contract
+ output contract
+ lifecycle status
+ trace metadata
```

FinDocQA 如果未来做 Retriever A/B，只需要确保同一上层问答流程可以通过配置选择实现，并记录本次实际使用的 implementation ID。

## 5. 当前不做

- 不把 FinDocQA 的 Retriever 代码直接搬进 Platform；
- 不把所有 RAG 路线强行统一成一个大接口；
- 不为了“插件化”重写现有 C3 / E4 评测链；
- 不把 Know-where（知道去哪里找）抽象成通用能力，除非另一个项目出现同类资源定位问题；
- 不用 Platform 架构替代当前瓶颈优先级。
