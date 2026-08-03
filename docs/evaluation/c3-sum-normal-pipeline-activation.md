# C3-Q SUM 正常主链激活实验 V1

## 1. 本轮解决的问题

C3-P SUM Binder 与 C3-M 求和执行器已经分别可用，但此前普通问题进入 Factory 主链后：

```text
PipelineFactory.build_workflow
→ RoutedSolver
→ CalculationSolver
→ Binder 调用 0
→ C3-M 调用 0
→ 正确确定性答案 0 / 3
```

本轮只增加一条严格失败关闭的接线：

```text
CalculationSolver.solve
→ 尝试 SourceBoundSumSeriesBinder.bind
→ ready=false：完全继续原 CalculationSolver 路径
→ ready=true：执行 SourceBoundNumericSeriesAggregator
→ execution.ok=true：返回可审计数值答案
→ execution 失败：返回确定性失败，不调用 Provider 伪装修复
```

没有修改 Binder 规则、C3-M 执行器、Factory、Workflow、Router、Assembler 或 Retriever。

## 2. 为什么只在 CalculationSolver 接入

当前第一瓶颈不是“不会求和”，而是：

> 已有 Binder 和 C3-M 能力无法从正常主链到达。

因此本轮只改变一个变量：

```text
普通计算主链是否调用现有 SUM Binder 和 C3-M
```

这样 before/after 差异可以归因于接线本身，而不是同时修改检索、Binder 或计算规则。

## 3. 激活条件

确定性 SUM 分支只允许普通开放问答：

```text
answer_format = freeform
无选项
_input_adapter = canonical_question_v1
不是正式 B split
没有 submission slot contract
```

以下路径保持原行为：

```text
正式 AFAC/B freeform
选择题、判断题和多选题
非 SUM 问题
Binder 证据不完整或存在歧义
Insurance calculation
其他 CalculationSolver 路径
```

## 4. 成功结果合同

Binder ready 且 C3-M 成功时，SolverResult 包含：

```text
solver = calculation
answer_source = c3_source_bound_sum_series
confidence = 1.0
computation_status = completed
computation_complete = true
provider_call_count = 0
prompt_tokens = 0
completion_tokens = 0
total_tokens = 0
legacy_execution_invoked = false
request_contract = SourceBoundNumericSeriesAggregationRequest
binding_trace
result_trace
source_lineage / source_refs
gate_status
audit_reasons
```

每个来源成员保留：

```text
doc_id
page_number
table / row / column 坐标
原始证据摘要
```

Workflow 继续执行答案格式和生产完整性校验，不绕过 `EnhancedBaselineWorkflow.process_one`。

## 5. 失败关闭规则

### 5.1 Binder 不 ready

```text
不调用 C3-M
不输出 c3_source_bound_sum_series
不把 Binder reason 当成用户答案
继续执行修改前的原路径
```

### 5.2 Binder ready，但 C3-M 失败

```text
answer = 空
computation_status = failed
保留 binding trace、execution error、audit reasons 和来源血缘
Provider / legacy / Token = 0
不回退到模型或旧计算路径生成伪确定答案
```

## 6. 同基线项目影响

冻结基线：

```text
evaluation_artifacts/c3_sum_normal_pipeline_activation_v1/baseline_report.json
SHA256 = 569464fa26e1576f8c86f3dcfd321269b89c3b42393565eaa38124a03ed8813e
```

同一 Factory、同一问题、同一结构化表格 loader 路径：

| 指标 | Before | After | Delta |
|---|---:|---:|---:|
| CalculationSolver 进入 | 3 / 3 | 3 / 3 | 0 |
| Binder 调用 | 0 / 3 | 3 / 3 | +3 |
| C3-M 调用 | 0 / 3 | 3 / 3 | +3 |
| 正确确定性激活 | 0 / 3 | 3 / 3 | +3 |

三个结果：

| 样例 | 答案 |
|---|---:|
| 部门利润 10、20、30 万元合计 | 60 |
| 项目成本 1,000、2,500、500 元总和 | 4000 |
| 区域净变动 20、(5)、10 万元共计 | 25 |

机器报告：

```text
evaluation_artifacts/c3_sum_normal_pipeline_activation_v1/after_report.json
```

## 7. 护栏结果

冻结的 33 个 Binder 反例全部进入修改后的 CalculationSolver：

```text
Binder rejected = 33 / 33
C3-M calls on rejected binding = 0
false deterministic activation = 0
稳定 reason 分类保持 = true
```

覆盖：

```text
无 SUM 意图
SUM 与 AVG 冲突
非结构化候选
缺行、重复行和协调截短
列歧义、标签歧义
混合单位、百分比和非法数值
row-99 来源攻击
错误表 URI
candidate.source 篡改
```

另外验证：

```text
非 SUM freeform 结果和 metadata 与旧路径完全一致
Insurance calculation 路由保持一致
Binder ready 但 C3-M 失败时不进入 Provider 路径
```

## 8. 当前结论

在固定本地边界内：

```text
Before = 0 / 3
After = 3 / 3
Delta = +3
Guardrail result = PASS
Provider / legacy / network / Token = 0
measurement_valid = true
```

这证明 B-01 的“能力存在但正常主链不可达”问题在固定 SUM 边界内被移除。

## 9. 不能外推的范围

本轮只激活：

```text
结构化单表
完整连续范围
唯一标签列
唯一数值列
明确 SUM 意图
```

本轮不代表：

```text
C3-N 或 C3-O 已接入正常主链
AVG / MIN / MAX 已支持
真实 PDF 表格一定能被正确解析和召回
真实用户问题中 structured-table 证据覆盖率已知
FinDocQA 总体准确率提升
```

准确表述是：

> 固定 structured-table SUM 边界从 0/3 提升到 3/3；真实项目覆盖范围仍未知。
