"""Config fallback parser + evidence token-budget coercion (P6b / no-PyYAML path).

Covers task card §2.2 / §2.4:
- the no-PyYAML ``_parse_simple_yaml`` parser supports nested
  ``evidence.token_budgets``;
- ``GroupedEvidenceAssembler`` honors valid config overrides, ignores unknown
  keys, and falls back to defaults on invalid values;
- end-to-end ``load_config`` reads the committed ``config.yaml`` and exposes
  the active P6 per-domain policy.
"""

from __future__ import annotations

from pathlib import Path

from contracts import ClassificationResult, QuestionLabel
from evidence.assembler import GroupedEvidenceAssembler
from utils.config import _coerce_scalar, _parse_simple_yaml, load_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


# ── fallback YAML parser ────────────────────────────────────────────────


def test_parse_simple_yaml_nested_token_budgets():
    text = (
        "model:\n"
        "  name: Qwen\n"
        "evidence:\n"
        "  token_budgets:\n"
        "    default: 10000\n"
        "    cross_doc: 40000\n"
        "    calculation: 50000\n"
    )
    cfg = _parse_simple_yaml(text)
    assert cfg["evidence"]["token_budgets"] == {
        "default": 10000, "cross_doc": 40000, "calculation": 50000,
    }


def test_parse_simple_yaml_scalar_sections():
    text = "retrieval:\n  top_k_per_doc: 5\n  windows_per_page: 3\n"
    cfg = _parse_simple_yaml(text)
    assert cfg["retrieval"]["top_k_per_doc"] == 5
    assert cfg["retrieval"]["windows_per_page"] == 3


def test_coerce_scalar_bool_int_string():
    assert _coerce_scalar("true") is True
    assert _coerce_scalar("False") is False
    assert _coerce_scalar("42") == 42
    assert _coerce_scalar("plain text") == "plain text"
    assert _coerce_scalar('"quoted"') == "quoted"


# ── assembler token-budget coercion ─────────────────────────────────────


def test_assembler_honors_valid_budget_overrides():
    asm = GroupedEvidenceAssembler(token_budgets={
        "cross_doc": 12345, "calculation": 99999,
    })
    cls = ClassificationResult(labels=[QuestionLabel.CROSS_DOC])
    bundle = asm.assemble(
        question=_q(), classification=cls, candidates=[],
    )
    assert bundle.metadata["token_budget"] == 12345
    assert bundle.metadata["evidence_budget_source"] == "config"


def test_assembler_ignores_unknown_keys():
    asm = GroupedEvidenceAssembler(token_budgets={"unknown_label": 1, "default": 7})
    # unknown key silently ignored; default applied
    cls = ClassificationResult(labels=[])
    bundle = asm.assemble(question=_q(), classification=cls, candidates=[])
    assert bundle.metadata["token_budget"] == 7


def test_assembler_falls_back_on_invalid_values():
    asm = GroupedEvidenceAssembler(token_budgets={
        "default": True,        # bool rejected
        "cross_doc": 0,         # non-positive rejected
        "calculation": "abc",   # non-numeric rejected
    })
    cls = ClassificationResult(labels=[QuestionLabel.CALCULATION])
    bundle = asm.assemble(question=_q(), classification=cls, candidates=[])
    # calculation fell back to its hard-coded default 50000
    assert bundle.metadata["token_budget"] == 50000


def test_assembler_defaults_when_no_config():
    asm = GroupedEvidenceAssembler()
    cls = ClassificationResult(labels=[QuestionLabel.FACT_LOOKUP])
    bundle = asm.assemble(question=_q(), classification=cls, candidates=[])
    assert bundle.metadata["token_budget"] == 10000
    assert bundle.metadata["evidence_budget_source"] == "default"


# ── end-to-end committed config ─────────────────────────────────────────


def test_load_committed_config_exposes_active_p6_policy():
    cfg = load_config(CONFIG_PATH)
    assert cfg["retrieval"]["context_flank_chars_by_domain"]["regulatory"] == 300
    assert cfg["retrieval"]["top_k_per_doc_by_domain"]["insurance"] == 4
    assert cfg["retrieval"]["top_k_per_doc_by_domain"]["financial_contracts"] == 4
    assert cfg["retrieval"]["context_flank_chars"] == 600  # global default unchanged
    assert cfg["evidence"]["token_budgets"]["calculation"] == 50000


def test_parse_simple_yaml_multiple_nested_maps_in_one_section():
    """P6e-11: a section may hold several nested maps (retrieval has both
    context_flank_chars_by_domain and top_k_per_doc_by_domain). Each must be
    parsed as its own map, not folded into the previous one. Before P6e-11 the
    second header was misread as a scalar key of the first map, so
    top_k_per_doc_by_domain was silently absent (KeyError) and its children
    polluted context_flank_chars_by_domain."""
    text = (
        "retrieval:\n"
        "  top_k_per_doc: 5\n"
        "  context_flank_chars_by_domain:\n"
        "    regulatory: 300\n"
        "  top_k_per_doc_by_domain:\n"
        "    insurance: 4\n"
        "    financial_contracts: 4\n"
    )
    cfg = _parse_simple_yaml(text)
    assert cfg["retrieval"]["top_k_per_doc"] == 5
    assert cfg["retrieval"]["context_flank_chars_by_domain"] == {"regulatory": 300}
    assert cfg["retrieval"]["top_k_per_doc_by_domain"] == {
        "insurance": 4, "financial_contracts": 4,
    }
    # the second nested map must not leak into the first one
    first = cfg["retrieval"]["context_flank_chars_by_domain"]
    assert "top_k_per_doc_by_domain" not in first
    assert "insurance" not in first
    assert "financial_contracts" not in first


def _q():
    from contracts import Question
    return Question(qid="q1", domain="insurance", text="t",
                    options={"A": "x"}, answer_format="text", doc_ids=["1"])
