# C3 文档缺失完整排名与独立归因

> **修复结论：父任务关于“8/15 共享同一根因，可晋级 `document_specificity_boost`”的结论已撤销。** 完整排名事实继续有效；独立归因得到最大共同根因仅 `6/15`，固定 `3.0 / 4.0 / 5.0` 三点敏感性也未形成稳健区间。最终裁决为 `NO_SINGLE_VARIABLE`，不授权产品代码修改。

## 1. 结论分层

```text
Measured facts
→ 15 个 DOCUMENT_MISS 的 46 文档完整排名可复现

Independent diagnosis
→ 只读取同页 base/business/scaffold 分数分解和竞争特征
→ 不读取 boost、simulated rank 或 recovered flag

Candidate evaluation
→ 独立标签文件 Hash 冻结后
→ 只评估既有 specificity candidate 的 boost 3 / 4 / 5

Final verdict
→ NO_SINGLE_VARIABLE
```

## 2. 冻结输入

- 父任务机器诊断：`handoffs/evaluator_executor/FDQA-C3W-DOCUMENT-MISS-DIAGNOSTIC-V1/evidence/document_miss_diagnostic.json`，SHA256 `658899451f570e0c2f8a481135743a52ac6697684bab0c6477e6a9014d64a653`。
- 独立归因：`handoffs/evaluator_executor/FDQA-C3W-DOCUMENT-MISS-DIAGNOSTIC-REPAIR-V1/evidence/independent_diagnosis.json`，SHA256 `aaceabc21acbb5d7f254984810194de01c7421780c0732ebc20f2dc1e62b59b1`。
- 固定敏感性：`handoffs/evaluator_executor/FDQA-C3W-DOCUMENT-MISS-DIAGNOSTIC-REPAIR-V1/evidence/candidate_sensitivity.json`，SHA256 `bdf982fce7e7d5de0e7d89c56a5a58a85fff0126e2f788ce8464b2617c688a36`。
- 固定案例：15；FinQA 5 / TAT-QA 10；候选池 46；Gold 文档在候选池 15/15。
- Provider / legacy / network / Token：0/0/0/0。

## 3. 可保留的完整排名事实

父任务的 Gold rank、score、Top1/Top5 gap 和 Top10 竞争文档已由 Evaluator 独立复现。本修复不重新解释这些事实为产品改善。

| Case | Dataset | Gold document | Base rank / score | Gap Top1 / Top5 |
|---|---|---|---:|---:|
| `AAPL/2005/page_83.pdf-1` | `finqa` | `finqa::AAPL/2005/page_83.pdf` | `10 / 42.00` | `80.00 / 22.00` |
| `AON/2009/page_46.pdf-1` | `finqa` | `finqa::AON/2009/page_46.pdf` | `6 / 32.00` | `24.00 / 4.00` |
| `AON/2009/page_46.pdf-2` | `finqa` | `finqa::AON/2009/page_46.pdf` | `9 / 40.00` | `20.00 / 4.00` |
| `AON/2014/page_47.pdf-1` | `finqa` | `finqa::AON/2014/page_47.pdf` | `6 / 32.00` | `24.00 / 0.00` |
| `MRO/2006/page_33.pdf-3` | `finqa` | `finqa::MRO/2006/page_33.pdf` | `6 / 48.00` | `16.00 / 0.00` |
| `01ba4058-8e2c-461e-8400-78be688442bf` | `tatqa` | `tatqa::doc::175` | `23 / 42.00` | `64.00 / 26.00` |
| `1148519a-e611-4859-8ebd-d87eed5ed048` | `tatqa` | `tatqa::doc::93` | `26 / 58.00` | `104.00 / 30.00` |
| `3d384cee-82de-48f1-98ff-a972404bce4c` | `tatqa` | `tatqa::doc::16` | `22 / 34.00` | `52.00 / 22.00` |
| `54df78bf-1e81-4ebe-ba8d-278fee472ffd` | `tatqa` | `tatqa::doc::16` | `10 / 34.00` | `50.00 / 10.00` |
| `567c418e-8e2c-489b-bba4-5b7985ee1590` | `tatqa` | `tatqa::doc::96` | `13 / 26.00` | `74.00 / 14.00` |
| `89e7bd4a-9716-4b7c-afbd-2d7c046f34db` | `tatqa` | `tatqa::doc::257` | `14 / 48.00` | `82.00 / 18.00` |
| `8c38b541-2093-416e-82c5-a107e8928782` | `tatqa` | `tatqa::doc::38` | `7 / 46.00` | `28.00 / 6.00` |
| `921426ff-bd1b-433c-886c-e38c4deaf900` | `tatqa` | `tatqa::doc::60` | `23 / 58.00` | `30.00 / 24.00` |
| `a75830fe-acba-4751-98cd-de697fe03b30` | `tatqa` | `tatqa::doc::199` | `41 / 38.00` | `68.00 / 56.00` |
| `d713cc10-43c6-4fe9-9808-24ddc7160f0f` | `tatqa` | `tatqa::doc::177` | `24 / 54.00` | `88.00 / 64.00` |

## 4. 独立归因方法

### 4.1 词项分组

```text
scaffold_numeric_terms
= 纯数字 + 聚合/比较/计数模板词 + 时间词 + 单位词

business_specific_terms
= document query terms - scaffold_numeric_terms
```

词表沿用父任务冻结版本，没有按案例、Gold 或模拟结果增删。

### 4.2 同页精确分解

对每个文档先按正式 base score 选择正文页，再在同一标题和同一正文页上分解：

```text
base_score
= title_base × 2 + selected_base_page_score
= business_contribution + scaffold_numeric_contribution
```

15 × 46 个文档的分解误差均不超过 `1e-9`。没有分别选择 business 最大页和 scaffold 最大页后再伪装成可加分解。

### 4.3 根因与候选机制隔离

根因函数的 AST 审计结果为 `PASS`。函数禁止读取：

```text
candidate boost
simulated score / rank
recovered_to_top5
specificity candidate name
```

独立标签文件先生成并固定 SHA256，随后敏感性脚本才允许运行。

## 5. 15-case 同页分解与独立标签

| Case | Base rank | Business rank | Scaffold rank | Gold contribution B / S | Rank5 advantage B / S | Independent root cause | Confidence |
|---|---:|---:|---:|---:|---:|---|---|
| `AAPL/2005/page_83.pdf-1` | 10 | 10 | 16 | `22.00 / 20.00` | `-14.00 / 36.00` | `NEAR_DUPLICATE_COMPETITION` | `medium` |
| `AON/2009/page_46.pdf-1` | 6 | 5 | 30 | `32.00 / 0.00` | `4.00 / 0.00` | `IDENTITY_SIGNAL_MISSING` | `medium` |
| `AON/2009/page_46.pdf-2` | 9 | 9 | 7 | `40.00 / 0.00` | `4.00 / 0.00` | `NEAR_DUPLICATE_COMPETITION` | `medium` |
| `AON/2014/page_47.pdf-1` | 6 | 5 | 20 | `32.00 / 0.00` | `0.00 / 0.00` | `IDENTITY_SIGNAL_MISSING` | `medium` |
| `MRO/2006/page_33.pdf-3` | 6 | 2 | 19 | `28.00 / 20.00` | `-8.00 / 8.00` | `GENERIC_OR_NUMERIC_TERM_DOMINANCE` | `high` |
| `01ba4058-8e2c-461e-8400-78be688442bf` | 23 | 33 | 23 | `0.00 / 42.00` | `0.00 / 26.00` | `DOCUMENT_CONTENT_OR_METADATA_GAP` | `high` |
| `1148519a-e611-4859-8ebd-d87eed5ed048` | 26 | 23 | 19 | `12.00 / 46.00` | `26.00 / 4.00` | `NEAR_DUPLICATE_COMPETITION` | `medium` |
| `3d384cee-82de-48f1-98ff-a972404bce4c` | 22 | 3 | 32 | `16.00 / 18.00` | `-16.00 / 38.00` | `GENERIC_OR_NUMERIC_TERM_DOMINANCE` | `high` |
| `54df78bf-1e81-4ebe-ba8d-278fee472ffd` | 10 | 3 | 29 | `16.00 / 18.00` | `-16.00 / 26.00` | `GENERIC_OR_NUMERIC_TERM_DOMINANCE` | `high` |
| `567c418e-8e2c-489b-bba4-5b7985ee1590` | 13 | 1 | 23 | `8.00 / 18.00` | `-8.00 / 22.00` | `GENERIC_OR_NUMERIC_TERM_DOMINANCE` | `high` |
| `89e7bd4a-9716-4b7c-afbd-2d7c046f34db` | 14 | 1 | 29 | `12.00 / 36.00` | `-12.00 / 30.00` | `GENERIC_OR_NUMERIC_TERM_DOMINANCE` | `high` |
| `8c38b541-2093-416e-82c5-a107e8928782` | 7 | 4 | 20 | `30.00 / 16.00` | `-8.00 / 14.00` | `GENERIC_OR_NUMERIC_TERM_DOMINANCE` | `high` |
| `921426ff-bd1b-433c-886c-e38c4deaf900` | 23 | 6 | 36 | `24.00 / 34.00` | `-16.00 / 40.00` | `NEAR_DUPLICATE_COMPETITION` | `medium` |
| `a75830fe-acba-4751-98cd-de697fe03b30` | 41 | 10 | 44 | `12.00 / 26.00` | `-12.00 / 68.00` | `UNRESOLVED` | `low` |
| `d713cc10-43c6-4fe9-9808-24ddc7160f0f` | 24 | 11 | 34 | `12.00 / 42.00` | `48.00 / 16.00` | `NEAR_DUPLICATE_COMPETITION` | `medium` |

### 5.1 逐题不等式依据

#### `AAPL/2005/page_83.pdf-1`

- Question：what was the average effective tax rate for the three year period?
- Business terms：`['effective', 'tax', 'rate']`
- Scaffold/numeric terms：`['average', 'three', 'year', 'period']`
- Gold base = business + scaffold：`42.000000 = 22.000000 + 20.000000`；selected page `1`。
- Rank5 inequality：`base_gap=22.000000 = business_advantage=-14.000000 + scaffold_advantage=36.000000`；分解误差 `0.000000000000`。
- Business-only rank `10`；scaffold-only rank `16`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`4`；question identity signal=`false`。
- Independent label：`NEAR_DUPLICATE_COMPETITION`。Business-only rank remains outside Top5 and Top5 contains a same-company adjacent-year source or a competitor covering all Gold matched business terms. Confidence=`medium`。

#### `AON/2009/page_46.pdf-1`

- Question：what is the average segment revenue , in millions?
- Business terms：`['segment', 'revenue']`
- Scaffold/numeric terms：`['average', 'millions']`
- Gold base = business + scaffold：`32.000000 = 32.000000 + 0.000000`；selected page `1`。
- Rank5 inequality：`base_gap=4.000000 = business_advantage=4.000000 + scaffold_advantage=0.000000`；分解误差 `0.000000000000`。
- Business-only rank `5`；scaffold-only rank `30`。
- Competition：same-company adjacent-year=`true`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`7`；question identity signal=`false`。
- Independent label：`IDENTITY_SIGNAL_MISSING`。The question lacks company/year/title/source identity and at least two Top10 competitors share all major Gold business terms. Confidence=`medium`。

#### `AON/2009/page_46.pdf-2`

- Question：what is the lowest segment operating income?
- Business terms：`['segment', 'operating', 'income']`
- Scaffold/numeric terms：`['lowest']`
- Gold base = business + scaffold：`40.000000 = 40.000000 + 0.000000`；selected page `1`。
- Rank5 inequality：`base_gap=4.000000 = business_advantage=4.000000 + scaffold_advantage=0.000000`；分解误差 `0.000000000000`。
- Business-only rank `9`；scaffold-only rank `7`。
- Competition：same-company adjacent-year=`true`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`5`；question identity signal=`false`。
- Independent label：`NEAR_DUPLICATE_COMPETITION`。Business-only rank remains outside Top5 and Top5 contains a same-company adjacent-year source or a competitor covering all Gold matched business terms. Confidence=`medium`。

#### `AON/2014/page_47.pdf-1`

- Question：what is the variation between the average and the highest operating margin?
- Business terms：`['operating', 'margin']`
- Scaffold/numeric terms：`['variation', 'between', 'average', 'highest']`
- Gold base = business + scaffold：`32.000000 = 32.000000 + 0.000000`；selected page `1`。
- Rank5 inequality：`base_gap=0.000000 = business_advantage=0.000000 + scaffold_advantage=0.000000`；分解误差 `0.000000000000`。
- Business-only rank `5`；scaffold-only rank `20`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`6`；question identity signal=`false`。
- Independent label：`IDENTITY_SIGNAL_MISSING`。The question lacks company/year/title/source identity and at least two Top10 competitors share all major Gold business terms. Confidence=`medium`。

#### `MRO/2006/page_33.pdf-3`

- Question：what was average propane sales in tbd for the three year period?
- Business terms：`['propane', 'sales', 'tbd']`
- Scaffold/numeric terms：`['average', 'three', 'year', 'period']`
- Gold base = business + scaffold：`48.000000 = 28.000000 + 20.000000`；selected page `1`。
- Rank5 inequality：`base_gap=0.000000 = business_advantage=-8.000000 + scaffold_advantage=8.000000`；分解误差 `0.000000000000`。
- Business-only rank `2`；scaffold-only rank `19`。
- Competition：same-company adjacent-year=`true`；Top5 covers all Gold business terms=`false`；Top10 shared-major count=`1`；question identity signal=`false`。
- Independent label：`GENERIC_OR_NUMERIC_TERM_DOMINANCE`。Gold business-only rank is within Top5 and the Rank5 failure gap is dominated by scaffold/numeric contribution: scaffold_advantage=8.000000 > max(business_advantage=-8.000000, 0), with base_gap=0.000000. Confidence=`high`。

#### `01ba4058-8e2c-461e-8400-78be688442bf`

- Question：How many items in the table had values provided in 2019 but not in 2018?
- Business terms：`[]`
- Scaffold/numeric terms：`['many', 'items', 'table', 'had', 'values', 'provided', '2019', 'but', 'not', '2018']`
- Gold base = business + scaffold：`42.000000 = 0.000000 + 42.000000`；selected page `1`。
- Rank5 inequality：`base_gap=26.000000 = business_advantage=0.000000 + scaffold_advantage=26.000000`；分解误差 `0.000000000000`。
- Business-only rank `33`；scaffold-only rank `23`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`false`；Top10 shared-major count=`0`；question identity signal=`false`。
- Independent label：`DOCUMENT_CONTENT_OR_METADATA_GAP`。Gold canonical title/body contains none of the question business terms. Confidence=`high`。

#### `1148519a-e611-4859-8ebd-d87eed5ed048`

- Question：How many years did the Income tax benefit exceed $1,500 thousand?
- Business terms：`['income', 'tax', 'benefit']`
- Scaffold/numeric terms：`['many', 'years', 'did', 'exceed', '1', '500', 'thousand']`
- Gold base = business + scaffold：`58.000000 = 12.000000 + 46.000000`；selected page `1`。
- Rank5 inequality：`base_gap=30.000000 = business_advantage=26.000000 + scaffold_advantage=4.000000`；分解误差 `0.000000000000`。
- Business-only rank `23`；scaffold-only rank `19`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`7`；question identity signal=`false`。
- Independent label：`NEAR_DUPLICATE_COMPETITION`。Business-only rank remains outside Top5 and Top5 contains a same-company adjacent-year source or a competitor covering all Gold matched business terms. Confidence=`medium`。

#### `3d384cee-82de-48f1-98ff-a972404bce4c`

- Question：How many expenses segments in 2019 were above $50 million?
- Business terms：`['expenses', 'segments']`
- Scaffold/numeric terms：`['many', '2019', 'above', '50', 'million']`
- Gold base = business + scaffold：`34.000000 = 16.000000 + 18.000000`；selected page `1`。
- Rank5 inequality：`base_gap=22.000000 = business_advantage=-16.000000 + scaffold_advantage=38.000000`；分解误差 `0.000000000000`。
- Business-only rank `3`；scaffold-only rank `32`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`3`；question identity signal=`false`。
- Independent label：`GENERIC_OR_NUMERIC_TERM_DOMINANCE`。Gold business-only rank is within Top5 and the Rank5 failure gap is dominated by scaffold/numeric contribution: scaffold_advantage=38.000000 > max(business_advantage=-16.000000, 0), with base_gap=22.000000. Confidence=`high`。

#### `54df78bf-1e81-4ebe-ba8d-278fee472ffd`

- Question：How many expenses segments  in 2018 were below $100 million?
- Business terms：`['expenses', 'segments']`
- Scaffold/numeric terms：`['many', '2018', 'below', '100', 'million']`
- Gold base = business + scaffold：`34.000000 = 16.000000 + 18.000000`；selected page `1`。
- Rank5 inequality：`base_gap=10.000000 = business_advantage=-16.000000 + scaffold_advantage=26.000000`；分解误差 `0.000000000000`。
- Business-only rank `3`；scaffold-only rank `29`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`2`；question identity signal=`false`。
- Independent label：`GENERIC_OR_NUMERIC_TERM_DOMINANCE`。Gold business-only rank is within Top5 and the Rank5 failure gap is dominated by scaffold/numeric contribution: scaffold_advantage=26.000000 > max(business_advantage=-16.000000, 0), with base_gap=10.000000. Confidence=`high`。

#### `567c418e-8e2c-489b-bba4-5b7985ee1590`

- Question：How many years did the amount of Finished Goods exceed $10,000 thousand?
- Business terms：`['finished', 'goods']`
- Scaffold/numeric terms：`['many', 'years', 'did', 'amount', 'exceed', '10', '000', 'thousand']`
- Gold base = business + scaffold：`26.000000 = 8.000000 + 18.000000`；selected page `1`。
- Rank5 inequality：`base_gap=14.000000 = business_advantage=-8.000000 + scaffold_advantage=22.000000`；分解误差 `0.000000000000`。
- Business-only rank `1`；scaffold-only rank `23`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`false`；Top10 shared-major count=`0`；question identity signal=`false`。
- Independent label：`GENERIC_OR_NUMERIC_TERM_DOMINANCE`。Gold business-only rank is within Top5 and the Rank5 failure gap is dominated by scaffold/numeric contribution: scaffold_advantage=22.000000 > max(business_advantage=-8.000000, 0), with base_gap=14.000000. Confidence=`high`。

#### `89e7bd4a-9716-4b7c-afbd-2d7c046f34db`

- Question：How many years did servicing fees exceed $3,000 thousand?
- Business terms：`['servicing', 'fees']`
- Scaffold/numeric terms：`['many', 'years', 'did', 'exceed', '3', '000', 'thousand']`
- Gold base = business + scaffold：`48.000000 = 12.000000 + 36.000000`；selected page `1`。
- Rank5 inequality：`base_gap=18.000000 = business_advantage=-12.000000 + scaffold_advantage=30.000000`；分解误差 `0.000000000000`。
- Business-only rank `1`；scaffold-only rank `29`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`false`；Top10 shared-major count=`0`；question identity signal=`false`。
- Independent label：`GENERIC_OR_NUMERIC_TERM_DOMINANCE`。Gold business-only rank is within Top5 and the Rank5 failure gap is dominated by scaffold/numeric contribution: scaffold_advantage=30.000000 > max(business_advantage=-12.000000, 0), with base_gap=18.000000. Confidence=`high`。

#### `8c38b541-2093-416e-82c5-a107e8928782`

- Question：How many years did net income exceed $30,000 thousand?
- Business terms：`['net', 'income']`
- Scaffold/numeric terms：`['many', 'years', 'did', 'exceed', '30', '000', 'thousand']`
- Gold base = business + scaffold：`46.000000 = 30.000000 + 16.000000`；selected page `1`。
- Rank5 inequality：`base_gap=6.000000 = business_advantage=-8.000000 + scaffold_advantage=14.000000`；分解误差 `0.000000000000`。
- Business-only rank `4`；scaffold-only rank `20`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`7`；question identity signal=`false`。
- Independent label：`GENERIC_OR_NUMERIC_TERM_DOMINANCE`。Gold business-only rank is within Top5 and the Rank5 failure gap is dominated by scaffold/numeric contribution: scaffold_advantage=14.000000 > max(business_advantage=-8.000000, 0), with base_gap=6.000000. Confidence=`high`。

#### `921426ff-bd1b-433c-886c-e38c4deaf900`

- Question：How many years did Total services exceed $5,000 million?
- Business terms：`['total', 'services']`
- Scaffold/numeric terms：`['many', 'years', 'did', 'exceed', '5', '000', 'million']`
- Gold base = business + scaffold：`58.000000 = 24.000000 + 34.000000`；selected page `1`。
- Rank5 inequality：`base_gap=24.000000 = business_advantage=-16.000000 + scaffold_advantage=40.000000`；分解误差 `0.000000000000`。
- Business-only rank `6`；scaffold-only rank `36`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`4`；question identity signal=`false`。
- Independent label：`NEAR_DUPLICATE_COMPETITION`。Business-only rank remains outside Top5 and Top5 contains a same-company adjacent-year source or a competitor covering all Gold matched business terms. Confidence=`medium`。

#### `a75830fe-acba-4751-98cd-de697fe03b30`

- Question：How many years did the company have cash proceeds received that exceeded $5,000 million?
- Business terms：`['cash', 'proceeds', 'received']`
- Scaffold/numeric terms：`['many', 'years', 'did', 'company', 'have', 'exceeded', '5', '000', 'million']`
- Gold base = business + scaffold：`38.000000 = 12.000000 + 26.000000`；selected page `1`。
- Rank5 inequality：`base_gap=56.000000 = business_advantage=-12.000000 + scaffold_advantage=68.000000`；分解误差 `0.000000000000`。
- Business-only rank `10`；scaffold-only rank `44`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`false`；Top10 shared-major count=`0`；question identity signal=`false`。
- Independent label：`UNRESOLVED`。The pre-registered observable rules do not support a stronger label. Confidence=`low`。

#### `d713cc10-43c6-4fe9-9808-24ddc7160f0f`

- Question：How many years did Stock-based compensation exceed $2,000 thousand?
- Business terms：`['stock', 'based', 'compensation']`
- Scaffold/numeric terms：`['many', 'years', 'did', 'exceed', '2', '000', 'thousand']`
- Gold base = business + scaffold：`54.000000 = 12.000000 + 42.000000`；selected page `1`。
- Rank5 inequality：`base_gap=64.000000 = business_advantage=48.000000 + scaffold_advantage=16.000000`；分解误差 `0.000000000000`。
- Business-only rank `11`；scaffold-only rank `34`。
- Competition：same-company adjacent-year=`false`；Top5 covers all Gold business terms=`true`；Top10 shared-major count=`5`；question identity signal=`false`。
- Independent label：`NEAR_DUPLICATE_COMPETITION`。Business-only rank remains outside Top5 and Top5 contains a same-company adjacent-year source or a competitor covering all Gold matched business terms. Confidence=`medium`。

## 6. 独立根因分布

| Root cause | Count | Share |
|---|---:|---:|
| `DOCUMENT_CONTENT_OR_METADATA_GAP` | 1 | 6.67% |
| `GENERIC_OR_NUMERIC_TERM_DOMINANCE` | 6 | 40.00% |
| `IDENTITY_SIGNAL_MISSING` | 2 | 13.33% |
| `NEAR_DUPLICATE_COMPETITION` | 5 | 33.33% |
| `UNRESOLVED` | 1 | 6.67% |

- 最大共同根因：`GENERIC_OR_NUMERIC_TERM_DOMINANCE`
- 案例数：`6/15`
- 独立问题数：`6`
- Confidence：`high when exact contribution inequality fires; medium/low for competition and unresolved labels`

父任务的 `8/4/3` 分布没有自动沿用。独立规则重算后，最大共同根因只有 `6/15`，已经低于晋级要求的 8 个案例和 8 个独立问题。

## 7. 固定候选敏感性

标签冻结后，只评估父任务已有候选：

```text
candidate_score = base_score + boost × business_specific_score
boost grid = 3.0 / 4.0 / 5.0
```

没有搜索新系数、新词表、新公式、IDF/BM25 或 title multiplier。

| Boost | Fixed15 recovered | Independent group recovered | Recall@1/3/5 | Old Top5 regressions | Table recall | Coordinates | BINDING_READY | Downstream guardrails | Point pass |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 3.0 | 6/15 | 5/6 | 27/40/45 | 0 | 38/54 | 108/185 | 35/54 | `true` | `false` |
| 4.0 | 8/15 | 6/6 | 27/42/47 | 0 | 38/54 | 108/185 | 35/54 | `true` | `false` |
| 5.0 | 8/15 | 6/6 | 27/42/46 | 1 | 37/54 | 105/185 | 34/54 | `false` | `false` |

### 7.1 逐点失败原因

- boost=3.0: fixed15_recovered_at_least_8=false
- boost=3.0: full54_recall_at5_at_least_47=false
- boost=3.0: independent_largest_group_recovered_all=false
- boost=4.0: independent_largest_group_recovered_all=false
- boost=5.0: downstream_guardrails_pass=false
- boost=5.0: full54_recall_at5_at_least_47=false
- boost=5.0: independent_largest_group_recovered_all=false
- boost=5.0: old_top5_regressions_zero=false
- independent largest root-cause group is below 8 cases / 8 unique questions

`boost=5.0` 的旧 Top5 退化案例：`['a2209be1-7953-49e1-b639-aa1ddcc9c398']`。该点同时使 Recall@5 从候选最佳点 47 降至 46、Table Source Recall@5 降至 37、坐标覆盖降至 105、BINDING_READY 降至 34，因此不能把 `4.0` 单点挑出来晋级。

## 8. 最终裁决

**`NO_SINGLE_VARIABLE`**

拒绝当前 specificity candidate 的原因是多重且独立的：

1. 独立最大根因只有 `6/15`、6 个独立问题，未达到 `8/15 + 8 questions`。
2. `boost=3.0` 只恢复 `6/15`，Recall@5 只有 `45/54`。
3. `boost=4.0` 虽恢复 `8/15`，但只是窄单点，并不能修复独立根因规模不足。
4. `boost=5.0` 出现 1 个旧 Top5 退化，Recall@5 和下游护栏同时下降。
5. 当前 54 题没有独立 holdout，不能把单点闭集结果当成可泛化能力。

因此：

```text
product change authorized = false
document_specificity_boost = rejected for current mainline
```

## 9. Gold 与闭集边界

- Gold ID 和完整 rank 只用于离线判错与验证排名事实。
- 独立根因函数不读取任何候选 after-rank 或 recovered 字段。
- 敏感性评分统一应用于 Full54，不读取 case ID 或官方答案。
- 无直接 Gold 泄漏不代表没有闭集过拟合；本次正是因无独立 holdout 和参数区间不稳健而拒绝晋级。

## 10. 下一合理分支

当前不继续实现 specificity boost。B-03 可由 Evaluator 在以下分支中选择一个新包：

1. 扩展一个在参数选择前冻结的独立 document-retrieval holdout；
2. 转向剩余 `TABLE_SOURCE_MISS` 的诊断；
3. 执行 P-KW1 结构导航探针，验证结构索引是否能补充文档身份与来源导航。

本修复不直接选择或授权其中任何产品实现。
