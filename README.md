# FinDocQA

FinDocQA 是一个面向金融长文档问答的工程基线，覆盖金融合同、财务报告、保险条款、监管文件和研究报告。

项目重点不是绑定某一场比赛或某一个模型，而是把长文档问答拆成可测试、可审计、可替换的工程链路：

题目解析 → 题型/策略识别 → 候选文档范围 → 文档内检索 → 证据选择与压缩 → Claim/选项原子化 → 证据绑定与计算 → 验证 → 失败分类与有界恢复 → 输出合同。

## 核心能力

- MinerU 为主的结构化解析，以及页面级选择性 Parser Fallback；
- 无 doc_ids 场景下的候选文档范围解析；
- 词法/结构信号检索与最小充分证据压缩；
- 计算、跨文档、比较、排序、否定、时序等复合题型策略；
- Claim 级 SUPPORT / REFUTE / UNRESOLVED 验证；
- 文档、页面、来源血缘追踪；
- Prompt Registry 与离线 A/B；
- 失败分类、主根因仲裁与 fail-closed 的有界恢复建议；
- Token、reasoning、输出格式和运行完整性审计。

## 目录

- src/：核心实现
- config/：运行、策略与 Prompt 配置
- scripts/：通用运行、解析、诊断和离线评估工具
- tests/：离线核心回归
- docs/：架构、模块接口、能力地图与历史背景

## 数据

仓库不包含比赛数据、原始 PDF、Provider 原始响应或真实提交 CSV。默认数据路径位于仓库同级的 data/，也可以通过配置或环境变量覆盖。

## 运行安全

- .env 不进入 Git；只提交 .env.example。
- 默认不应自动执行付费模型调用。
- 证据、lineage 或 Binding 不明确时优先 fail closed。
- Parser fallback 必须局限在已确认的文档/页面范围内。

## 测试

在安装项目依赖的 Python 环境中运行：

    python -m pytest -q

当前公开快照在本地完成了完整离线回归后生成。

## 项目来源

FinDocQA 最初来自 AFAC 2026 金融长文档问答实践，之后已将逐轮比赛任务、排行榜专用逻辑、Provider 运行产物和提交历史从公开代码快照中剥离。少量能力演进背景保留在 docs/history/。

## License

当前仓库尚未声明开源许可证。公开使用前请由仓库所有者选择并添加适合的 LICENSE（例如 MIT 或 Apache-2.0）。
