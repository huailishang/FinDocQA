# Architecture

## 总体结构

金融长文档问答采用分层、可审计的流水线：

输入题目 → Question Strategy → Document Scope → Retrieval → Evidence Assembly → Minimal Evidence → Solver → Claim Verification → Failure Arbitration → Output Contract。

## 数据层

PDF 先通过 MinerU 等解析器转换为带页码和结构信息的语料。解析结果应保留文档 ID、页面、章节、表格和原始来源。页面质量异常时可以触发选择性 fallback，但 fallback 必须限定在同一文档和同一页面附近。

## 检索层

DocumentScopeResolver 负责文档级候选范围；Retriever 只在有效范围内做页内或块级召回。Evidence Assembly 负责去重、补上下文和来源归一化。Minimal Sufficient Evidence 再做覆盖约束下的压缩。

## 推理层

Question Strategy 使用基础题型加复合标签描述问题，例如 calculation、cross_document、comparison、ranking、negation 和 temporal_scope。Solver 根据题型执行选择题判断、抽取或确定性计算。

## 验证层

Claim atomizer 将复杂陈述拆成局部原子事实；Verifier 绑定主体、指标、时间、值、单位、条件和例外，并输出 SUPPORT、REFUTE 或 UNRESOLVED。答案不能仅依赖标签、裸数字或跨文档拼接证据。

## 恢复层

失败信号先进入 Failure Taxonomy 和 Composite Failure Arbitration。只有确定的缺证才建议 corrective retrieval；血缘、Binding 和计算问题分别走重绑、重验和重算。Provider、预算、运行完整性和未知失败默认停止或升级，不自动重试。

## 输出层

输出必须经过答案格式、槽位、reasoning、Token 和 lineage 合同校验。运行时产生的 checkpoint、Provider 原始响应和 evaluation artifacts 不纳入版本控制。

## 扩展方向

后续可以在不破坏上述合同的前提下增加 Embedding、Rerank、图检索、动态记忆和 Agent 编排。
