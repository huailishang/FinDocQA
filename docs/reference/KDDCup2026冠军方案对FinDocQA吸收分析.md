# KDD Cup 2026 DataAgents 冠军方案对 FinDocQA 的吸收分析

> 研究对象：`zhezh/kddcup2026_champion`
>
> 关联中长期方向：Knowhere Document Memory、LLM Wiki Knowledge Compilation
>
> 结论日期：2026-08-13

## 1. 核心结论

这套冠军方案对 FinDocQA 的主要价值，不是“再换一个 RAG / Retriever”，而是补充 **Agent Runtime（智能体运行时）**：

```text
先理解问题
→ 压缩当前工作上下文
→ 主动探查真实数据/资料
→ 根据探查结果再决定查询/计算方法
→ 只允许通过受控工具执行
→ 自检输出
→ 保留轮数、Token、失败状态
```

FinDocQA 当前更强的是：

```text
证据 lineage
Answer Contract
确定性计算
Claim / Fact Binding
Verifier
失败闭合与评测
```

冠军方案更值得补给 FinDocQA 的是：

```text
动态探索
Soft Working Set（软工作集）
受限工具面
任务级干净运行环境
运行时可观察性
```

因此目标不是把 FinDocQA 改造成冠军项目，而是：

```text
FinDocQA 的证据可信链
        +
冠军方案的 Agent Runtime
        +
Knowhere 式 Document Memory
```

---

## 2. 它和 Knowhere 的关系

### 2.1 两者解决的是不同层

Knowhere 解决：

> **资料被解析后，怎样变成 Agent 能导航的长期文档记忆？**

它重点在：

```text
Parser / MinerU
→ section tree
→ hierarchy path
→ chunks / tables / figures
→ summaries
→ links / graph
→ 可追溯 Document Memory
```

冠军方案解决：

> **面对当前问题，Agent 应该先看什么、实际探什么、发现不够后下一步去哪？**

它重点在 query-time runtime：

```text
question
→ relevance screening
→ explore_data
→ solver planning
→ restricted tools
→ run / self-check
```

所以最通俗的比喻是：

```text
Knowhere = 地图 + 路网 + 楼层目录
冠军 Runtime = 导航员 + 驾驶策略
FinDocQA Verifier = 到达目的地后的验货员
```

### 2.2 对 FinDocQA 中长期架构的组合

建议把原来的 Knowhere 中长期方向扩成：

```text
原始 PDF / Office / Markdown
        ↓
Parser / MinerU
        ↓
Canonical Document
        ↓
Knowhere-like Document Memory
章节树 / 页面 / 表格 / 图片 / 跨文档链接
        ↓
Discover / Navigate Tool Surface
        ↓
FinDocQA Query-time Agent Runtime
C1 Question Understanding
→ C1.5 Evidence Plan
→ Active Evidence Workspace
→ Gap-driven Explore
→ Solver / Calculation
→ Verification
        ↓
Answer / Judge
```

LLM Wiki 仍然属于更高一层的 **Knowledge Compilation（知识编译）**：把长期重复使用的主题、实体、规则、对比提前整理成派生知识空间；但事实、数值和条款最终仍要回到 Canonical source evidence。

### 2.3 当前项目其实已经有半套 Runtime

FinDocQA 不是从零开始。

已有：

```text
src/question/understanding.py
→ 已能识别领域、题型、答案形态、时间、比较、计算、跨文档等

src/retrieval/query_plan.py
→ 已能拆 entity / year / metric / relation / section / negative 等检索信号

src/evidence_completion/gap_driven_controller.py
→ 已有 requirement → retrieve → grade → bind facts
   → assess sufficiency → targeted retrieve → recompute 状态机
```

现在缺的更像是把这些能力统一成：

```text
当前问题需要什么证据
→ 当前桌面上有哪些证据
→ 缺什么
→ 去哪里补
→ 补完后是否足够
```

即统一的 **Active Evidence Workspace（活动证据工作区）**。

---

## 3. 冠军方案真实主链里最值得吸收的机制

> 注意：区分“最终主链已启用”和“仓库里存在但未接主链”的实验代码，不能看到文件名就全部当作冠军有效秘诀。

### 3.1 Table Relevance 的 Soft Filter（软过滤）——优先级最高

冠军主链会先判断哪些结构化表更相关，但对低相关表只折叠 prompt 中的 schema 描述：

```text
高相关表
→ 放进当前上下文

低相关表
→ 暂时折叠描述
→ 底层 explore_data 仍注册
→ Solver 需要时仍能重新发现
```

这意味着一次相关性误判的代价从：

```text
永久漏掉数据
```

变成：

```text
多探一步
```

这正对应 FinDocQA 后续最值得考虑的变化：

```text
TopK = 最终候选集
```

逐步变成：

```text
TopK = 当前 Active Working Set
全量可恢复空间仍存在
```

#### 对应 FinDocQA

```text
retrieval/document_scope.py
retrieval/query_plan.py
retrieval/*
evidence_completion/gap_driven_controller.py
```

#### 候选设计

```text
Global Evidence Space
        ↓
Soft Scope Gate
        ↓
Active Evidence Workspace
        ↓
Solver / Verifier 发现 gap
        ↓
Targeted Explore
        ↓
从 Global Space 拉回新证据
```

#### 是否现在实现

否。

原因：当前第一瓶颈仍是 B-06；B-03 虽有历史 Retrieval miss，但 alias / derived / causal 当前各只有 2 个，没有单一机制达到项目 `>=4 independent cases` 的产品实验门槛。

先登记为 B-03 重新激活时的第一候选通用机制。

---

### 3.2 Explore Before Solve（先探查再求解）——与 Knowhere 最直接的连接

冠军 Solver 明确鼓励在写最终 SQL / solver 前使用 `explore_data`：

```text
看 tables
看 schema
看样本
验证 join
验证粒度
验证 NULL / 去重 / 时间口径
        ↓
再写最终计算逻辑
```

这与 FinDocQA 现在主要根据 question text 生成 Query Plan 的区别是：

```text
当前：
问题文字
→ 推断应该搜什么

未来：
问题文字
→ 初始计划
→ 实际查看资料结构/局部证据
→ 根据观测修正计划
→ 再继续检索/计算
```

#### 和 Knowhere 的组合

Knowhere 的 section tree / graph / path 可以成为 FinDocQA 的 `explore_document_memory` 工具底座：

```text
\documents
\sections <doc>
\children <section>
\tables <section>
\search <query>
\neighbors <node>
```

Agent 不必一次把所有 Chunk 塞进上下文，而是先导航，再按需要读取。

---

### 3.3 Restricted Tool Surface（受限工具面）——长期必须吸收

冠军 Solver 明确只开放四类核心工具：

```text
explore_data
run_solver
read_solver
edit_solver
```

并关闭通用 filesystem / execute 等能力。

核心思想：

```text
Agent 决定“做什么”
Tool Contract 决定“允许怎么做”
```

#### 对应 FinDocQA

未来不要变成：

```text
LLM
→ shell
→ 任意文件
→ 任意 Python
→ 任意数据库
```

而应逐步固定：

```text
inspect_document
navigate_structure
retrieve_evidence
inspect_table
calculate
verify_claim
submit_answer
```

每个工具输出都应进入 FinDocQA 已有的 lineage / binding / audit 体系。

优先级：P1（重要，但不是当前第一瓶颈）。

---

### 3.4 Solver Scaffold（求解脚手架）与干净重试

冠军主链先生成固定 `solver.py` scaffold，再只允许 Agent 补查询逻辑；失败 attempt 前恢复 pristine scaffold，避免上一轮脏状态污染下一轮。

FinDocQA 可吸收的不是“最多重试 5 次”，而是：

```text
固定输入/输出 contract
→ 每次执行从可证明的 clean state 开始
→ 失败结果和成功结果不混用
→ 不能拿上一次残留产物冒充本次成功
```

这与 FinDocQA 现有：

```text
experiments/freeze.py
runtime_safety.py
checkpoint / ledger
Evaluator-Executor evidence
```

高度一致。

优先吸收 clean-state / artifact identity，不吸收为了得到成功结果而反复语义重试。

---

### 3.5 Runtime Observability（运行时可观察性）

冠军主入口会记录：

```text
Agent round
每轮耗时
工具调用名
Token
请求数
阶段耗时
```

FinDocQA 已经有 provider ledger / token accounting / evaluation artifacts，因此不需要复制实现；值得补的是统一到“每个 Answer Run 的 stage trace”。

候选统一轨迹：

```text
question understood
→ document scope
→ evidence retrieved
→ evidence gap
→ targeted explore
→ solver
→ calculation
→ verifier
→ answer gate
→ judge
```

这对未来项目瓶颈定位比再增加零散日志更重要。

---

## 4. 哪些仓库内容不能直接当成冠军主链能力

### 4.1 Standalone Pre-Agent

仓库存在 `pre_agent.py`，但最终入口里相关调用被注释。

因此：

```text
可以参考设计
!=
已被最终冠军主链证明有效
```

FinDocQA 不应因为有这个文件就立刻增加一层 LLM question pre-agent。

### 4.2 Standalone Plan-Agent

同理，独立 `plan_agent` 更适合作为设计参考。

最终 Solver 本身启用了 structured planning，但 FinDocQA 若增加 C1.5，应做结构化 Evidence Plan Contract，而不是复制自由 Markdown 计划。

### 4.3 多次重试直到成功

冠军比赛运行时允许多 attempt，是比赛工程策略。

FinDocQA 当前实验原则是：

```text
失败必须保留
不能为了得到漂亮结果而重跑
只有纯网络 transport failure 才允许受控重试
```

所以这部分不吸收。

### 4.4 比赛输出与多模态专用逻辑

暂不吸收：

- `prediction.csv` 比赛特化输出；
- Video / ASR 专用链；
- 与 DataAgent-Bench 文件布局绑定的 scaffold；
- 具体模型组合；
- 为 leaderboard 设计的 retry / parallelism 参数。

---

## 5. “冠军架构 → FinDocQA 模块 → 优先级 → 验证方式”

| 冠军机制 | FinDocQA 对应 | 当前缺口 | 优先级 | 最小验证 |
|---|---|---|---|---|
| Table soft relevance | `retrieval/*` + `evidence_completion/*` | TopK 仍容易被当成硬候选 | P0 候选 | 历史 Retrieval miss 上做 Active/Global workspace replay；同一机制需覆盖 >=4 独立 case 才进入产品实验 |
| Explore before solve | `question/*` + `retrieval/query_plan.py` + `gap_driven_controller.py` | Query Plan 主要来自题面，真实探查反馈还未统一回写 | P0/P1 | 零产品 shadow trace：记录 initial plan → explore observation → revised request，测是否能恢复历史 miss |
| Restricted tools | `solvers/*` + `calculation/*` + `verification/*` | 各模块存在，但还不是一个清晰 Agent Tool Surface | P1 | 定义只读 Tool Contract，不先改业务逻辑；验证 side-effect / lineage 完整性 |
| Clean scaffold | `experiments/*` + `runtime_safety.py` | 已有大部分基础 | P1 | 统一 answer-run artifact identity / clean state / no stale output |
| Round/tool observability | `evaluation/*` + `runtime_safety.py` | Provider 级较强，Answer Runtime stage trace 分散 | P1 | 单题统一 trace，能回答“时间/Token/失败发生在哪一步” |
| Pre-Agent | `question/understanding.py` | 当前规则理解已存在 | P2 | 只有 C1 失败形成 >=4 通用族才考虑模型辅助 |
| Standalone Plan-Agent | 新 `EvidencePlan` contract | 尚无统一显式计划对象 | P2 | 先 shadow，不进入产品路由；验证是否减少 targeted retrieval 轮数或提高 evidence sufficiency |
| Video / ASR | 无 | 非当前范围 | P3 | 暂不做 |

---

## 6. 建议的新中长期运行时架构

```text
C0 Question Adapter
        ↓
C1 Query Understanding
        ↓
C1.5 Evidence Plan（新增候选，不立即开发）
需要证明什么 / 需要什么证据 / 时间 / 范围 / 计算 / 答案形态
        ↓
Document Memory / Knowledge Navigation
Knowhere-like hierarchy + canonical source
        ↓
Soft Scope Gate
        ↓
Active Evidence Workspace
        ↓
Explore / Retrieve / Grade / Bind
        ↓
Evidence Sufficiency
   ┌─────────────┴─────────────┐
   │ sufficient                │ gap
   ↓                           ↓
Solver                    Targeted Explore
   ↑                           │
   └───────────────────────────┘
        ↓
Deterministic Calculation
        ↓
Verifier / Answer Contract
        ↓
Judge / Evaluation
        ↓
Answer
```

关键变化只有两个：

1. Retrieval 输出从“最终候选”降级为“当前工作集”；
2. Agent 可以依据证据缺口回到可导航全局空间继续找，但所有动作受工具合同和 lineage 约束。

---

## 7. 与现有 Knowhere / LLM Wiki 规划如何合并

原来的三个概念应改成一条连续路线：

```text
P-KW1：Knowhere-like Structure Navigation
建立可导航 Document Memory
        ↓
P-DA1：Soft Evidence Workspace
让 Query-time Agent 在小工作集与全局记忆之间切换
        ↓
P-DA2：Explore Before Solve / Evidence Plan
让实际观测反向修正检索和计算计划
        ↓
P-LW1：LLM Wiki Knowledge Compilation
对长期重复主题做派生知识编译
```

其中：

```text
Knowhere = 底层“去哪找”的地图
Champion Runtime = 中间“这次怎么找”的策略
LLM Wiki = 上层“哪些知识值得提前整理”的长期缓存/知识编译
FinDocQA = 证据、计算、验证与回答可信链
```

---

## 8. 当前执行顺序

当前第一瓶颈仍是 `B-06`，H-28 已得到首个 independent fresh-context Judge 证据，但只有 known-wrong 方向。

因此现在不因为冠军项目而跳转开发。

```text
当前：
B-06 / H-29
→ 先补 independent known-correct freeform Judge evidence

并行规划层：
→ 登记冠军 Runtime + Knowhere 合并架构

未来 B-03 真正重新成为第一瓶颈：
→ 首先验证 P-DA1 Soft Evidence Workspace
→ 若需要更强结构导航，再联合 P-KW1
```

产品实验仍执行既有门槛：

> 一个通用机制若不能解释/覆盖至少 4 个独立失败案例，不进入产品修改。

---

## 9. 参考项目

- KDD Cup 2026 DataAgents 冠军方案：`https://github.com/zhezh/kddcup2026_champion`
- KDD Cup 2026 DataAgent-Bench Starter Kit：`https://github.com/HKUSTDial/kddcup2026-data-agents-starter-kit`
- Knowhere：`https://github.com/Ontos-AI/knowhere`
- LLM Wiki：`https://github.com/nashsu/llm_wiki`

重点阅读：

- `src/data_agent_baseline/zz_agent_v2.py`
- `src/data_agent_baseline/agents_v2/solver_agent.py`
- `src/data_agent_baseline/agents_v2/doc_relevance_agent.py`
- `src/data_agent_baseline/agents_v2/table_relevance_agent.py`
- `src/data_agent_baseline/agents_v2/plan_agent.py`（设计参考，需区分是否接入最终主链）
- `src/data_agent_baseline/agents_v2/pre_agent.py`（设计参考，最终入口调用被注释）
