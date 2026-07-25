"""Per-domain retrieval config resolution + coercion/fallback (P6e-9 / P6g-3).

Covers task card §2.1 and §2.2: active domain overrides resolve correctly,
non-overridden domains fall back to global defaults, and malformed config
values (bool / zero / negative / non-integral float / float-like string /
non-numeric string) fall back safely without crashing.
"""

from __future__ import annotations

from retrieval.hybrid import LexicalHybridRetriever


def _make(**kwargs):
    """Build a retriever with only config-coercion-relevant args."""
    return LexicalHybridRetriever(processed_docs_dir=None, **kwargs)


# ── §2.1 active per-domain policy ──────────────────────────────────────


def test_regulatory_resolves_flank_300():
    r = _make(context_flank_chars_by_domain={"regulatory": 300})
    assert r._resolve_flank("regulatory") == 300


def test_non_regulatory_falls_back_to_global_flank_600():
    r = _make(context_flank_chars_by_domain={"regulatory": 300})
    for domain in ("insurance", "financial_contracts", "financial_reports",
                   "research", "unknown"):
        assert r._resolve_flank(domain) == 600


def test_insurance_resolves_top_k_4():
    r = _make(top_k_per_doc_by_domain={"insurance": 4, "financial_contracts": 4})
    assert r._resolve_top_k("insurance") == 4


def test_financial_contracts_resolves_top_k_4():
    r = _make(top_k_per_doc_by_domain={"insurance": 4, "financial_contracts": 4})
    assert r._resolve_top_k("financial_contracts") == 4


def test_other_domains_fall_back_to_global_top_k_5():
    r = _make(top_k_per_doc=5, top_k_per_doc_by_domain={"insurance": 4})
    for domain in ("regulatory", "research", "financial_reports", "unknown"):
        assert r._resolve_top_k(domain) == 5


def test_global_flank_default_600_when_absent():
    r = _make()
    assert r.context_flank_chars == 600
    assert r._resolve_flank("anything") == 600


# ── §2.2 coercion / fallback ────────────────────────────────────────────


def test_coerce_positive_int_accepts_valid():
    assert LexicalHybridRetriever._coerce_positive_int(4) == 4
    assert LexicalHybridRetriever._coerce_positive_int(4.0) == 4
    assert LexicalHybridRetriever._coerce_positive_int("4") == 4


def test_coerce_positive_int_rejects_invalid():
    # bool, zero, negative, non-integral float, float-like string, non-numeric
    assert LexicalHybridRetriever._coerce_positive_int(True) is None
    assert LexicalHybridRetriever._coerce_positive_int(0) is None
    assert LexicalHybridRetriever._coerce_positive_int(-3) is None
    assert LexicalHybridRetriever._coerce_positive_int(4.5) is None
    assert LexicalHybridRetriever._coerce_positive_int("4.0") is None
    assert LexicalHybridRetriever._coerce_positive_int("4.5") is None
    assert LexicalHybridRetriever._coerce_positive_int("abc") is None


def test_coerce_flank_falls_back_to_600_on_invalid():
    assert LexicalHybridRetriever._coerce_flank(True) == 600
    assert LexicalHybridRetriever._coerce_flank(0) == 600
    assert LexicalHybridRetriever._coerce_flank(-5) == 600
    assert LexicalHybridRetriever._coerce_flank(4.5) == 600
    assert LexicalHybridRetriever._coerce_flank("abc") == 600
    assert LexicalHybridRetriever._coerce_flank(None) == 600


def test_coerce_domain_map_drops_invalid_keeps_valid():
    raw = {"insurance": 4, "bad_bool": True, "bad_zero": 0,
           "bad_float": 4.5, "bad_str": "x", "ok_str": "5"}
    coerced = LexicalHybridRetriever._coerce_domain_map(raw)
    assert coerced == {"insurance": 4, "ok_str": 5}


def test_coerce_domain_map_handles_non_mapping():
    assert LexicalHybridRetriever._coerce_domain_map(None) == {}
    assert LexicalHybridRetriever._coerce_domain_map("not a dict") == {}
    assert LexicalHybridRetriever._coerce_domain_map([]) == {}


def test_document_resolution_prefers_primary_then_fallback(tmp_path):
    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    primary_doc = primary / "regulatory" / "same"
    fallback_same = fallback / "regulatory" / "same"
    fallback_only = fallback / "regulatory" / "strict_v3_008"
    primary_doc.mkdir(parents=True)
    fallback_same.mkdir(parents=True)
    fallback_only.mkdir(parents=True)
    (primary_doc / "page_0001.md").write_text("primary", encoding="utf-8")
    (fallback_same / "page_0001.md").write_text("fallback same", encoding="utf-8")
    (fallback_only / "page_0001.md").write_text("fallback only", encoding="utf-8")

    retriever = LexicalHybridRetriever(
        processed_docs_dir=primary,
        fallback_processed_docs_dirs=[fallback],
    )

    assert retriever._resolve_doc_dir("regulatory", "same") == primary_doc
    assert retriever._resolve_doc_dir("regulatory", "strict_v3_008") == fallback_only
    assert retriever._resolve_doc_dir("regulatory", "missing") is None
