# C3 成员范围不完整失败归因

> 结论：12 个 `MEMBER_RANGE_INCOMPLETE` 中，10 个属于 `ROW_LABEL_MATCH`，2 个属于 `RANGE_EXPANSION`。最大共同根因覆盖 10/12（83.33%），可晋级一个单变量产品实验；两个范围扩展案例不并入该实验。

## 1. 输入与边界

- Task ID：`FDQA-C3U-MEMBER-RANGE-INCOMPLETE-DIAGNOSTIC-V1`
- 固定案例：`12`（FinQA `8`，TAT-QA `4`）
- Gold source Top5：`12/12`
- `MEMBER_RANGE_INCOMPLETE`：`12/12`
- Gold scope 注入：`0/12`
- Provider / legacy / network / Token：`0/0/0/0`

### 冻结输入 Hash

| 文件 | SHA256 |
|---|---|
| `evaluation_artifacts/c3_document_query_stopword_filter_v1/after_report.json` | `667b59e00b977287bc91b2849efd469e64f65fbfa196be2943f2624b6a183da6` |
| `evaluation_artifacts/c3_question_table_evidence_retrieval_baseline_v1/case_manifest.json` | `9ab30f6b0fd960cb35b8821784cd7e256abd46851364fb7057a60e398894951b` |
| `tests/test_c3_question_table_evidence_retrieval.py` | `9123409e7b3cc503ef30e3911de879a5384dd146d597b5c430f4990da73adb15` |
| `docs/evaluation/PROJECT_BOTTLENECK_MAP.md` | `2d96973117bc744f527ed52028bd609d28659366a76c7b0e614e98a0064a8f5c` |

## 2. 分类定义

| 分类 | 定义 |
|---|---|
| `SOURCE_SELECTION` | 正确 source 在 Top5，但后续成员检索未使用或使用错误 source。 |
| `ROW_LABEL_MATCH` | 正确表可用，但问题语义未落到目标行/成员。 |
| `RANGE_EXPANSION` | 已命中表行或章节种子，但未覆盖完整连续成员范围。 |
| `COORDINATE_PROJECTION` | 内容存在但未投影为正确坐标。 |
| `SOURCE_DATA_GAP` | 适配来源缺失所需结构或成员。 |
| `GOLD_OR_MEASUREMENT_MISMATCH` | Gold/评测定义与可观察证据不一致。 |
| `UNRESOLVED` | 证据不足。 |

## 3. 12-case 归因总表

| Case | 数据集 / 能力 | Gold source / Rank | Gold / 已覆盖 / 缺失 | 当前成员轨迹 | 主根因 | 无 Gold 可推导 |
|---|---|---|---:|---|---|---|
| `AAPL/2008/page_78.pdf-2` | finqa / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `finqa://dev/AAPL/2008/page_78.pdf-2` / `5` | `3 / 0 / 3` | 窗口 `0-1800`；表 `3642-3890`；窗口内表坐标 `0`；选中行 `3`；margin `8` | `ROW_LABEL_MATCH` | `true` |
| `AAPL/2014/page_38.pdf-1` | finqa / `SOURCE_BOUND_TABLE_ARGMAX_LABEL` | `finqa://dev/AAPL/2014/page_38.pdf-1` / `3` | `3 / 0 / 3` | 窗口 `0-1800`；表 `3460-3924`；窗口内表坐标 `0`；选中行 `1`；margin `9` | `ROW_LABEL_MATCH` | `true` |
| `AON/2011/page_134.pdf-3` | finqa / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `finqa://dev/AON/2011/page_134.pdf-3` / `3` | `3 / 0 / 3` | 窗口 `0-1800`；表 `2862-3124`；窗口内表坐标 `0`；选中行 `4`；margin `6` | `ROW_LABEL_MATCH` | `true` |
| `GS/2016/page_77.pdf-2` | finqa / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `finqa://dev/GS/2016/page_77.pdf-2` / `5` | `3 / 0 / 3` | 窗口 `0-1800`；表 `2383-2657`；窗口内表坐标 `0`；选中行 `1`；margin `3` | `ROW_LABEL_MATCH` | `true` |
| `LMT/2015/page_56.pdf-2` | finqa / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `finqa://dev/LMT/2015/page_56.pdf-2` / `3` | `3 / 0 / 3` | 窗口 `0-1800`；表 `4038-4229`；窗口内表坐标 `0`；选中行 `1`；margin `3` | `ROW_LABEL_MATCH` | `true` |
| `LMT/2015/page_56.pdf-4` | finqa / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `finqa://dev/LMT/2015/page_56.pdf-4` / `1` | `3 / 0 / 3` | 窗口 `0-1800`；表 `4038-4229`；窗口内表坐标 `0`；选中行 `1`；margin `3` | `ROW_LABEL_MATCH` | `true` |
| `LMT/2016/page_48.pdf-4` | finqa / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `finqa://dev/LMT/2016/page_48.pdf-4` / `4` | `3 / 0 / 3` | 窗口 `0-1800`；表 `2798-2990`；窗口内表坐标 `0`；选中行 `2`；margin `3` | `ROW_LABEL_MATCH` | `true` |
| `MRO/2007/page_134.pdf-3` | finqa / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION` | `finqa://dev/MRO/2007/page_134.pdf-3` / `3` | `3 / 0 / 3` | 窗口 `0-1800`；表 `4621-5015`；窗口内表坐标 `0`；选中行 `3`；margin `2` | `ROW_LABEL_MATCH` | `true` |
| `08ec1b4c-f5dc-4654-b931-4ddd23f81113` | tatqa / `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY` | `tatqa://table/e614befa-40ae-43c0-93b1-385899b6b181` / `3` | `3 / 0 / 3` | 窗口 `0-1800`；表 `4971-5271`；窗口内表坐标 `0`；选中行 `6`；margin `2` | `ROW_LABEL_MATCH` | `true` |
| `378b81b7-c7d6-46f9-aca6-58729440a889` | tatqa / `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY` | `tatqa://table/9afaa852-4103-4782-9109-34104931dbc1` / `1` | `15 / 5 / 10` | 窗口 `0-1800`；表 `1511-2583`；窗口内表坐标 `24`；选中行 `None`；margin `2` | `RANGE_EXPANSION` | `true` |
| `9e3c7e60-2851-455f-835d-7ba3e276ffc2` | tatqa / `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY` | `tatqa://table/008149bc-f997-49d9-a981-f01f19579451` / `2` | `2 / 0 / 2` | 窗口 `0-1800`；表 `4198-4776`；窗口内表坐标 `0`；选中行 `7`；margin `2` | `ROW_LABEL_MATCH` | `true` |
| `e2665282-60dd-4ef1-b29b-fde6a2628d9d` | tatqa / `SOURCE_BOUND_TABLE_SECTION_CARDINALITY` | `tatqa://table/4bf2b6c8-e1f4-4d67-8008-c2ebcf478ed4` / `1` | `7 / 0 / 7` | 窗口 `0-1800`；表 `4694-5108`；窗口内表坐标 `0`；选中行 `None`；margin `0` | `RANGE_EXPANSION` | `true` |

## 4. 逐题原始字段与表结构摘要

### 4.1 `AAPL/2008/page_78.pdf-2`

- Question：what was the average change in unrealized gains on derivative instruments?
- Dataset / capability：`finqa` / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION`
- Gold source / Top5 rank：`finqa://dev/AAPL/2008/page_78.pdf-2` / `5`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r3c1=`$ 19`; r3c2=`$ -3 ( 3 )`; r3c3=`$ -1 ( 1 )`
- Gold table rows：r3 `change in unrealized gains on derivative instruments` → ['change in unrealized gains on derivative instruments', '$ 19', '$ -3 ( 3 )', '$ -1 ( 1 )']
- Candidate trace：rank `5`；matched `['was', 'the', 'change', 'in', 'gains', 'on', 'derivative']`；candidate span `[0, 1800]`；table span `[3642, 3890]`；candidate table rows `[]`。
- Row-label simulation Top3：r3 `change in unrealized gains on derivative instruments` score=14 matched=['change', 'unrealized', 'gain', 'derivative', 'instrument']; r1 `changes in fair value of derivatives` score=6 matched=['change', 'derivative']; r2 `adjustment for net gains/ ( losses ) realized and included in net income` score=2 matched=['gain']
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.2 `AAPL/2014/page_38.pdf-1`

- Question：in what year was the cash cash equivalents and marketable securities the highest?
- Dataset / capability：`finqa` / `SOURCE_BOUND_TABLE_ARGMAX_LABEL`
- Gold source / Top5 rank：`finqa://dev/AAPL/2014/page_38.pdf-1` / `3`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r1c1=`$ 155239`; r1c2=`$ 146761`; r1c3=`$ 121251`
- Gold table rows：r1 `cash cash equivalents and marketable securities` → ['cash cash equivalents and marketable securities', '$ 155239', '$ 146761', '$ 121251']
- Candidate trace：rank `3`；matched `['in', 'year', 'was', 'the', 'cash', 'equivalents', 'and', 'marketable', 'securities']`；candidate span `[0, 1800]`；table span `[3460, 3924]`；candidate table rows `[]`。
- Row-label simulation Top3：r1 `cash cash equivalents and marketable securities` score=11 matched=['cash', 'equivalent', 'marketable', 'securitie']; r5 `cash generated by operating activities` score=2 matched=['cash']; r6 `cash used in investing activities` score=2 matched=['cash']
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.3 `AON/2011/page_134.pdf-3`

- Question：what is the highest value for total operating segments during this period?
- Dataset / capability：`finqa` / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION`
- Gold source / Top5 rank：`finqa://dev/AON/2011/page_134.pdf-3` / `3`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r4c1=`11287`; r4c2=`8512`; r4c3=`7546`
- Gold table rows：r4 `total operating segments` → ['total operating segments', '11287', '8512', '7546']
- Candidate trace：rank `3`；matched `['is', 'the', 'for', 'total', 'operating', 'segments', 'during']`；candidate span `[0, 1800]`；table span `[2862, 3124]`；candidate table rows `[]`。
- Row-label simulation Top3：r4 `total operating segments` score=8 matched=['total', 'operating', 'segment']; r6 `total revenue` score=2 matched=['total']; r0 `years ended december 31` score=0 matched=[]
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.4 `GS/2016/page_77.pdf-2`

- Question：in millions for 2016 2015 , and 2014 , what are total equity securities?
- Dataset / capability：`finqa` / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION`
- Gold source / Top5 rank：`finqa://dev/GS/2016/page_77.pdf-2` / `5`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r1c1=`$ 2573`; r1c2=`$ 3781`; r1c3=`$ 4579`
- Gold table rows：r1 `equity securities` → ['equity securities', '$ 2573', '$ 3781', '$ 4579']
- Candidate trace：rank `5`；matched `['in', 'for', '2015', 'and', '2014', 'are', 'securities']`；candidate span `[0, 1800]`；table span `[2383, 2657]`；candidate table rows `[]`。
- Row-label simulation Top3：r1 `equity securities` score=6 matched=['equity', 'securitie']; r2 `debt securities and loans` score=3 matched=['securitie']; r3 `total net revenues` score=2 matched=['total']
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.5 `LMT/2015/page_56.pdf-2`

- Question：what was average net sales for space systems in millions from 2013 to 2015?
- Dataset / capability：`finqa` / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION`
- Gold source / Top5 rank：`finqa://dev/LMT/2015/page_56.pdf-2` / `3`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r1c1=`$ 9105`; r1c2=`$ 9202`; r1c3=`$ 9288`
- Gold table rows：r1 `net sales` → ['net sales', '$ 9105', '$ 9202', '$ 9288']
- Candidate trace：rank `3`；matched `['was', 'net', 'sales', 'for', 'space', 'systems', 'in', '2013', 'to']`；candidate span `[0, 1800]`；table span `[4038, 4229]`；candidate table rows `[]`。
- Row-label simulation Top3：r1 `net sales` score=3 matched=['net', 'sale']; r0 `` score=0 matched=[]; r2 `operating profit` score=0 matched=[]
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.6 `LMT/2015/page_56.pdf-4`

- Question：what was average net sales for space systems in millions from 2013 to 2015?
- Dataset / capability：`finqa` / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION`
- Gold source / Top5 rank：`finqa://dev/LMT/2015/page_56.pdf-4` / `1`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r1c1=`$ 9105`; r1c2=`$ 9202`; r1c3=`$ 9288`
- Gold table rows：r1 `net sales` → ['net sales', '$ 9105', '$ 9202', '$ 9288']
- Candidate trace：rank `1`；matched `['was', 'net', 'sales', 'for', 'space', 'systems', 'in', '2013', 'to']`；candidate span `[0, 1800]`；table span `[4038, 4229]`；candidate table rows `[]`。
- Row-label simulation Top3：r1 `net sales` score=3 matched=['net', 'sale']; r0 `` score=0 matched=[]; r2 `operating profit` score=0 matched=[]
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.7 `LMT/2016/page_48.pdf-4`

- Question：what were average operating profit for aeronautics in millions between 2014 and 2016?
- Dataset / capability：`finqa` / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION`
- Gold source / Top5 rank：`finqa://dev/LMT/2016/page_48.pdf-4` / `4`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r2c1=`1887`; r2c2=`1681`; r2c3=`1649`
- Gold table rows：r2 `operating profit` → ['operating profit', '1887', '1681', '1649']
- Candidate trace：rank `4`；matched `['operating', 'profit', 'for', 'aeronautics', 'in', '2014', 'and', '2016']`；candidate span `[0, 1800]`；table span `[2798, 2990]`；candidate table rows `[]`。
- Row-label simulation Top3：r2 `operating profit` score=6 matched=['operating', 'profit']; r3 `operating margin` score=3 matched=['operating']; r0 `` score=0 matched=[]
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.8 `MRO/2007/page_134.pdf-3`

- Question：what was the average expected life of the options for the three year period?
- Dataset / capability：`finqa` / `SOURCE_BOUND_NUMERIC_SERIES_AGGREGATION`
- Gold source / Top5 rank：`finqa://dev/MRO/2007/page_134.pdf-3` / `3`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r3c1=`5.0`; r3c2=`5.1`; r3c3=`5.5`
- Gold table rows：r3 `expected life in years` → ['expected life in years', '5.0', '5.1', '5.5']
- Candidate trace：rank `3`；matched `['the', 'of', 'options', 'for', 'three', 'year', 'period']`；candidate span `[0, 1800]`；table span `[4621, 5015]`；candidate table rows `[]`。
- Row-label simulation Top3：r3 `expected life in years` score=5 matched=['expected', 'life']; r2 `expected annual dividends per share` score=3 matched=['expected']; r4 `expected volatility` score=3 matched=['expected']
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.9 `08ec1b4c-f5dc-4654-b931-4ddd23f81113`

- Question：How many years did Total Product revenue exceed $35,000 million?
- Dataset / capability：`tatqa` / `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY`
- Gold source / Top5 rank：`tatqa://table/e614befa-40ae-43c0-93b1-385899b6b181` / `3`
- Gold / covered / missing coordinates：`3 / 0 / 3`
- Gold coordinate raw values：r6c1=`39,005`; r6c2=`36,709`; r6c3=`35,705`
- Gold table rows：r6 `Total Product` → ['Total Product', '39,005', '36,709', '35,705']
- Candidate trace：rank `3`；matched `['total', 'product', 'revenue', 'million']`；candidate span `[0, 1800]`；table span `[4971, 5271]`；candidate table rows `[]`。
- Row-label simulation Top3：r6 `Total Product` score=5 matched=['total', 'product']; r1 `Revenue:` score=3 matched=['revenue']; r5 `Other Products` score=3 matched=['product']
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.10 `378b81b7-c7d6-46f9-aca6-58729440a889`

- Question：How many components of deferred tax assets exceeded $50,000 thousand in 2019?
- Dataset / capability：`tatqa` / `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY`
- Gold source / Top5 rank：`tatqa://table/9afaa852-4103-4782-9109-34104931dbc1` / `1`
- Gold / covered / missing coordinates：`15 / 5 / 10`
- Gold coordinate raw values：r3c1=`$183,297`; r4c1=`6,165`; r5c1=`9,590`; r6c1=`10,401`; r7c1=`81,731`; r8c1=`66,268`; r9c1=`42,464`; r10c1=`15,345`; r11c1=`7,617`; r12c1=`2,179`; r13c1=`5,853`; r14c1=`9,878`; r15c1=`7,799`; r16c1=`19,195`; r17c1=`21,907`
- Gold table rows：r3 `Net operating loss carry forward` → ['Net operating loss carry forward', '$183,297', '$119,259']; r4 `Receivables` → ['Receivables', '6,165', '7,111']; r5 `Inventories` → ['Inventories', '9,590', '7,634']; r6 `Compensated absences` → ['Compensated absences', '10,401', '8,266']; r7 `Accrued expenses` → ['Accrued expenses', '81,731', '81,912']; r8 `Property, plant and equipment, principally due to differences in depreciation and amortization` → ['Property, plant and equipment, principally due to differences in depreciation and amortization', '66,268', '97,420']; r9 `Domestic federal and state tax credits` → ['Domestic federal and state tax credits', '42,464', '70,153']; r10 `Foreign jurisdiction tax credits` → ['Foreign jurisdiction tax credits', '15,345', '25,887']; r11 `Equity compensation–Domestic` → ['Equity compensation–Domestic', '7,617', '7,566']; r12 `Equity compensation–Foreign` → ['Equity compensation–Foreign', '2,179', '2,401']; r13 `Domestic federal interest carry forward` → ['Domestic federal interest carry forward', '5,853', '—']; r14 `Cash flow hedges` → ['Cash flow hedges', '9,878', '—']; r15 `Unrecognized capital loss carry forward` → ['Unrecognized capital loss carry forward', '7,799', '—']; r16 `Revenue recognition` → ['Revenue recognition', '19,195', '—']; r17 `Other` → ['Other', '21,907', '18,176']
- Candidate trace：rank `1`；matched `['components', 'of', 'deferred', 'tax', 'assets', 'thousand', 'in', '2019']`；candidate span `[0, 1800]`；table span `[1511, 2583]`；candidate table rows `[0, 1, 2, 3, 4, 5, 6, 7]`。
- Row-label simulation Top3：r2 `Deferred tax assets:` score=6 matched=['deferred', 'tax', 'asset']; r18 `Total deferred tax assets before valuation allowances` score=6 matched=['deferred', 'tax', 'asset']; r20 `Net deferred tax assets` score=6 matched=['deferred', 'tax', 'asset']
- Root cause：`RANGE_EXPANSION`。目标表段已进入候选，但固定字符窗口在完整成员范围结束前截断。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.11 `9e3c7e60-2851-455f-835d-7ba3e276ffc2`

- Question：How many years did Gross deferred tax assets exceed $400 million?
- Dataset / capability：`tatqa` / `SOURCE_BOUND_TABLE_PREDICATE_CARDINALITY`
- Gold source / Top5 rank：`tatqa://table/008149bc-f997-49d9-a981-f01f19579451` / `2`
- Gold / covered / missing coordinates：`2 / 0 / 2`
- Gold coordinate raw values：r7c1=`426`; r7c2=`395`
- Gold table rows：r7 `Gross deferred tax assets` → ['Gross deferred tax assets', '426', '395']
- Candidate trace：rank `2`；matched `['how', 'deferred', 'tax', 'assets', 'million']`；candidate span `[0, 1800]`；table span `[4198, 4776]`；candidate table rows `[]`。
- Row-label simulation Top3：r7 `Gross deferred tax assets` score=8 matched=['gross', 'deferred', 'tax', 'asset']; r1 `Deferred tax assets:` score=6 matched=['deferred', 'tax', 'asset']; r9 `Deferred tax assets, net of valuation allowance` score=6 matched=['deferred', 'tax', 'asset']
- Root cause：`ROW_LABEL_MATCH`。问题可唯一匹配目标表行，但页级窗口锚定在更早的叙述性同词位置，未进入任何表格坐标。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

### 4.12 `e2665282-60dd-4ef1-b29b-fde6a2628d9d`

- Question：How many Executive Officers are there in the company as at 24 February 2020?
- Dataset / capability：`tatqa` / `SOURCE_BOUND_TABLE_SECTION_CARDINALITY`
- Gold source / Top5 rank：`tatqa://table/4bf2b6c8-e1f4-4d67-8008-c2ebcf478ed4` / `1`
- Gold / covered / missing coordinates：`7 / 0 / 7`
- Gold coordinate raw values：r1c0=`Leigh R Fox`; r2c0=`Andrew R Kaiser`; r3c0=`Christi H. Cornette`; r4c0=`Thomas E. Simpson`; r5c0=`Christopher J. Wilson`; r6c0=`Joshua T. Duckworth`; r7c0=`Suzanne E. Maratta`
- Gold table rows：r1 `Leigh R Fox` → ['Leigh R Fox', '47', 'President and Chief Executive Officer']; r2 `Andrew R Kaiser` → ['Andrew R Kaiser', '51', 'Chief Financial Officer']; r3 `Christi H. Cornette` → ['Christi H. Cornette', '64', 'Chief Culture Officer']; r4 `Thomas E. Simpson` → ['Thomas E. Simpson', '47', 'Chief Operating Officer']; r5 `Christopher J. Wilson` → ['Christopher J. Wilson', '54', 'Vice President and General Counsel']; r6 `Joshua T. Duckworth` → ['Joshua T. Duckworth', '41', 'Vice President of Treasury, Corporate Finance and Investor Relations']; r7 `Suzanne E. Maratta` → ['Suzanne E. Maratta', '37', 'Vice President and Corporate Controller']
- Candidate trace：rank `1`；matched `['executive', 'officers', 'are', 'in', 'the', 'company', 'as', 'at', '2020']`；candidate span `[0, 1800]`；table span `[4694, 5108]`；candidate table rows `[]`。
- Row-label simulation Top3：r0 `Name` score=0 matched=[]; r1 `Leigh R Fox` score=0 matched=[]; r2 `Andrew R Kaiser` score=0 matched=[]
- Root cause：`RANGE_EXPANSION`。章节标题已命中，但固定字符窗口结束于表格成员之前，未扩展到该章节的连续表行。
- Runtime derivation：`true`；只依赖 question、candidate capability、table structure、retrieved source/member evidence 与 lineage。
- Gold leakage audit：runtime Gold coordinate=`false`，case ID=`false`，official answer=`false`，retriever gold scope injected=`false`。

## 5. 共同根因统计

| Root cause | Case count | Share |
|---|---:|---:|
| `RANGE_EXPANSION` | `2` | `16.67%` |
| `ROW_LABEL_MATCH` | `10` | `83.33%` |

- `largest_common_root_cause`：`ROW_LABEL_MATCH`
- `largest_common_case_count`：`10`
- `largest_common_share`：`83.33%`
- `confidence`：`high`

共同模式不是 Gold 坐标缺失，也不是 source 选错。10 个案例的目标行标签能从 question 与 canonical table 行标签中形成唯一正分匹配，但当前页级窗口优先锚定表前的叙述性同词位置，导致目标行完全没有进入候选证据。

## 6. Gold 泄漏边界

| 环节 | 允许依赖 | 禁止依赖 | 结果 |
|---|---|---|---|
| 离线诊断 | gold_source_object_id, gold_coordinates, terminal_layer | 不作为运行时条件 | PASS |
| 运行时机制 | question, candidate_capability, table_structure, retrieved_source_member_evidence, lineage_metadata | gold_coordinates, case_id, official_answer, case_specific_branch | `PASS` |

行标签模拟在运行时侧只遍历当前已检索 source 的 canonical table 行标签，并用 question terms 评分；Gold 仅用于离线核对所选行是否正确，不进入选择逻辑。

## 7. 单变量裁决

**裁决：`PROMOTE_SINGLE_VARIABLE`**

### Exactly one principal change

在 canonical lexical evidence candidate 构造中增加表行标签锚定：当问题词与 canonical table 某一行标签形成唯一正分匹配时，候选证据改为该完整表行（含表头和全部列），而不是页文本中第一个同词位置的固定字符窗口。

- 单一产品模块：`src/retrieval/canonical_lexical.py`
- 目标根因：`ROW_LABEL_MATCH`
- 预计最大影响：`10/12`，置信度 `high`
- 禁止同时修改：`src/retrieval document ranking and top_k`, `src/evidence/structured_tables.py`, `src/calculation/**`, `src/solvers/**`, `evaluator/manifest/Gold scorer`

### 冻结指标

| 边界 | 当前 | H-07 最低通过线 | 只读模拟上界（非产品实测） |
|---|---:|---:|---:|
| Row-label group complete | `0/10` | `>=8/10` | `10/10` |
| Row-label group coordinates | `0/29` | `>=23/29` | `29/29` |
| All 12 complete | `0/12` | `>=8/12` | `10/12` |
| All 12 coordinates | `5/51` | `>=28/51` | `34/51` |
| Full54 BINDING_READY | `22/54` | `>=30/54` | `32/54` |
| Full54 coordinate coverage | `70/185` | `>=93/185` | `99/185` |

### Full54 护栏

- `document_recall_at_5`：`must remain 39/54`
- `table_source_recall_at_5`：`must remain 34/54`
- `existing_binding_ready_regressions`：`0`
- `lost_document_cases`：`0`
- `provider_legacy_network_token_calls`：`0/0/0/0`
- `query_terms_score_top_k_document_ranking`：`unchanged`

### 回滚条件

- row_label_group gain is below 8 complete cases
- any existing BINDING_READY case regresses
- document or table-source recall decreases
- lost_document_cases becomes non-zero
- implementation requires Gold coordinates, case IDs, or a second product module

## 8. 不并入本实验的分支

`RANGE_EXPANSION`：`378b81b7-c7d6-46f9-aca6-58729440a889`, `e2665282-60dd-4ef1-b29b-fde6a2628d9d`

这 2 题需要多行/章节范围扩展，不能与行标签选择硬拼进同一主变量。

## 9. 下一步

由 Evaluator 独立复核本诊断。如果通过，下一包应是 H-07 capability experiment：只在 `src/retrieval/canonical_lexical.py` 改一个主变量——表行标签锚定与完整行候选构造；使用同一 12-case / 54-case evaluator 做前后对比。两个 `RANGE_EXPANSION` 案例留在 B-03 的多行/章节范围分支，不能混入 H-07。
