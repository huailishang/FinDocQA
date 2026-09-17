# Amendment 01｜No-Dependency Runtime Text Gate

Task ID: `FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1`

Date: `2026-09-17`

Owner: Evaluator

## Why this amendment is required

H-60 has not made any Cloudflare call. Executor stopped before the external-call boundary after discovering that the inherited H-59 runtime page-text artifact is not authority-compatible with the frozen H-58 cohort.

Independent evaluator recheck confirms the mismatch is systematic rather than a single corrupt row:

```text
H-58 canonical evidence page references checked = 15
H-59 matching canonical page-text hashes          = 0
H-59 mismatching canonical page-text hashes       = 15
```

H-58 froze page-text authority using:

```text
FinanceBench revision = cc39aeb4afdf33909ee1412188bf89035950c2eb
extractor             = PyMuPDF
extractor_version     = 1.28.0
extraction            = page.get_text("text", sort=True)
newline normalization = CRLF/CR -> LF; add final LF when non-empty
```

Therefore copying `H59_PAGE_TEXTS_FOR_TOKEN_PREFLIGHT.jsonl` into H-60 would introduce a second principal variable: page-text extraction drift. Executor correctly failed closed.

The follow-up attempt to obtain a task-local PyMuPDF installation was outside the intended experiment boundary. Environment mutation is not part of H-60 and must not be performed automatically.

## Amendment

H-60 keeps the same cohort, provider/model, retrieval lanes, TopK, RRF60 baseline, bounded-workspace candidate, qualification thresholds, API budget, and principal change.

A mandatory **Phase 0 local runtime-text gate** is inserted before any Cloudflare credential lookup or external request.

### Phase 0A｜No-install policy

The executor MUST NOT install, download, upgrade, vendor, bootstrap, or otherwise mutate Python/package-manager state for H-60.

Forbidden examples include, but are not limited to:

```text
pip install / pip download
uv run with dependency resolution / uv pip
conda install / mamba install
poetry install
wheel download or unpack for import
get-pip / pip.pyz bootstrap
ad-hoc task-local site-packages
```

Using a dependency that is already installed in an existing interpreter is allowed. Merely importing/probing that existing dependency is allowed.

If an exact compatible extractor is not already available, the executor must stop with a local blocker. It must not repair the environment.

### Phase 0B｜Allowed runtime-text sources

H-60 runtime page text may come from exactly one of these sources:

1. a pre-existing repository artifact whose provenance proves the same frozen FinanceBench Git blobs and PyMuPDF `1.28.0` extraction profile, and whose H-58 canonical-page hashes all match; or
2. fresh local re-extraction from the frozen FinanceBench Git blobs using an **already installed** PyMuPDF `1.28.0` interpreter and the exact H-58 extraction/normalization algorithm.

`H59_PAGE_TEXTS_FOR_TOKEN_PREFLIGHT.jsonl` is explicitly rejected as a runtime-text source because the evaluator measured `15/15` canonical hash mismatches.

No alternate PDF parser, alternate PyMuPDF version, OCR path, HTML text, MinerU text, or rewritten/normalized text is allowed inside H-60.

### Phase 0C｜Required local evidence

Before any Cloudflare call, executor must produce:

- `RUNTIME_TEXT_PREFLIGHT.json`
- `H60_RUNTIME_INPUTS.jsonl` only when the gate passes

`RUNTIME_TEXT_PREFLIGHT.json` must expose at least:

```text
status = PASS | BLOCKED
no_dependency_install_policy = ENFORCED
dependency_install_attempts = 0
dependency_download_attempts = 0
h59_canonical_refs_checked
h59_canonical_hash_mismatches
runtime_text_source
extractor
extractor_version
financebench_revision
page_count
query_count
canonical_hash_checks
canonical_hash_mismatches
runtime_input_sha256
```

A `PASS` requires all of:

```text
page_count = 1019
query_count = 12
canonical_hash_checks = 15
canonical_hash_mismatches = 0
extractor = PyMuPDF
extractor_version = 1.28.0
financebench_revision = cc39aeb4afdf33909ee1412188bf89035950c2eb
dependency_install_attempts = 0
dependency_download_attempts = 0
```

### Phase 0D｜Blocker behavior

If Phase 0 cannot pass using already available local capability/assets:

```text
Cloudflare preflight calls = 0
Cloudflare runtime calls   = 0
primary measurement       = NOT_MEASURED
```

Executor must write/update `REPORT.md` with `EXECUTION_BLOCKED_LOCAL_RUNTIME_TEXT_SOURCE`, keep the capability verdict `NOT_MEASURED / INCONCLUSIVE`, route back to Evaluator, and stop.

This is an execution-environment blocker, not a rejection of H-60's evidence-workspace hypothesis.

## Runner enforcement

`run_h60_cloudflare_workspace.py` must refuse to proceed unless:

1. `RUNTIME_TEXT_PREFLIGHT.json` exists and says `PASS`;
2. dependency install/download attempts are both `0`;
3. extractor/version/revision match the frozen authority;
4. `H60_RUNTIME_INPUTS.jsonl` SHA256 matches the preflight record;
5. existing H-58 canonical hash checks still pass.

This guard must execute before Cloudflare credentials are used for any external request.

## Validation-plan repair

Add a mandatory local `VP-00` gate proving the runtime-text preflight and zero-install policy. Existing VP-01..VP-08 remain semantically unchanged.

## Authorization and routing

No new authority is granted.

```text
authorization_commit = false
authorization_push = false
authorization_history_rewrite = false
authorization_api_call = true  # unchanged; Cloudflare embedding-only, and only after Phase 0 PASS
```

The task remains H-60 and remains routed to Executor. The executor's next action is **Phase 0 only**. It may proceed to Cloudflare only if Phase 0 returns `PASS` without any environment mutation.
