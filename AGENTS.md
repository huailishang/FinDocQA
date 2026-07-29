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
- Keep parser fallbacks narrow and page-scoped.
- Historical competition conclusions belong in docs/history/, not in active code paths.

## Local test environment

- Run project tests with the Windows Conda `agent` environment by default.
- Python: `<LOCAL_SOFTWARE>\Anaconda\workspace\.conda\envs\agent\python.exe`
- `pytest` is installed in this environment (verified version: 9.0.3 on 2026-07-27).
- The standalone Windows Python at `<LOCAL_SOFTWARE>\Python\install\python.exe` does not currently have `pytest`; do not use it for the project test suite unless its environment is explicitly updated.
- Preferred command: `<LOCAL_SOFTWARE>\Anaconda\workspace\.conda\envs\agent\python.exe -m pytest -q`
