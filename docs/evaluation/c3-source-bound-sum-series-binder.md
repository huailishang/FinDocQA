# C3-P 来源绑定求和序列 Binder V1

## 1. 本轮完成了什么

本轮新增一个数据集无关的来源绑定器：

```text
明确 SUM 问题
+ 单个结构化表格
+ 完整连续的行范围证明
+ 一个标签列
+ 一个唯一数值列
+ 一致单位和维度
→ SourceBoundNumericSeriesAggregationRequest
```

它解决的是：

> 已经检索到结构化表格以后，如何把问题、完整行范围、数值列和来源坐标可靠地组装成现有 C3-M 可以执行的 request。

它不复制求和逻辑。成功后仍然调用现有：

```text
SourceBoundNumericSeriesAggregator.execute(request)
```

## 2. 阶段边界

本轮只是 **request 构造能力**，不是运行主链接入。

```text
已完成：
结构化证据 → 来源绑定 SUM request

未完成：
普通问题自动调用 Binder
Shadow 自动调用 Binder
生产 Router / Workflow 接入
AVG / MIN / MAX
C3-N / C3-O Binder
```

因此当前调用方式仍是显式的：

```text
调用者准备 Question + EvidenceBundle
→ SourceBoundSumSeriesBinder.bind
→ 得到 SourceBoundNumericSeriesAggregationRequest
→ 显式交给 C3-M
```

## 3. 为什么要增加完整范围证明

仅看到当前有 3 行，不能证明表格只有 3 行。候选可能在检索阶段漏掉第 4 行，也可能只取了前两行。

结构化表格加载器现在为同一张表的每一行附带统一范围事实：

```text
table_source_object_id
table_data_row_count
row_span_start
row_span_end_exclusive
row_span_complete
row_span_start_explicit
table_row_indices
table_row_sources
table_range_digest
table_range_proof_version
```

其中 `table_range_digest` 基于完整表格的以下内容生成：

```text
doc_id
page_idx
table_index
headers
全部 row_index
全部 canonical_source
全部 cell_texts
```

Binder 要求：

```text
候选行集合
= 声明的完整行清单
= 连续范围 [start, end)
= table_data_row_count
= digest 所证明的原始表格内容
```

只协调修改 `row_count`、`end` 和候选列表，无法通过旧的完整表格摘要，因此会失败关闭。

摘要只证明“候选内容和范围是否与 loader 输出一致”，不能代替来源身份校验。Binder 还会精确验证：

```text
expected_table_source_object_id
= mineru_json_source
  + "#page_idx=" + page_idx
  + "&table_index=" + table_index

expected_canonical_source
= expected_table_source_object_id
  + "&row_index=" + row_index
```

并要求：

```text
table_source_object_id == expected_table_source_object_id
canonical_source == expected_canonical_source
candidate.source == expected_canonical_source
```

因此，即使同时伪造 `table_row_sources` 并重新计算 `table_range_digest`，row-99 或错误表 URI 仍会失败关闭。`table_range_digest` 不是密码学签名，也不宣称能够抵御整个证据生产端被替换。

## 4. V1 绑定规则

### 4.1 问题必须明确要求求和

支持：

```text
合计 / 总和 / 共计 / 求和 / 之和 / 总计
```

拒绝：

```text
没有 SUM 意图
SUM 与平均、最大、最小、相差、增长率或占比同时出现
```

### 4.2 必须是唯一单表

所有候选必须属于同一个：

```text
doc_id + page_idx + table_index + table_source_object_id
```

跨文档、跨页、跨表或来源对象不一致均拒绝。

### 4.3 必须是完整连续范围

必须满足：

```text
row_index 无缺口、无重复
候选数量与声明行数一致
首尾范围一致
候选来源与完整来源清单一致
表格摘要重新计算一致
```

### 4.4 必须唯一绑定列

V1 只接受：

```text
一个标签列
一个由问题指标和表头共同确认的数值列
```

例如：

```text
部门 | 收入 | 利润
问题：各部门金额合计是多少？
```

“收入”和“利润”都可能是金额，Binder 不猜，直接拒绝。

### 4.5 数值、单位与来源

支持的最低数值格式：

```text
10
1,234.50
(25.0) 代表负数
```

支持单位：

```text
元 / 万元 / 亿元 / number
```

不做单位换算。混合单位、百分比、空值、文本值、NaN 和 Infinity 均拒绝。

每个成功成员包含：

```text
连续 position
Decimal value
unit / dimension
FormulaSourceRef
source_coordinate
source_object_id
成员标签
```

来源坐标只使用已经通过身份校验的 URI：

```text
source_object_id = expected_table_source_object_id
source_coordinate = expected_canonical_source + column_index
FormulaSourceRef.source = expected_table_source_object_id
```

不会使用未经验证的 `table_source_object_id`、`canonical_source` 或 `candidate.source`。

### 4.6 明细与汇总行不能混用

以下结构会拒绝：

```text
一部 10
二部 20
合计 30
```

因为同时求和明细和汇总行会重复计算。V1 不自动猜测应该删除哪一行。

## 5. 正向结果

| 样例 | 输入 | Binder | C3-M 结果 |
|---|---|---|---:|
| P1 金额求和 | 10、20、30 万元 | ready=true | 60 |
| P2 千分位 | 1,000、2,500、500 元 | ready=true | 4000 |
| P3 负数 | 20、(5)、10 万元 | ready=true | 25 |

三组均满足：

```text
request 类型正确
来源顺序完整
trace 完整
Provider / legacy / network / Token = 0
```

## 6. 失败关闭矩阵

机器评估覆盖 33 个反例，包括原 30 类以及 3 类来源身份攻击：

```text
无 SUM 意图
SUM 与 AVG 冲突
空候选
非结构化表格
跨文档 / 跨页 / 跨表
行缺口 / 行重复 / 起点错误
声明行数过小或过大
缺少范围证明
协调截短候选与 row_count
数值列缺失或歧义
标签列缺失或歧义
空标签 / 空数值 / 非法数值 / 非有限数
混合金额单位
金额与百分比混合
重复 canonical_source
来源对象不一致
row_index=1 但 URI 协调伪造成 row-99
page/table 字段不变但整套表 URI 协调伪造
metadata canonical_source 正确但 candidate.source 单独不一致
明细与汇总行混合
metadata 畸形
问题指标与表头不匹配
```

所有失败均满足：

```text
ready=false
request=None
稳定 reason code
不猜测修复缺失证据
不调用模型或网络
```

## 7. 当前结果

机器报告：

```text
evaluation_artifacts/c3_source_bound_sum_series_binder_v1/report.json
```

当前结果：

```text
positive cases = 3 / 3
negative cases = 33 / 33
C3-M correct = 3 / 3
source lineage complete = true
Provider / legacy / network calls = 0
Tokens = 0
measurement_valid = true
```

## 8. 下一步边界

下一步不能直接把 Binder 无条件塞进普通主链。后续需要单独评估：

```text
普通检索结果中何时具备完整结构化表格候选
→ 何时允许尝试 Binder
→ Binder ready=true 后如何受控交给 C3-M
→ Binder 失败时如何保留原有回答路径
```

本轮不包含上述接线工作。
