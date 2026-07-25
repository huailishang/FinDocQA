"""Verification-only recovery of exact contract fields from same-source assets.

The augmenter never changes solver candidates or prompt context.  It searches the
question's explicitly allowed document IDs in the existing MinerU page/full-text
assets and exposes bounded, source-traceable windows to production verification.
No answer, option label, or QID is encoded here.
"""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from contracts import EvidenceCandidate, Question


_FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "subject_credit_rating": ("主体信用评级", "主体评级", "主体信用等级"),
    "registration_approval_ceiling": ("同意注册", "注册的批复", "注册", "面值不超过"),
    "registration_amount_wording": ("注册金额", "注册规模", "注册额度", "注册", "面值不超过"),
    "issue_scale_cap": ("本期债券发行金额", "本期发行金额", "发行规模", "发行金额", "不超过"),
    "penalty_interest_multiplier": ("违约金", "违约罚息", "罚息", "150%", "200%"),
    "lead_underwriter": ("主承销商", "牵头主承销商", "簿记管理人"),
    "sponsor_institution": ("保荐机构", "保荐人", "主承销商"),
    "initial_conversion_price": ("初始转股价格", "初始转股价"),
    "post_conversion_debt_ratio": ("全部转股后", "可转债全部转股后", "资产负债率"),
    "stock_code": ("股票代码", "证券代码"),
    "issue_date": ("发行日期", "发行首日", "发行安排", "T 日", "T日"),
    "issue_month": ("发行日期", "发行首日", "发行安排", "T 日", "T日"),
    "cover_month": ("募集说明书", "2025年", "二〇二五年"),
    "reporting_period": ("报告期", "最近三年一期", "2025年1-9月"),
    "financial_data_as_of_date": ("报告期末", "截至", "2025年9月30日"),
    "downward_conversion_price_revision_clause": ("转股价格向下修正", "向下修正条款"),
    "conditional_redemption_clause": ("有条件赎回条款", "有条件赎回", "赎回条款"),
    "disclosure_obligation_clause": ("及时、公平地履行信息披露义务", "公平地履行信息披露义务"),
    "holder_protection_clause": ("回售选择权", "回售条款", "有条件赎回条款", "债券持有人的权利"),
    "responsibility_statement_clause": ("董事、高级管理人员", "高级管理人员", "真实性、准确性和完整性承担"),
    "issuer_name": ("发行人名称", "公司名称", "中文名称", "发行人", "募集说明书"),
    "trustee_institution": ("债券受托管理人", "受托管理人名称", "受托管理人"),
    "listing_venue": ("上市地点", "证券上市地点", "上市地"),
    "document_type": ("募集说明书", "报告书（草案）", "报告书(草案)"),
    "transaction_structure": ("发行股份购买资产", "募集配套资金"),
    "stock_short_name": ("股票简称", "证券简称"),
    "debt_asset_ratio": ("资产负债率",),
    "payment_date": ("兑付日", "到期兑付日", "本金兑付日"),
    "announcement_date": ("发行公告日期", "发行公告刊登日期", "公告日期"),
    "notification_deadline_days": ("日内通知", "通知期限", "报告出具之日起"),
    "profit_total_series": ("利润总额",),
    "default_interest_formula": ("违约金具体计算方式", "违约利息", "本金和利息"),
    "asset_impairment_compensation_clause": ("资产减值补偿", "减值补偿"),
    "market_price_floor_clause": ("初始转股价格不低于", "不低于募集说明书公告"),
}

_ABSENCE_CERTIFIABLE_FIELDS = {
    "downward_conversion_price_revision_clause",
}


def detect_contract_field_types(text_value: str) -> tuple[str, ...]:
    """Return verification capabilities required by a stem or option."""
    text = "".join(str(text_value or "").replace("％", "%").split())
    fields: list[str] = []
    checks = (
        ("subject_credit_rating", any(token in text for token in ("主体评级", "主体信用评级", "主体信用等级"))),
        ("registration_amount_wording", any(token in text for token in ("注册金额", "注册规模", "注册额度"))),
        ("registration_approval_ceiling", "注册批复" in text or "注册上限" in text),
        ("issue_scale_cap", any(token in text for token in ("发行金额上限", "发行规模上限", "发行金额为", "发行规模设定", "发行规模"))),
        ("penalty_interest_multiplier", "违约罚息" in text or ("违约" in text and "%" in text)),
        ("lead_underwriter", "主承销商" in text),
        ("trustee_institution", "受托管理人" in text),
        ("sponsor_institution", any(token in text for token in ("保荐机构", "保荐人"))),
        ("initial_conversion_price", "初始转股" in text),
        ("post_conversion_debt_ratio", "全部转股后" in text and "资产负债率" in text),
        ("stock_code", "股票代码" in text or "证券代码" in text),
        ("stock_short_name", "股票简称" in text or "证券简称" in text),
        ("issue_date", "发行日期" in text or "发行日" in text),
        ("announcement_date", "公告日期" in text),
        ("issue_month", "发行日期" in text and re.search(r"20\d{2}年\d{1,2}月", text) is not None),
        ("downward_conversion_price_revision_clause", "转股价格向下修正" in text or "向下修正" in text),
        ("conditional_redemption_clause", "有条件赎回" in text),
        ("disclosure_obligation_clause", "及时、公平" in text and "信息披露义务" in text),
        ("holder_protection_clause", any(token in text for token in ("保护性条款", "回售选择权", "赎回安排"))),
        ("responsibility_statement_clause", "董事" in text and "高级管理人员" in text and "真实性" in text),
        ("issuer_name", any(token in text for token in ("发行人名称", "发行人是", "发行人名称中包含"))),
        ("listing_venue", "上市地点" in text or "证券上市地点" in text),
        ("document_type", "文件类型" in text or ("募集说明书" in text and "定义" in text)),
        ("transaction_structure", "发行股份购买资产" in text and "募集配套资金" in text),
        ("debt_asset_ratio", "资产负债率" in text),
        ("payment_date", "兑付日" in text),
        ("notification_deadline_days", "通知" in text and ("日内" in text or "期限" in text)),
        ("profit_total_series", "利润总额" in text and any(token in text for token in ("趋势", "逐年", "下降", "上升"))),
        ("default_interest_formula", "违约" in text and any(token in text for token in ("本金和利息", "计算基数", "计算公式"))),
        ("asset_impairment_compensation_clause", "资产减值补偿" in text),
        ("market_price_floor_clause", "初始转股价格" in text and "不低于" in text),
    )
    for field, enabled in checks:
        if enabled:
            fields.append(field)
    return tuple(dict.fromkeys(fields))


@lru_cache(maxsize=512)
def _read_text(path_text: str) -> str:
    path = Path(path_text)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _bounded_windows(
    text: str,
    hints: Sequence[str],
    *,
    radius: int = 900,
    preserve_adjacent_blocks: bool = False,
) -> list[tuple[int, str]]:
    positions: list[int] = []
    for hint in hints:
        start = 0
        while True:
            index = text.find(hint, start)
            if index < 0:
                break
            positions.append(index)
            start = index + max(1, len(hint))
    output: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index in sorted(set(positions)):
        start = max(0, index - radius)
        end = min(len(text), index + radius)
        # Prefer bounded semantic/table blocks when available.
        left_break = max(text.rfind("\n\n", start, index), text.rfind("<table", start, index))
        right_paragraph = text.find("\n\n", index, end)
        right_table = text.find("</table>", index, end)
        if left_break >= 0:
            start = left_break if text.startswith("<table", left_break) else left_break + 2
        right_candidates = [
            value
            for value in (
                -1 if preserve_adjacent_blocks else right_paragraph,
                right_table + 8 if right_table >= 0 else -1,
            )
            if value >= 0
        ]
        if right_candidates:
            end = min(right_candidates)
        window = text[start:end].strip()
        digest = re.sub(r"\s+", "", window)
        if not window or digest in seen:
            continue
        seen.add(digest)
        output.append((start, window))
    return output


class ContractExactFieldEvidenceAugmenter:
    """Add same-source exact-field windows to the verification candidate view."""

    def __init__(
        self,
        *,
        full_text_root: Path | str,
        retrieval_root: Path | str,
        max_windows_per_field_doc: int = 3,
    ) -> None:
        self.full_text_root = Path(full_text_root)
        self.retrieval_root = Path(retrieval_root)
        self.max_windows_per_field_doc = max(1, int(max_windows_per_field_doc))

    def _files(self, domain: str, doc_id: str) -> list[Path]:
        files: list[Path] = []
        page_dir = self.retrieval_root / domain / doc_id
        if page_dir.is_dir():
            files.extend(sorted(page_dir.glob("page_*.md")))
        auto_dir = self.full_text_root / domain / doc_id / "auto"
        if auto_dir.is_dir():
            preferred = auto_dir / f"{doc_id}.md"
            if preferred.is_file():
                files.append(preferred)
            files.extend(path for path in sorted(auto_dir.glob("*.md")) if path != preferred)
        return files

    def augment(
        self,
        question: Question,
        candidates: Sequence[EvidenceCandidate],
    ) -> tuple[list[EvidenceCandidate], dict[str, Any]]:
        fields = tuple(
            dict.fromkeys(
                field
                for text_value in (question.text, *question.options.values())
                for field in detect_contract_field_types(str(text_value))
            )
        )
        query_text = "\n".join(
            [str(question.text or ""), *(str(value) for value in question.options.values())]
        )
        query_numeric_literals = tuple(
            dict.fromkeys(
                match.group(1)
                for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|％|亿元|元|日)", query_text)
            )
        )
        output = list(candidates)
        seen = {
            (str(candidate.doc_id), str(candidate.source), re.sub(r"\s+", "", str(candidate.text)))
            for candidate in output
        }
        added: list[dict[str, Any]] = []
        if not fields:
            return output, {"enabled": True, "fields": [], "candidates_added": 0, "added": []}

        for doc_id in map(str, question.doc_ids):
            files = self._files(str(question.domain), doc_id)
            for field in fields:
                hints = _FIELD_HINTS.get(field, ())
                ranked: list[tuple[int, int, Path, str]] = []
                for path in files:
                    text = _read_text(str(path))
                    if not text:
                        continue
                    for offset, window in _bounded_windows(
                        text,
                        hints,
                        preserve_adjacent_blocks=field in {
                            "lead_underwriter",
                            "trustee_institution",
                            "sponsor_institution",
                            "issuer_name",
                            "listing_venue",
                            "stock_short_name",
                            "announcement_date",
                        },
                    ):
                        specificity = sum(1 for hint in hints if hint in window)
                        semantic_exact_bonus = 0
                        if field in {
                            "registration_amount_wording",
                            "registration_approval_ceiling",
                            "issue_scale_cap",
                            "penalty_interest_multiplier",
                            "initial_conversion_price",
                            "post_conversion_debt_ratio",
                            "debt_asset_ratio",
                            "payment_date",
                            "notification_deadline_days",
                            "profit_total_series",
                        } and query_numeric_literals and any(
                            re.search(rf"(?<!\d){re.escape(literal)}(?!\d)", window)
                            for literal in query_numeric_literals
                        ):
                            semantic_exact_bonus += 1500
                        if field == "registration_amount_wording" and any(
                            label in window for label in ("注册金额", "注册规模", "注册额度")
                        ):
                            semantic_exact_bonus += 2500
                        compact_window = re.sub(r"\s+", "", window)
                        if field == "responsibility_statement_clause" and (
                            ("董事" in compact_window or "高级管理人员" in compact_window)
                            and "真实性" in compact_window
                            and any(token in compact_window for token in ("承担", "责任", "保证"))
                        ):
                            semantic_exact_bonus += 2200
                        if field == "disclosure_obligation_clause" and (
                            "及时、公平" in compact_window and "信息披露义务" in compact_window
                        ):
                            semantic_exact_bonus += 2200
                        if field == "holder_protection_clause" and any(
                            token in compact_window for token in ("回售选择权", "回售条款", "有条件赎回条款", "债券持有人的权利")
                        ):
                            semantic_exact_bonus += 1800
                        if field in {"lead_underwriter", "trustee_institution", "sponsor_institution"}:
                            has_role = any(
                                label in window
                                for label in (
                                    "主承销商",
                                    "牵头主承销商",
                                    "债券受托管理人",
                                    "受托管理人名称",
                                    "受托管理人",
                                    "保荐机构",
                                    "保荐人",
                                )
                            )
                            has_legal_entity = re.search(
                                r"[\u4e00-\u9fff]{2,40}(?:股份有限公司|有限责任公司|有限公司)",
                                window,
                            ) is not None
                            if has_role and has_legal_entity:
                                semantic_exact_bonus = 1000
                            if any(label in window for label in ("前次", "时任", "历史", "历次")):
                                semantic_exact_bonus -= 1500
                            if any(label in window for label in ("目录", "声明")) and not has_legal_entity:
                                semantic_exact_bonus -= 500
                            if path.name == "page_0001.md" and has_role and has_legal_entity:
                                semantic_exact_bonus += 500
                        # Page files are preferred for concise lineage unless a
                        # full-text table contains the exact field label omitted
                        # by page OCR.
                        page_bonus = 20 if path.name.startswith("page_") else 0
                        ranked.append((specificity * 100 + semantic_exact_bonus + page_bonus, offset, path, window))
                if not ranked and field in _ABSENCE_CERTIFIABLE_FIELDS:
                    full_text = next(
                        (
                            path
                            for path in files
                            if not path.name.startswith("page_")
                            and path.name == f"{doc_id}.md"
                        ),
                        None,
                    )
                    if full_text is not None:
                        text = _read_text(str(full_text))
                        source = str(full_text).replace("\\", "/")
                        scan_digest = sha256(text.encode("utf-8")).hexdigest()
                        window = (
                            "COMPLETE_DOCUMENT_SCAN\n"
                            f"field={field}\n"
                            "occurrences=0\n"
                            f"source_sha256={scan_digest}"
                        )
                        key = (doc_id, source, re.sub(r"\s+", "", window))
                        if key not in seen:
                            seen.add(key)
                            output.append(EvidenceCandidate(
                                domain=str(question.domain),
                                doc_id=doc_id,
                                source=source,
                                text=window,
                                score=49999.0,
                                retriever="contract_exact_field_verification",
                                metadata={
                                    "verification_only": True,
                                    "contract_exact_field": field,
                                    "same_source_asset": True,
                                    "source_asset": source,
                                    "source_offset": 0,
                                    "complete_document_scan": True,
                                    "field_occurrences": 0,
                                    "source_sha256": scan_digest,
                                },
                            ))
                            added.append({
                                "doc_id": doc_id,
                                "field": field,
                                "source": source,
                                "rank": 0,
                                "complete_document_scan": True,
                                "field_occurrences": 0,
                            })
                ranked.sort(key=lambda row: (row[0], -len(row[3])), reverse=True)
                for rank, (_, offset, path, window) in enumerate(
                    ranked[: self.max_windows_per_field_doc], start=1
                ):
                    source = str(path).replace("\\", "/")
                    if not path.name.startswith("page_"):
                        source = f"{source}#char={offset}"
                    key = (doc_id, source, re.sub(r"\s+", "", window))
                    if key in seen:
                        continue
                    seen.add(key)
                    candidate = EvidenceCandidate(
                        domain=str(question.domain),
                        doc_id=doc_id,
                        source=source,
                        text=window,
                        score=float(50000 - rank),
                        retriever="contract_exact_field_verification",
                        metadata={
                            "verification_only": True,
                            "contract_exact_field": field,
                            "same_source_asset": True,
                            "source_asset": str(path).replace("\\", "/"),
                            "source_offset": offset,
                        },
                    )
                    output.append(candidate)
                    added.append({
                        "doc_id": doc_id,
                        "field": field,
                        "source": source,
                        "rank": rank,
                    })
        return output, {
            "enabled": True,
            "fields": list(fields),
            "candidates_added": len(added),
            "added": added,
        }
