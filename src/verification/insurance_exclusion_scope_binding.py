"""Product-specific insurance exclusion clause binding.

QID-agnostic: routes a product name to declared local documents, scans explicit
exclusion sections, and requires decisive condition terms to co-occur with an
exclusion relation. Weak keyword hits never qualify as canonical support.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib, re
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class ExclusionProposition:
    product: str
    event_terms: tuple[str, ...]
    facility_condition_terms: tuple[str, ...]
    relation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compact(value: Any) -> str:
    return re.sub(r"[\s，。；：、（）()《》\[\]【】‘’“”\"'<>]+", "", str(value or "")).lower()


def route_product_document(*, repo_root: Path, declared_doc_ids: Sequence[str], product_terms: Sequence[str]) -> dict[str, Any]:
    rows=[]
    for doc_id in map(str,declared_doc_ids):
        roots=[repo_root/'data/processed_mineru_retrieval/insurance'/doc_id,repo_root/'data/processed_mineru/insurance'/doc_id]
        paths=[]
        for root in roots:
            if root.is_file(): paths.append(root)
            elif root.is_dir(): paths.extend(sorted(root.rglob('*.md')))
        seen=set()
        for path in paths:
            if path in seen: continue
            seen.add(path)
            text=path.read_text(encoding='utf-8-sig',errors='replace')
            body=compact(text[:8000])
            hit=sum(1 for term in product_terms if compact(term) in body)
            if hit:
                rows.append({'doc_id':doc_id,'path':str(path.resolve()),'product_term_hits':hit,'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    if not rows:
        return {'matched':False,'reason':'PRODUCT_DOCUMENT_NOT_FOUND','candidates':[]}
    rows.sort(key=lambda r:(-r['product_term_hits'],r['doc_id'],r['path']))
    best=rows[0]
    return {'matched':True,'reason':'PRODUCT_TITLE_MATCH','selected':best,'candidates':rows}


def bind_exclusion(*, repo_root: Path, declared_doc_ids: Sequence[str], proposition: ExclusionProposition) -> dict[str, Any]:
    product_terms=tuple(term for term in re.split(r'[：:（）()\s]+',proposition.product) if len(term)>=2)
    route=route_product_document(repo_root=repo_root,declared_doc_ids=declared_doc_ids,product_terms=product_terms)
    if not route['matched']:
        return {'status':'UNRESOLVED','reason':route['reason'],'route':route,'proposition':proposition.to_dict(),'weak_keyword_only_rejected':True}
    selected_doc=route['selected']['doc_id']
    roots=[repo_root/'data/processed_mineru_retrieval/insurance'/selected_doc,repo_root/'data/processed_mineru/insurance'/selected_doc]
    rows=[]
    for root in roots:
        if not root.exists(): continue
        paths=[root] if root.is_file() else sorted(root.rglob('*.md'))
        for path in paths:
            text=path.read_text(encoding='utf-8-sig',errors='replace')
            lines=text.splitlines()
            for i,line in enumerate(lines):
                body=compact(line)
                decisive=[term for term in proposition.facility_condition_terms if compact(term) in body]
                event=[term for term in proposition.event_terms if compact(term) in body]
                if not decisive and not event: continue
                start=max(0,i-3); end=min(len(lines),i+4); span='\n'.join(lines[start:end])
                span_body=compact(span)
                exclusion=any(compact(t) in span_body for t in ('责任免除','不承担给付保险金的责任','不承担保险责任','除外责任'))
                decisive_all=all(compact(t) in span_body for t in proposition.facility_condition_terms)
                event_context=any(compact(t) in span_body for t in proposition.event_terms)
                rows.append({'doc_id':selected_doc,'path':str(path.resolve()),'line_start':start+1,'line_end':end,'span':span,'span_sha256':hashlib.sha256(span.encode()).hexdigest(),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'exclusion_marker':exclusion,'facility_condition_complete':decisive_all,'event_context_present':event_context,'matched_facility_terms':decisive,'matched_event_terms':event})
    strong=[r for r in rows if r['exclusion_marker'] and r['facility_condition_complete']]
    if strong:
        strong.sort(key=lambda r:(0 if r['event_context_present'] else 1,r['path'],r['line_start']))
        return {'status':'SUPPORTED','reason':'DIRECT_PRODUCT_EXCLUSION_CLAUSE','route':route,'proposition':proposition.to_dict(),'selected_source':strong[0],'all_strong_sources':strong,'weak_keyword_only_rejected':True,'complete_exclusion_scope_required_for_absence':True}
    return {'status':'UNRESOLVED','reason':'NO_DIRECT_COMPLETE_EXCLUSION_BINDING','route':route,'proposition':proposition.to_dict(),'retrieval_rows':rows,'weak_keyword_only_rejected':True,'complete_exclusion_scope_required_for_absence':True}
