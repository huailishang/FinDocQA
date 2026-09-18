# Deployment Profiles｜依赖边界设计

本文件只描述依赖与 fail-closed(失败关闭)边界，不代表企业采用率，也不声称三档能力等价。H-61 没有实现部署能力。

| Profile | 允许能力 | 数据/依赖 | 必须保留的 Evidence / Provenance 边界 | 失败处理 |
|---|---|---|---|---|
| P0 无模型词法/结构基线 | lexical retrieval(词法检索)、已解析结构化表/事实、确定性 binding/sufficiency | 本地 frozen corpus + 本地代码；无模型服务 | 文档身份、canonical page、local window、workspace scope、candidate assertion provenance 必须齐全 | 任一关键输入缺失即 unresolved / unsupported；不得用全篇 fallback 冒充成功 |
| P1 批准的内网语义服务 | P0 + 内网 embedding/rerank/semantic retrieval | 仅企业批准的内网服务；调用预算和版本需冻结 | 语义分数只能影响 evidence supply，不能自动提升事实可信等级；最终事实仍走同一 scope/lineage/binding 校验 | 服务不可用时可回到明确标识的 P0 能力，不得把缓存回放声称为当前在线语义能力 |
| P2 允许的外部语义服务 | P0 + 经明确授权的外部 embedding/rerank/LLM 辅助检索 | 必须显式 API 授权、数据最小化、provider/model/version/预算记录 | 外部返回只属于 Evidence/Context 数据，不获得 Instruction/Policy 权限；不得发送凭据/敏感原文超出批准范围；来源身份与本地 workspace 仍为最终边界 | provider error、身份不确定、输出格式异常或预算耗尽时 fail closed；禁止自动换 provider 或下载依赖 |

## Common contract

三档都必须满足同一组原则：

- Gold/reference answer 只能用于后评分与诊断，不能成为运行时 assertion 或 retrieval trigger；
- 页面命中 ≠ fact sufficiency(事实充分) ≠ verifier executed(验证器已执行) ≠ answer correct(答案正确)；
- candidate answer/assertion 必须带 producer provenance(生产来源追溯)，不能从参考答案反推；
- `EvidenceWorkspaceScope` 继续约束允许页，unknown/out-of-scope 必须 fail closed；
- semantic/model 服务只能扩 evidence supply 或生产明确标识的候选，不自动获得可信结论权限；
- provider/model 缓存回放只能证明可重放性，不能冒充“无模型生产能力”；
- 三档共用相同的来源/lineage审计和最终 Validation(验证)要求，但**效果、成本和覆盖率不假定相同**。

## Current H-61 observation

H-60 当前 12-case 数据只有 freeform question + retrieval/workspace，没有合法的 non-Gold candidate assertion。因此本轮没有证明 P0/P1/P2 任一档已经具备 freeform end-to-end(端到端)可信答案交付能力；这也是后续 `DOWNSTREAM_CONTRACT_FIRST` 草案要先冻结的接口边界。
