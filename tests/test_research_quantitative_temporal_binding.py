from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path: sys.path.insert(0,str(ROOT/'src'))

from verification.research_quantitative_temporal_binding import bind_claim_to_texts, parse_proposition
from verification.strict_atom_provenance import audit_strict_atom_provenance


def test_direct_growth_statement_does_not_require_derived_formula():
    claim='2022 年居民可支配收入增速降至 5%'
    source='居民可支配收入增长动能减弱，2022 年增速降至 5%，2023-2025 年从 6.33%降至 4.99%。'
    row=bind_claim_to_texts(claim,[source])
    assert row['status']=='SUPPORTED'
    assert row['reason']=='DIRECT_GROWTH_STATEMENT'
    assert row['growth_mode']=='DIRECT_GROWTH_STATEMENT'


def test_growth_without_direct_rate_is_classified_as_derived_required():
    claim='2022 年居民可支配收入增速降至 5%'
    source='居民可支配收入2021年为100元，2022年为105元。'
    row=bind_claim_to_texts(claim,[source])
    assert row['status']=='UNRESOLVED'
    assert row['growth_mode']=='DERIVED_GROWTH_REQUIRED'


def test_realized_claim_conflicts_with_forecast_source_even_when_number_matches():
    claim='2025 年冰雪装备市场规模已达 846.6 亿元'
    source='预计2025年冰雪装备市场规模将达到846.6亿元。'
    row=bind_claim_to_texts(claim,[source])
    assert row['status']=='CONTRADICTED'
    assert row['reason']=='TEMPORAL_STATUS_CONFLICT'
    assert row['status_relation']=='TEMPORAL_STATUS_CONFLICT'


def test_numeric_value_conflict_detects_tenfold_error():
    claim='2025 年冰雪装备市场规模已达 8466 亿元'
    source='在此背景下，2025年冰雪装备市场规模已达846.6亿元。'
    row=bind_claim_to_texts(claim,[source])
    assert row['status']=='CONTRADICTED'
    assert row['reason']=='NUMERIC_VALUE_OR_UNIT_CONFLICT'


def test_transition_direction_is_semantically_decisive():
    claim='2025 年居民收入增速与人均名义 GDP 增速剪刀差由负转正'
    source='同时 2025 年居民收入增速与人均名义 GDP 增速剪刀差由正转负。'
    row=bind_claim_to_texts(claim,[source])
    assert row['status']=='CONTRADICTED'
    assert row['reason']=='TRANSITION_DIRECTION_CONFLICT'
    assert row['claim']['transition_direction']=='NEG_TO_POS'
    assert row['best']['source']['transition_direction']=='POS_TO_NEG'


def test_generic_growth_word_does_not_match_unrelated_metric():
    claim='2025 年居民收入增速与人均名义 GDP 增速剪刀差由负转正'
    unrelated='截至2025年12月，服务零售增速持续领跑商品零售，累计同比高出1.7pcts。'
    row=bind_claim_to_texts(claim,[unrelated])
    assert row['status']=='UNRESOLVED'


def test_disposable_income_does_not_create_false_may_modal():
    claim='2022 年居民可支配收入增速降至 5%'
    source='居民可支配收入增长动能减弱，2022 年增速降至 5%。'
    row=audit_strict_atom_provenance(option_text=claim,source_texts=[source],fact_status='SUPPORTED')
    assert 'MAY' not in row['claim_atoms']['modal_polarity']
    assert row['promotion_allowed'] is True
