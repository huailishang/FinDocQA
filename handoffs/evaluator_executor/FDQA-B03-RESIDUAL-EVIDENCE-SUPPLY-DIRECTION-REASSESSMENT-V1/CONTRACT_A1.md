# Frozen Task Contract｜H-61 检索收口与证据消费边界审计

Task ID: `FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1`
Workflow: `evaluator-executor-workflow/v2.2`
Task kind: `evaluator_design`
Contract state: `CONTRACT_FROZEN`
Risk: L0
Branch: `main`
Baseline HEAD: `849ff3bda52481c7ecb999d5967a851910624265`
Pre-existing changes: 本轮设计前工作区无项目改动；执行前记录本包与地图的未提交设计变更。

## Strategic basis

Project map: `docs/evaluation/PROJECT_BOTTLENECK_MAP.md`
Map revision: `2026-09-18-r86`
Active bottleneck: `B-03`
Hypothesis: `H-61`
Dispatch mode: SINGLE
Measurement status: measured（历史检索）；unknown（端到端收益）
Expected project impact: NOT_APPLICABLE；本包建立决策依据，不宣称能力改善。

全链路：问题 → 文档/页面供给 → 检索 → 证据工作区 → 来源绑定/计算/验证 → 可交付答案。解析、来源追溯、部分计算及工作区范围路由已有证据；B-03 仍为首要已测损失，B-06 自由文本评分为次级瓶颈，B-05 复杂表格影响待测。不扩展 Runtime / Harness / Context 控制职责。

H-60 原判保持 PASS / IMPROVED / SWITCH / NOT_QUALIFIED：同通道 RRF60 Top5 7/12 → 最多10页工作区9/12，恢复2题/2文档族、旧命中损失0。H-49 混合检索已达自己的门槛；H-60 未达标不否定混合检索。

关键限制：H-60 `downstream_support_measurement()` 因没有非 Gold 候选答案断言，直接写入12条 `VERIFIER_UNSUPPORTED`，没有逐题执行验证器。这是输入适用性声明，不能解释为12次验证失败、验证器能力为零、可信答案零退化的实证。页面命中也不证明事实充分或答案正确。

原 H-61 只寻找残余检索方向，可能持续消耗在少量案例上。A1 将其改为一次有界决策审计：检索诊断收口，下游消费边界必须审计，最后建议一个后续任务。不是同时实施两个能力实验，也不预设转向B-06。

## Single objective

使用既有冻结数据与只读代码，产出可重放的全12题阶段漏斗、残余诊断、消费契约审计及一份下一任务草案。“找到共同根因”或“恢复至少3题”不是本诊断通过条件。

## Frozen input authority

`DESIGN_INPUT_MANIFEST.json` 在设计时冻结具体文件SHA256；执行者先校验，不得刷新hash消除漂移。必读H-60复核/L3、边界结果、排名、运行时输入、下游声明及其生成脚本；H-50全46题矩阵与复核；H-43/H-53反证；H-56R1路由复核；现有断言/绑定/充分性/工作区/调用路由代码。

Gold只用于后评分与诊断标注；不得把参考答案转成运行时断言。`INPUT_MANIFEST.json` 记录实际使用文件及hash。额外本地文件仅限上述来源追溯所指文件，使用前记录身份和用途；禁止新题集、下载和读取凭据。

## Work units and budget

按顺序执行，一份报告、一次L2提交：

1. **收益与成本复算。** 全12题比较原词法、语义、RRF60 Top5与工作区，统一用all-Gold-pages命中口径。记录保护命中损失、页数及同一缓存page-text来源的字符数。字符数只是代理成本，不冒充token、延迟、金额。不得重新生成向量、修改排名或跨历史模型比较。
2. **残余一次收口。** H-60三条union miss与H-50全部18条BOTH_MISS。以(cohort,qid)保留观察，按qid和文档身份检查重叠；重复观察不算独立案例。讨论H-43/H-53反证；不扫邻页半径/权重/TopK，不强迫统一根因。
3. **下游消费审计。** 对全部12题区分页面命中、候选断言存在、验证是否执行、事实充分/绑定/答案评价是否被观测。沿脚本及产品代码列出producer→consumer、所需字段、页码口径、scope传播、未知输入处理。静态输入不支持不能直接判为实现缺陷。可引用既有动态证据；没有匹配观测则NOT_MEASURED。本包不生成答案、不运行Solver/Judge、不实现适配。
4. **一次方向建议。** 分别输出检索候选结论与下一任务优先级，比较收益范围、证据强度、成本和依赖；产出恰好一个后续任务草案，不自动执行。

最多1个候选机制，在诊断结果前写`MECHANISM_FREEZE.json`，无候选则null。最多2次完整离线生成（首次与重放），另允许1次实现错误修正后的重放，不是调参额度。全语义排名仅为缓存可直接复算时的可选诊断，缺缓存不阻断收口。零API/新数据/安装/产品修改；现有冻结轨迹足够支持本次审计，无需新增外部数据集研究。

## Decision rules

不改历史阈值或H-60原判。将后续决策分三层：

| 层次 | 本包可得结论 | 禁止外推 |
|---|---|---|
| 观察到收益 | +2保留为实验配置和回放资产 | 不等于默认启用 |
| 值得有限实验 | 机制明确、成本受控、可证伪，写独立验证计划 | 3题/2文档族不是统计显著性 |
| 默认启用/可靠交付 | 本包未证明；需未来冻结场景、样本、效应、成本及下游正确性指标 | 小样本零损失不等于总体无退化 |

检索候选仍需>=3独立qid、>=2文档族、同一Gold-free机制、历史反证分析及新样本失败条件；不足记SIGNAL_ONLY或NO_SINGLE_DIRECTION，不补题调参。该门槛不阻止处理一个确定的接口缺口，也不把+2收益清零。

下一任务建议只取以下之一，必须有比较依据：

- `DOWNSTREAM_CONTRACT_FIRST`：没有更强可实施检索机制，且代码/输入审计证明缺少候选断言生产或消费契约；下一步先冻结最小接口、离线评测和必要调用预算，不直接扩验证器。答案生产与B-06评分器不是同一问题。
- `RETRIEVAL_PROBE_FIRST`：候选达标，并说明比下游契约工作更值得优先投入；仅建议下一独立实验。
- `HOLD_FOR_MISSING_EVIDENCE`：必要来源缺失/冲突或依赖未知，明确最小缺失证据。正常“无共同根因”不是阻断。

B-05/B-06/B-07都要比较，不预先提升排序。人工阅读内容只作带出处诊断，不作自动正确标签。

## Required outputs and schema

- `INPUT_MANIFEST.json`：`files`路径→SHA256；`external_api_calls=0,model_calls=0,dependency_install_attempts=0`。
- `CASE_FUNNEL.jsonl`：12行，`qid,document_family,lexical_gold_reach,semantic_gold_reach,baseline_gold_reach,candidate_gold_reach,baseline_pages,candidate_pages,baseline_text_chars,candidate_text_chars,candidate_claim_available,verifier_executed,fact_sufficiency,answer_correctness`。历史无断言/验证，两布尔为false；事实充分性、答案正确性为NOT_MEASURED，不算错误。
- `RESIDUAL_CASES.jsonl`：H60三条+H50十八条，`cohort,qid,document_family,source_ref,diagnostic_label,evidence_refs`；允许UNRESOLVED。source_ref为冻结文件路径，证据定位为path:line或path#symbol。
- `MECHANISM_FREEZE.json`：`mechanism`（null或对象）、`runtime_features,trigger,action,parameters,max_candidates=1`；无候选时机制相关字段可null。L3审查生成顺序和数据流，不能只信gold_free=true。
- `DOWNSTREAM_AUDIT.json`：`h60_status_origin=STATIC_INPUT_APPLICABILITY,observed_verifier_executions=0,observed_answer_evaluations=0,runtime_claim_source=null,edges,missing_contracts,scope_and_lineage_findings`。每条edge含producer、consumer、required_fields、evidence_refs；每个判断附文件/符号/行号，不冒充动态测试。
- `DECISION.json`：`retrieval_direction`（CANDIDATE_DIRECTION/SIGNAL_ONLY/NO_SINGLE_DIRECTION）、`supporting_cases`（cohort/qid/document_family）、`next_action,reason,evidence_refs,alternatives`（B-03/B-05/B-06/B-07比较）、`historical_verdicts_unchanged=true,default_enable_authorized=false,product_readiness_proven=false,end_to_end_improvement=NOT_MEASURED`。候选须有historical_counterevidence与fresh_failure_condition。
- `NEXT_TASK_DRAFT.md`：恰好一个DRAFT_ONLY后续包，写输入→输出→边界、可复用模块、反例/测量/停止条件、授权需求。
- `DEPLOYMENT_PROFILES.md`：无模型词法/结构基线、批准的内网语义服务、允许的外部服务三档；只设计依赖，不实现、不声称企业采用率或三档同等效果；来源和失败关闭要求一致。
- `run_analysis.py`：任务本地薄离线入口，`--output-dir`写CASE_FUNNEL/RESIDUAL_CASES/DOWNSTREAM_AUDIT/DECISION四个机器产物；两次生成逐字节一致。机制冻结和输入manifest不随输出重建。不得运行历史API主入口。
- `REPORT.md`、两次self-check记录、原始EV和L2-GATE；报告含全局链路、Impact comparison、Project-impact verdict建议NOT_APPLICABLE、未测项、下一步价值，不自签最终PASS。

## Acceptance criteria

- AC-01 输入hash与H-60正式历史结果可验证，历史原判不变。
- AC-02 全12题从原页集合独立复算；7→9/恢复2/损失0复现，字符同源，未测答案不加入正确或错误分母。
- AC-03 残余精确覆盖3+18条观察、去重明确；最多一个机制，讨论H-43/H-53反证；无机制也可通过。
- AC-04 静态声明与动态执行区分有代码依据；消费字段/来源范围链有定位，未知项明确，不制造答案。
- AC-05 分层决策、一个后续草案及三档部署边界成立；不改H-60、不自动启用、不预设B-06第一；候选接受人工因果复核。
- AC-06 两次输出稳定，变更限定允许范围，无API/安装/产品变化；L3审runner调用路径，不能仅信零调用字段。
- AC-07 报告区分静态、历史动态及未测事实；L2通过再提交，路由符合角色要求。

## Allowed scope

执行者仅新增本任务规定产物、薄分析脚本及evidence，正常流转可更新CURRENT.md。本次设计冻结的A1合同/计划、check_packet.py、DESIGN_INPUT_MANIFEST、REVIEW_RUBRIC及原合同/备份均只读；地图由评估者维护。src/config/tests、历史包与数据只读。需要通用实现时另立工程包，逻辑入src并配离线测试。

## Exclusions

无产品修改、历史gate更改、新向量/模型/API调用、安装下载、答案生成、Gold断言、排名/TopK/邻页调参、新题集、Runtime或安全项目扩张。缓存重放不冒充无模型生产部署。

## Validation plan

Validation plan file: `handoffs/evaluator_executor/FDQA-B03-RESIDUAL-EVIDENCE-SUPPLY-DIRECTION-REASSESSMENT-V1/VALIDATION_PLAN_A1.yaml`

冻结检查器核对hash/集合/漏斗/产物/重放；L3另按REVIEW_RUBRIC.md检查因果、Gold隔离和优先级。检查命令使用Windows Conda agent；启动共享runner前将该环境Python目录加入当前进程PATH，让计划中的python使用agent。已知agent缺PyYAML，共享runner/validator使用本机已有PyYAML的系统Python启动，子检查仍经PATH使用agent；不得安装依赖或将机器绝对路径写入计划。启动时记录两个解释器的实际路径与版本。

## Stop conditions

必需输入缺失/漂移、需要外部调用/产品修改、计划不适用、重放修正预算耗尽时停止并报告。可选诊断缺失、无共同机制、缺候选答案不算整包失败；完成其余审计并明确边界。不得为通过而新增恢复题。

## Authorization

authorization_commit = false
authorization_push = false
authorization_history_rewrite = false
authorization_api_call = false

## Amendments

A1：2026-09-18用户授权重新制定；原合同/计划及PRE_AMENDMENT_01备份保留，当前只执行A1。没有新能力运行，没有改变H-60裁决。详见AMENDMENT-01-BOUNDED-CLOSURE-AND-CONSUMER-AUDIT.md。
