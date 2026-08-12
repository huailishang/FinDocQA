# E4 Freeform Scoring Policy

Policy version: `v1.0`

Decision date: `2026-08-11`

Owner: Evaluator

Applies to: FinDocQA E4 最终答案正确性评估，尤其是 FinanceBench / 本地真实金融长文档 QA 的 freeform 输出。

## 1. 决策

正式采用：

`ADOPT_LAYERED_SEMANTIC_REVIEW`

核心不是“找一个更复杂的 scorer 把所有答案都判成 0/1”，而是：

```text
候选答案
   ↓
L1 确定性高置信裁决
   ├─ 能严格证明正确 → AUTO_CORRECT
   ├─ 能严格证明错误 → AUTO_INCORRECT
   └─ 不能严格证明   → SEMANTIC_REVIEW_REQUIRED
                              ↓
L2 独立语义复核
   ├─ CORRECT
   ├─ INCORRECT
   └─ CANNOT_ASSESS
                              ↓
L3 人工 / Gold 校准与 meta-eval
```

`ABSTAIN / SEMANTIC_REVIEW_REQUIRED` 是合法输出，不算自动判错，也不能从分母中偷偷删除。

## 2. 为什么不继续修 deterministic scorer

项目自身证据已经给出停止线：

```text
FinanceBench real E4 = 8 cases / 3 docs
Evaluator semantic oracle = 2 correct / 6 incorrect
raw exact/value = 0 / 8
H-16/H-16R1 frozen oracle agreement = 8 / 8
H-16R1 frozen contradiction negatives = 6 / 6
H-16R1 benign controls = 4 / 4
Evaluator unseen semantic probes = 5 / 8
clear false accepts = 3
```

H-16R1 证明 lexical contradiction guard 可以修掉冻结表达，但换成语义等价的新表达后，仍出现：

```text
Gold = 65% higher
Pred = 65% higher, but that figure was wrong; 64% is correct
→ false accept

Gold = 8.70
Pred = 8.70 is incorrect; the correct value is 8.71
→ false accept
```

因此当前问题不是“还缺几个 cue”，而是 deterministic anchor/cue 方法本身不具备 unrestricted freeform semantic understanding。

结论：停止 `not / revised / wrong / actual / incorrect ...` 的词表式扩展。

## 3. 外部公开参考如何吸收

### 3.1 可吸收

公开评估框架与研究普遍支持以下结构：

1. 简单、确定性的 string/value 检查与 model-graded / semantic evaluation 分层；
2. judge 不应在所有样本上无条件强判，应允许 selective evaluation / escalation；
3. semantic judge 应使用明确 rubric，并对 judge 本身做 meta-eval；
4. 允许 abstention 后，应同时报告 coverage 与 agreement，而不是只报告单一 accuracy。

主要参考：

- OpenAI Graders: https://platform.openai.com/docs/api-reference/graders
- Trust or Escalate: https://arxiv.org/abs/2407.18370
- G-Eval: https://aclanthology.org/2023.emnlp-main.153/
- Ragas RAG evaluation: https://github.com/vibrantlabsai/ragas/blob/master/docs/howtos/applications/evaluate-and-improve-rag.md
- REFLECT meta-evaluation: https://arxiv.org/abs/2605.19196

### 3.2 不直接照搬

FinDocQA 不直接复制任何外部框架：

- 不把 text similarity 当金融答案 correctness；
- 不因为用了 LLM judge 就把它视为 Gold；
- 不把产品 Retriever 输出作为 final correctness judge 的权威依据；
- 不在当前 8-case 小样本上宣称拥有 selective-evaluation 论文中的统计保证；
- 未通过本项目 meta-eval 前，semantic judge 只能是候选评估器。

外部资料只证明“这个架构方向合理”，项目裁决仍由 FinDocQA 自身 evidence 决定。

## 4. L1：确定性裁决的权限边界

原则：**宁可少自动判，也不要错判后污染 E4 指标。**

### 4.1 `AUTO_CORRECT`

只有在答案正确性可以通过结构化、可重复规则直接证明时才允许自动判正确。

首版允许：

1. 规范化后 exact equality；
2. 对原子答案的 canonical value equality，例如纯数字 / 百分比 / 日期 / identifier，在解析后只有一个候选值且与 Gold 完全一致；
3. 明确结构化集合答案，在 canonicalization 后成员集合完全一致，且没有冲突成员。

首版**不允许**仅因为以下条件就 `AUTO_CORRECT`：

- Gold anchor recall = 1；
- content token recall 超阈值；
- fuzzy/text similarity 很高；
- 某些否定/修正 regex 没有命中；
- Retriever HIT；
- LLM 自报高 confidence。

尤其是描述型 freeform，即使所有 Gold anchor 都出现，也默认不能由 anchor scorer 单独判正确。

### 4.2 `AUTO_INCORRECT`

只有在错误可以通过高置信规则直接证明时允许自动判错。

首版允许：

1. Gold 是可回答的非拒答答案，而 prediction 明确拒答 / 无法确认 / 无法计算；
2. Gold 是原子数字 / 日期 / identifier，prediction 可无歧义解析为唯一候选值，且与 Gold 明确不一致；
3. Gold 是明确结构化集合，prediction 的 canonical set 存在确定缺失或冲突，且不存在自由文本语义歧义。

首版**不允许**仅因为以下条件就 `AUTO_INCORRECT`：

- descriptive answer 缺一个 anchor；
- token recall 低；
- 文本长度不同；
- 与 Gold wording 不同；
- Retriever MISS。

这些只能作为诊断信号；除非满足上面的高确定性条件，否则进入语义复核。

### 4.3 `SEMANTIC_REVIEW_REQUIRED`

以下默认进入 L2：

- 描述型、多句 freeform；
- 因果、比较、条件、趋势、解释、归因；
- 否定、转折、修正、历史值与当前值并存；
- 多个候选数字 / identifier；
- prediction 与 Gold 在 wording 上不同但可能语义等价；
- deterministic 信号互相冲突；
- Gold/reference 自身存在歧义或不完整风险；
- 任何无法通过结构化规则“证明”的正确/错误判断。

## 5. L2：独立语义复核合同

### 5.1 输入

semantic reviewer 可以读取：

```text
question
Gold/reference answer
authoritative Gold evidence / official evidence page（若存在）
predicted answer
answer-shape / task-type metadata（若为冻结 schema）
```

默认不读取：

```text
产品 Retriever rank / score
产品 Solver route
产品 Verifier verdict
Provider 自报 confidence
已有 deterministic final verdict
qid-specific allowlist
```

目的：避免把产品链路失败或 deterministic scorer 偏差泄漏进最终 semantic correctness 裁决。

### 5.2 输出

固定三值：

```text
CORRECT
INCORRECT
CANNOT_ASSESS
```

并必须输出：

```text
reason_code
short_rationale
required_gold_elements_covered
contradiction_found
uncertainty_reason（若 CANNOT_ASSESS）
```

不以自由文本“感觉差不多”作为最终记录。

### 5.3 Rubric

按以下顺序判断：

1. 回答是否覆盖 Gold 的核心结论；
2. 必需数字、单位、identifier、方向、比较关系是否一致；
3. 是否存在改变最终结论的矛盾、否定、修正或错误值；
4. 多部分问题是否覆盖所有必需部分；
5. 额外信息只有在与 Gold 冲突或改变结论时才判负面；
6. Gold/evidence 不足以唯一判断时必须 `CANNOT_ASSESS`。

## 6. L3：semantic judge 的 meta-eval

任何 LLM / 模型 judge 在晋级为自动 L2 前，都必须先证明“这把第二层尺子自己够准”。

最低要求：

1. 与被评模型的生成链路隔离；
2. rubric、输入字段、输出标签冻结；
3. 在人工/官方已标注 real outputs 上独立比较 agreement；
4. synthetic/adversarial probes 只能作为 trust tests，不能混入真实 E4 accuracy；
5. 单独统计 false accept / false reject / abstain；
6. 当前 8 real outputs 只能作为第一块校准证据，不能据此宣称泛化可信；
7. 至少还需要一个独立 real-output slice 后，才能讨论把 model judge 升级成 authoritative automatic L2。

在此之前，L2 的权威实现仍是 Evaluator / calibrated human review；model judge 只能 shadow。

## 7. 指标定义

E4 后续不再只输出一个 accuracy。

至少同时报告：

```text
scorable_total
auto_decided
auto_coverage = auto_decided / scorable_total
auto_correct_decisions
auto_incorrect_decisions
auto_agreement（仅 calibration / labeled set）
semantic_review_required
semantic_review_coverage
cannot_assess
unresolved_rate
final_correct
final_incorrect
final_semantic_accuracy（仅当所有 scorable cases 已得到最终 verdict）
```

如果仍有 `CANNOT_ASSESS`：

- 不把它算错；
- 不从分母中删除；
- 不强行发布单一点 `final_semantic_accuracy`；
- 报告 resolved accuracy + unresolved rate，必要时同时给 correctness lower/upper bound。

## 8. 与产品失败层严格分离

最终 E4 correctness 与以下指标分开：

```text
Retrieval HIT / MISS
Generation correctness
Delivery accepted / blocked
Scoring route
Provider calls / tokens / cost
```

当前 8-case 已经证明：

```text
Retrieval MISS 6/6 → semantic wrong
Retrieval HIT 2/2 → semantic correct generation
```

这可以用于定位 B-03，但不能把 `Retrieval HIT` 直接当成答案正确。

同样，`01858` 是 semantic correct 但 delivery blocked，因此 generation correctness 与 delivery correctness 必须继续分开。

## 9. 当前 8-case 按新 policy 的解释

基于现有人工/Evaluator 已冻结事实，不重新调用 Provider：

```text
6 个明确 refusal / cannot-confirm wrong answers
→ 可进入 AUTO_INCORRECT 候选类

2 个描述型 semantic-correct answers
→ 不允许 anchor scorer 自动判正确
→ SEMANTIC_REVIEW_REQUIRED
→ 已有 Evaluator oracle 最终判 CORRECT
```

因此当前 8-case 可以作为首个 policy replay 的预期：

```text
预计 deterministic auto coverage = 6/8
预计 auto decisions = 6 个 INCORRECT
预计 semantic-review coverage = 2/8
最终 semantic result = 2 correct / 6 incorrect
```

注意：`6/8 auto coverage` 只是对当前固定 slice 的预期回放，不代表未来 FinanceBench 自动覆盖率。

## 10. 当前项目裁决

```text
Option A：Deterministic binary authority
→ REJECT
原因：H-16R1 unseen probes 已出现 clear false accept。

Option B：Deterministic triage + abstain
→ ACCEPT AS L1
原因：可以保护高确定性裁决，不强迫不确定 case 二元化。

Option C：Layered semantic evaluation
→ ADOPT AS FULL E4 POLICY
原因：B 只能分流，不能完成 freeform 最终 correctness；L2/L3 才能闭合 B-06。
```

最终选择：

`ADOPT_LAYERED_SEMANTIC_REVIEW`

## 11. 下一步

下一任务不是再修 scorer，而是做一次**零 Provider 的 scoring-policy shadow replay**：

```text
冻结 v1 policy
→ 对现有 8 real outputs 回放 L1 route
→ 验证是否得到 6 AUTO_INCORRECT + 2 REVIEW_REQUIRED
→ 对 parent/adversarial probes 检查“复杂语义不得被错误 AUTO_CORRECT”
→ 固化 route / reason / metrics schema
```

只有 L1 路由和指标账本稳定后，才设计/校准 L2 model judge。任何新的 paid E4 仍需要单独 Human 授权。
