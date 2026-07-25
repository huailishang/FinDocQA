"""R2 zero-evidence diagnostic — synthetic tests.

Tests cover:
1. Full funnel with all docs present and a real evidence bundle.
2. Missing parsed doc: resolved_doc_count drops, warning recorded.
3. Empty parsed dir: pages=0, indexed=0, zero-evidence stage = indexed_chunk_count.
4. No evidence bundle: retrieved/post_filter/context = 0.
5. Per-qid filtering and aggregate report JSON + Markdown output.
6. zero_evidence_stage pinpoints the first zero stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from contracts import EvidenceBundle, EvidenceCandidate, Question
from diagnostics.zero_evidence import diagnose_question, diagnose_questions


def _make_question(qid: str = "case_013", doc_ids=("1", "2")) -> Question:
    return Question(
        qid=qid,
        domain="insurance",
        text="测试问题",
        options={"A": "选项A", "B": "选项B"},
        answer_format="mcq",
        doc_ids=doc_ids,
    )


def _make_bundle_simple(qid: str, candidate_count: int = 3, context_chars: int = 500) -> EvidenceBundle:
    """Build a synthetic EvidenceBundle for testing."""
    from contracts import ClassificationResult
    q = _make_question(qid)
    candidates = tuple(
        EvidenceCandidate(
            domain="insurance", doc_id="1", source=f"page_0001.md",
            text=f"证据片段 {i}",
        )
        for i in range(candidate_count)
    )
    return EvidenceBundle(
        question=q,
        classification=ClassificationResult(labels=[]),
        candidates=candidates,
        prompt_context="x" * context_chars,
        estimated_tokens=context_chars // 4,
        metadata={"evidence_count": candidate_count},
    )


@pytest.fixture
def processed_root_with_docs(tmp_path: Path) -> Path:
    """A processed_docs root with insurance/1 and insurance/2, each with 2 pages."""
    root = tmp_path / "processed"
    for doc_id in ("1", "2"):
        doc_dir = root / "insurance" / doc_id
        doc_dir.mkdir(parents=True)
        for page in (1, 2):
            (doc_dir / f"page_{page:04d}.md").write_text(f"文档 {doc_id} 第 {page} 页内容。" * 100, encoding="utf-8")
    return root


# ── 1. full funnel with all docs present ──────────────────────────────


def test_full_funnel_all_docs_present(processed_root_with_docs: Path):
    q = _make_question(doc_ids=("1", "2"))
    bundle = _make_bundle_simple("case_013", candidate_count=3, context_chars=500)
    diag = diagnose_question(q, processed_root=processed_root_with_docs, evidence_bundle=bundle)

    assert diag.referenced_doc_count == 2
    assert diag.resolved_doc_count == 2
    assert diag.parsed_page_count == 4  # 2 docs × 2 pages
    assert diag.indexed_chunk_count > 0
    assert diag.retrieved_candidate_count == 3
    assert diag.post_filter_evidence_count == 3
    assert diag.solver_context_chars == 500
    assert diag.zero_evidence_stage == ""


# ── 2. missing parsed doc ─────────────────────────────────────────────


def test_missing_doc_reduces_resolved_count(processed_root_with_docs: Path):
    q = _make_question(doc_ids=("1", "99"))  # doc 99 does not exist
    bundle = _make_bundle_simple("case_014", candidate_count=2, context_chars=300)
    diag = diagnose_question(q, processed_root=processed_root_with_docs, evidence_bundle=bundle)

    assert diag.referenced_doc_count == 2
    assert diag.resolved_doc_count == 1  # only doc 1 resolved
    assert any("doc_id=99" in w for w in diag.warnings)


# ── 3. empty parsed dir (no pages) ────────────────────────────────────


def test_empty_parsed_dir_is_zero_evidence(tmp_path: Path):
    root = tmp_path / "processed"
    doc_dir = root / "insurance" / "1"
    doc_dir.mkdir(parents=True)
    # No page_*.md files.
    q = _make_question(doc_ids=("1",))
    diag = diagnose_question(q, processed_root=root, evidence_bundle=None)

    assert diag.resolved_doc_count == 1
    assert diag.parsed_page_count == 0
    assert diag.indexed_chunk_count == 0
    # Without a bundle, retrieved/post_filter are 0 too.
    assert diag.zero_evidence_stage != ""


# ── 4. no evidence bundle ─────────────────────────────────────────────


def test_no_bundle_zeros_retrieved_and_context(processed_root_with_docs: Path):
    q = _make_question()
    diag = diagnose_question(q, processed_root=processed_root_with_docs, evidence_bundle=None)

    assert diag.retrieved_candidate_count == 0
    assert diag.post_filter_evidence_count == 0
    assert diag.solver_context_chars == 0
    assert diag.zero_evidence_stage == "retrieved_candidate_count"


# ── 5. aggregate report JSON + Markdown ───────────────────────────────


def test_aggregate_report_formats(processed_root_with_docs: Path):
    q1 = _make_question("case_013", doc_ids=("1", "2"))
    q2 = _make_question("case_014", doc_ids=("1",))
    bundle1 = _make_bundle_simple("case_013", 3, 500)
    bundle2 = _make_bundle_simple("case_014", 2, 300)
    report = diagnose_questions(
        [q1, q2],
        processed_root=processed_root_with_docs,
        bundles={"case_013": bundle1, "case_014": bundle2},
    )

    assert report.question_count == 2
    assert report.zero_evidence_count == 0

    # JSON is valid and has expected structure.
    data = json.loads(report.json_text)
    assert data["question_count"] == 2
    assert "stage_totals" in data
    assert "questions" in data
    assert len(data["questions"]) == 2

    # Markdown has the funnel table.
    assert "| qid |" in report.markdown_text
    assert "case_013" in report.markdown_text
    assert "case_014" in report.markdown_text


# ── 6. zero_evidence_stage pinpoints first zero ───────────────────────


def test_zero_stage_points_to_first_zero(tmp_path: Path):
    # doc with pages but no bundle -> indexed > 0, retrieved = 0.
    root = tmp_path / "processed"
    doc_dir = root / "insurance" / "1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "page_0001.md").write_text("内容" * 1000, encoding="utf-8")
    q = _make_question(doc_ids=("1",))
    diag = diagnose_question(q, processed_root=root, evidence_bundle=None)

    assert diag.parsed_page_count == 1
    assert diag.indexed_chunk_count > 0
    assert diag.retrieved_candidate_count == 0
    assert diag.zero_evidence_stage == "retrieved_candidate_count"


# ── 7. CLI smoke test (catches --help / import regressions) ───────────
#
# This test exists because of a real regression: an earlier R2 build imported
# a nonexistent `QuestionLoader` symbol and only failed at CLI invocation
# time. The synthetic unit tests above did not catch it because they call the
# core functions directly, never the CLI entrypoint. Running `--help` via
# subprocess exercises the full import chain (scripts/ -> diagnostics ->
# agent.factory -> data.loader) the same way a local L3 operator would.


def test_cli_help_succeeds_without_import_error():
    """`python scripts/zero_evidence_diagnostic.py --help` must exit 0.

    A non-zero exit or an ImportError here means the CLI loader contract
    broke (e.g. importing a symbol that does not exist in data.loader).
    """
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "zero_evidence_diagnostic.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=60,
    )
    # --help exits 0 on success; ImportError / SystemExit(non-zero) on failure.
    assert proc.returncode == 0, (
        f"CLI --help failed (exit {proc.returncode}).\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "Zero-evidence" in proc.stdout or "zero-evidence" in proc.stdout.lower()
    # The loader import bug produced an ImportError in stderr; guard against it.
    assert "ImportError" not in proc.stderr


# ── 8. CLI reads processed_docs from config via dict access ───────────
#
# Regression for the R2 bug where main() used ``config.paths.processed_docs``
# (attribute access) on the dict returned by load_config(), raising
# ``AttributeError: 'dict' object has no attribute 'paths'`` whenever
# ``--processed-root`` was omitted. This test runs the real CLI end-to-end
# with a minimal config and NO ``--processed-root``: if the dict-access fix
# regresses, the subprocess raises AttributeError and exits non-zero.


def _write_minimal_config(repo_root: Path, tmp_path: Path) -> Path:
    """Write a config.yaml whose paths resolve to empty tmp dirs.

    ``raw_dataset`` points at a tmp dir with no ``questions/group_a`` so the
    loader returns an empty list (no AttributeError, no missing-data crash).
    ``processed_docs`` points at a tmp dir so the dict-access path is
    exercised without touching real data.
    """
    raw_dataset = tmp_path / "raw_dataset"
    raw_dataset.mkdir(parents=True, exist_ok=True)
    processed = tmp_path / "processed_docs"
    processed.mkdir(parents=True, exist_ok=True)
    # minimal config — only the keys main()/factory read.
    config_text = (
        "paths:\n"
        f"  raw_dataset: {raw_dataset}\n"
        f"  processed_docs: {processed}\n"
        "  output_dir: output\n"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    return config_path


def test_cli_reads_processed_docs_from_config_dict(tmp_path: Path):
    """Without --processed-root, the CLI must read paths.processed_docs from
    the config dict (not via attribute access) and exit 0."""
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    config_path = _write_minimal_config(repo_root, tmp_path)
    script = repo_root / "scripts" / "zero_evidence_diagnostic.py"

    proc = subprocess.run(
        [sys.executable, str(script), "--config", str(config_path)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=60,
    )

    # The pre-fix bug raised AttributeError and exited non-zero. A passing run
    # with no questions prints the "No questions matched" notice and exits 0.
    assert proc.returncode == 0, (
        f"CLI exited {proc.returncode} without --processed-root (dict-access bug?).\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "AttributeError" not in proc.stderr
    assert "No questions matched" in proc.stdout


# ── 9. EvidenceBundle JSON round-trip ─────────────────────────────────
#
# The CLI's --evidence-artifacts / --dump-evidence path relies on a faithful
# (de)serialization of EvidenceBundle. Pin it directly.


def test_bundle_to_json_roundtrip_preserves_funnel_counts():
    from diagnostics.zero_evidence import bundle_to_json, bundle_from_json

    bundle = _make_bundle_simple("case_013", candidate_count=3, context_chars=500)
    restored = bundle_from_json(bundle_to_json(bundle))

    assert len(restored.candidates) == len(bundle.candidates) == 3
    assert len(restored.prompt_context) == len(bundle.prompt_context) == 500
    assert restored.metadata.get("evidence_count") == 3
    # diagnose_question reads the same fields, so a restored bundle must drive
    # the same funnel numbers as the original.
    d_orig = diagnose_question(_make_question("case_013"), processed_root=Path("/nonexistent"), evidence_bundle=bundle)
    d_rest = diagnose_question(_make_question("case_013"), processed_root=Path("/nonexistent"), evidence_bundle=restored)
    assert d_rest.retrieved_candidate_count == d_orig.retrieved_candidate_count
    assert d_rest.post_filter_evidence_count == d_orig.post_filter_evidence_count
    assert d_rest.solver_context_chars == d_orig.solver_context_chars


# ── 10. CLI runs REAL retrieval by default (no faked zeros) ───────────
#
# Regression for the R2/R4 complaint: main() used to call
# diagnose_questions(..., bundles=None), forcing retrieved/post_filter/context
# to 0 for every question — a fake "zero-evidence" signal. The fixed CLI runs
# the real offline retriever+assembler, so a question whose docs parse and
# match must report NON-zero retrieval and NOT be flagged zero-evidence.


def _write_retrieval_fixture(tmp_path: Path) -> tuple:
    """Write a minimal config + one real question + one parsed page.

    The page contains the question's key terms so the lexical retriever scores
    it > 0 (and even with zero term matches the retriever's first-page fallback
    yields >=1 candidate, so retrieved_candidate_count is never a faked 0).
    Returns (config_path, qid).
    """
    raw_dataset = tmp_path / "raw_dataset"
    questions_dir = raw_dataset / "questions" / "group_a"
    questions_dir.mkdir(parents=True)
    qid = "ins_t_live_001"
    question = {
        "qid": qid,
        "domain": "insurance",
        "question": "等待期内因意外伤害出险，保险公司是否承担保险责任？",
        "options": {"A": "承担", "B": "不承担"},
        "answer_format": "mcq",
        "doc_ids": ["1"],
    }
    (questions_dir / f"{qid}.json").write_text(
        json.dumps(question, ensure_ascii=False), encoding="utf-8"
    )

    processed = tmp_path / "processed_docs"
    doc_dir = processed / "insurance" / "1"
    doc_dir.mkdir(parents=True)
    page = (
        "# 保险条款\n\n"
        "## 责任免除\n"
        "等待期内因意外伤害出险，本公司不承担保险责任。\n\n"
        "## 保险责任\n"
        "本合同承担的保险责任如下。\n"
    )
    (doc_dir / "page_0001.md").write_text(page, encoding="utf-8")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "paths:\n"
        f"  raw_dataset: {raw_dataset}\n"
        f"  processed_docs: {processed}\n"
        "  output_dir: output\n",
        encoding="utf-8",
    )
    return config_path, qid


def test_cli_live_retrieval_reports_nonzero_not_faked_zero(tmp_path: Path):
    """Default CLI run traces the REAL funnel: retrieved/post_filter/context > 0."""
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    config_path, qid = _write_retrieval_fixture(tmp_path)
    script = repo_root / "scripts" / "zero_evidence_diagnostic.py"
    out_json = tmp_path / "report.json"

    proc = subprocess.run(
        [
            sys.executable, str(script),
            "--config", str(config_path),
            "--qid", qid,
            "--output-json", str(out_json),
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=120,
    )

    assert proc.returncode == 0, (
        f"CLI failed (exit {proc.returncode}).\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["question_count"] == 1
    q = data["questions"][0]
    # The core assertion: retrieval was actually run, so these are NOT the
    # faked zeros the old "no bundle" path produced.
    assert q["resolved_doc_count"] == 1
    assert q["parsed_page_count"] == 1
    assert q["retrieved_candidate_count"] > 0, (
        f"retrieved_candidate_count should be >0 (real retrieval), got {q}"
    )
    assert q["post_filter_evidence_count"] > 0, (
        f"post_filter_evidence_count should be >0 (real assembly), got {q}"
    )
    assert q["solver_context_chars"] > 0, (
        f"solver_context_chars should be >0 (real context), got {q}"
    )
    assert q["zero_evidence_stage"] == "", (
        f"should not be flagged zero-evidence, got stage={q['zero_evidence_stage']}"
    )


def test_cli_dump_then_reuse_evidence_artifacts(tmp_path: Path):
    """--dump-evidence persists bundles; --evidence-artifacts reloads them so the
    second run reports identical funnel numbers WITHOUT re-running retrieval
    (no 'fall back to live retrieval' warning)."""
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    config_path, qid = _write_retrieval_fixture(tmp_path)
    script = repo_root / "scripts" / "zero_evidence_diagnostic.py"
    dump_dir = tmp_path / "evidence"
    report_a = tmp_path / "report_live.json"
    report_b = tmp_path / "report_artifact.json"

    # Run 1: live retrieval, dump bundles.
    proc1 = subprocess.run(
        [
            sys.executable, str(script),
            "--config", str(config_path),
            "--qid", qid,
            "--dump-evidence", str(dump_dir),
            "--output-json", str(report_a),
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=120,
    )
    assert proc1.returncode == 0, f"run1 failed:\n{proc1.stderr}"
    assert (dump_dir / f"{qid}.json").is_file(), "bundle was not dumped"

    # Run 2: load the dumped bundle as a real artifact (no live retrieval).
    proc2 = subprocess.run(
        [
            sys.executable, str(script),
            "--config", str(config_path),
            "--qid", qid,
            "--evidence-artifacts", str(dump_dir),
            "--output-json", str(report_b),
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=120,
    )
    assert proc2.returncode == 0, f"run2 failed:\n{proc2.stderr}"
    # The artifact was found, so the CLI must NOT warn about falling back.
    assert "falling back to live retrieval" not in proc2.stdout, (
        f"artifact should have been loaded, not fallen back:\n{proc2.stdout}"
    )

    a = json.loads(report_a.read_text(encoding="utf-8"))["questions"][0]
    b = json.loads(report_b.read_text(encoding="utf-8"))["questions"][0]
    for stage in (
        "retrieved_candidate_count",
        "post_filter_evidence_count",
        "solver_context_chars",
    ):
        assert b[stage] == a[stage], f"{stage}: artifact run {b[stage]} != live run {a[stage]}"


def test_cli_missing_artifact_falls_back_to_live_with_warning(tmp_path: Path):
    """A missing artifact must NOT silently produce a faked zero: the CLI falls
    back to live retrieval and emits a warning naming the qid."""
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    config_path, qid = _write_retrieval_fixture(tmp_path)
    script = repo_root / "scripts" / "zero_evidence_diagnostic.py"
    empty_artifacts = tmp_path / "no_artifacts"
    empty_artifacts.mkdir()
    out_json = tmp_path / "report.json"

    proc = subprocess.run(
        [
            sys.executable, str(script),
            "--config", str(config_path),
            "--qid", qid,
            "--evidence-artifacts", str(empty_artifacts),
            "--output-json", str(out_json),
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        timeout=120,
    )
    assert proc.returncode == 0, f"CLI failed:\n{proc.stderr}"
    # Missing artifact -> explicit fallback warning (not a silent zero).
    assert "falling back to live retrieval" in proc.stdout
    # And because live retrieval ran, the numbers are real, not faked zeros.
    q = json.loads(out_json.read_text(encoding="utf-8"))["questions"][0]
    assert q["retrieved_candidate_count"] > 0
    assert q["zero_evidence_stage"] == ""
