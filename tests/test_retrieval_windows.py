"""Retrieval path consistency, multi-window, and dedup behavior (P4 / P6e / P6g).

Covers task card §2.3:
- main retrieval and per-option retrieval share the same effective domain
  top_k/flank policy (per-option calls ``_retrieve_doc`` which calls
  ``_resolve_top_k``/``_resolve_flank`` on the same ``question.domain``);
- single-page documents can yield multiple distinct windows;
- same-page distinct snippets are not removed by source-only dedup.

Uses ``tmp_path`` fixtures only; no real dataset or API.
"""

from __future__ import annotations

from pathlib import Path

from contracts import ClassificationResult, Question, QuestionLabel
from retrieval.hybrid import LexicalHybridRetriever


def _write_page(doc_dir: Path, domain: str, doc_id: str, page_name: str, text: str) -> Path:
    d = doc_dir / domain / doc_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / page_name
    p.write_text(text, encoding="utf-8")
    return p


def test_main_retrieval_uses_per_domain_top_k(tmp_path):
    """Main retrieval pass honors ``_resolve_top_k(domain)``: with insurance
    top_k=2 and 5 distinct matching pages, only 2 candidates are returned."""
    r = LexicalHybridRetriever(
        processed_docs_dir=tmp_path,
        top_k_per_doc=5,
        top_k_per_doc_by_domain={"insurance": 4},
    )
    # Override top_k to 2 for this domain to make the cap observable.
    r._top_k_by_domain = {"insurance": 2}
    titles = ("责任免除", "等待期", "保险责任", "现金价值", "退保")
    for i, t in enumerate(titles, start=1):
        _write_page(tmp_path, "insurance", "1", f"page_{i:04d}.md", t * 50)
    q = Question(qid="t1", domain="insurance", text="保险责任",
                 options={"A": "x"}, answer_format="text", doc_ids=["1"])
    cls = ClassificationResult(labels=[])
    cands = r.retrieve(q, cls)
    assert len(cands) == 2  # capped by per-domain top_k=2, not global 5


def test_per_option_retrieval_shares_same_top_k(tmp_path):
    """Per-option path calls ``_retrieve_doc`` (same ``_resolve_top_k``), so a
    multi_option question on an insurance top_k=2 domain never exceeds the
    per-pass cap and stays within the same domain policy."""
    r = LexicalHybridRetriever(
        processed_docs_dir=tmp_path,
        top_k_per_doc=5,
        top_k_per_doc_by_domain={"insurance": 2},
    )
    titles = ("责任免除", "等待期", "保险责任", "现金价值", "退保")
    for i, t in enumerate(titles, start=1):
        _write_page(tmp_path, "insurance", "1", f"page_{i:04d}.md", t * 50)
    q = Question(qid="m1", domain="insurance", text="保险责任",
                 options={"A": "责任免除", "B": "等待期"},
                 answer_format="mcq", doc_ids=["1"])
    cls = ClassificationResult(labels=[QuestionLabel.MULTI_OPTION])
    cands = r.retrieve(q, cls)
    # Every candidate comes from the same doc/page set; main pass capped at 2,
    # each per-option pass also capped at 2 and de-duped by (source, text[:80]).
    assert all(c.doc_id == "1" for c in cands)
    assert len(cands) <= 6  # main(2) + 2 options * 2, deduped


def test_single_page_yields_multiple_distinct_windows():
    """A single long page with two separated match regions contributes more
    than one non-overlapping window via ``_score_text_top_n``."""
    r = LexicalHybridRetriever(processed_docs_dir=None)
    part1 = "责任免除条款内容" * 50          # ~400 chars, high-score region 1
    filler = "无关注释内容填充" * 300         # ~2400 chars, no match terms
    part2 = "等待期规定说明" * 50            # ~400 chars, high-score region 2
    text = part1 + filler + part2
    wins = r._score_text_top_n(text, ["责任免除", "等待期"], [], 3)
    assert len(wins) >= 2
    # Kept windows must not overlap by more than 60%
    for i in range(len(wins)):
        for j in range(i + 1, len(wins)):
            s1, e1 = wins[i][1], wins[i][2]
            s2, e2 = wins[j][1], wins[j][2]
            ov = max(0, min(e1, e2) - max(s1, s2))
            mn = min(e1 - s1, e2 - s2)
            assert mn == 0 or ov / mn <= 0.6


def test_same_page_distinct_snippets_not_deduped():
    """``_deduplicate_windows`` keys on text overlap, not source alone, so two
    distinct snippets from the same page are both kept."""
    r = LexicalHybridRetriever(processed_docs_dir=None)
    page = Path("/fake/page_0001.md")
    a = (10.0, page, "aaaa" * 400, 0, 1600, [], {})
    b = (9.0, page, "bbbb" * 400, 2000, 3600, [], {})  # same page, no overlap
    out = r._deduplicate_windows([a, b])
    assert len(out) == 2


def test_flank_override_reflected_in_before_text(tmp_path):
    """regulatory flank=300 produces a ~300-char ``before_text``, vs ~600 for
    the global default, proving ``_resolve_flank`` is applied in ``_retrieve_doc``."""
    filler = "无关注释内容" * 500              # 2000 chars, pushes match past window 0
    text = filler + "责任免除条款" + "尾部填充" * 50
    _write_page(tmp_path, "regulatory", "1", "page_0001.md", text)

    r_reg = LexicalHybridRetriever(
        processed_docs_dir=tmp_path,
        context_flank_chars_by_domain={"regulatory": 300},
    )
    r_global = LexicalHybridRetriever(processed_docs_dir=tmp_path)

    q = Question(qid="f1", domain="regulatory", text="责任免除",
                 options={"A": "x"}, answer_format="text", doc_ids=["1"])
    cls = ClassificationResult(labels=[])

    c_reg = r_reg.retrieve(q, cls)
    c_global = r_global.retrieve(q, cls)
    assert c_reg and c_global
    # Match window starts at 1350 (second window), so before_text length tracks flank.
    assert len(c_reg[0].before_text) <= 300
    assert len(c_global[0].before_text) > 300  # global 600 yields a longer flank


def test_numeric_scoring_ignores_thousands_separator(tmp_path):
    retriever = LexicalHybridRetriever(tmp_path)
    score, *_ = retriever._score_text(
        "经营活动产生的现金流量净额 1,332 亿元，同比增长。",
        ["经营活动产生的现金流量净额", "1332亿元"],
        ["1332亿"],
    )
    assert score > 0


def test_per_option_retrieval_can_surface_exact_clause_page(tmp_path):
    doc_dir = tmp_path / "insurance" / "16"
    doc_dir.mkdir(parents=True)
    (doc_dir / "page_0001.md").write_text("目录 退保 现金价值 解除合同", encoding="utf-8")
    (doc_dir / "page_0004.md").write_text(
        "# 1.3 犹豫期\n您可以在此期间提出解除本合同，我们将无息退还您所支付的全部保险费。",
        encoding="utf-8",
    )
    question = Question(
        qid="case_018",
        domain="insurance",
        text="哪些产品在犹豫期内解除合同会退还全部已交保险费？",
        options={"D": "平安富鸿金生在犹豫期内解除合同退还全部保险费"},
        answer_format="multi",
        doc_ids=["16"],
    )
    classification = ClassificationResult(labels=[QuestionLabel.MULTI_OPTION, QuestionLabel.FACT_LOOKUP])
    retriever = LexicalHybridRetriever(tmp_path, top_k_per_doc=1)
    candidates = retriever.retrieve(question, classification)
    assert any(candidate.source.endswith("page_0004.md") for candidate in candidates)
