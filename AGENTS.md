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
