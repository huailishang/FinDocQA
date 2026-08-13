# Knowhere 与 LLM Wiki 对 FinDocQA 的借鉴分析

## 1. 核心结论

Knowhere 与 LLM Wiki 都不能替换 FinDocQA，它们分别补充两层不同能力：

```text
原始 PDF / Markdown / Benchmark 数据
        ↓
Parser / MinerU
        ↓
Knowhere 式 Document Memory
章节树、表格、图片、坐标、结构路径、跨文档导航
        ↓
LLM Wiki 式 Knowledge Compilation
来源摘要、概念、实体、规则、对比、综合页面
        ↓
FinDocQA QA Chain
问题理解 → 文档召回 → 证据检索 → 证据绑定
→ 确定性计算 → 验证 → 输出
```

三层的事实边界必须固定：

```text
原始文档及坐标 = 最终事实来源
Document Memory = 原始文档的结构导航层
Wiki 页面 = 可失效、可重建的派生知识层
```

最终回答不能只引用 Wiki 摘要。涉及事实、数值、条款、条件、例外和计算时，必须回到原始文档证据并保留 lineage。

---

## 2. Knowhere 值得借鉴什么

Knowhere 的价值不在于再次执行 PDF → Markdown，而在 Parser 之后继续处理文档结构，使 Agent 不再只面对平面的 Chunk 列表。

### 2.1 结构导航对象

FinDocQA 后续可以在 Canonical Document 之上补充：

```text
DocumentMemory
├─ document_id
├─ section_tree
├─ section_path
├─ parent / child
├─ previous / next sibling
├─ tables
├─ figures
├─ source assets
├─ cross-document links
└─ source hash / lineage
```

证据不再只是：

```text
Chunk A
Chunk B
Chunk C
```

而是：

```text
文档
└─ 第三章
   └─ 3.2 收入
      ├─ 段落
      ├─ 表格 T03
      │  ├─ 表头
      │  ├─ 行 R01
      │  └─ 行 R02
      └─ 图片 F02
```

### 2.2 分层检索

借鉴后的检索链应逐步从一次性平面 TopK 转为：

```text
问题
→ 文档级召回
→ 章节 / 页面导航
→ 表格 / 段落定位
→ 完整行、成员范围和坐标
→ EvidenceBundle
```

这与当前 B-03 的问题直接相关：正确文档或表格可能已找到，但固定文本窗口没有覆盖完整表行或成员范围。

### 2.3 与现有 MinerU 的关系

FinDocQA 已有 MinerU 解析结果，不应为了使用 Knowhere 重跑全部 PDF。

优先验证：

```text
现有 MinerU Markdown / JSON / 图片
→ Structure Adapter
→ section_tree.json
→ tables.json
→ figures.json
→ links.json
→ Document Memory Package
```

Parser 继续可替换，Document Memory 只消费 Canonical Document，不绑定 MinerU 私有格式。

### 2.4 当前不直接引入的部分

暂不进入主线：

- 完整 Knowhere 服务栈；
- 全量跨文档图构建；
- 为所有图片调用 VLM；
- 因项目概念吸引而重做已稳定的 MinerU 层；
- 在没有 E2 指标改善证据前替换现有 Retriever。

---

## 3. LLM Wiki 值得借鉴什么

LLM Wiki 的核心不是普通问答，而是把反复使用的知识提前编译成可维护、可增量更新的 Markdown 知识空间。

### 3.1 三层分离

建议沿用其核心边界：

```text
Raw Sources
不可变原始资料
        ↓
Wiki
LLM 生成、可更新、可删除重建的派生页面
        ↓
Schema / Rules
页面类型、来源要求、摄入、更新、Lint 和失效规则
```

对 FinDocQA 来说，应进一步强化来源绑定：

```yaml
page_type: concept
title: 信用卡可用额度
sources:
  - document_id: xxx
    page: 18
    section_path: 第三章/额度管理
    source_hash: abc123
review_status: pending
```

每个可用于回答的 Claim 还应继续绑定到原文页、表格、行或坐标。

### 3.2 建议的 Wiki 页面类型

第一阶段只保留必要页面：

```text
wiki/
├─ index.md
├─ overview.md
├─ sources/       单份资料摘要
├─ concepts/      概念、指标、方法
├─ entities/      机构、产品、法规主体
├─ rules/         规则、条件、例外、适用范围
├─ processes/     流程和环节
├─ comparisons/   版本、制度、产品对比
└─ queries/       已验证、值得沉淀的问题结论
```

不要求一开始建设知识图谱或桌面 UI。

### 3.3 对 FinDocQA 查询链的使用方式

精确事实或计算题：

```text
问题
→ 原始 Document Memory
→ 表格 / 条款证据
→ 计算与验证
```

宽泛主题或跨文档问题：

```text
问题
→ Wiki 定位主题、概念和相关文件
→ Document Memory 定位章节和原始证据
→ FinDocQA 验证并回答
```

Wiki 只能作为导航和已有知识复用层，不能作为无条件可信的最终证据。

### 3.4 必须补充的可靠性机制

- 原始资料不可变；
- Wiki 页面保留 `sources[]`；
- 来源修改后，相关页面标记 stale；
- 来源删除后，执行级联失效而不是保留孤立结论；
- 新旧来源矛盾时保留版本和冲突记录；
- 定期检查孤立页面、过期结论、无来源 Claim 和断链引用；
- 重要结论进入 `review_status: accepted` 前需要人工或规则复核。

---

## 4. 与 FinDocQA 模块的对应关系

| 外部思想 | FinDocQA 位置 | 作用 | 不承担的职责 |
|---|---|---|---|
| Knowhere 文档结构记忆 | M3 Normalizer / Structuralizer 之后，M6/M7 之前 | 章节树、表格、图片、坐标和结构导航 | 最终答案生成、确定性计算 |
| LLM Wiki 知识编译 | Canonical Store 之上的派生知识层 | 主题目录、概念、实体、规则、对比和跨文档复用 | 原始事实来源、最终证据 |
| FinDocQA QA Chain | M5—M11 | 问题理解、检索、绑定、计算、验证和输出 | 替代原始资料或隐藏来源 |

长期目标结构：

```text
Canonical Document Store
        ├─ Document Memory Index
        │   └─ 服务精确结构导航
        └─ Compiled Wiki
            └─ 服务主题导航和跨文档知识复用

两者最终都回到 Canonical source lineage
```

---

## 5. 后续实验路线

这两个方向继续保留在后续规划层。当前第一瓶颈已经转为 B-06 / H-29，因此不打断当前 Judge 证据完善主线。

结合 KDD Cup 2026 DataAgents 冠军方案后，Knowhere 的定位进一步明确：它负责建立**可导航的 Document Memory（文档记忆）**，而冠军方案提供 query-time Agent Runtime（查询时智能体运行时）的“先探查、再规划、发现证据缺口后继续导航”策略。两者应串成：

```text
Knowhere-like Structure Navigation
→ Soft Evidence Workspace
→ Explore Before Solve / Evidence Plan
→ FinDocQA Solver / Verifier
```

详细对应关系见 `KDDCup2026冠军方案对FinDocQA吸收分析.md`。

### P-KW1｜Knowhere 式结构导航探针

目标：验证章节树和结构路径能否提升 E2 Retrieval，而不是先建设完整平台。

最小范围：

```text
10～20 份已有 MinerU 文档
→ 重建 heading / section tree
→ 绑定 table / figure / source block
→ 输出 Document Memory Package
→ 与当前平面检索同基线比较
```

核心指标：

- Required Document Recall@K；
- Section / Page Recall@K；
- Table Source Recall@K；
- Coordinate Coverage；
- BINDING_READY；
- 旧命中退化；
- 构建成本、查询延迟和 Token。

晋级条件：同一评测集上产生可归因改善，且结构构建成本可接受。

停止条件：只增加复杂度、不能稳定提升证据召回，或必须重做全部 Parser 才能工作。

### P-LW1｜LLM Wiki 式知识编译探针

目标：验证持久主题目录是否减少跨文档问题的重复检索和重新归纳。

最小范围：

```text
10～20 份同主题文档
→ 生成 index / source / concept / comparison 页面
→ 每个 Claim 绑定原文来源
→ 测试新增、修改、删除资料后的增量维护
```

核心指标：

- Wiki Claim 来源回溯率；
- 错误或无来源 Claim 数量；
- 主题定位准确率；
- 跨文档问题检索步骤和 Token 变化；
- stale / delete cascade 正确率；
- 人工维护成本。

晋级条件：来源回溯稳定，主题导航明显减少重复检索，且没有把派生摘要误当原始事实。

停止条件：Wiki 更新成本高于收益、来源失效无法可靠传播，或生成页面长期需要大量人工修补。

### 推荐顺序

```text
当前先完成 H-07
→ 再做 P-KW1 结构导航探针
→ P-KW1 有收益后决定是否扩大 Document Memory
→ 同期可准备 P-LW1 的 Schema 和小样品
→ 不直接建设完整 Wiki / Graph 平台
```

---

## 6. 明确排除项

- 不在当前 `CURRENT.md` 中加入 Knowhere 或 LLM Wiki；
- 不因外部项目功能丰富而改变当前瓶颈优先级；
- 不替换现有 MinerU 结果；
- 不让 Wiki 摘要成为最终答案的唯一来源；
- 不在没有评测证据时引入知识图谱、向量库或新 UI；
- 不把两个方向合并成一个大包同时开发。

---

## 7. 参考项目

- Knowhere：`https://github.com/Ontos-AI/knowhere`
- LLM Wiki：`https://github.com/nashsu/llm_wiki`
- LLM Wiki 方法说明：`https://github.com/nashsu/llm_wiki/blob/main/llm-wiki.md`
