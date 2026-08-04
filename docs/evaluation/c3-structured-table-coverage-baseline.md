# C3 结构化表格覆盖率基线

## 目标

本基线回答一个问题：冻结的 190 份 MinerU 文档中，有多少文档能够通过现有 `load_structured_table_rows_with_audit` 产出带完整来源身份的结构化表格行。

本轮只测量，不修改 loader、检索、Assembler、Binder、Solver 或其他产品实现。

## 冻结分母

- 文档数：190
- Manifest：`evaluation_artifacts/c3_structured_table_coverage_baseline_v1/corpus_manifest.json`
- Manifest SHA256：`b4189049478125eae4ec4c3fc2406e16de791f44e35778578ce297a463a1678a`
- 领域：合同 14、财报 10、保险 16、监管 130、研究 20

## 测量方法

```text
冻结 manifest
→ 对 190 份文档逐一调用现有 structured-table loader
→ 保留原 loader audit 与 unsupported issues
→ 独立校验行来源、表对象、行范围和 range digest
→ 每份文档归入唯一终态
→ 从文档级记录重新计算全部聚合指标
→ 双跑比较机器报告字节
```

机器报告采用“表级公共身份 + 行级最小字段”的结构，避免把同一张表的范围字段在每一行重复保存，同时仍可展开后逐行复核。

## 基线结果

### 总体

| 指标 | 数值 |
|---|---:|
| 文档数 | 190 |
| 有 table 元素的文档 | 77 |
| 有成功加载表格的文档 | 77 |
| 有成功加载行的文档 | 77 |
| 所有加载行身份完整的文档 | 77 |
| 没有 table 元素的文档 | 113 |
| 看到的表格数 | 8,195 |
| 成功加载的表格数 | 6,071 |
| 成功加载的行数 | 77,525 |
| 不支持的表格布局数 | 2,124 |
| content list 读取错误 | 0 |

终态分布：

```text
NO_TABLE_ELEMENTS: 113
ROWS_LOADED_IDENTITY_COMPLETE: 77
```

本轮未出现：

```text
CONTENT_LIST_READ_ERROR
TABLES_SEEN_NONE_LOADABLE
TABLES_LOADED_NO_ROWS
ROWS_LOADED_IDENTITY_INCOMPLETE
```

### 分领域

| 领域 | 文档数 | 有完整行证据文档 | 无 table 文档 | 表格 seen / loaded | 加载行数 |
|---|---:|---:|---:|---:|---:|
| financial_contracts | 14 | 14 | 0 | 3,723 / 2,497 | 32,942 |
| financial_reports | 10 | 10 | 0 | 3,297 / 2,860 | 32,322 |
| insurance | 16 | 11 | 5 | 133 / 98 | 1,215 |
| regulatory | 130 | 23 | 107 | 700 / 289 | 7,231 |
| research | 20 | 19 | 1 | 342 / 327 | 3,815 |

### 最大失败 issue

| issue | 数量 |
|---|---:|
| `empty_or_image_table` | 2,038 |
| `rowspan_extends_beyond_table` | 62 |
| `span_collision` | 28 |

这些 issue 是表级失败，不等同于文档整体失败。77 份有表文档中仍可能同时包含可加载表和不可加载表。

## 结论边界

这次测量把 B-02 从“覆盖未知”变成了可复核的文档、表格和行级基线，但不能外推为：

- 问题级检索召回率；
- 自然语言问题一定能绑定到这些表格行；
- C3-N 或 C3-O 已接入正常主链；
- FinDocQA 总体答案准确率。

基线显示的主要分层事实是：113 份文档没有 table 元素；在其余 77 份文档中，现有 loader 均能产出至少一行完整身份的结构化证据。同时，2,124 张表仍因图像表或复杂跨行布局未被加载。下一步应由评估者结合问题级需求判断，先测问题到表格行的召回，还是针对最大失败桶做解析能力实验。

## 运行

```bash
python scripts/evaluate_c3_structured_table_coverage.py
python scripts/evaluate_c3_structured_table_coverage.py --validate-only
```

机器报告：

```text
evaluation_artifacts/c3_structured_table_coverage_baseline_v1/report.json
```
