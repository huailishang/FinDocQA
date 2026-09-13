# Embedding 主备 Provider 策略

## 1. 目的

FinDocQA 不再只依赖单一 Embedding Provider（嵌入服务商）。长期采用“一个主 Provider + 一个备用 Provider”的策略，降低免费额度耗尽、限流和网络不可达带来的研发中断。

当前只保留两条路线：

```text
PRIMARY（主）
Hugging Face Inference Providers
→ Scaleway
→ Qwen/Qwen3-Embedding-8B

BACKUP（备）
Cloudflare Workers AI
→ @cf/qwen/qwen3-embedding-0.6b
```

Voyage AI 当前不注册、不配置、不作为第三备用。

## 2. 当前已验证状态

| 角色 | Provider | 模型 | 维度 | 状态 |
| --- | --- | --- | ---: | --- |
| 主 | Hugging Face → Scaleway | `Qwen/Qwen3-Embedding-8B` | 4096 | 已通过真实 embedding 探针 |
| 备 | Cloudflare Workers AI | `@cf/qwen/qwen3-embedding-0.6b` | 1024 | 已通过真实 embedding 探针 |

主路线在 Windows 网络栈下已验证可用；当前 WSL 到 Hugging Face Router 的网络不稳定，因此涉及 HF routed inference（HF 路由推理）的外部调用优先走 Windows-side runner（Windows 侧运行器）。

Cloudflare 备用路线也已在 Windows 侧真实调用通过。

## 3. 最重要的约束：不同模型不能混用同一个向量索引

虽然两个模型都属于 Qwen3 Embedding 系列，但它们不是同一个 Embedding Profile（嵌入配置档）：

```text
Qwen3-Embedding-8B   = 4096 dimensions
Qwen3-Embedding-0.6B = 1024 dimensions
```

因此：

- 不能把 8B 与 0.6B 的向量写入同一个 index（索引）。
- 不能在一批已由 8B 建库的数据中间，透明切到 0.6B 继续写。
- 切换模型时，必须同时切换对应的 embedding cache（嵌入缓存）与 vector index（向量索引）。

建议长期使用固定 Profile ID：

```text
qwen3-8b-hf-scaleway-v1
qwen3-0.6b-cloudflare-v1
```

以后任何向量缓存、索引、离线产物都应带 `embedding_profile_id`，至少绑定：

```text
model
+ model revision
+ dimension
+ query instruction
+ normalization
+ tokenizer / preprocessing
```

## 4. 主备切换规则

日常开发环境采用“主优先、故障再切备”，而不是轮流调用。

```text
请求
→ 优先 PRIMARY
→ PRIMARY 正常：继续使用 PRIMARY
→ PRIMARY 额度耗尽 / 429 / 5xx / timeout / network unavailable
→ 明确切换到 BACKUP Profile
→ 使用 BACKUP 自己的 cache / index
```

以下情况允许触发备用判断：

```text
quota exhausted / payment-required style quota failure
429 rate limit
5xx provider failure
timeout
network unavailable
```

以下情况不允许用切换 Provider 来掩盖错误：

```text
400 invalid request
401 invalid credential
403 permission error
schema error
model configuration error
```

这类问题应直接失败并修配置。

## 5. Benchmark / 冻结实验与日常研发必须区分

对于冻结 Benchmark（基准实验）或 capability experiment（能力实验），如果模型本身是实验变量之一，则不得自动切模型。

例如 H-59 已冻结：

```text
semantic model = Qwen/Qwen3-Embedding-8B
```

因此 H-59 内：

```text
HF / Scaleway 不可用或额度不足
→ STOP
→ 返回 Evaluator
→ 不自动改用 Cloudflare 0.6B
```

否则实验就从“只比较 early Top5 contraction vs bounded workspace”变成同时更换 Embedding 模型，结论失效。

Cloudflare 目前定位为后续日常开发、非冻结实验或显式批准的新实验的备用能力。

## 6. 凭证保存规则

凭证只允许保存在本地已忽略文件：

```text
.env.retrieval.local
```

只记录变量名，不在项目文档、日志、REPORT、Git 历史中保存真实值：

```text
HF_TOKEN
CF_ACCOUNT_ID
CF_AI_AUTH
```

探针输出不得持久化 Token、Account ID 或凭证明文。

## 7. Cloudflare 免费备用能力

当前已验证模型：

```text
provider = cloudflare-workers-ai
model = @cf/qwen/qwen3-embedding-0.6b
dimension = 1024
context window = 8192 tokens
```

Cloudflare Workers AI 免费层当前按每日 Neurons（计算额度）重置，适合作为日常研发备用；具体免费额度与模型计费可能变化，正式批量执行前仍应做当期额度预检。

## 8. 后续工程化方向

H-59 完成前不修改 `config/**`，避免破坏冻结实验的 AC-08。

H-59 结束后，再将本策略工程化为统一 `EmbeddingProviderRouter（嵌入服务路由器）`，建议至少具备：

```text
provider health
quota / rate-limit state
embedding_profile_id
circuit breaker（熔断）
retry policy（重试规则）
profile-isolated cache / index
provider attempt ledger（调用台账）
```

未来若这套能力在 FinDocQA 稳定，再考虑抽取到 `agent-runtime-platform` 作为公共 Provider Routing（服务商路由）能力。

## 9. 当前结论

```text
主：HF / Scaleway / Qwen3-Embedding-8B
备：Cloudflare Workers AI / Qwen3-Embedding-0.6B
第三备用：无

日常研发：主不可用时可显式切备用 Profile
冻结实验：禁止静默换模型
不同 Profile：缓存和索引严格隔离
```
