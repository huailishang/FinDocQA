# Config

长期配置包括：
- config.yaml：主运行配置；
- insurance_product_documents.json：保险文档映射；
- answer_slot_contracts.example.json：历史赛题输出槽合同兼容配置；
- question_strategy_matrix.json：复合题型与策略路由；
- prompt_registry.json：可审计 Prompt Registry。

配置文件不得包含 API Key。运行密钥只通过环境变量注入。
