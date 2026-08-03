# C3 阶段退出门：Post-H-01 正常主链状态

## 结论

    阶段裁决：EXIT_OPERATOR_EXPANSION
    下一主线：BINDING_AND_EVIDENCE_ASSEMBLY
    测量有效：true

H-01 完成后，C3-M 已不再是“能力存在但正常主链不可达”：

    C3-M SUM：NORMAL_PIPELINE = ACTIVE
    C3-N 谓词计数：仍缺来源绑定
    C3-O 区段计数：仍缺来源绑定

退出门现在按能力检查实际接线范围，不再使用“正常链中任何 C3 符号都不允许出现”的接线前前提。

## 1. 四层调用矩阵

| 能力 | ORACLE_RUNTIME | EXPLICIT_C3_CALL | SHADOW_OBSERVER | NORMAL_PIPELINE |
|---|---|---|---|---|
| C3-M 数值序列聚合 | ACTIVE | EXPLICIT_CALLER_ONLY | BLOCKED_BY_MISSING_EVIDENCE | ACTIVE |
| C3-N 严格谓词计数 | ACTIVE | EXPLICIT_CALLER_ONLY | BLOCKED_BY_MISSING_EVIDENCE | BLOCKED_BY_MISSING_BINDING |
| C3-O 区段成员计数 | ACTIVE | EXPLICIT_CALLER_ONLY | BLOCKED_BY_MISSING_EVIDENCE | BLOCKED_BY_MISSING_BINDING |

Oracle 与显式调用层只证明“给出完整来源绑定 request 后，产品执行器会正确执行”。正常主链层验证普通问题经过 Factory、Workflow、Router 和 CalculationSolver 后，是否真实构造 request 并调用产品执行器。

## 2. C3-M 正常主链已经激活

C3-M 探针使用真实结构化表格 loader：

    本地 MinerU content_list_v2 JSON
    → load_structured_table_rows
    → structured-table EvidenceCandidate
    → PipelineFactory.build_workflow
    → EnhancedBaselineWorkflow.process_one
    → RoutedSolver
    → CalculationSolver
    → SourceBoundSumSeriesBinder
    → SourceBoundNumericSeriesAggregator

实测结果：

    问题：三个部门利润 10、20、30 万元，求合计
    request constructor hits：1
    C3-M executor hits：1
    answer：60
    answer_source：c3_source_bound_sum_series
    final_state：accepted
    source_lineage_complete：true
    Provider / legacy / network / Token：0

固定 structured-table SUM 边界已经从接线前的 0/3 提升到 3/3。本退出门只复核一个代表性 C3-M 探针，不把它外推为真实 PDF 检索覆盖率或总体问答准确率。

## 3. C3-N 与 C3-O 仍然失败关闭

两个负向探针继续走真实 Factory 计算路径，但当前没有对应 Binder：

    CalculationSolver entered：true
    request assembly observed：false
    product executor invoked：false
    status：BLOCKED_BY_MISSING_BINDING
    Provider / legacy / network / Token：0

当前状态是：

    C3-M：已接入
    C3-N：待来源绑定 request builder
    C3-O：待来源绑定 request builder

## 4. Capability-aware 静态审计

旧审计规则：正常链出现任意 C3 request/executor 符号，就把测量判为无效。该规则在 H-01 接线后已经过时。

新规则：

    C3-M request/executor
      允许：src/solvers/calculation.py
      禁止：run.py、factory.py、workflow.py、router.py

    C3-N/C3-O request/executor
      禁止出现在全部正常链路径

    ExplicitC3Pipeline / Shadow
      不得冒充正常产品接线

    Oracle 与 Adapter
      必须保留三类 executor 和 request 合同支持

机器报告新增：

    expected_normal_wiring_by_capability
    unexpected_normal_chain_executor_symbols_present
    unexpected_normal_chain_request_contract_symbols_present
    normal_wiring_matches_expectation

当前结果：

    C3-M observed paths = [calculation_solver]
    C3-N observed paths = []
    C3-O observed paths = []
    unexpected executor symbols = false
    unexpected request symbols = false
    normal_wiring_matches_expectation = true

## 5. Shadow 与正常执行分开计数

H-01 之后，C3-M 会在正常 CalculationSolver 路径执行。该执行不能被误记为 Shadow 执行。

退出门分别记录 NORMAL_PIPELINE 与 SHADOW_OBSERVER 的 request/executor hits。C3-M 正常路径命中各 1 次，但 Shadow 仍然：

    state = BLOCKED
    pipeline_invoked = false
    request hits = 0
    executor hits = 0

因此，“正常主链已激活”和“Shadow 没有执行产品能力”不会混在一起。

## 6. 阶段裁决为什么仍是退出算子扩展

当前 NORMAL_PIPELINE 激活数量为 1，仍有 2 个能力卡在来源绑定：

    ACTIVE = 1
    BLOCKED_BY_MISSING_BINDING = 2

阶段规则只针对尚未激活的能力判断首个阻断层：

    C3-N/O 已进入 CalculationSolver
    + request assembly 未发生
    + product executor 未调用
    → primary_blocker_is_integration = true

剩余长尾算子仍没有规模至少为 5 的唯一绑定、完整证明、可选择产品能力族。因此：

    stage_decision = EXIT_OPERATOR_EXPANSION
    recommended_next_layer = BINDING_AND_EVIDENCE_ASSEMBLY

下一步应继续解决 C3-N/C3-O 的来源绑定与证据组装，而不是继续增加新的长尾算子。

## 7. 产物与边界

机器报告：evaluation_artifacts/c3_stage_exit_gate_v1/report.json

评估脚本：scripts/evaluate_c3_stage_exit.py

专项测试：tests/test_c3_stage_exit_gate.py

边界声明：

    只证明固定本地 C3-M structured-table SUM 正常链已激活
    不证明真实 PDF 表格召回率
    不证明 C3-N/C3-O 已接入
    不证明 FinDocQA 总体准确率
