"""Automatic arbitration between baseline and current-run answers."""
from __future__ import annotations
import random, re
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Optional
from contracts import Question
from utils.llm_client import OpenAICompatibleClient, LLMClientUnavailable
from verification.dual_lineage import accepted_final_state

@dataclass(frozen=True)
class ArbitrationDecision:
    qid: str
    answer: str
    source: str
    reason: str
    model: str = ""
    raw_output: str = ""
    def to_dict(self) -> dict[str, Any]: return asdict(self)

def canonical_answer(answer: str, answer_format: str) -> str:
    value=(answer or "").strip().upper()
    if answer_format=="multi": return "".join(sorted(set(ch for ch in value if ch in "ABCD")))
    if answer_format=="tf": return value if value in {"A","B","T","F"} else ""
    return value if value in {"A","B","C","D"} else ""

def duplicate_option_decision(question: Question, attempted_answer: str) -> Optional[ArbitrationDecision]:
    attempted=canonical_answer(attempted_answer,question.answer_format)
    if not attempted or question.answer_format!="mcq": return None
    text=str(question.options.get(attempted[0],"")).strip()
    matches=sorted(k for k,v in question.options.items() if str(v).strip()==text)
    if not text or len(matches)<2: return None
    return ArbitrationDecision(question.qid,matches[0],"deterministic_duplicate_mapping",f"duplicate option text across {','.join(matches)}; choose first")

def _render_evidence(record: Mapping[str,Any],limit:int=18000)->str:
    meta=dict(record.get("metadata") or {}); chunks=[]
    ev=meta.get("evidence_by_doc") or {}
    if isinstance(ev,Mapping):
        for doc_id,value in ev.items(): chunks.append(f"[DOC {doc_id}]\n{value}")
    raw=str(meta.get("solver_raw_output") or (record.get("solver_result") or {}).get("raw_output") or "")
    if raw: chunks.append("[CURRENT MODEL REASONING]\n"+raw)
    return "\n\n".join(chunks)[:limit]

def _parse_final_answer(text:str,answer_format:str)->str:
    for pattern in (r"最终答案\s*[:：]\s*([A-D]+|T|F)",r"FINAL_ANSWER\s*[:：]\s*([A-D]+|T|F)"):
        matches=re.findall(pattern,text or "",flags=re.I)
        if matches:
            answer=canonical_answer(matches[-1],answer_format)
            if answer:return answer
    return ""

def arbitrate_with_model(question:Question,baseline_answer:str,current_answer:str,record:Mapping[str,Any],client:OpenAICompatibleClient)->ArbitrationDecision:
    baseline=canonical_answer(baseline_answer,question.answer_format); current=canonical_answer(current_answer,question.answer_format)
    candidates=[("候选1",baseline),("候选2",current)]; random.Random(f"20260704:{question.qid}").shuffle(candidates)
    options="\n".join(f"{k}. {v}" for k,v in question.options.items())
    candidate_text="\n".join(f"{name}：{answer or '空'}" for name,answer in candidates)
    meta=dict(record.get("metadata") or {})
    prompt=f"""你是金融问答最终裁决器。历史候选不是标准答案，不得偏向旧答案。仅根据题目、选项、证据和可复核计算逐项判断。若关键证据确实缺失、计算变量缺失或题目无法唯一映射，输出 UNRESOLVABLE。\n\n题号：{question.qid}\n题型：{question.answer_format}\n题目：{question.text}\n选项：\n{options}\n\n匿名候选：\n{candidate_text}\n\n阻断原因：{meta.get('blocking_reasons') or meta.get('blocking_reason') or record.get('error')}\n本轮尝试答案：{current or '空'}\n证据与本轮推理：\n{_render_evidence(record)}\n\n最后一行只能是“最终答案：<合法答案>”或“最终答案：UNRESOLVABLE”。"""
    result=client.chat([{"role":"user","content":prompt}],max_tokens=1800)
    if "UNRESOLVABLE" in result.content.upper():
        return ArbitrationDecision(question.qid,baseline,"inherited_baseline_unresolvable","arbiter declared unresolvable",result.model,result.content)
    answer=_parse_final_answer(result.content,question.answer_format)
    if not answer:
        return ArbitrationDecision(question.qid,baseline,"inherited_baseline_unresolvable","invalid arbiter output",result.model,result.content)
    return ArbitrationDecision(question.qid,answer,"judge_model_selected","arbiter independently re-judged from evidence",result.model,result.content)

def decide_answer(question:Question,baseline_answer:str,record:Mapping[str,Any],client:Optional[OpenAICompatibleClient])->ArbitrationDecision:
    meta=dict(record.get("metadata") or {}); state=str(meta.get("final_state") or "accepted"); error=record.get("error")
    current=canonical_answer(str(record.get("answer") or meta.get("attempted_answer") or ""),question.answer_format)
    baseline=canonical_answer(baseline_answer,question.answer_format)
    if accepted_final_state(state) and not error and current:
        return ArbitrationDecision(question.qid,current,"new_answer_verified","production checks accepted current answer")
    duplicate=duplicate_option_decision(question,current)
    if duplicate:return duplicate
    evidence_present=bool(meta.get("evidence_count") or meta.get("evidence_by_doc") or meta.get("solver_raw_output"))
    reasons=str(meta.get("blocking_reasons") or meta.get("blocking_reason") or error or "")
    hard=any(token in reasons for token in (
        "source_evidence_absent", "llm_error",
        "calculation_incomplete", "truncation_risk",
        "no_unique_option_match", "unused_material_variables",
    ))
    if client is not None and evidence_present and not hard:
        try:return arbitrate_with_model(question,baseline,current,record,client)
        except LLMClientUnavailable as exc:return ArbitrationDecision(question.qid,baseline,"inherited_baseline_unresolvable",f"arbiter failed: {exc}")
    return ArbitrationDecision(question.qid,baseline,"inherited_baseline_unresolvable","hard evidence/runtime failure" if hard else "no arbiter or usable evidence")
