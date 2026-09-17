# Evaluator Review｜H-60 Cloudflare Bounded Workspace Capability

Task ID: `FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1`

Workflow: `evaluator-executor-workflow/v2.2`

Task kind: `capability_experiment`

Reviewed baseline HEAD: `b057674`

## 评估前检查

FinDocQA 的主链仍是：文档/页面供给 → Retrieval lanes → bounded Evidence Workspace → deterministic verification/计算 → freeform answer evaluation。H-55/H-56/H-56R1 已关闭 workspace scope 与真实 product-route reachability；H-58 已提供 fresh two-sided cohort；H-59 只被外部额度阻断。H-60 位于 B-03 第一瓶颈上，专门验证同一 lexical + Cloudflare semantic lanes 下，`early fixed Top5 contraction` 改为 `bounded union workspace before verification` 是否足以达到预声明 evidence-reach qualification。

评估者先以无语义变化方式补齐 v2.2 CONTRACT 缺失的 schema 字段/Exclusions，并运行共享 `validate_workflow.py`，结果为 `OK`。随后接受 unchanged experiment snapshot，路由 `READY_FOR_REVIEW / Evaluator`，独立运行冻结 Validation Plan；未重复任何 Cloudflare live call。

## L3 Independent Gate

- Gate result: PASS
- Gate summary: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/L3-GATE.json
- Validation plan: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/VALIDATION_PLAN.yaml

L3 = `9/9 PASS`，mandatory failures = `0`。输入权威、lane identity、Gold 后评分、API 预算、产品代码保护、报告边界与 evaluator routing 均独立复现。

## Acceptance matrix

| AC | Evaluator decision | 结论 |
|---|---|---|
| AC-01 | 通过 | H-58 parent L3/REVIEW 与 fresh12 `4 hit / 8 miss / 7 miss families` 独立复现。 |
| AC-02 | 通过 | 冻结 cohort/source authority 未漂移；Phase 0 为 1019 pages + 12 queries，15/15 canonical page hashes 匹配，Gold 未进入 runtime input。 |
| AC-03 | 通过 | Cloudflare `@cf/qwen/qwen3-embedding-0.6b`、1024d、batch16、12 query vectors 均符合合同；H-59 8B cache 未复用。 |
| AC-04 | 通过 | baseline/candidate 使用相同 lexical/semantic lane；baseline 为 equal-weight RRF60→Top5，candidate 为去重 union workspace<=10。 |
| AC-05 | 通过 | qualification 算术严格按冻结规则计算；runtime outputs 在 Gold scoring 前冻结。结果 `qualified=false` 是有效测量结果，不是验收失败。 |
| AC-06 | 通过 | 12/12 为 `VERIFIER_UNSUPPORTED`，未错误计作 workspace failure；candidate trusted loss=0。 |
| AC-07 | 通过 | 81 successful / 81 physical attempts，1 preflight + 80 runtime；无 paid fallback、生成/Judge/Solver/reranker 调用。 |
| AC-08 | 通过 | H-56R1 accepted hashes 保持；H-60 未修改 `src/**`、`config/**`、`tests/**`。 |
| AC-09 | 通过 | REPORT 区分 task execution 与 project impact，并明确禁止把本实验解释为 0.6B-vs-8B 模型优劣。 |

## Capability result

冻结 primary measurement 为：

```text
baseline RRF60 Top5 gold reach = 7/12
candidate bounded workspace gold reach = 9/12
recovered_cases = 2
recovered_document_families = 2
protected_loss = 0
workspace_max_unique_pages = 10
qualification threshold = recovered>=3 + families>=2 + protected_loss=0 + workspace<=10
```

因此：

```text
capability_decision = NOT_QUALIFIED
```

两个真实 recovery 是：

```text
financebench_id_02024 / VERIZON_2021_10K
financebench_id_03856 / ADOBE_2017_10K
```

这证明 late contraction / bounded workspace 能消除一部分 fixed-Top5 lane competition；但 `2 < 3`，不能按事前规则晋级，也不能在 H-60 内通过改 TopK、RRF 权重、阈值、provider/model 或 cohort 把结果“救”到 3。

## Residual bottleneck interpretation

candidate 仍有 3 个 Gold miss，而且 Gold 不在 lexical Top5 ∪ semantic Top5 中：

```text
financebench_id_00521 / ULTABEAUTY_2023_10K / Gold 57
financebench_id_04735 / ADOBE_2015_10K / Gold 59,63
financebench_id_03882 / AMCOR_2020_10K / Gold 50
```

其中 ULTA 的 semantic lane 已到 56/59，AMCOR 已到 48/51，Gold 只差近邻页；ADOBE 2015 则是更深的 page-localization miss。由此 H-60 将 B-03 的第一未知点从“是否过早收缩”推进到“bounded workspace 之后仍缺 evidence supply / selective exploration”。这不是 pure neighbor expansion 的重新开放：H-53 已否决无差别固定半径扩页；下一步必须先证明一个 Gold-free、选择性、可证伪的 exploration/supply mechanism，而不是继续扩大 workspace。

Evaluator 额外做了零 API cross-cohort sanity check：把“只填补已检索页之间的小间隙”应用到 H-50 的 18 个 `BOTH_MISS`，`max_gap=2/3/4` 均为 `0/18` recovery。故 H-60 的两个近邻信号不足以把 gap-fill/neighbor 重新升级为下一能力方向；下一步必须先做 residual evidence-supply direction reassessment。

## Project impact verdict

Impact verdict: IMPROVED

`IMPROVED` 仅表示 verification-boundary evidence reach 从 `7/12` 提升到 `9/12` 且 protected loss=0。它不等于 capability qualification、产品就绪或允许继续同一 contraction-timing 参数路线。

项目主瓶颈 B-03 仍未关闭，但定位更窄：workspace scope/reachability 已闭环，late contraction 有正向但不足；当前剩余主要问题是 workspace 前后的 evidence supply / selective exploration。B-06 继续保持 secondary，B-05 仍 WATCH，B-07 仍 secondary。

Continuation: `SWITCH`。

下一步应使用已有 H-60 persisted embeddings/traces 做零 API residual evidence-supply diagnostic；先区分 near-boundary supply 与 deep localization miss，禁止直接调 TopK/固定邻页半径或新增模型调用。

## RV-EV-01

- AC: AC-02, AC-07
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-01.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-01.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-01.stderr.log

## RV-EV-02

- AC: AC-01
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-02.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-02.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-02.stderr.log

## RV-EV-03

- AC: AC-02, AC-03
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-03.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-03.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-03.stderr.log

## RV-EV-04

- AC: AC-03, AC-04
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-04.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-04.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-04.stderr.log

## RV-EV-05

- AC: AC-05
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-05.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-05.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-05.stderr.log

## RV-EV-06

- AC: AC-06
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-06.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-06.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-06.stderr.log

## RV-EV-07

- AC: AC-07
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-07.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-07.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-07.stderr.log

## RV-EV-08

- AC: AC-08, AC-09
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-08.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-08.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-08.stderr.log

## RV-EV-09

- AC: AC-07, AC-09
- Meta: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-09.meta.json
- Stdout: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-09.stdout.log
- Stderr: handoffs/evaluator_executor/FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1/evidence/l3/RV-EV-09.stderr.log

## Final verdict

PASS
