"""Evaluator-owned offline checks; semantic acceptance additionally needs REVIEW_RUBRIC."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

TASK = Path(__file__).resolve().parent
ROOT = TASK.parents[2]
BASE = TASK.parent
H60 = BASE / 'FDQA-B03-FRESH12-CLOUDFLARE-BOUNDED-WORKSPACE-CAPABILITY-V1'
H50 = BASE / 'FDQA-B03-CROSS-COHORT-LANE-ARBITRATION-DIAGNOSTIC-V1'
OUTPUTS = ('CASE_FUNNEL.jsonl', 'RESIDUAL_CASES.jsonl', 'DOWNSTREAM_AUDIT.json', 'DECISION.json')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(files):
    assert files
    for name, expected in files.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT), name
        assert path.is_file() and digest(path) == expected, name


def authority():
    manifest = read(TASK / 'DESIGN_INPUT_MANIFEST.json')
    hashes(manifest['files'])
    hashes(manifest['protected_files'])
    s = read(H60 / 'BOUNDARY_CAPABILITY_SUMMARY.json')
    assert (s['case_count'], s['baseline_gold_reach_cases'], s['candidate_gold_reach_cases'],
            s['recovered_cases'], s['protected_loss'], s['qualified']) == (12, 7, 9, 2, 0, False)
    gate = read(H60 / 'evidence/l3/L3-GATE.json')
    assert gate['result'] == 'PASS' and gate['checks_total'] == 9 and gate['mandatory_failures'] == 0
    assert 'capability_decision = NOT_QUALIFIED' in (H60 / 'REVIEW.md').read_text(encoding='utf-8')
    assert len(rows(H50 / 'LANE_OUTCOME_MATRIX.jsonl')) == 46
    assert len([r for r in rows(H50 / 'LANE_OUTCOME_MATRIX.jsonl') if r['lane_outcome_class'] == 'BOTH_MISS']) == 18


def funnel(directory):
    source = read(H60 / 'BOUNDARY_CAPABILITY_SUMMARY.json')['cases']
    baseline = {r['qid']: r for r in rows(H60 / 'BASELINE_RRF60_RESULTS.jsonl')}
    candidate = {r['qid']: r for r in rows(H60 / 'CANDIDATE_WORKSPACE_RESULTS.jsonl')}
    actual = rows(directory / 'CASE_FUNNEL.jsonl')
    assert len(actual) == 12 and len({r['qid'] for r in actual}) == 12
    actual = {r['qid']: r for r in actual}
    assert set(actual) == {r['qid'] for r in source}
    pages = {(r['document_family'], r['page']): r['text'] for r in rows(H60 / 'H60_RUNTIME_INPUTS.jsonl') if r['kind'] == 'page'}
    for old in source:
        assert old['baseline_rrf60_top5'] == baseline[old['qid']]['verification_pages']
        assert old['candidate_union_workspace'] == candidate[old['qid']]['verification_workspace_pages']
        assert old['lexical_top5'] == baseline[old['qid']]['lexical_top5']
        assert old['semantic_top5'] == baseline[old['qid']]['semantic_top5']
        new = actual[old['qid']]
        assert new['document_family'] == old['document_family']
        gold = set(old['canonical_gold_pages'])
        assert gold
        for field, page_key in [('lexical_gold_reach', 'lexical_top5'), ('semantic_gold_reach', 'semantic_top5'),
                                ('baseline_gold_reach', 'baseline_rrf60_top5'), ('candidate_gold_reach', 'candidate_union_workspace')]:
            assert new[field] is gold.issubset(old[page_key]), (old['qid'], field)
        for prefix, page_key in [('baseline', 'baseline_rrf60_top5'), ('candidate', 'candidate_union_workspace')]:
            expected = old[page_key]
            assert new[prefix + '_pages'] == expected
            assert len(expected) == len(set(expected))
            count = sum(len(pages[(old['document_family'], p)]) for p in expected)
            assert new[prefix + '_text_chars'] == count
        assert new['candidate_claim_available'] is False and new['verifier_executed'] is False
        assert new['fact_sufficiency'] == new['answer_correctness'] == 'NOT_MEASURED'


def expected_residual():
    a = [('H60', r['qid'], r['document_family'], (H60 / 'BOUNDARY_CAPABILITY_SUMMARY.json').relative_to(ROOT).as_posix())
         for r in read(H60 / 'BOUNDARY_CAPABILITY_SUMMARY.json')['cases'] if not r['candidate_gold_reach']]
    b = [('H50', r['question_id'], r['doc_name'], (H50 / 'LANE_OUTCOME_MATRIX.jsonl').relative_to(ROOT).as_posix())
         for r in rows(H50 / 'LANE_OUTCOME_MATRIX.jsonl') if r['lane_outcome_class'] == 'BOTH_MISS']
    assert len(a) == 3 and len(b) == 18
    return set(a + b)


def residual(directory):
    actual = rows(directory / 'RESIDUAL_CASES.jsonl')
    assert len(actual) == 21
    assert {(r['cohort'], r['qid'], r['document_family'], r['source_ref']) for r in actual} == expected_residual()
    assert all(r['diagnostic_label'] and r['evidence_refs'] for r in actual)
    mechanism = read(TASK / 'MECHANISM_FREEZE.json')
    assert mechanism['max_candidates'] == 1
    assert {'mechanism', 'runtime_features', 'trigger', 'action', 'parameters'} <= mechanism.keys()
    if mechanism['mechanism'] is not None:
        assert isinstance(mechanism['mechanism'], dict)
        assert mechanism['runtime_features'] and mechanism['trigger'] and mechanism['action']


def downstream(directory):
    a = read(directory / 'DOWNSTREAM_AUDIT.json')
    assert a['h60_status_origin'] == 'STATIC_INPUT_APPLICABILITY'
    assert a['observed_verifier_executions'] == a['observed_answer_evaluations'] == 0
    assert a['runtime_claim_source'] is None
    assert a['edges'] and a['missing_contracts'] and a['scope_and_lineage_findings']
    for edge in a['edges']:
        assert all(edge[k] for k in ('producer', 'consumer', 'required_fields', 'evidence_refs'))


def decision(directory):
    d = read(directory / 'DECISION.json')
    assert d['retrieval_direction'] in {'CANDIDATE_DIRECTION', 'SIGNAL_ONLY', 'NO_SINGLE_DIRECTION'}
    assert d['next_action'] in {'DOWNSTREAM_CONTRACT_FIRST', 'RETRIEVAL_PROBE_FIRST', 'HOLD_FOR_MISSING_EVIDENCE'}
    assert d['historical_verdicts_unchanged'] is True
    assert d['default_enable_authorized'] is False and d['product_readiness_proven'] is False
    assert d['end_to_end_improvement'] == 'NOT_MEASURED'
    assert d['reason'] and d['evidence_refs']
    assert {'B-03', 'B-05', 'B-06', 'B-07'} <= d['alternatives'].keys()
    supported = d['supporting_cases']
    valid = {(c, q, f) for c, q, f, _ in expected_residual()}
    assert all((r['cohort'], r['qid'], r['document_family']) in valid for r in supported)
    if d['retrieval_direction'] == 'CANDIDATE_DIRECTION':
        assert read(TASK / 'MECHANISM_FREEZE.json')['mechanism'] is not None
        assert len({r['qid'] for r in supported}) >= 3
        assert len({r['document_family'] for r in supported}) >= 2
        assert d['historical_counterevidence'] and d['fresh_failure_condition']
    if d['next_action'] == 'RETRIEVAL_PROBE_FIRST':
        assert d['retrieval_direction'] == 'CANDIDATE_DIRECTION'
    draft = (TASK / 'NEXT_TASK_DRAFT.md').read_text(encoding='utf-8')
    assert 'DRAFT_ONLY' in draft and d['next_action'] in draft
    assert (TASK / 'DEPLOYMENT_PROFILES.md').stat().st_size > 0


def replay(mode):
    # Frozen CLI, separate output paths: Executor artifacts are never overwritten by L3.
    out = TASK / 'evidence' / ('l3-replay' if mode == 'evaluator' else 'replay2')
    subprocess.run([sys.executable, str(TASK / 'run_analysis.py'), '--output-dir', str(out)], cwd=ROOT, check=True, timeout=300)
    for name in OUTPUTS:
        assert (TASK / name).read_bytes() == (out / name).read_bytes(), name
    funnel(out)
    residual(out)
    downstream(out)
    decision(out)


def scope_report(mode):
    m = read(TASK / 'INPUT_MANIFEST.json')
    hashes(m['files'])
    assert set(read(TASK / 'DESIGN_INPUT_MANIFEST.json')['files']) <= set(m['files'])
    assert m['external_api_calls'] == m['model_calls'] == m['dependency_install_attempts'] == 0
    status = subprocess.check_output(['git', 'status', '--porcelain', '--', 'src', 'config', 'tests'], cwd=ROOT, text=True)
    assert not status.strip(), status
    report = (TASK / 'REPORT.md').read_text(encoding='utf-8')
    for text in ('Impact comparison', 'Project-impact verdict', 'NOT_APPLICABLE', 'NOT_MEASURED'):
        assert text in report, text
    for name in ('self-check-round1.json', 'self-check-round2.json'):
        assert read(TASK / 'evidence' / name)
    cur = (BASE / 'state/CURRENT.md').read_text(encoding='utf-8')
    assert f'task_id: {TASK.name}' in cur and 'authorization_api_call: false' in cur
    state, role = ('READY_FOR_REVIEW', 'Evaluator') if mode == 'evaluator' else ('EXECUTING', 'Executor')
    assert f'state: {state}' in cur and f'current_role: {role}' in cur


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', choices=['authority', 'funnel', 'residual', 'downstream', 'decision', 'replay', 'scope-report'], required=True)
    parser.add_argument('--mode', choices=['executor', 'evaluator'], default='executor')
    args = parser.parse_args()
    authority()
    if args.check in {'funnel', 'residual', 'downstream', 'decision'}:
        globals()[args.check](TASK)
    elif args.check == 'replay':
        replay(args.mode)
    elif args.check == 'scope-report':
        scope_report(args.mode)
    print(args.check + '=PASS; semantic review remains evaluator-owned')


if __name__ == '__main__':
    main()
