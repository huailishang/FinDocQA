# 京东海博 AI-Native 知识库对 FinDocQA 的借鉴分析

## 1. 定位结论

京东海博团队的 AI 知识库实践，对 FinDocQA 最重要的启发不是“再做一个 RAG”，而是把企业知识从被动资料库升级为 **面向智能体的上下文底座**。

可以把这一层理解为“企业知识大脑”，但在工程上更准确的定义是：

```text
Enterprise Context Fabric
= Knowledge + Evidence + Structure + Rules + State + Lineage
```

它不等于一个统一大知识库，也不负责替代所有业务系统。它的职责是把企业已有的文档、数据、规则、流程、对象关系和已验证结论，整理成 Agent 可以按任务导航、组合、验证和消费的上下文。

FinDocQA 在这套架构中的定位仍然保持不变：负责 **Enterprise Knowledge / Evidence Context**，最终以 Evidence Service / MCP 能力提供给 Control Plane 和业务 Agent。

## 2. 为什么这比传统知识库更接近“企业大脑”

传统知识库通常解决：

```text
文档存进去
→ 用户搜索
→ 找到相关片段
→ 展示或问答
```

面向 Agent 的企业上下文底座需要进一步解决：

```text
当前任务是什么
→ 需要哪些企业对象 / 规则 / 证据 / 历史状态
→ 去哪里找
→ 哪些内容可信
→ 哪些内容已过期或冲突
→ 如何组合成最小充分上下文
→ 哪些事实必须回到原始证据
→ 哪些能力可以继续调用
```

因此未来企业 AI 进入更多工作流后，竞争点会逐渐从“模型会不会回答”转向“企业能不能稳定提供高质量、可验证、可维护的上下文”。

## 3. 海博实践与 FinDocQA 的共同方向

海博实践最值得吸收的是 **Knowledge Compilation 前移**：

```text
原始资料
→ 持续抽取 / 整理 / 关联 / 索引
→ 形成可导航知识
→ Query-time 只做任务相关导航和取证
```

这与 FinDocQA 已有长期路线一致：

```text
Raw / Canonical Document
        ↓
Document Memory
章节、页面、表格、坐标、结构关系
        ↓
Compiled Knowledge
概念、实体、规则、指标、对比、跨文档主题
        ↓
Evidence Service
检索、取证、绑定、计算、验证
        ↓
Business Agent / Control Plane
```

区别在于：金融场景必须比一般工程知识库更严格地保留 Evidence Authority。任何派生知识都不能替代最终原始证据。

## 4. 企业上下文不是一个库，而是多个上下文层

建议未来统一用“企业上下文底座”而不是“单一知识库”理解整体结构：

```text
Enterprise Context Fabric
├─ Knowledge Context
│  ├─ Document Memory
│  ├─ Compiled Knowledge
│  └─ Concepts / Rules / Metrics
├─ Evidence Context
│  ├─ source document
│  ├─ page / section / table / cell
│  └─ provenance / hash / validity
├─ Business Context
│  ├─ customer / account / card / payment / repayment
│  ├─ contracts / events / states
│  └─ enterprise semantics
├─ Runtime Context
│  ├─ current task
│  ├─ permissions
│  ├─ tool state
│  └─ short-term working context
└─ Capability Context
   ├─ available MCP / tools
   ├─ contracts
   └─ execution constraints
```

FinDocQA 不应该吞并这些层，而是把 Knowledge / Evidence 两层做深，并通过稳定接口向上层 Context / Control Plane 提供服务。

## 5. FinDocQA 应坚持的三层事实权威

```text
L0  Canonical Source
    原始 PDF / 文档 / 表格 / 页面
    = 最终事实权威

L1  Document Memory
    章节、页面、结构、表格、坐标、导航关系
    = 原始事实的结构导航

L2  Compiled Knowledge
    概念、实体、规则、指标、比较、跨文档主题
    = 可删除、可重建、可失效的派生知识
```

规则：

- L2 可以加速定位，但不能替代 L0；
- 每个关键 Claim 必须能回溯 L0；
- L1 / L2 均必须保留 lineage；
- 来源更新后，派生知识必须支持 stale / superseded / conflict；
- 不确定或冲突的知识必须显式进入 Knowledge Gap，而不是猜测补全。

## 6. 新增：Knowledge Gap 作为一等对象

海博“待澄清问题”的思想非常适合 FinDocQA。

建议未来定义：

```yaml
type: knowledge_gap
subject: example
claim: example claim
status: unresolved
reason:
  - conflicting_sources
candidate_sources:
  - document_A/page_12
  - document_B/page_8
conflict:
  - value_A
  - value_B
```

这样 Agent 可以区分：

- 没有找到证据；
- 找到了但证据不足；
- 多来源冲突；
- 规则已过期；
- 下游 Binding 无法完成。

这比普通 RAG 的“无答案”更适合企业工作流。

## 7. 后续实验：P-KC1 Knowledge Compilation A/B Probe

原 P-LW1“LLM Wiki 式知识编译探针”升级为：

### P-KC1｜Financial Knowledge Compilation A/B Probe

目标：

> 验证 maintenance-time knowledge compilation 是否能降低 query-time 的检索、归纳和 Token 成本，同时不损害 Evidence Authority。

最小范围：

```text
10～20 份同主题金融文档

A 组：
Raw / Document Memory
→ query-time retrieval
→ evidence
→ answer

B 组：
Compiled Knowledge Index
→ topic / concept navigation
→ 回到 Document Memory / Canonical Source
→ evidence
→ answer
```

必须比较：

- Required Evidence Recall；
- Claim → 原始来源回溯率；
- 跨文档问题正确率；
- query-time 检索轮数；
- Token / latency；
- stale / superseded / conflict 传播正确率；
- 人工维护成本；
- 是否出现派生知识误当原始事实。

晋级条件：

- 来源回溯稳定；
- Query-time 成本明显下降；
- 不降低证据召回和验证可靠性；
- 维护成本可接受；
- 至少在跨文档 / 宽主题问题上产生可归因收益。

停止条件：

- 只是多维护了一份摘要；
- 更新成本高于查询收益；
- 无法可靠传播来源失效；
- Compiled Knowledge 开始成为事实黑盒；
- 为了知识编译而打断当前产品主瓶颈。

## 8. 与当前主线的关系

这项方向 **不改变当前 H-74 / B-CN-01 主线优先级**。

当前第一问题仍是：

```text
Candidate Evidence
→ bounded workspace
→ evidence retention
→ semantic / binding
→ calculation / verdict
```

要先确认真实首损发生在哪里。

Knowledge Compilation 属于长期 Context / Knowledge 基础设施方向，应在当前主链瓶颈有足够证据后做 bounded probe，而不是现在建设完整 Wiki / Graph / 企业大脑平台。

## 9. 长期产品定位

FinDocQA 不应停留为“金融 RAG 问答系统”。

长期更合适的定位是：

```text
Enterprise Knowledge / Evidence Infrastructure
        ↓
Evidence Service / MCP
        ↓
Enterprise Context Fabric
        ↓
Business Agents / Workflows
```

所谓“企业知识大脑”，本质上不是一个大模型或一个数据库，而是一套让企业知识、证据和业务上下文持续变成 Agent 可消费输入的框架。

FinDocQA 是其中 Knowledge / Evidence 这一块的核心能力来源，而不是整个企业大脑的全部。
