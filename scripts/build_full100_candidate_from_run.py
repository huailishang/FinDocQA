#!/usr/bin/env python3
"""Build a complete candidate using verified answers, model arbitration and baseline fallback."""
from __future__ import annotations
import argparse,csv,hashlib,json,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parent.parent; SRC=ROOT/'src'
if str(SRC) not in sys.path: sys.path.insert(0,str(SRC))
from data.loader import JsonQuestionLoader
from utils.llm_client import OpenAICompatibleClient
from verification.answer_arbitration import decide_answer
from verification.replacement_qualification import qualification_from_record


def canonical_answer(value: Any) -> str:
    return ''.join(ch for ch in str(value or '').upper() if 'A' <= ch <= 'Z')


def accepted_only_decision(
    qid: str,
    baseline_answer: str,
    record: dict[str, Any] | None,
    *,
    answer_format: str | None = None,
    option_texts: dict[str, str] | None = None,
    independent_oracle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not record:
        return {
            "qid": qid,
            "answer": baseline_answer,
            "source": "inherited_baseline_not_accepted",
            "reason": "run record missing",
            "model": "",
            "raw_output": "",
            "new_final_state": "",
            "new_answer": "",
            "replacement_qualification": None,
        }
    qualification = qualification_from_record(
        qid=qid,
        baseline_answer=baseline_answer,
        record=record,
        answer_format=answer_format,
        option_texts=option_texts,
        independent_oracle=independent_oracle,
    )
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    final_state = str(metadata.get("final_state") or "").strip().lower()
    new_answer = canonical_answer(qualification.get("proposed_answer"))
    accepted = bool(qualification["replacement_allowed"])
    return {
        "qid": qid,
        "answer": canonical_answer(qualification.get("effective_answer")) if accepted else baseline_answer,
        "source": "new_answer_accepted" if accepted else "inherited_baseline_not_accepted",
        "reason": "canonical replacement qualification passed" if accepted else ";".join(qualification["reasons"]),
        "model": "",
        "raw_output": "",
        "new_final_state": final_state,
        "new_answer": new_answer,
        "replacement_qualification": qualification,
    }

def read_csv(path:Path):
    with path.open('r',encoding='utf-8-sig',newline='') as h:
        r=csv.DictReader(h); return list(r.fieldnames or []),list(r)
def sha256(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument('--baseline',required=True,type=Path); p.add_argument('--debug-results',required=True,type=Path)
    p.add_argument('--questions-dir',required=True,type=Path); p.add_argument('--output',required=True,type=Path)
    p.add_argument('--manifest',type=Path); p.add_argument('--config',type=Path,default=ROOT/'config.yaml'); p.add_argument('--no-arbiter',action='store_true'); p.add_argument('--accepted-only',action='store_true')
    a=p.parse_args()
    fields,base_rows=read_csv(a.baseline); business=[r for r in base_rows if r['qid'].strip().lower()!='summary']
    base={r['qid'].strip():r for r in business}
    if len(base)!=len(business): raise SystemExit('duplicate baseline qids')
    records=json.loads(a.debug_results.read_text(encoding='utf-8')); recs={str(r.get('qid') or '').strip():r for r in records if str(r.get('qid') or '').strip()}
    questions={q.qid:q for q in JsonQuestionLoader(a.questions_dir).load()}
    config={}
    if a.config.exists():
        try:
            import yaml; config=yaml.safe_load(a.config.read_text(encoding='utf-8')) or {}
        except Exception: config={}
    client=None if (a.no_arbiter or a.accepted_only) else OpenAICompatibleClient.from_env(config)
    output=[]; decisions=[]; changed=[]
    for row0 in base_rows:
        qid=row0['qid'].strip()
        if qid.lower()=='summary': output.append(dict(row0)); continue
        baseline_answer=canonical_answer(row0['answer'])
        if a.accepted_only:
            question = questions.get(qid)
            d=accepted_only_decision(
                qid, baseline_answer, recs.get(qid),
                answer_format=getattr(question, 'answer_format', None),
                option_texts=getattr(question, 'options', None),
            )
        elif qid not in questions or qid not in recs:
            d={'qid':qid,'answer':baseline_answer,'source':'inherited_baseline_unresolvable','reason':'question or run record missing','model':'','raw_output':''}
        else:d=decide_answer(questions[qid],baseline_answer,recs[qid],client).to_dict()
        row=dict(row0); row['answer']=d['answer']; output.append(row); decisions.append(d)
        if row['answer']!=canonical_answer(row0['answer']):changed.append({'qid':qid,'baseline':canonical_answer(row0['answer']),'candidate':row['answer'],'source':d['source']})
    qids=[r['qid'].strip() for r in output if r['qid'].strip().lower()!='summary']
    if len(qids)!=len(set(qids)) or set(qids)!=set(base): raise SystemExit('candidate qid set differs from baseline')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=fields); w.writeheader(); w.writerows(output)
    counts={}
    for d in decisions:counts[d['source']]=counts.get(d['source'],0)+1
    manifest={'baseline_file':str(a.baseline),'baseline':str(a.baseline),'baseline_sha256':sha256(a.baseline),'run_debug_results':str(a.debug_results),'debug_results':str(a.debug_results),'candidate_file':str(a.output),'candidate':str(a.output),'candidate_sha256':sha256(a.output),'business_qid_count':len(qids),'unique_business_qid_count':len(set(qids)),'complete_qid_set':set(qids)==set(base),'accepted_new_answer_count':counts.get('new_answer_accepted',0),'fallback_to_baseline_count':counts.get('inherited_baseline_not_accepted',0),'decision_source_counts':counts,'source_counts':counts,'changed_answer_count':len(changed),'changed_answers':changed,'fallback_qids':[d['qid'] for d in decisions if d.get('source')=='inherited_baseline_not_accepted'],'rejected_new_answer_qids':[d['qid'] for d in decisions if d.get('source')=='inherited_baseline_not_accepted' and d.get('new_answer')],'submission_authorized':False,'leaderboard_upload_authorized':False,'accepted_only':bool(a.accepted_only),'decisions':decisions}
    mp=a.manifest or a.output.with_suffix('.manifest.json'); mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ('business_qid_count','unique_business_qid_count','complete_qid_set','decision_source_counts','changed_answer_count')},ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
