# H-61 A2执行者修订入口

先读CURRENT.md、CONTRACT_A2.md及Amendment 02。评估者确认原报告指出的矛盾成立：any-Gold为7→9，all-Gold为7→7；原A1错误不归责执行者，原REPORT/L2/机器输出均保留。

只补三件文档/决策产物：

1. REPORT_A2.md：引用原报告与产物，说明AC-02已修订，明确两种口径、两条部分覆盖及字符/页预算；引用新的L2证据，不自签最终PASS。
2. DECISION_A2.json：保留原决定与未测边界，future_thresholds_status=TO_BE_FROZEN_PER_EXPERIMENT；fresh_failure_condition不再对所有未来实验一律要求恢复3题。
3. NEXT_TASK_DRAFT_A2.md：仍一个DRAFT_ONLY任务。说明候选答案输入未就绪，标记NOT_READY_FOR_REAL_EXECUTION；先做契约设计，真实执行覆盖测量以候选来源核验为前置。模拟接口正例与真实题能力严格分开。

用原双解释器启动方法运行VALIDATION_PLAN_A2.yaml，输出到evidence/a2-l2，禁止覆盖evidence/L2-GATE或原EV。VP-06只复用已冻结的原重放及产物hash；不要额外运行run_analysis.py。本次不需要补模型调用、重跑检索或重新生成四个机器输出。

通过后共享workflow validator复核，再提交评估者。当前EXECUTING / Executor保持，未宣称任务最终通过。启动说明A1及其原件仅供审计。
