from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path: sys.path.insert(0,str(ROOT/'src'))

from verification.insurance_exclusion_scope_binding import ExclusionProposition, bind_exclusion


def proposition():
    return ExclusionProposition(
        product='平安产险预防接种意外伤害保险',
        event_terms=('接种疫苗','异常反应'),
        facility_condition_terms=('不具有','预防接种条件','单位','接种疫苗'),
        relation='EXCLUSION',
    )


def test_product_specific_route_selects_vaccination_policy_doc():
    row=bind_exclusion(repo_root=ROOT,declared_doc_ids=['1','4','7','13'],proposition=proposition())
    assert row['route']['matched'] is True
    assert row['route']['selected']['doc_id']=='7'


def test_direct_exclusion_clause_supports_complete_condition():
    row=bind_exclusion(repo_root=ROOT,declared_doc_ids=['1','4','7','13'],proposition=proposition())
    assert row['status']=='SUPPORTED'
    assert row['reason']=='DIRECT_PRODUCT_EXCLUSION_CLAUSE'
    span=row['selected_source']['span']
    assert '责任' in span and '不承担给付保险金的责任' in span
    assert '不具有卫生主管部门要求的预防接种条件的单位接种疫苗' in ''.join(span.split())


def test_wrong_product_scope_does_not_reuse_other_product_clause():
    row=bind_exclusion(repo_root=ROOT,declared_doc_ids=['1','4'],proposition=proposition())
    assert row['status']=='UNRESOLVED'
    assert row['weak_keyword_only_rejected'] is True
