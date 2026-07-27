"""Resumable manifest tests (Lane A, remote-offline).

Tests cover:
1. resume=False re-adapts every document and overwrites the manifest.
2. resume=True first run writes the manifest with source signatures.
3. resume=True second run skips documents whose sources are unchanged.
4. resume=True re-adapts a document whose source file changed.
5. resume=True re-adapts a document whose source file was deleted.
6. resume=True picks up newly added documents.
7. manifest structure: version, domain, per-doc status + signature.
8. skipped document returns a result with the same non-path fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from structure.mineru_adapter import adapt_corpus, _manifest_path, _load_manifest

FIXTURE_DOC = Path(__file__).parent / "fixtures" / "mineru" / "sample_doc"


def _make_mineru_corpus(root: Path, domain: str, doc_ids) -> Path:
    """Build a synthetic MinerU corpus by copying the checked-in fixture."""
    domain_dir = root / "mineru" / domain
    for doc_id in doc_ids:
        d = domain_dir / doc_id
        (d / "auto").mkdir(parents=True, exist_ok=True)
        items = json.loads(
            (FIXTURE_DOC / "auto" / "sample_doc_content_list_v2.json").read_text(encoding="utf-8")
        )
        (d / "auto" / f"{doc_id}_content_list_v2.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8"
        )
    return root / "mineru"


# ── 1. resume=False re-adapts and overwrites ─────────────────────────


def test_no_resume_readapts_all(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a", "b"])
    target = tmp_path / "target"
    r1 = adapt_corpus(mineru_root, target, domain="insurance")
    assert len(r1) == 2
    # No manifest written when resume=False.
    assert not _manifest_path(target, "insurance").is_file()


# ── 2. resume=True writes manifest with signatures ───────────────────


def test_resume_writes_manifest(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    adapt_corpus(mineru_root, target, domain="insurance", resume=True)

    manifest = _load_manifest(target, "insurance")
    assert manifest["version"] == 2
    assert len(manifest["adapter_fingerprint"]) == 64
    assert manifest["domain"] == "insurance"
    assert "a" in manifest["docs"]
    entry = manifest["docs"]["a"]
    assert entry["status"] == "completed"
    assert entry["reconstruction_mode"] == "content_list_v2"
    assert entry["page_count"] == 3
    assert entry["degraded"] is False
    sig = entry["source_signature"]
    assert sig  # non-empty
    # Every recorded source file has a real sha256 (hex, 64 chars).
    for path, sha in sig.items():
        assert len(sha) == 64
        assert Path(path).is_file()


# ── 3. resume=True skips unchanged documents ─────────────────────────


def test_resume_skips_unchanged_documents(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a", "b"])
    target = tmp_path / "target"

    r1 = adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    # Capture page file content + mtimes after first run.
    page_a = (target / "insurance" / "a" / "page_0001.md").read_text(encoding="utf-8")
    mtime_a = (target / "insurance" / "a" / "page_0001.md").stat().st_mtime_ns

    r2 = adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    assert len(r2) == 2
    # Skipped doc returns the same non-path fields.
    a1 = next(r for r in r1 if r.doc_id == "a")
    a2 = next(r for r in r2 if r.doc_id == "a")
    assert a2.reconstruction_mode == a1.reconstruction_mode
    assert a2.page_count == a1.page_count
    assert a2.degraded == a1.degraded
    assert a2.warnings == a1.warnings
    # Page file content unchanged.
    assert (target / "insurance" / "a" / "page_0001.md").read_text(encoding="utf-8") == page_a
    # Page file was NOT rewritten (mtime unchanged) — proof of skip.
    assert (target / "insurance" / "a" / "page_0001.md").stat().st_mtime_ns == mtime_a


# ── 4. resume=True re-adapts when source changed ─────────────────────


def test_resume_readapts_when_source_changed(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    adapt_corpus(mineru_root, target, domain="insurance", resume=True)

    # Mutate the source content list (different text on page 1).
    cl = mineru_root / "insurance" / "a" / "auto" / "a_content_list_v2.json"
    data = json.loads(cl.read_text(encoding="utf-8"))
    data[0][0]["content"]["title_content"][0]["content"] = "改后的标题"
    cl.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    r2 = adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    page1 = (target / "insurance" / "a" / "page_0001.md").read_text(encoding="utf-8")
    assert "改后的标题" in page1  # re-adapted


# ── 5. resume=True re-adapts when source deleted ─────────────────────


def test_resume_readapts_when_source_deleted(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    adapt_corpus(mineru_root, target, domain="insurance", resume=True)

    # Delete the content list — the doc now has no MinerU output.
    (mineru_root / "insurance" / "a" / "auto" / "a_content_list_v2.json").unlink()

    r2 = adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    a = next(r for r in r2 if r.doc_id == "a")
    # No content list and no markdown -> degraded, 0 pages.
    assert a.degraded is True
    assert a.page_count == 0


# ── 6. resume=True picks up newly added documents ────────────────────


def test_resume_picks_up_new_documents(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    adapt_corpus(mineru_root, target, domain="insurance", resume=True)

    # Add a second document.
    _make_mineru_corpus(tmp_path, "insurance", ["a", "b"])
    r2 = adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    assert {r.doc_id for r in r2} == {"a", "b"}
    # 'a' was skipped (page content unchanged), 'b' was newly adapted.
    assert (target / "insurance" / "b" / "page_0001.md").is_file()


# ── 7. manifest structure ────────────────────────────────────────────


def test_manifest_records_adapted_at_timestamp(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    manifest = _load_manifest(target, "insurance")
    ts = manifest["docs"]["a"]["adapted_at"]
    assert isinstance(ts, str)
    assert ts.startswith("20")  # ISO date prefix


def test_manifest_is_valid_json_on_disk(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    raw = _manifest_path(target, "insurance").read_text(encoding="utf-8")
    # Must be parseable and contain the expected top-level keys.
    data = json.loads(raw)
    assert data["version"] == 2
    assert len(data["adapter_fingerprint"]) == 64
    assert data["domain"] == "insurance"
    assert isinstance(data["docs"], dict)


# ── 8. skipped result parity ─────────────────────────────────────────


def test_skipped_result_has_same_source_files(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    r1 = adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    r2 = adapt_corpus(mineru_root, target, domain="insurance", resume=True)
    a1 = next(r for r in r1 if r.doc_id == "a")
    a2 = next(r for r in r2 if r.doc_id == "a")
    assert a2.source_files == a1.source_files


def test_resume_invalidates_cache_when_adapter_fingerprint_changes(tmp_path: Path):
    mineru_root = _make_mineru_corpus(tmp_path, "insurance", ["a"])
    target = tmp_path / "target"
    adapt_corpus(mineru_root, target, domain="insurance", resume=True)

    page = target / "insurance" / "a" / "page_0001.md"
    page.write_text("STALE-CACHED-OUTPUT\n", encoding="utf-8")
    manifest_path = _manifest_path(target, "insurance")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["adapter_fingerprint"] = "stale-adapter"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    adapt_corpus(mineru_root, target, domain="insurance", resume=True)

    assert "STALE-CACHED-OUTPUT" not in page.read_text(encoding="utf-8")
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert refreshed["adapter_fingerprint"] != "stale-adapter"
