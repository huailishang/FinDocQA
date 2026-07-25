# Tests

测试集只验证可以长期复用的能力，不保留比赛逐包 Gate 测试。

当前重点覆盖：
- answer parsing、validation、arbitration；
- freeform、多槽和正式输出合同；
- calculation 与 cross-document；
- MinerU、语料质量、parser fallback；
- document scope、retrieval、evidence sufficiency；
- question strategy、Prompt Registry；
- Claim atomization、semantic binding、provenance；
- failure taxonomy、bounded recovery、runtime integrity；
- token accounting 与安全 runner。

测试默认离线运行，不应依赖历史 evaluation artifacts 或真实 Provider。
