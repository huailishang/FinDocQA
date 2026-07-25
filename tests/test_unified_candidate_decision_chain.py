from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from contracts import Question, QuestionAnswerContract
from evaluation.unified_candidate_decision_chain import (
    assign_evidence_grade,
    decompose_option,
    extract_question_predicate,
    predicate_alignment,
    decide_question,
)


def _q(text: str, options: dict[str, str], fmt: str = 'mcq') -> Question:
    contract = QuestionAnswerContract(
        schema_version='test', qid='q', raw_type='单选题' if fmt == 'mcq' else '多选题',
        raw_answer_format=fmt, answer_format=fmt, allowed_labels=tuple(options),
        min_selected=1, max_selected=1 if fmt == 'mcq' else len(options),
        canonical_order=tuple(options), source_of_truth='test',
    )
    return Question(qid='q', domain='financial_contracts', text=text, options=options, answer_format=fmt, doc_ids=('d1','d2'), answer_contract=contract)


def test_question_predicate_alignment_is_generic_and_not_label_hardcoded():
    p = extract_question_predicate('结合两份文档，以下哪项准确反映了其中关于违约赔偿或发行规模的细节？')
    assert set(p.restricted_terms) == {'违约赔偿', '发行规模'}
    assert predicate_alignment(p, '违约金计算公式包含150%的惩罚系数').status == 'PASS'
    assert predicate_alignment(p, '本期发行金额不超过5亿元').status == 'PASS'
    assert predicate_alignment(p, '发行人是某投资集团').status == 'FAIL'
    p2 = extract_question_predicate('以下哪项准确反映了其中关于发行人身份的细节？')
    assert predicate_alignment(p2, '违约金计算公式包含150%的惩罚系数').status == 'FAIL'
    assert predicate_alignment(p2, '发行人是某投资集团').status == 'PASS'


def test_compound_claim_decomposition_supports_and_or_negation_conditions():
    atoms = decompose_option('A成立且B不成立，或者在2025年条件下C超过10%')
    assert len(atoms) >= 3
    assert any(atom.polarity == 'negative' for atom in atoms)
    assert any(atom.periods == ('2025',) for atom in atoms)


def test_evidence_grade_dynamic():
    src = [{'source_role':'direct_fact'}]
    assert assign_evidence_grade(status='supported', sources=src) == 'DIRECT_CLAUSE_SUPPORT'
    assert assign_evidence_grade(status='contradicted', sources=src) == 'DIRECT_CLAUSE_CONTRADICTION'
    assert assign_evidence_grade(status='supported', sources=src, formula='x+y', variables={'x':1,'y':2}) == 'DERIVED_TOOL_RESULT'
    assert assign_evidence_grade(status='unresolved', sources=[]) == 'PARTIAL_RETRIEVAL_ABSENCE'


def test_full_option_and_answer_contract_before_candidate():
    q = _q('以下哪项准确反映了其中关于违约赔偿或发行规模的细节？', {
        'A':'发行规模为10亿元','B':'违约金包含150%','C':'主体评级AAA','D':'发行人为某公司'
    })
    result = decide_question(q, {
        'A':{'status':'contradicted','sources':[{'source_role':'direct_fact'}]},
        'B':{'status':'supported','sources':[{'source_role':'direct_fact'}]},
        'C':{'status':'contradicted','sources':[{'source_role':'direct_fact'}]},
        'D':{'status':'unresolved','sources':[]},
    })
    assert result['options']['D']['predicate_alignment']['status'] == 'FAIL'
    assert result['all_options_closed'] is True
    assert result['answer_contract_closed'] is True
    assert result['recomputed_answer'] == 'B'
