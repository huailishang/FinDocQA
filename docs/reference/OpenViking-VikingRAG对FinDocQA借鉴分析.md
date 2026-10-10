# OpenViking / VikingRAG 对 FinDocQA 的借鉴分析（2026-10-10）

> 状态：REFERENCE_ONLY / 后续候选实验（未冻结、未执行、未验收）。本文件只记录公开机制、与现有架构的对应关系、可证伪实验建议；不改变 Evaluator–Executor CURRENT、已冻结 H-79 合同和 Product 默认行为。

## 1. 一句话结论

**优先吸收 VikingRAG 的“先定位文档与章节 → 按证据缺口逐层读取 → 有界补检 → 原始证据验证”；TrieHI 的目录范围思想暂复用现有 Document Scope；VikingMem 的记忆合并与冲突管理留作长期参考。**

不直接把 OpenViking 全栈作为 FinDocQA 的新存储/检索基础设施，不重做 MinerU，不新增第二套 Runtime/Harness。把它视为现有 Know-where / Document Memory 路线的具体工程参考，先验证确切瓶颈和净收益。

## 2. 官方来源与成熟度

| 来源 | 核心机制 | 公开资料 / 成熟度边界 |
| --- | --- | --- |
| OpenViking | 统一 Memory / Resource / Skill 的虚拟目录；L0 abstract、L1 overview、L2 details 分层加载，检索轨迹可观察 | https://github.com/volcengine/OpenViking；部分核心机制已集成，不代表论文中全部高级实验都已成为稳定接口 |
| VikingMem | 事件与实体当前状态分开管理；抽取、更新、合并、冲突保留 | https://arxiv.org/abs/2605.29640；OpenViking 公开部分核心能力 |
| TrieHI | 目录范围约束与结构维护；向量排名前收窄检索范围 | https://arxiv.org/abs/2606.16903；应先验证是否真的需要额外索引 |
| VikingRAG | 将语义检索与目录/文档结构结合，逐层补足证据 | https://arxiv.org/abs/2609.11390；论文中轨迹复用和自适应升级是研究机制，不等于可直接安全上线 |
| VikingRAG 复现实验 | VikingRAG / VikingRAG-E / VikingRAG-E+ 运行与 FinanceBench 等适配器 | https://github.com/rucdatascience/VikingRAG；可参考实验和日志合同，不直接复制运行依赖 |

实验论文中报告的准确率、Token 节省和升级策略仅属于其特定数据/方法设定，不是 FinDocQA 的收益承诺。特别是“证据已足够”的停止判断可能误判；金融问答不可让单一生成模型的充分性判断覆盖 Claim 与来源校验。

代码吸收前检查开源许可证：OpenViking 主仓声明 AGPLv3，而 FinDocQA 是 Apache-2.0；优先独立实现已公开的通用思想，代码复制或依赖引入必须另行完成许可审查。

## 3. 与现有 FinDocQA 的实际对应

当前工程链：问题/策略 → Document Scope → 文档/页面检索 → 有界 Evidence Workspace / EvidenceBundle → Claim/计算绑定 → 验证 → 恢复/输出。

| 机制 | 本项目已有基础 | 真实缺口 / 候选动作 | 裁决 |
| --- | --- | --- | --- |
| VikingRAG 层级导航 | CanonicalPage / CanonicalBlock、section_path、table_id、来源页码；Knowhere 路线已经设计 | 可导航 section-tree 与 page/table/row 的只读结构视图及相邻补读，完整主链仍需 A/B | P1，优先 |
| VikingRAG 缺口驱动补检 | QueryPlan、EvidenceWorkspace、Claim SUPPORT / REFUTE / UNRESOLVED、有界恢复 | 显式 Requirement 清单、缺口类型、定向扩展与可解释停止原因 | P2，优先 |
| VikingRAG 检索轨迹复用 | 有实验记录和来源血缘 | 可失效的检索路线缓存，按语料/版本/问题结构键隔离 | P3，后置 |
| TrieHI 目录范围 | Document Scope / required-doc 分离和文档/页血缘 | 当前不缺独立的目录向量索引；先核验项目/语料/版本隔离 | REFERENCE_ONLY |
| VikingMem 状态演化 | 历史记录、任务状态、Source/Claim provenance | 版本冲突、superseded/stale 标记，可归入未来企业知识维护能力 | P4，后置 |

**不等同于**原始文档导航层、业务状态库和模型记忆层：FinDocQA 只负责 Knowledge / Evidence；Runtime、Harness、Agent Skill 演化属于 Control Plane / 公共执行框架，不把 Neo4j 式技能经验蒸馏与 VikingMem 混成一个新增底座。

## 4. 与当前真实瓶颈的先后关系

以 2026-10-02 项目 `CURRENT.md` / `PROJECT_BOTTLENECK_MAP.md` 的最新已核记录为准：

- H-79 为 **CONTRACT_FROZEN / Executor**；目标是仅用 opt-in IDF-normalized unique-term page score，比较旧/新页面评分，不改变语料、查询、窗口、Top5、H-69 admission 或默认 Product。
- 七道已暴露开发诊断题的基线 any-Gold Top5 为 **3/7**；H-78 四家公司共 **919 页**已完成来源/页面可复现审计，部分标注页在原排序为 40 / 62。以上是**页面检索诊断指标，不是答案正确率，也不是盲测泛化**。
- 当前 B-CN-01 为页级排序/证据供给；B-CN-02 为独立中文验证覆盖不足。已有 downstream evidence / binding 首损，不能简单把所有问题归为“Token 不够”。
- **先完成 H-79 的 L2 / 独立 L3 与 Evaluator 裁决**，再依据其结果决定下一项实验是否转为结构导航。不得向 H-79 合同塞新索引、导航、Summary、额外模型或更多评测题。实验参考必须先与 AFAC Lucky / AAADogs / 过目不忘路线对照，遵守 `docs/reference/对比计划.md` 第 6 节优先级。

## 5. 候选最小实现：P-KW1-VR（不是新任务合同）

这是现有 P-KW1 Know-where 结构导航探针的 VikingRAG 补充假设，**不重复另建一套 Document Memory 平台**：

```text
Source PDF / existing MinerU outputs
  → CanonicalDocument (source_id / document_id / page / block / table / lineage)
  → read-only SectionTree / DocumentNavigationView
  → [scope-bounded] 目录/页面初检
  → Evidence Requirements: metric / entity / year / period / unit / basis / member range / formula
  → 读取对应原始 Canonical pages / table rows（仅导航可用摘要，不以摘要作答案事实）
  → Requirement coverage: ready / partial / missing / conflict
      ├─ ready: 原始来源绑定、单位/范围齐全 → EvidenceBundle → C3/Verifier
      └─ partial/missing/conflict: 仅在受限范围补检；预算耗尽仍 UNRESOLVED
  → page / table / row provenance + retrieval trajectory + stop_reason
```

最小接口建议（不是承诺立即编写）：`DocumentNavigationView` 提供 `list_sections(document_id)`、`resolve_section(section_path)`、`read_source_span(page_key, block/table/row range)`；不得产生无源摘要当证据。输入必须带 scope、corpus_snapshot、版本和 source hash。输出除候选 PageKey、evidence blocks 外，还需记录导航动作、范围、缺口、消耗、停止原因和所有 source lineage。

停止条件分两层：
1. **候选结束**：每个必要事实槽有可追溯、无冲突、口径/单位正确的原始见证，或预算已耗尽；不能仅由模型口头判断“足够”。
2. **答案放行**：现有 Claim Binder / Calculator / Verifier 独立通过，才允许输出确定结论；否则标 `UNRESOLVED` 或受控恢复。

边界反例必须覆盖：同名报告跨公司/项目、不同年份同一指标、2024/2025 预算并存、PDF 改版导致旧缓存失效、跨页表头/表体、行成员不全、金额单位变化、证据互相矛盾以及已达预算却未找齐证据。

## 6. 后续单变量 A/B 与晋级门槛

依赖 H-79 结束且独立样本可用，由 Evaluator **另行冻结**合同后再执行，不自行启用外部框架、模型 API、安装依赖或重解析原始 PDF。

| 项目 | A：现有路径 | B：候选结构导航 |
| --- | --- | --- |
| 输入 | 相同 Corpus/Hash、Question、Gold、Document Scope、预算和原始 Canonical 文档 | 完全相同 |
| 变化变量 | 原有页面检索 → 有界证据准入 | 仅在选页/读段环节启用只读结构路径与证据缺口补检 |
| 输出对照 | 全排序、Top5、≤10 页 EvidenceWorkspace、完整 PageKey/来源 | 同合同，再提供导航轨迹及每轮缺口状态 |
| 主要判据 | any-Gold Page Recall@5、Required Evidence Recall、跨页表格完整性、first-loss | 同左，逐题/公司报告 gains、regressions |
| 安全与成本 | 准确来源、页码、单位、冲突、误停止；读取页数、Token、延迟 | 不得牺牲来源/验证/现有基线命中换成本下降 |

**晋级必要条件**：现有已命中样本不得退化；原始来源、Scope、PageKey、lineage 不可漂移；不得将 partial/missing 错判 ready；能够在至少两个独立问题、不同公司/文档中形成可复现的证据恢复收益，且新结构的时间/Token 成本合理。仅凭已暴露的七题不能宣称泛化：需独立中文未曝光切片；若暂无 holdout，裁决最多为探索性结果。

**停止条件**：只能靠 qid 特调、需要扩大合同变量、额外库/模型成本大于收益、破坏现有 Evidence/Verifier、缺原始 PDF 或页面血缘、独立复现不一致；维持原 Product 默认并记录假设失败。

## 7. 预计行动顺序与责任边界

1. **P0 当前**：先闭合 H-79 的冻结评分实验，保持所有脏工作区文件、H-79 源码和 CURRENT 不动。
2. **P1 结构导航**：参考既有 P-KW1（`Knowhere与LLM-Wiki借鉴分析.md`）和 B03 下一代检索设计，只建立 Canonical 派生只读 section/page/table 导航探针。
3. **P2 缺口补证**：结合 AFAC Lucky 的 Requirement–Evidence ID、ready/partial/missing，验证 page/table evidence 到后续 Claim 的保留与缺口处理。若 P1 无收益，不强行推进。
4. **P3 经验复用**：仅将已独立验证的检索轨迹作为可失效提示；缓存键包含 corpus/document/version/query-plan，source hash 变化必须失效，不跳过来源重新绑定。
5. **P4 知识更新**：源文件版本、stale/superseded/conflict 的治理与跨项目 scope 隔离作为企业知识侧长期候选，先验证出现真实需求，再评估是否需要 VikingMem / TrieHI 类独立组件。

这份研究是**下阶段实验备选依据**，不是“立项立即开发”，也不改变 FinDocQA 的 Know-where / Knowledge Compilation 两阶段路线。未来若 Evaluator 选择该假设，必须生成新的冻结任务、独立 Gold/预算、L2/L3 验收；任何正式晋级依据均来自本项目测量，而非外部论文排行榜。

## 8. 交叉参考

- `docs/reference/Knowhere与LLM-Wiki借鉴分析.md`：已有 P-KW1 / P-KC1 与原始证据权威边界。
- `docs/reference/B03下一代检索三路线实验设计.md`：现有 Query Planning / Know-where Lite / Semantic 实验历史。
- `docs/reference/对比计划.md` 第 6 节：AFAC2026 获奖方案优先级与单变量验证规则。
- `docs/evaluation/PROJECT_BOTTLENECK_MAP.md`：实时瓶颈与正式评估证据。
- `handoffs/evaluator_executor/state/CURRENT.md`：唯一当前执行状态；本参考文件不得覆盖它。
