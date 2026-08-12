# FinDocQA — Agent Rules

## Repository goal

Keep FinDocQA reusable for financial long-document QA. Prefer generic capabilities over dataset-, qid-, provider-, or leaderboard-specific fixes.

## Safety and reproducibility

- Never commit .env, API keys, raw provider responses, runtime checkpoints or generated evaluation artifacts.
- Real API calls and paid model runs require explicit user authorization.
- No qid/answer hardcoding in src/, config/ or reusable scripts.
- Preserve document/page/source lineage for evidence used in an answer.
- Retrieval and recovery logic should fail closed when evidence, lineage or binding is ambiguous.

## Engineering rules

- Put reusable logic in src/; keep scripts thin.
- Add focused offline tests for reusable modules.
- Prefer deterministic preprocessing and verification before adding LLM calls.
- When exploring a new direction and there is no clear implementation path yet, first search GitHub and other public sources for relevant projects, papers, documentation, or established practices. Critically separate reusable ideas from context-specific assumptions, absorb only the parts supported by evidence and project constraints, then adapt or correct them for FinDocQA instead of copying them directly.
- Keep parser fallbacks narrow and page-scoped.
- Historical competition conclusions belong in docs/history/, not in active code paths.

## Local test environment

- Run project tests with the Windows Conda `agent` environment by default.
- Python: `<LOCAL_SOFTWARE>\Anaconda\workspace\.conda\envs\agent\python.exe`
- `pytest` is installed in this environment (verified version: 9.0.3 on 2026-07-27).
- The standalone Windows Python at `<LOCAL_SOFTWARE>\Python\install\python.exe` does not currently have `pytest`; do not use it for the project test suite unless its environment is explicitly updated.
- Preferred command: `<LOCAL_SOFTWARE>\Anaconda\workspace\.conda\envs\agent\python.exe -m pytest -q`

<!-- BEGIN localagent-common:codexpro-shell-safety -->
## CodexPro Shell 与文件落盘安全

- Markdown、YAML、JSON 等文本文件优先使用文件 `write/edit` 工具，不使用 `echo`、`printf`、`cat` 或 Here-doc 拼接完整正文。
- 命令中包含反引号、`$()`、复杂正则、多层引号或多行脚本时，先把脚本写入文件，再通过 Bash 执行该脚本。
- Bash 只负责执行命令和检查结果，不承担富文本模板渲染。
- 批量生成或修改文件后，提交前必须检查关键标题、关键标识、文件数量和 `git diff`，防止内容被 Shell 展开或转义破坏。
- Windows 路径调用优先使用项目已验证的命令形式；不要在同一条命令中混合 PowerShell、cmd、WSL Bash 多层转义。
<!-- END localagent-common:codexpro-shell-safety -->
