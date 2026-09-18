"""Design-only checker smoke. Synthetic outputs are NOT Executor evidence."""
import copy
import json
from pathlib import Path
import tempfile

import check_packet as check


def main():
    check.authority()
    source = check.read(check.H60 / 'BOUNDARY_CAPABILITY_SUMMARY.json')['cases']
    texts = {(r['document_family'], r['page']): r['text']
             for r in check.rows(check.H60 / 'H60_RUNTIME_INPUTS.jsonl') if r['kind'] == 'page'}
    fixtures = []
    for r in source:
        gold = set(r['canonical_gold_pages'])
        item = {k: r[k] for k in ('qid', 'document_family')}
        for key, field in [('lexical_gold_reach', 'lexical_top5'), ('semantic_gold_reach', 'semantic_top5'),
                           ('baseline_gold_reach', 'baseline_rrf60_top5'), ('candidate_gold_reach', 'candidate_union_workspace')]:
            item[key] = not (gold - set(r[field]))
        for key, field in [('baseline', 'baseline_rrf60_top5'), ('candidate', 'candidate_union_workspace')]:
            item[key + '_pages'] = r[field]
            item[key + '_text_chars'] = sum(len(texts[(r['document_family'], p)]) for p in r[field])
        item.update(candidate_claim_available=False, verifier_executed=False,
                    fact_sufficiency='NOT_MEASURED', answer_correctness='NOT_MEASURED')
        fixtures.append(item)
    with tempfile.TemporaryDirectory(prefix='h61-design-') as directory:
        output = Path(directory)
        def write(data):
            (output / 'CASE_FUNNEL.jsonl').write_text('\n'.join(json.dumps(r) for r in data), encoding='utf-8')
        write(fixtures)
        check.funnel(output)
        print('synthetic_valid_funnel=PASS (not capability evidence)')
        mutations = []
        mutations.append(('missing_case', fixtures[:-1]))
        wrong_page = copy.deepcopy(fixtures)
        wrong_page[0]['candidate_pages'] = [999999]
        mutations.append(('wrong_page', wrong_page))
        false_answer = copy.deepcopy(fixtures)
        false_answer[0]['answer_correctness'] = 'CORRECT'
        mutations.append(('unmeasured_as_correct', false_answer))
        for name, values in mutations:
            write(values)
            try:
                check.funnel(output)
            except AssertionError:
                print(name + '=REJECTED_AS_EXPECTED')
            else:
                raise AssertionError('checker accepted ' + name)
    print('design_smoke=PASS; H61 execution remains NOT_RUN')


if __name__ == '__main__':
    main()
