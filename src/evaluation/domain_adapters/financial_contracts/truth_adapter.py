"""Financial-contract truth adapter with deterministic cross-document binding.

The adapter is intentionally QID-agnostic.  Claims are derived from question
option text, then bound to canonical evidence candidates per required document.
Cross-document universal and pairwise relations are aggregated only after each
document-local fact has been independently extracted.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question
from evaluation.domain_adapters.truth import (
    TruthOptionResult,
    TruthQuestionResult,
    TruthSource,
    candidates_for_docs,
    compact,
    provenance_for_fragments,
    result_from_options,
)

CAPABILITY = "financial_contracts:cross_document_field_relation_v2"


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _doc_refs(text: str, declared: Sequence[str]) -> tuple[str, ...]:
    docs = tuple(str(doc) for doc in declared)
    explicit = re.findall(r"(?:fc_)?text[_-]?0*(\d+)", text, re.I)
    if explicit:
        wanted = {f"text{int(value):02d}" for value in explicit}
        selected = tuple(doc for doc in docs if doc in wanted)
        if selected:
            return selected
    if docs:
        if any(token in text for token in ("第一份", "首份", "文档一")):
            return (docs[0],)
        if len(docs) >= 2 and any(token in text for token in ("第二份", "后一份", "文档二")):
            return (docs[1],)
    return docs


def _field(text: str) -> str:
    value = compact(text)
    if all(compact(token) in value for token in ("转股后", "具体", "资产负债率")):
        return "post_conversion_debt_ratio_forecast"
    any_rules = (
        (("受托管理人",), "bond_trustee"),
        (("发行人名称", "发行主体"), "issuer_name"),
        (("证券公司",), "issuer_category"),
        (("主体信用评级", "主体信用等级", "主体评级"), "subject_rating"),
        (("发行金额上限",), "issue_scale_cap"),
        (("股票代码", "证券代码", "标注的代码"), "stock_or_security_code"),
        (("证券简称", "股票简称"), "security_short_name"),
        (("资产负债率",), "debt_asset_ratio_values"),
        (("违约情形", "违约相关条款", "违约条款"), "default_clause_presence"),
        (("证券上市地点", "上市地点"), "listing_venue"),
        (("本期发行金额", "本期债券发行金额"), "current_issue_amount"),
        (("注册金额", "注册额度", "总注册金额"), "registration_amount"),
        (("募集说明书", "文件类型"), "document_type"),
        (("初始转股价格",), "initial_conversion_price"),
        (("主承销商",), "lead_underwriter"),
    )
    for tokens, field in any_rules:
        if any(compact(token) in value for token in tokens):
            return field
    if "发行规模" in value:
        return "issue_scale_cap"
    if "违约" in value and ("描述" in value or "条款" in value):
        return "default_clause_presence"
    return "unresolved"


def _expected(text: str, field: str) -> tuple[Any, str]:
    if field == "subject_rating":
        match = re.search(r"\b(AAA|AA\+|AA|A\+)\b", text, re.I)
        return (match.group(1).upper(), "rating") if match else ("not_applicable", "not_applicable")
    if field in {"issuer_name", "bond_trustee"}:
        companies = re.findall(r"([\u4e00-\u9fffA-Za-z（）()]+(?:股份有限公司|有限责任公司|有限公司|集团有限公司))", text)
        if companies:
            return companies[-1], "text"
    if field == "issuer_category" and "证券公司" in text:
        return "证券公司", "category"
    if field == "security_short_name":
        match = re.search(r"(?:证券简称|股票简称)(?:是|为|[:：])?\s*([\u4e00-\u9fffA-Za-z0-9]+)", text)
        if match:
            return match.group(1), "text"
    if field == "stock_or_security_code":
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if match:
            return match.group(1), "code"
    if field == "debt_asset_ratio_values":
        values = re.findall(r"(\d+(?:\.\d+)?)\s*%", text.replace("％", "%"))
        return values, "%"
    quoted = re.search(r"[“\"]([^”\"]+)[”\"]", text)
    if quoted:
        return quoted.group(1), "text"
    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", text.replace("％", "%"))
    if percent:
        return percent.group(1), "%"
    amount = re.search(r"(\d+(?:\.\d+)?)\s*(亿元|元/股|元)", text)
    if amount:
        return amount.group(1), amount.group(2)
    for token in ("募集说明书", "发行公告", "深圳证券交易所"):
        if token in text:
            return token, "text"
    return "not_applicable", "not_applicable"


def _relation(text: str, field: str) -> str:
    value = compact(text)
    if any(token in value for token in ("两份", "两家", "所有requireddocs", "均")):
        if any(token in value for token in ("低于", "高于", "不同", "相同")):
            pass
        elif field in {"default_clause_presence", "issue_scale_cap"} and any(token in value for token in ("均", "两份", "两家")):
            return "all_presence"
        else:
            return "all_eq"
    if "第二份" in text and "低于第一份" in text:
        return "pairwise_lt"
    if "第二份" in text and "高于第一份" in text:
        return "pairwise_gt"
    if "不同" in text:
        return "pairwise_different"
    if "相同" in text:
        return "pairwise_same"
    if field == "debt_asset_ratio_values" and len(re.findall(r"\d+(?:\.\d+)?\s*%", text)) >= 1:
        return "contains_values"
    if field == "post_conversion_debt_ratio_forecast":
        return "specific_value_presence"
    return "eq"


def parse_claim(question: Question, label: str) -> dict[str, Any]:
    text = str(question.options[label])
    field = _field(text)
    expected, unit = _expected(text, field)
    docs = _doc_refs(text, question.doc_ids)
    relation = _relation(text, field)
    # Universal claims always quantify over the full declared document set.
    if relation in {"all_eq", "all_presence", "pairwise_lt", "pairwise_gt", "pairwise_different", "pairwise_same"}:
        docs = tuple(str(doc) for doc in question.doc_ids)
    return {
        "option": label,
        "text": text,
        "required_doc_ids": list(docs),
        "field": field,
        "expected_value": expected,
        "unit": unit,
        "relation": relation,
        "document_scope": "cover_or_identity" if field in {"stock_or_security_code", "security_short_name", "issuer_name"} else "body_or_table",
    }


def _unresolved(label: str, claim: Mapping[str, Any], reason: str) -> TruthOptionResult:
    return TruthOptionResult(
        option=label,
        claim=claim,
        status="unresolved",
        blockers=(reason,),
        reason="contract field or cross-document relation not independently closed",
    )


def _candidate_priority(field: str, candidate: EvidenceCandidate) -> tuple[int, int, int, int]:
    source = str(candidate.source)
    text = str(candidate.text)
    page1 = int(bool(re.search(r"page_0*1[.]md", source, re.I)))
    direct_issuer = int("发行主体：" in text or "发行主体:" in text)
    identity = int(any(token in text for token in ("股票代码", "证券代码", "证券简称", "股票简称", "发行人：", "发行主体：")))
    exact = int(field in {"stock_or_security_code", "security_short_name", "issuer_name", "issuer_category"} and identity)
    if field == "issuer_category":
        return direct_issuer, exact, page1, -len(text)
    return exact, page1, direct_issuer, -len(text)


def _field_fragments(field: str, candidate: EvidenceCandidate) -> list[tuple[str, str]]:
    text = str(candidate.text or "")
    search_text = re.sub(r"<[^>]+>", " ", text)
    rows: list[tuple[str, str]] = []

    def add_matches(patterns: Sequence[str], value_group: int = 1) -> None:
        for pattern in patterns:
            for match in re.finditer(pattern, search_text, re.I):
                value = match.group(value_group).strip()
                rows.append((match.group(0), value))

    if field == "issuer_name":
        # Bind only an explicit issuer identity.  A blank cover label such as
        # 发行人： must not consume the following underwriter/trustee line.
        direct_rows: list[tuple[str, str]] = []
        for line in search_text.splitlines():
            stripped = line.strip()
            for label_token in ("发行主体", "发行人名称", "发行人"):
                if f"{label_token}：" not in stripped and f"{label_token}:" not in stripped:
                    continue
                pattern = rf"{label_token}[：:][ 	]*([^|，。]+?(?:股份有限公司|有限责任公司|有限公司|集团有限公司))"
                match = re.search(pattern, stripped)
                if match:
                    direct_rows.append((match.group(0), match.group(1).strip()))
                    break
        if direct_rows:
            rows.extend(direct_rows)
        else:
            for line in search_text.splitlines():
                if "募集说明书" not in line:
                    continue
                match = re.search(r"([^#]{0,60}?(?:股份有限公司|有限责任公司|有限公司|集团有限公司))[^#]{0,80}募集说明书", line)
                if match:
                    rows.append((match.group(0), match.group(1).strip()))
                    break
    elif field == "subject_rating":
        add_matches([r"主体(?:信用)?(?:等级|评级)[^A-Z]{0,30}(AAA|AA\+|AA|A\+)"])
    elif field == "issue_scale_cap":
        add_matches([
            r"本期债券(?:发行)?(?:总)?(?:规模|发行总额)[^\d]{0,50}(?:不超过|为)?\s*(\d+(?:\.\d+)?)\s*亿元",
            r"本期债券[^。；]{0,80}规模[^\d]{0,40}(?:不超过|为)?\s*(\d+(?:\.\d+)?)\s*亿元",
            r"发行规模[：:]\s*本期债券[^\d]{0,50}(?:不超过|为)?\s*(\d+(?:\.\d+)?)\s*亿元",
        ])
    elif field == "registration_amount":
        add_matches([r"(?:注册金额|注册额度|总注册金额|注册发行规模)[^\d]{0,50}(?:不超过|为)?\s*(\d+(?:\.\d+)?)\s*亿元"])
    elif field == "bond_trustee":
        add_matches([
            r"债券受托管理人[：:]\s*([^\n|，。]+)",
            r"受托管理人名称[：:]\s*([^\n|，。]+)",
            r"聘任了?\s*([^，。\n]+?(?:股份有限公司|有限责任公司|有限公司))\s*担任[^，。\n]{0,30}债券受托管理人",
        ])
    elif field == "issuer_category":
        # Prefer an explicit issuer-identity line.  A cover may contain names
        # of underwriters and trustees next to a blank 发行人： label; those
        # names must never be mistaken for the issuer.
        direct_rows: list[tuple[str, str]] = []
        for line in search_text.splitlines():
            stripped = line.strip()
            if "发行主体：" in stripped or "发行主体:" in stripped:
                match = re.search(r"发行主体[：:][ 	]*([^|，。]+?(?:股份有限公司|有限责任公司|有限公司|集团有限公司))", stripped)
                if match:
                    issuer = match.group(1).strip()
                    direct_rows.append((match.group(0), "证券公司" if "证券" in issuer else "非证券公司"))
            elif "发行人：" in stripped or "发行人:" in stripped:
                match = re.search(r"发行人[：:][ 	]*([^|，。]+?(?:股份有限公司|有限责任公司|有限公司|集团有限公司))", stripped)
                if match:
                    issuer = match.group(1).strip()
                    direct_rows.append((match.group(0), "证券公司" if "证券" in issuer else "非证券公司"))
        if direct_rows:
            rows.extend(direct_rows)
        else:
            for line in search_text.splitlines():
                if "募集说明书" not in line:
                    continue
                match = re.search(r"([^#]{0,60}?(?:股份有限公司|有限责任公司|有限公司|集团有限公司))[^#]{0,80}募集说明书", line)
                if match:
                    issuer = match.group(1).strip()
                    rows.append((match.group(0), "证券公司" if "证券" in issuer else "非证券公司"))
                    break
    elif field == "stock_or_security_code":
        add_matches([r"(?:股票代码|证券代码)[：:]\s*(\d{6})(?!\d)"])
    elif field == "security_short_name":
        add_matches([r"(?:证券简称|股票简称)[：:]\s*([^\s\n|，。]+)"])
    elif field == "debt_asset_ratio_values":
        if "资产负债率" in search_text:
            for match in re.finditer(r"\d+(?:\.\d+)?\s*%", search_text.replace("％", "%")):
                rows.append((text, match.group(0).replace("%", "").strip()))
    elif field == "post_conversion_debt_ratio_forecast":
        for match in re.finditer(r"[^。\n]{0,180}(?:转股后|陆续转股)[^。\n]{0,180}资产负债率[^。\n]{0,180}", search_text):
            fragment = match.group(0)
            values = re.findall(r"\d+(?:\.\d+)?\s*%", fragment.replace("％", "%"))
            rows.append((fragment, values[-1].replace("%", "") if values else "NO_SPECIFIC_PERCENT"))
        for match in re.finditer(r"[^。\n]{0,180}资产负债率[^。\n]{0,180}(?:转股后|陆续转股)[^。\n]{0,180}", search_text):
            fragment = match.group(0)
            values = re.findall(r"\d+(?:\.\d+)?\s*%", fragment.replace("％", "%"))
            rows.append((fragment, values[-1].replace("%", "") if values else "NO_SPECIFIC_PERCENT"))
    elif field == "default_clause_presence":
        match = re.search(r"[^。\n]{0,120}(?:违约情形|违约责任|违约事项及纠纷解决机制)[^。\n]{0,180}", search_text)
        if match:
            rows.append((match.group(0), "present"))
    elif field == "document_type":
        for phrase in ("募集说明书", "发行公告", "报告书"):
            if phrase in search_text:
                rows.append((phrase, phrase))
    elif field == "listing_venue":
        add_matches([r"(?:上市地点|股票上市地)[：:]\s*([^\n|，。]+)"])
    elif field == "initial_conversion_price":
        add_matches([r"初始转股价格[^\d]{0,50}(\d+(?:\.\d+)?)\s*元/股"])
    elif field == "lead_underwriter":
        add_matches([r"(?:牵头)?主承销商[：:]\s*([^\n|，。]+)"])
    elif field == "current_issue_amount":
        add_matches([r"本期(?:债券)?发行(?:金额|总额)[^\d]{0,50}(?:不超过|为)?\s*(\d+(?:\.\d+)?)\s*亿元"])
    return rows


def _doc_fact(repo_root: Path, field: str, doc_id: str, candidates: Sequence[EvidenceCandidate]) -> dict[str, Any] | None:
    scoped = [candidate for candidate in candidates if str(candidate.doc_id) == str(doc_id)]
    for candidate in sorted(scoped, key=lambda c: _candidate_priority(field, c), reverse=True):
        fragments = _field_fragments(field, candidate)
        if not fragments:
            continue
        fragment, value = fragments[0]
        source = TruthSource.from_candidate(repo_root=repo_root, candidate=candidate, relevance_fields=("document", "field_name", "value", "scope"))
        provenance = provenance_for_fragments(source=source, fields={field: (value, fragment, f"contract_{field}_crossdoc_v2")})
        return {"doc_id": str(doc_id), "candidate": candidate, "fragment": fragment, "value": value, "source": source, "provenance": provenance}
    return None


def _doc_values(repo_root: Path, field: str, doc_id: str, candidates: Sequence[EvidenceCandidate]) -> dict[str, Any] | None:
    scoped = [candidate for candidate in candidates if str(candidate.doc_id) == str(doc_id)]
    for candidate in sorted(scoped, key=lambda c: _candidate_priority(field, c), reverse=True):
        fragments = _field_fragments(field, candidate)
        if not fragments:
            continue
        values = [value for _, value in fragments]
        source = TruthSource.from_candidate(repo_root=repo_root, candidate=candidate, relevance_fields=("document", "field_name", "values", "scope"))
        fragment = fragments[0][0]
        provenance = provenance_for_fragments(source=source, fields={field: (values, fragment, f"contract_{field}_values_v2")})
        return {"doc_id": str(doc_id), "candidate": candidate, "fragment": fragment, "values": values, "source": source, "provenance": provenance}
    return None


def _closed_result(label: str, claim: Mapping[str, Any], status: str, facts: Sequence[Mapping[str, Any]], reason: str, binding: Mapping[str, str]) -> TruthOptionResult:
    sources = tuple(fact["source"] for fact in facts)
    provenance = tuple(item for fact in facts for item in fact["provenance"])
    return TruthOptionResult(option=label, claim=claim, status=status, sources=sources, provenance=provenance, binding=binding, reason=reason)


def evaluate_option(*, repo_root: Path, label: str, claim: Mapping[str, Any], candidates: Sequence[EvidenceCandidate]) -> TruthOptionResult:
    field = str(claim["field"])
    relation = str(claim["relation"])
    docs = [str(doc) for doc in claim.get("required_doc_ids") or []]
    if field == "unresolved" or not docs:
        return _unresolved(label, claim, "contract_field_or_doc_scope_unresolved")

    if relation == "contains_values":
        expected = {str(value) for value in (claim.get("expected_value") or [])}
        scoped = [candidate for candidate in candidates if str(candidate.doc_id) == docs[0]]
        best_fact = None
        best_overlap = -1
        for candidate in scoped:
            fragments = _field_fragments(field, candidate)
            if not fragments:
                continue
            actual = {str(value) for _, value in fragments}
            overlap = len(expected & actual)
            source = TruthSource.from_candidate(repo_root=repo_root, candidate=candidate, relevance_fields=("document", "field_name", "values", "scope"))
            fragment = fragments[0][0]
            provenance = provenance_for_fragments(source=source, fields={field: (sorted(actual), fragment, f"contract_{field}_values_v2")})
            fact = {"doc_id": docs[0], "candidate": candidate, "fragment": fragment, "values": sorted(actual), "source": source, "provenance": provenance}
            if overlap > best_overlap:
                best_overlap, best_fact = overlap, fact
            if expected and expected.issubset(actual):
                return _closed_result(label, claim, "supported", [fact], "all claimed debt-ratio values were checked in the same document-local field row", {"required_doc": "match", "field_name": "match", "values": "match"})
        if not best_fact:
            return _unresolved(label, claim, "debt_ratio_values_not_found")
        return _closed_result(label, claim, "contradicted", [best_fact], "claimed debt-ratio values were not jointly present in the best matching document-local field row", {"required_doc": "match", "field_name": "match", "values": "conflict"})

    if relation == "specific_value_presence":
        fact = _doc_fact(repo_root, field, docs[0], candidates)
        if not fact:
            return _unresolved(label, claim, "post_conversion_debt_ratio_statement_not_found")
        status = "supported" if fact["value"] != "NO_SPECIFIC_PERCENT" else "contradicted"
        return _closed_result(label, claim, status, [fact], "post-conversion debt-ratio statement checked for an explicit predicted percentage", {"required_doc": "match", "field_name": "match", "specific_percentage": "present" if status == "supported" else "absent"})

    if relation in {"all_eq", "all_presence"}:
        facts: list[Mapping[str, Any]] = []
        expected = compact(claim.get("expected_value"))
        statuses: list[str] = []
        for doc in docs:
            fact = _doc_fact(repo_root, field, doc, candidates)
            if not fact:
                return _unresolved(label, claim, f"missing_{field}_in_{doc}")
            facts.append(fact)
            if relation == "all_presence":
                statuses.append("supported")
            else:
                actual = compact(fact["value"])
                exact_fields = {"issuer_category", "subject_rating", "stock_or_security_code", "security_short_name"}
                value_match = (expected == actual) if field in exact_fields else bool(expected and (expected == actual or expected in actual or actual in expected))
                statuses.append("supported" if value_match else "contradicted")
        status = "contradicted" if "contradicted" in statuses else "supported"
        return _closed_result(label, claim, status, facts, "each required document was independently bound before universal aggregation", {"required_docs": "all_bound", "quantifier": "all", "aggregate": status})

    if relation in {"pairwise_lt", "pairwise_gt", "pairwise_different", "pairwise_same"}:
        if len(docs) < 2:
            return _unresolved(label, claim, "pairwise_relation_requires_two_docs")
        left = _doc_fact(repo_root, field, docs[0], candidates)
        right = _doc_fact(repo_root, field, docs[1], candidates)
        if not left or not right:
            return _unresolved(label, claim, "pairwise_field_missing_in_required_doc")
        lv, rv = str(left["value"]), str(right["value"])
        if relation in {"pairwise_lt", "pairwise_gt"}:
            lnum, rnum = _decimal(lv), _decimal(rv)
            if lnum is None or rnum is None:
                return _unresolved(label, claim, "pairwise_numeric_parse_failed")
            ok = rnum < lnum if relation == "pairwise_lt" else rnum > lnum
        elif relation == "pairwise_different":
            ok = compact(lv) != compact(rv)
        else:
            ok = compact(lv) == compact(rv)
        status = "supported" if ok else "contradicted"
        return _closed_result(label, claim, status, [left, right], "same field was extracted independently from doc A and doc B before pairwise comparison", {"doc_a": "bound", "doc_b": "bound", "pairwise_comparator": "match" if ok else "conflict"})

    fact = _doc_fact(repo_root, field, docs[0], candidates)
    if not fact:
        return _unresolved(label, claim, f"missing_exact_{field}_in_required_doc")
    expected = compact(claim.get("expected_value"))
    actual = compact(fact["value"])
    if field == "default_clause_presence":
        status = "supported"
    elif expected in {"", "not_applicable"}:
        return _unresolved(label, claim, "expected_value_unresolved")
    else:
        status = "supported" if expected == actual or expected in actual or actual in expected else "contradicted"
    return _closed_result(label, claim, status, [fact], "document-local exact field was independently compared", {"required_doc": "match", "field_name": "match", "value": "match" if status == "supported" else "conflict"})


def evaluate(*, repo_root: Path, question: Question, candidates: Sequence[EvidenceCandidate]) -> TruthQuestionResult:
    option_results: dict[str, TruthOptionResult] = {}
    answer_format = question.answer_contract.answer_format if question.answer_contract else question.answer_format
    if answer_format == "tf":
        for label in question.options:
            claim = {"option": label, "text": question.options[label], "required_doc_ids": list(question.doc_ids), "field": "compound_document_proposition"}
            option_results[label] = _unresolved(label, claim, "contract_tf_compound_parser_not_implemented")
        return result_from_options(question=question, option_results=option_results, task_type="true_false", lane="FC-F", implementation_status="PARTIAL", capability=CAPABILITY)
    for label in question.options:
        claim = parse_claim(question, label)
        scoped = candidates_for_docs(candidates, claim["required_doc_ids"])
        option_results[label] = evaluate_option(repo_root=repo_root, label=label, claim=claim, candidates=scoped) if scoped else _unresolved(label, claim, "missing_required_doc")
    relation_fields = {str(row.claim.get("relation")) for row in option_results.values()}
    lane = "FC-XDOC" if relation_fields & {"all_eq", "all_presence", "pairwise_lt", "pairwise_gt", "pairwise_different", "pairwise_same"} else "FC-I"
    return result_from_options(question=question, option_results=option_results, task_type="contract_cross_document_truth", lane=lane, implementation_status="IMPLEMENTED_CROSS_DOCUMENT_V2", capability=CAPABILITY)
