# E4 L2 Semantic Judge Calibration Contract

Contract version: `v1.0`

Status: `DESIGN_ONLY_ZERO_PROVIDER`

Applies to: FinDocQA E4 freeform semantic review 的未来 L2 judge 校准与 shadow meta-eval。

## 1. 目的与边界

本合同解决一个问题：**在任何 model/LLM judge 获得 L2 自动语义裁决权限之前，先把它允许看什么、必须输出什么、如何被校准、什么证据可以用于晋级写死。**

当前任务只冻结设计，不实现、不选择、不调用任何 Provider/model，不产生 paid E4，也不改变产品 AnswerAB、Retriever、Solver、Verifier 或 L1 scoring router。

当前冻结的 8 个 FinanceBench real outputs 只能作为第一块、开发可见的校准证据。它们对第一轮 sanity/calibration 是必要（necessary）的，但对泛化可信不是充分（sufficient）的。

## 2. L2 reviewer 输入权限

### 2.1 Allowed inputs

L2 semantic reviewer 只允许读取与答案语义正确性直接相关的冻结信息：

```text
question
Gold/reference answer
authoritative Gold evidence / official evidence page（when available）
predicted answer
frozen answer-shape / task-type metadata（when available）
```

这些输入用于回答：prediction 是否在语义上满足 Gold/reference 与权威 evidence 所要求的核心结论。

### 2.2 Forbidden as judge authority

以下信息可以在其他诊断账本中存在，但**不得作为 L2 correctness judge 的裁决 authority**：

```text
Retriever rank / score
Solver route
Verifier verdict
Provider self-confidence
existing deterministic final verdict
qid-specific / case-specific allowlist
```

原因：这些信号来自被评产品链路或已有 scorer，本身可能正是错误来源。将它们注入 L2 会造成循环论证或 label leakage。

特别规定：

- Retriever HIT 不是答案正确的证明；Retriever MISS 也不是答案错误的最终证明。
- Solver/Verifier 的内部判断不能替代 Gold/reference + authoritative evidence 的语义比较。
- Provider self-confidence 不具有 correctness authority。
- deterministic L1 final verdict 不作为 L2 输入 authority；L2 必须能独立复核进入该层的样本。

## 3. L2 输出合同

L2 semantic decision **只能**取三值之一：

```text
CORRECT
INCORRECT
CANNOT_ASSESS
```

每条结果必须至少包含：

```text
reason_code
short_rationale
required_gold_elements_covered
contradiction_found
uncertainty_reason
```

字段语义：

| Field | 含义 |
|---|---|
| `reason_code` | 结构化裁决原因类别；用于聚合与审计，不使用 benchmark/qid 特例 |
| `short_rationale` | 简短说明 prediction 与 Gold/evidence 的关键一致、缺失或冲突 |
| `required_gold_elements_covered` | Gold 核心结论/必要组成是否被覆盖；多部分问题应能表示缺失 |
| `contradiction_found` | 是否发现会改变最终结论的矛盾、反向关系、错误值或否定 |
| `uncertainty_reason` | 仅当无法可靠裁决时记录不足来源；`CANNOT_ASSESS` 不得伪装成二元 verdict |

**Rationale is not Gold.** 模型生成的 rationale 只是审计记录，不等于 Gold truth，也不能因为 rationale 看起来合理就提升为权威标签。

## 4. 语义裁决顺序

未来 L2 实现应按冻结 policy 的顺序判断：

1. prediction 是否覆盖 Gold/reference 的核心结论；
2. 必需数字、单位、identifier、方向、比较关系是否一致；
3. 是否存在改变结论的矛盾、否定、修正或错误值；
4. 多部分问题是否覆盖所有必要部分；
5. 额外信息仅在与 Gold/evidence 冲突或改变结论时构成负面；
6. Gold/reference/evidence 不足以唯一判断时必须输出 `CANNOT_ASSESS`。

这一层不是文本相似度判分，也不允许因为 wording 接近就强判 `CORRECT`。

## 5. 两个严格分离的校准层

任何 future judge meta-eval 必须把数据物理或逻辑地拆成两个 strata，并分别产出独立结果文件/指标段。

### 5.1 `REAL_CALIBRATION`

定义：真实产品/模型生成的 real model outputs，且已有 human / Evaluator / official label 可作为参考裁决。

用途：

- 衡量 judge 对真实 E4 输出的 agreement；
- 统计 false accept、false reject、abstain；
- 评估是否存在对真实金融问答表达的系统性误判。

当前冻结的 **8** 个 FinanceBench real outputs 可以作为第一个 `REAL_CALIBRATION` slice，但它们开发可见、规模很小，只能形成第一轮 sanity evidence，不能宣称 generalization。

在任何 authority promotion 之前，**至少还必须存在一个 independent real-output slice**，并由 Evaluator 独立复核。这里不预设该 slice 的固定题数，因为当前项目证据不足以支持任意 quota。

### 5.2 `TRUST_TEST`

定义：synthetic、adversarial、counterexample、paraphrase、contradiction 等专门构造的 trust probes。

用途：

- 暴露 judge 对否定、修正、错误值、同义表达、额外上下文、拒答等 failure mode；
- 验证安全边界与失效模式；
- 作为 counterexample regression。

`TRUST_TEST` **不得混入** `REAL_CALIBRATION` 的真实 E4 accuracy、agreement 或任何 real denominator。synthetic/adversarial 样本通过得再多，也不能抬高真实 E4 accuracy；失败则作为 trust-risk 单独报告。

## 6. Meta-eval 指标

### 6.1 REAL_CALIBRATION metrics

至少记录：

```text
real_total
judge_decided
judge_abstain
judge_coverage
agreement
false_accept
false_reject
cannot_assess
unresolved_rate
```

定义：

```text
real_total
= 有参考标签、进入本次 REAL_CALIBRATION 的真实输出总数

judge_decided
= judge 输出 CORRECT 或 INCORRECT 的数量

judge_abstain
= judge 输出 CANNOT_ASSESS 的数量

judge_coverage
= judge_decided / real_total

agreement
= judge 的 CORRECT/INCORRECT 与已冻结参考标签一致的数量 / real_total
  （同时必须保留 abstain 计数，禁止只在 decided 子集上报一个漂亮 agreement）

false_accept
= judge 把 known-wrong answer 错误标成 CORRECT 的数量

false_reject
= judge 把 known-correct answer 错误标成 INCORRECT 的数量

cannot_assess
= 输出 CANNOT_ASSESS 的数量

unresolved_rate
= cannot_assess / real_total
```

`false_accept` 优先级高：它意味着错误答案可能被自动评估系统放行。`false_reject` 同样必须单独记录，避免正确答案被系统性误判。

### 6.2 TRUST_TEST metrics

TRUST_TEST 单独报告，例如：

```text
trust_total
trust_pass
trust_fail
trust_abstain
failure_family
```

不得把这些数值加进 `real_total`、`agreement`、real E4 accuracy 或其它真实分母。

## 7. Promotion boundary

本合同完成后，任何 model judge **仍然是 `shadow-only`**。

当前 8 real outputs 只是第一道 sanity/calibration gate。它们即便 8/8 agreement，也不能单独证明 judge 有权在后续 E4 中自动做 authoritative L2 verdict。

未来 authority promotion 至少需要：

1. frozen judge input/output schema 与 rubric；
2. REAL_CALIBRATION 与 TRUST_TEST 分层结果；
3. 当前 8 之外的一个 `independent real-output slice`；
4. false_accept / false_reject / abstain / coverage 等完整 meta-eval 证据；
5. Evaluator 单独发布后续 promotion contract 并明确批准 authority。

本任务**不设数值 threshold**。未来 sample-count、agreement threshold、false-accept tolerance 等阈值只能基于新的项目证据和风险要求冻结，不能现在为了凑一个“通过线”任意发明。

如果后续 independent slice 暴露明显 false accept、数据泄漏、judge 与被评链路耦合、或者只能靠高 abstain 才维持表面 agreement，则不得晋级 authority；应继续 `shadow-only` 或停止该 judge 路线。

## 8. Future execution boundary

### This task｜Design only

```text
design contract only
Provider / LLM / network = 0
不选模型
不实现 judge runtime
不运行 shadow judge
```

### Future task A｜Reusable judge interface / harness

只有设计被接受且确有必要时，才可实现 provider-agnostic 的 L2 judge interface、schema validator、result ledger 与 REAL_CALIBRATION / TRUST_TEST 分层 harness。

要求：judge 输出不得直接改写产品 answer generation、Retriever、Solver 或 Verifier 行为。

### Future task B｜Bounded shadow judge run

只有获得独立 authorization 后，才能选择具体 Provider/model 做 bounded shadow run。该任务必须冻结：

```text
model/provider
real slice
trust-test slice
call/token/cost budget
prompt/rubric version
resume/checkpoint semantics
```

shadow 结果只进入 evaluator evidence，不成为在线产品 verdict。

### Future task C｜Authority-promotion review

只有 accumulated meta-eval evidence 包括 independent real-output slice 后，Evaluator 才能创建 authority-promotion review。该 review 决定：

```text
保持 shadow-only
扩大校准
拒绝该 judge
或在明确受限范围内授予 authoritative L2 权限
```

没有这个独立 promotion review，任何 shadow judge 都不得自动成为 E4 Gold judge。

## 9. 与产品链路解耦

L2 judge 是**评估平面**，不是产品推理平面：

```text
产品：question → retrieval → solving → verification → predicted answer
评估：question + Gold/evidence + predicted answer → L2 semantic review
```

禁止形成：

```text
judge verdict
→ 回写 Retriever rank
→ 改 Solver route
→ 改 Verifier verdict
→ 改当前被评答案
```

需要利用 judge failure 做产品实验时，应先把失败归因形成新的 bottleneck/hypothesis，再以独立任务修改产品，不能在同一 calibration run 内闭环调参。

## 10. 当前裁决

```text
L1 deterministic triage
→ 已有冻结边界，继续保留

L2 model judge
→ NOT_CALIBRATED
→ SHADOW_ONLY

current real calibration evidence
→ 8 development-visible FinanceBench outputs
→ necessary first sanity evidence
→ not sufficient for generalization

independent real-output slice
→ REQUIRED BEFORE AUTHORITY PROMOTION

Provider/model selection
→ NOT PART OF THIS CONTRACT
```

该合同的价值不是“证明某个模型已经会判分”，而是防止下一步一调用 LLM judge 就默认把它当 Gold。后续所有 L2 judge 实验必须先证明这把尺子本身可靠，再讨论自动判分权限。
