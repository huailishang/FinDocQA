"""A2 independent metric audit; historical outputs remain immutable."""
import argparse
import json
import check_packet as old


def authority():
    old.authority()
    snap = old.read(old.TASK / 'A2_REVIEW_SNAPSHOT.json')
    old.hashes(snap['files'])


def metric_audit():
    old.funnel(old.TASK)
    source = old.read(old.H60 / 'BOUNDARY_CAPABILITY_SUMMARY.json')['cases']
    base = {r['qid']: r['verification_pages'] for r in old.rows(old.H60 / 'BASELINE_RRF60_RESULTS.jsonl')}
    cand = {r['qid']: r['verification_workspace_pages'] for r in old.rows(old.H60 / 'CANDIDATE_WORKSPACE_RESULTS.jsonl')}
    result = {}
    for label, predicate in [('any_gold', lambda g, p: bool(g & p)), ('all_gold', lambda g, p: not (g - p))]:
        pairs = []
        for r in source:
            g = set(r['canonical_gold_pages'])
            assert g
            pairs.append((predicate(g, set(base[r['qid']])), predicate(g, set(cand[r['qid']]))))
        result[label] = dict(baseline=sum(a for a, b in pairs), candidate=sum(b for a, b in pairs),
                             recovered=sum(b and not a for a, b in pairs), lost=sum(a and not b for a, b in pairs))
    assert result['any_gold'] == dict(baseline=7, candidate=9, recovered=2, lost=0)
    assert result['all_gold'] == dict(baseline=7, candidate=7, recovered=0, lost=0)
    partial = {r['qid']: sorted(set(r['canonical_gold_pages']) - set(cand[r['qid']])) for r in source
               if set(r['canonical_gold_pages']) & set(cand[r['qid']]) and not set(r['canonical_gold_pages']) <= set(cand[r['qid']])}
    assert partial == {'financebench_id_02024': [63], 'financebench_id_03856': [57]}
    result['partial_missing_pages'] = partial
    f = old.rows(old.TASK / 'CASE_FUNNEL.jsonl')
    result['page_counts'] = [sum(len(r[k + '_pages']) for r in f) for k in ('baseline', 'candidate')]
    result['text_chars'] = [sum(r[k + '_text_chars'] for r in f) for k in ('baseline', 'candidate')]
    assert result['page_counts'] == [60, 104]
    assert result['text_chars'] == [250530, 434584]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def revised_decision():
    old.decision(old.TASK)
    previous = old.read(old.TASK / 'DECISION.json')
    revised = old.read(old.TASK / 'DECISION_A2.json')
    assert revised['next_action'] == previous['next_action']
    for key in ('default_enable_authorized', 'product_readiness_proven', 'end_to_end_improvement', 'retrieval_direction'):
        assert revised[key] == previous[key]
    assert revised['future_thresholds_status'] == 'TO_BE_FROZEN_PER_EXPERIMENT'
    assert revised['fresh_failure_condition'] != previous['fresh_failure_condition']
    draft = (old.TASK / 'NEXT_TASK_DRAFT_A2.md').read_text(encoding='utf-8')
    assert 'DRAFT_ONLY' in draft and revised['next_action'] in draft
    assert 'NOT_READY_FOR_REAL_EXECUTION' in draft


def preserved_replay(mode):
    if mode == 'evaluator':
        old.replay(mode)
    else:
        gate = old.read(old.TASK / 'evidence/L2-GATE.json')
        assert gate['result'] == 'PASS' and gate['checks_total'] == 7
        for name in old.OUTPUTS:
            assert (old.TASK / name).read_bytes() == (old.TASK / 'evidence/replay2' / name).read_bytes()
        print('unchanged_A1_replay_evidence=REUSED; no new analysis generation')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--check', required=True, choices=['authority', 'funnel', 'residual', 'downstream', 'decision', 'replay', 'scope-report'])
    p.add_argument('--mode', default='executor', choices=['executor', 'evaluator'])
    a = p.parse_args()
    authority()
    if a.check == 'funnel':
        metric_audit()
    elif a.check in {'residual', 'downstream'}:
        getattr(old, a.check)(old.TASK)
    elif a.check == 'decision':
        revised_decision()
    elif a.check == 'replay':
        preserved_replay(a.mode)
    elif a.check == 'scope-report':
        old.scope_report(a.mode)
        report = (old.TASK / 'REPORT_A2.md').read_text(encoding='utf-8')
        assert all(s in report for s in ('Impact comparison', 'Project-impact verdict', 'NOT_APPLICABLE', 'NOT_MEASURED', 'any-Gold', 'all-Gold'))
    print(a.check + '=PASS; A2 semantic review still required')


if __name__ == '__main__':
    main()
