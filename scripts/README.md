# Scripts

本目录只保留可以复用的运行、诊断和离线评估工具。

主要类别：
- MinerU 适配、批处理和语料校验；
- 文档范围与检索质量评估；
- `evaluate_retrieval_ab.py`：在不调用模型的前提下，对多个 Retriever 做“原始检索 → 证据组装 → Solver 可见证据”的 A/B；
- `evaluate_answer_ab.py`：E4 最终答案 A/B。默认只做 0 API 预检；真实运行必须显式开启 `--execute --allow-provider-calls` 并设置调用上限，可用 `--fixed-model` 固定 A/B 模型；逐题写 checkpoint，同时分开统计答案正确率、Correct-but-Blocked / Incorrect-but-Accepted 与门禁误拒/误放；
- Prompt Registry 离线 A/B；
- 基线冻结和候选构建；
- 安全的模型调用 runner；
- 通用 verifier、zero-evidence 和 artifact 诊断。

逐题、逐包、Slot 或排行榜冲刺脚本不属于长期仓库资产，已删除。历史结论见 docs/history/。
