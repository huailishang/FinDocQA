# 并行任务结论归档 — 2026-07-25

本文件替代原先大量 handoffs、并行 worktree 和 evaluation_artifacts，只记录做过什么、结论是什么、最终留下什么。

| 工作线 | 最终结论 | 最终保留 |
| --- | --- | --- |
| P03A 答案/提交合同 | 离线 PASS；多槽顺序、qid 顺序、Token 方程均验证 | 正式输出/合同代码 |
| P03B Scope Lineage | PASS；candidate scope 能传播到 Retriever / Evidence，并对范围扩展 fail-closed | 文档范围与 lineage 实现 |
| P03C Token Runtime | PASS；逐题 ledger、汇总方程、预算约束成立 | Token accounting / writer |
| P03E Context | PASS/OFFLINE；上下文预算与证据压缩有效 | 上下文选择与压缩逻辑 |
| P03E Binding | PASS/OFFLINE；Option/Claim 同源事实绑定有效 | Binding 相关通用实现 |
| P04E Minimal Evidence | PASS；可在不破坏数字、年份、公式、选项、否定/条件/例外覆盖的前提下缩减证据 | minimal_sufficient_set.py |
| P04F Parser Calibration | PASS 作为诊断候选；不应全局切换 PyMuPDF | 结论吸收到选择性解析器策略 |
| P04G Query Plan | Shadow PASS，但 standalone promotion 被拒；query plan 必须与真实检索收益绑定 | query_plan.py 作为实验能力 |
| P05 Lineage | PASS；Solver 证据引用规范化 | 已吸收到主实现 |
| P06 Financial Parser | PASS_OFFLINE_DIAGNOSTIC_NO_PROMOTION；只有正确文档、正确页、目标指标同时满足时才允许局部 fallback | selective_parser_fallback.py |
| P11 Strategy / Evidence / Claim | A 题型策略 PASS；B 最小证据 PASS_SHADOW；早期 Claim scope 需收窄 | 最终版策略、最小证据、后续 P13 Claim 实现 |
| P13 Claim Verifier | 条件/例外/时间作用域收窄，Claim 原子逐项绑定 | Claim atom + verifier 模块 |
| P12/P14 Recovery | 离线 PASS；复合失败先仲裁主根因，再生成最多一步的恢复建议；Provider/预算/完整性错误 fail-closed | failure taxonomy / arbitrator / recovery policy / shadow trace |
| Slot1 S2/S3/S4 | 仅比赛执行分片；结果最终被统一 R6 覆盖 | 不保留工作区和运行产物 |
| P09 Stage-A Snapshot | 临时快照，用于当时恢复/对账 | 不保留目录 |
| Phase1 Local Archive | 历史评测大量本地过程文件，约 3GB | 只保留 历史评测最终基线和历史分数 |

## 并行分支记录

以下 commit 仅作为历史定位，不要求继续保留 worktree：

- bb-p0-03a-answer-contract — 179767d2ab8de82b5104edd2a4e5ff5c98eeb5e5
- bb-p0-03b-scope-propagation — 8598e66fd517d98b130ffac06845bd077617e57b
- bb-p0-03c-token-runtime — ca2203670fbba85f9e36920c311667e3e6a8abcd
- bb-p0-03e-option-binding — 5c2f2e1317363c872fc0f0b39087c058d5fbe502
- bb-p0-03e-context-budget — b92901e3e90ab758d31c33544aaf5b98cc4967fe
- bb-p0-04e-minimal-evidence — e6c6616bc7a9c73aec1ed8184a8397823f432189
- bb-p0-04f-cross-parser-fallback — ac6d7d24f3ef823e85a42bafc29425f8a385395f
- bb-p0-05-r1-lineage — 30e9198ba3bf05c9eb0b6658f8e0e441dad0270d
- bb-slot1-s2-fin — c340054bb4fca0a2d3789cc719d2a2195cc7d343
- bb-slot1-s4-reg — 8a8143a10b1179e41e5eaa7dc1362c969261412d

后续若重新优化，应从当前主仓库能力出发，不恢复这些旧 worktree。
