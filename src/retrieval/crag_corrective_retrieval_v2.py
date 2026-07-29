"""QID-agnostic bounded CRAG V2 helpers over existing local corrective retrieval."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

MAX_CRAG_ROUNDS = 2
VALID_QUALITY = {"CORRECT", "AMBIGUOUS", "INCORRECT"}
_DOMAIN_ANCHORS = {
    "financial_contracts": ("发行人", "发行", "债券", "交易"),
    "financial_reports": ("年度", "营业收入", "净利润", "财务"),
    "insurance": ("保险责任", "责任免除", "赔付", "条款"),
    "regulatory": ("规定", "处罚", "监管", "施行"),
    "research": ("预计", "同比", "市场", "价格"),
}
_SEMANTIC_TERMS = (
    "发行公告日期", "公告日期", "发行日期", "施行日期", "施行", "废止",
    "营业收入", "净利润", "现金流量", "现金分红", "研发投入", "市占率",
    "市场份额", "渗透率", "同比", "环比", "保存期限", "客户尽职调查",
    "保险责任", "责任免除", "赔付", "主体评级", "注册金额", "违约罚息",
    "重大资产重组", "信息披露", "行政处罚", "董事长", "主管人员",
)
_NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?\s*(?:%|％|亿元|万元|元|倍|年|月|日)?")
_WORD_RE = re.compile(r"[一-鿿A-Za-z]{2,16}")
_STOP = {
    "以下", "哪些", "关于", "文档", "内容", "描述", "判断", "正确", "错误",
    "符合", "第一份", "第二份", "公司", "产品", "选项", "说法", "陈述",
}
_TRUE_FALSE = {"正确", "错误", "是", "否", "对", "错", "true", "false", "yes", "no"}


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _uniq(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        key = _compact(value)
        if value and key and key not in seen:
            seen.add(key)
            out.append(value)
    return tuple(out)


def is_judgment_question(answer_format: str, options: Mapping[str, Any]) -> bool:
    fmt = str(answer_format or "").lower()
    if fmt in {"tf", "boolean", "judge"}:
        return True
    values = {_compact(value) for value in options.values()}
    normalized = {_compact(value) for value in _TRUE_FALSE}
    return bool(values) and values.issubset(normalized) and len(values) <= 2


def judgment_query_text(question_text: str) -> str:
    """Remove the judgment shell while retaining the proposition being tested."""
    text = str(question_text or "").strip()
    patterns = (
        r"^.*?判断以下说法是否正确[:：]\s*",
        r"^.*?判断以下陈述是否正确[:：]\s*",
        r"^.*?判断陈述是否正确[:：]\s*",
        r"^.*?判断题[:：]\s*",
        r"^.*?判断[:：]\s*",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            text = text[match.end():]
            break
    text = re.sub(r"(?:，?该说法是否正确[？?]?)$", "", text).strip("。；; \t\n")
    return text or str(question_text or "")


def query_text_for_option(
    *,
    question_text: str,
    option_text: str,
    answer_format: str,
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose retrieval text from semantics, never from a judgment label itself."""
    judgment = is_judgment_question(answer_format, options)
    selected = judgment_query_text(question_text) if judgment else str(option_text or "")
    return {
        "query_text": selected,
        "judgment_question": judgment,
        "query_source": "QUESTION_PROPOSITION" if judgment else "OPTION_PROPOSITION",
    }


def rewrite_query(domain: str, option_text: str, *, round_number: int) -> dict[str, Any]:
    if round_number not in {1, 2}:
        raise ValueError("round_number must be 1 or 2")
    numeric = _uniq(m.group(0) for m in _NUM_RE.finditer(option_text))
    semantic = _uniq(term for term in _SEMANTIC_TERMS if term in str(option_text or ""))
    words = sorted(
        _uniq(w for w in _WORD_RE.findall(option_text) if w not in _STOP),
        key=lambda x: (-len(x), x),
    )
    if round_number == 1:
        terms = _uniq((*numeric, *semantic, *words[:8]))
        strategy = "ROUND1_EXACT_ENTITY_METRIC_NUMERIC"
    else:
        terms = _uniq((*numeric, *semantic, *words[:12], *_DOMAIN_ANCHORS.get(domain, ())[:3]))
        strategy = "ROUND2_DOMAIN_ANCHORS_PARENT_CONTEXT"
    return {
        "domain": domain,
        "round_number": round_number,
        "raw_text": option_text,
        "terms": list(terms),
        "numeric_terms": list(numeric),
        "semantic_terms": list(semantic),
        "strategy": strategy,
    }


def grade_retrieval(query: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]) -> str:
    """Grade retrieval quality by required evidence atoms, never term overlap alone."""
    if not sources:
        return "INCORRECT"
    if any(
        bool(
            source.get("wrong_entity")
            or source.get("wrong_document")
            or source.get("wrong_year")
            or source.get("wrong_field")
        )
        for source in sources
    ):
        return "INCORRECT"

    required = dict(query.get("required_atoms") or {})
    if required:
        missing = [name for name, bound in required.items() if not bool(bound)]
        required_operands = int(query.get("required_comparison_operands") or 0)
        bound_operands = int(query.get("bound_comparison_operands") or 0)
        if required_operands and bound_operands < required_operands:
            missing.append(f"comparison_operands:{bound_operands}/{required_operands}")
        return "CORRECT" if not missing else "AMBIGUOUS"

    terms = [_compact(x) for x in query.get("terms", []) if _compact(x)]
    nums = [_compact(x) for x in query.get("numeric_terms", []) if _compact(x)]
    best_terms = 0
    best_nums = 0
    for source in sources:
        text = _compact(source.get("span") or source.get("local_window") or "")
        best_terms = max(best_terms, sum(1 for term in terms if term in text))
        best_nums = max(best_nums, sum(1 for term in nums if term in text))
    if nums and best_nums < len(nums):
        return "AMBIGUOUS"
    if best_terms:
        return "AMBIGUOUS"
    return "INCORRECT"


def parent_context(sources: Sequence[Mapping[str, Any]], *, max_chars: int = 2400) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source in sources:
        row = dict(source)
        span = str(row.get("span") or row.get("local_window") or "")
        row["parent_context"] = span[:max_chars]
        row["parent_context_expanded"] = True
        out.append(row)
    return out


def _document_text_files(repo_root: Path, domain: str, doc_id: str) -> tuple[Path, ...]:
    """Return canonical local text files for one declared document only.

    Retrieval-page markdown is preferred because it is already split into child
    units.  The full MinerU markdown is a fallback when no retrieval pages exist.
    """
    root = Path(repo_root).resolve()
    data_root = root / "data"
    retrieval_dir = data_root / "processed_mineru_retrieval" / str(domain) / str(doc_id)
    retrieval_files = tuple(sorted(path for path in retrieval_dir.glob("*.md") if path.is_file()))
    if retrieval_files:
        return retrieval_files
    auto_dir = data_root / "processed_mineru" / str(domain) / str(doc_id) / "auto"
    return tuple(sorted(path for path in auto_dir.glob("*.md") if path.is_file()))


def directed_child_retrieval(
    *,
    repo_root: Path,
    domain: str,
    required_doc_ids: Sequence[str],
    rewritten_query: Mapping[str, Any],
    round_number: int,
    max_hits: int = 8,
    context_lines: int = 2,
) -> list[dict[str, Any]]:
    """Execute a rewritten query against child text units in required docs.

    The rewritten query is the actual retrieval input: only its ``terms`` are
    scored.  No whole-document candidate pool is preloaded into the verifier.
    """
    if round_number not in {1, 2}:
        raise ValueError("round_number must be 1 or 2")
    terms = _uniq(str(value) for value in rewritten_query.get("terms", ()) if str(value).strip())
    if not terms:
        return []
    semantic = {_compact(value) for value in rewritten_query.get("semantic_terms", ()) if _compact(value)}
    numeric = {_compact(value) for value in rewritten_query.get("numeric_terms", ()) if _compact(value)}
    rows: list[dict[str, Any]] = []
    for doc_id in _uniq(str(value) for value in required_doc_ids):
        for path in _document_text_files(Path(repo_root), domain, doc_id):
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            for index, line in enumerate(lines):
                compact_line = _compact(line)
                line_matches = [term for term in terms if _compact(term) and _compact(term) in compact_line]
                if not line_matches:
                    continue
                start = max(0, index - context_lines)
                end = min(len(lines), index + context_lines + 1)
                span = "\n".join(lines[start:end])
                compact_span = _compact(span)
                matched = [term for term in terms if _compact(term) in compact_span]
                if not matched:
                    continue
                semantic_hits = sum(1 for term in semantic if term in compact_span)
                numeric_hits = sum(1 for term in numeric if term in compact_span)
                score = float(len(matched) + 2 * semantic_hits + 2 * numeric_hits)
                span_hash = hashlib.sha256(span.encode("utf-8")).hexdigest()
                rows.append({
                    "doc_id": str(doc_id),
                    "source_path": str(path),
                    "line_start": start + 1,
                    "line_end": end,
                    "span": span,
                    "local_window": span,
                    "span_sha256": span_hash,
                    "matched_terms": matched,
                    "hit_terms": matched,
                    "hit_count": len(matched),
                    "semantic_hit_count": semantic_hits,
                    "numeric_hit_count": numeric_hits,
                    "score": score,
                    "round_number": round_number,
                    "retrieval_stage": "DIRECTED_CHILD_RETRIEVAL",
                    "rewritten_query_executed": True,
                })
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (-float(item["score"]), item["doc_id"], item["source_path"], item["line_start"])):
        key = (str(row["doc_id"]), str(row["span_sha256"]))
        if key not in dedup:
            dedup[key] = row
    ranked = list(dedup.values())
    # Cross-document claims must not lose a real hit merely because one required
    # document contributes many higher-scoring windows. Preserve the best actual
    # hit per required document first, then fill the remaining global Top-K.
    selected: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for doc_id in _uniq(str(value) for value in required_doc_ids):
        best = next((row for row in ranked if str(row["doc_id"]) == doc_id), None)
        if best is None:
            continue
        key = (str(best["doc_id"]), str(best["span_sha256"]))
        selected.append(best)
        seen_keys.add(key)
        if len(selected) >= max_hits:
            return selected
    for row in ranked:
        key = (str(row["doc_id"]), str(row["span_sha256"]))
        if key in seen_keys:
            continue
        selected.append(row)
        seen_keys.add(key)
        if len(selected) >= max_hits:
            break
    return selected


def expand_parent_context_from_child_hits(
    child_hits: Sequence[Mapping[str, Any]],
    *,
    parent_radius_lines: int = 12,
    max_chars: int = 6000,
) -> list[dict[str, Any]]:
    """Expand parent context by reopening the exact child-hit source file.

    Expansion is impossible without a child hit and therefore cannot be faked by
    preloading a full document before retrieval.
    """
    output: list[dict[str, Any]] = []
    for child in child_hits:
        path = Path(str(child.get("source_path") or ""))
        start = int(child.get("line_start") or 0)
        end = int(child.get("line_end") or 0)
        if not path.is_file() or start <= 0 or end < start:
            continue
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        parent_start = max(0, start - 1 - parent_radius_lines)
        parent_end = min(len(lines), end + parent_radius_lines)
        parent = "\n".join(lines[parent_start:parent_end])[:max_chars]
        row = dict(child)
        row.update({
            "parent_context": parent,
            "parent_line_start": parent_start + 1,
            "parent_line_end": parent_end,
            "parent_context_expanded": True,
            "parent_expansion_trigger": "CHILD_HIT",
            "trigger_child_span_sha256": str(child.get("span_sha256") or ""),
        })
        output.append(row)
    return output


def classify_two_rounds(rounds: Sequence[Mapping[str, Any]]) -> str:
    grades = [str(row.get("retrieval_quality") or "INCORRECT") for row in rounds[:MAX_CRAG_ROUNDS]]
    if "CORRECT" in grades:
        return "CORRECT"
    if "AMBIGUOUS" in grades:
        return "AMBIGUOUS"
    return "INCORRECT"


__all__ = [
    "MAX_CRAG_ROUNDS",
    "VALID_QUALITY",
    "is_judgment_question",
    "judgment_query_text",
    "query_text_for_option",
    "rewrite_query",
    "grade_retrieval",
    "parent_context",
    "directed_child_retrieval",
    "expand_parent_context_from_child_hits",
    "classify_two_rounds",
]
