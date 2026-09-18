# 执行者启动说明

你是执行者。先读取 `handoffs/evaluator_executor/state/CURRENT.md`，本任务只按 `CONTRACT_A1.md` 与 `VALIDATION_PLAN_A1.yaml` 执行，原 CONTRACT.md / VALIDATION_PLAN.yaml 是修订前记录。

目标：一次完成全12题检索收益/成本漏斗、3+18条残余收口、证据消费契约审计，建议恰好一个下一任务。没有共同检索机制也可成功；不得为了凑第三题调参，也不得把静态VERIFIER_UNSUPPORTED当成验证器实测失败。

执行顺序：

1. 记录HEAD、初始差异、环境；用Conda agent运行check_packet.py --check authority，核对冻结输入。
2. 记录将采用的最多一个机制或null，写MECHANISM_FREEZE.json；正常进入EXECUTING / Executor。
3. 完成合同四个工作单元，产出全部规定文件。run_analysis.py生成四个机器产物，首次输出到本任务目录。
4. 准备报告和两次self-check；运行共享run_validation.py，参数为 --repo . --plan 本任务/VALIDATION_PLAN_A1.yaml --out 本任务/evidence --mode executor。计划VP-06执行第二次重放并比较。不要事先额外跑一次完整重放后又让VP-06重复消耗预算；修正重跑需记录原因。L3独立重放不计入执行者预算。
5. L2全部通过后运行共享validate_workflow.py --repo . --current handoffs/evaluator_executor/state/CURRENT.md。报告仅提交事实和建议；按技能流程由评估者接受快照、切换READY_FOR_REVIEW后运行L3。不得自签PASS。

环境：本机agent可用但没有PyYAML，共享runner/validator使用已有PyYAML的系统Python；其子命令的PATH必须优先指向agent环境。先记录系统Python路径，再设置子进程PATH；不要把共享工具本身改用agent启动，也不要安装依赖。记录实际解释器路径于本地运行证据，不写入通用配置。

明确禁止：API/模型/答案生成/Judge/Solver、下载/安装、新题集、参数搜索、产品修改、提交/推送。下一任务仅DRAFT_ONLY，未获本包自动执行授权。
