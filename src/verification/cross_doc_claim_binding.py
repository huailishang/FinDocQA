"""Cross-document option certification with per-document subclaims.

A cross-document statement cannot be certified from one matching passage.  This
module decomposes quantified or comparative statements into document-local
subclaims and aggregates them conservatively.  Missing evidence remains
ambiguous; only an explicit same-field or same-relation conflict is a
contradiction.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from verification.typed_claim_binding import certify_typed_option_claim

_ALL_MARKERS = (
    "两份文档均", "两份文件均", "两个文档都", "两个文件都", "各文档均", "各文件均",
    "两家发行人均", "两家公司均", "两份文档都", "两份文件都", "所有文档", "所有文件",
)
_ANY_MARKERS = ("其中一份文档", "其中一份文件", "任一文档", "任一文件", "至少一份文档", "至少一份文件")
_DOC_TOKEN_RE = re.compile(r"(?:fc_)?text[_-]?0*(\d+)", re.IGNORECASE)
_STOCK_CODE_RE = re.compile(
    r"(?:股票|证券)代码(?:\s|：|:|<[^>]+>){0,120}?(\d{6})(?:\.(?:SZ|SH))?",
    re.IGNORECASE,
)
_STOCK_SHORT_NAME_RE = re.compile(
    r"(?:股票|证券)简称(?:\s|：|:|<[^>]+>){0,120}?(?:为|是)?(?:\s|：|:|<[^>]+>)*"
    r"([*ＳＴSTA-Za-z0-9\u4e00-\u9fff·]{2,24})",
    re.IGNORECASE,
)
_RATING_RE = re.compile(r"(?:主体信用评级|主体评级|主体信用等级)(?:为|是|达到|：|:)?\s*(AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB)", re.IGNORECASE)
_BOND_RATING_RE = re.compile(r"(?:债项信用评级|债券信用评级|可转债信用等级|债券信用等级)(?:为|是|达到|：|:)?\s*(AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB|负值)", re.IGNORECASE)
_ISSUE_DATE_RE = re.compile(
    r"(?:发行日期|发行日|发行时间)(?:为|是|：|:)?\s*(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*月?(?:\s*(\d{1,2})\s*日?)?"
)
_ISSUE_SCHEDULE_T_DAY_RE = re.compile(
    r"(20\d{2})年(\d{1,2})月(\d{1,2})日[^<\n]{0,40}"
    r"</td><td[^>]*>\s*T\s*日\s*</td><td[^>]*>[^<]{0,160}(?:发行|申购|配售)",
    re.IGNORECASE,
)
_INITIAL_CONVERSION_PRICE_RE = re.compile(
    r"初始转股(?:价格|价)(?:为|是|：|:)?\s*(\d+(?:\.\d+)?)\s*元(?:/股)?"
)
_POST_CONVERSION_DEBT_RATIO_RE = re.compile(
    r"(?:全部|全额)(?:可转债)?转股后[^。；\n]{0,220}?资产负债率[^。；\n]{0,80}?(\d+(?:\.\d+)?)\s*%"
)
_PENALTY_MULTIPLIER_RE = re.compile(
    r"(?:违约金|违约罚息|罚息|逾期利息)[^。；\n]{0,260}?(\d+(?:\.\d+)?)\s*%"
)
_LEAD_UNDERWRITER_RE = re.compile(
    r"(?:主承销商|牵头主承销商)(?:/[^：:\n]{0,20})?(?:为|是|：|:|</td><td[^>]*>)*\s*"
    r"([\u4e00-\u9fffA-Za-z（）()·]{4,50}?(?:股份有限公司|有限责任公司|有限公司))"
)
_SPONSOR_RE = re.compile(
    r"(?:保荐机构|保荐人)(?:/[^：:\n]{0,24})?(?:/主承销商)?(?:为|是|：|:|</td><td[^>]*>)*\s*"
    r"([\u4e00-\u9fffA-Za-z（）()·]{4,50}?(?:股份有限公司|有限责任公司|有限公司))"
)
_REGISTRATION_AMOUNT_RE = re.compile(
    r"(?:注册金额|注册规模|注册额度)(?:为|是|：|:|</td><td[^>]*>)*\s*"
    r"(?:不超过|不高于|上限为)?\s*(?:（含）|\(含\))?\s*(\d[\d,]*(?:\.\d+)?)\s*亿元"
)
_REGISTRATION_APPROVAL_RE = re.compile(
    r"(?:同意|获准|批复)[^。；\n]{0,180}?注册[^。；\n]{0,180}?(?:发行)?面值(?:总额)?不超过\s*(\d[\d,]*(?:\.\d+)?)\s*亿元"
)
_ISSUE_SCALE_RE = re.compile(
    r"(?:本期(?:债券)?(?:发行)?规模|发行规模|发行金额|发行总额)"
    r"(?:\s|：|:|<[^>]+>|[^。；]){0,260}?"
    r"(?:不超过|不高于|上限(?:为|是)?|最高(?:为|是)?|为|是|：|:)"
    r"\s*(?:人民币)?\s*(\d[\d,]*(?:\.\d+)?)\s*亿元",
    re.IGNORECASE,
)
_EXPECTED_YM_RE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月")
_DEBT_ASSET_RATIO_RE = re.compile(r"资产负债率[^\n]{0,180}?(\d+(?:\.\d+)?)\s*%")
_COMPANY_NAME_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z（）()·]{4,60}?(?:股份有限公司|有限责任公司|集团有限公司|有限公司))"
)
_LISTING_VENUE_RE = re.compile(
    r"(?:上市地点|证券上市地点|上市地)(?:为|是|：|:|</td><td[^>]*>)*\s*"
    r"(上海证券交易所|深圳证券交易所|北京证券交易所)"
)
_PAYMENT_DATE_RE = re.compile(
    r"(?:到期兑付日|本金兑付日|兑付日)(?:为|是|：|:)?\s*"
    r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?"
)
_NOTIFICATION_DAYS_RE = re.compile(
    r"(?:报告出具之日起|知悉后|发生后|应当在|需在|须在)?[^。；\n]{0,80}?"
    r"(\d+|一|二|三|四|五|六|七|八|九|十)\s*(?:个)?(?:工作日|交易日|日)内[^。；\n]{0,80}?(?:通知|告知|披露)"
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("％", "%")


def _canonical_doc_id(value: Any) -> str:
    text = str(value or "")
    match = _DOC_TOKEN_RE.search(text)
    if match:
        return f"text{int(match.group(1)):02d}"
    return text.strip()


def _canonical_answer_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"supported", "support", "支持", "true"}:
        return "supported"
    if text in {"contradicted", "contradict", "反驳", "refuted", "false"}:
        return "contradicted"
    return "ambiguous"


def _has_all_quantifier(text: str) -> bool:
    compact = _compact(text)
    if any(marker in compact for marker in _ALL_MARKERS):
        return True
    return bool(re.search(r"(?:两份(?:文档|文件)|两个(?:文档|文件)|两家(?:发行人|公司))[^。；]{0,45}?(?:均|都)", compact))


def _has_any_quantifier(text: str) -> bool:
    compact = _compact(text)
    return any(marker in compact for marker in _ANY_MARKERS)


def is_cross_doc_option(option_text: str, required_doc_ids: Sequence[str]) -> bool:
    text = _compact(option_text)
    if len(required_doc_ids) < 2:
        return False
    if _has_all_quantifier(text) or _has_any_quantifier(text):
        return True
    if "第一份文档" in text or "第二份文档" in text:
        return True
    explicit_docs = {_canonical_doc_id(match.group(0)) for match in _DOC_TOKEN_RE.finditer(option_text)}
    required = {_canonical_doc_id(value) for value in required_doc_ids}
    return bool(explicit_docs & required)



def _expected_field_value(field_type: str, option_text: str) -> Any:
    if field_type == "issue_date":
        match = _EXPECTED_YM_RE.search(option_text)
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}" if match else None
    if field_type == "subject_credit_rating":
        match = re.search(r"(?:AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB)", option_text, re.IGNORECASE)
        return match.group(0).upper() if match else None
    if field_type == "bond_credit_rating":
        match = re.search(r"(?:AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB|负值)", option_text, re.IGNORECASE)
        return match.group(0).upper() if match else None
    if field_type == "issue_scale_cap":
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*亿元", option_text)
        return float(match.group(1).replace(",", "")) if match else None
    if field_type == "debt_asset_ratio":
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", option_text.replace("％", "%"))
        return float(match.group(1).replace(",", "")) if match else None
    if field_type == "stock_code":
        match = re.search(r"\d{6}", option_text)
        return match.group(0) if match else None
    if field_type == "stock_short_name":
        match = re.search(
            r"(?:股票|证券)简称(?:为|是|：|:)?\s*([*ＳＴSTA-Za-z0-9\u4e00-\u9fff·]{2,24})",
            option_text,
            re.IGNORECASE,
        )
        return match.group(1) if match else None
    if field_type in {
        "initial_conversion_price",
        "post_conversion_debt_ratio",
        "penalty_interest_multiplier",
    }:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:元(?:/股)?|%)", option_text.replace("％", "%"))
        return float(match.group(1).replace(",", "")) if match else None
    if field_type in {"lead_underwriter", "sponsor_institution"}:
        role_pattern = (
            r"(?:主承销商|牵头主承销商)"
            if field_type == "lead_underwriter"
            else r"(?:保荐机构|保荐人)"
        )
        match = re.search(
            role_pattern
            + r"(?:均)?(?:为|是|：|:)?\s*"
            + r"([\u4e00-\u9fffA-Za-z（）()·]{4,50}?(?:股份有限公司|有限责任公司|有限公司))",
            option_text,
        )
        return match.group(1) if match else None
    if field_type == "registration_amount_wording":
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*亿元", option_text)
        return {"semantic": "registration_amount", "value": float(match.group(1).replace(",", ""))} if match else None
    if field_type == "registration_approval_ceiling":
        match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*亿元", option_text)
        return {"semantic": "registration_approval_ceiling", "value": float(match.group(1).replace(",", ""))} if match else None
    if field_type == "issuer_name":
        contains_match = re.search(
            r"发行人(?:名称)?(?:中)?包含[‘'\"“]?([^’'\"”。，,]{2,40})",
            option_text,
        )
        if contains_match:
            return contains_match.group(1).strip()
        match = re.search(
            r"发行人(?:名称)?(?:为|是|：|:)?\s*"
            r"([\u4e00-\u9fffA-Za-z（）()·]{4,60}?(?:股份有限公司|有限责任公司|集团有限公司|有限公司))",
            option_text,
        )
        return match.group(1).strip() if match else None
    if field_type == "trustee_institution":
        match = re.search(
            r"(?:明确)?指定\s*([\u4e00-\u9fffA-Za-z（）()·]{4,60}?(?:股份有限公司|有限责任公司|有限公司))"
            r"(?:为|担任)(?:债券)?受托管理人",
            option_text,
        )
        if match:
            return match.group(1).strip()
        match = re.search(
            r"(?:债券)?受托管理人(?:为|是|：|:)?\s*"
            r"([\u4e00-\u9fffA-Za-z（）()·]{4,60}?(?:股份有限公司|有限责任公司|有限公司))",
            option_text,
        )
        return match.group(1).strip() if match else None
    if field_type == "listing_venue":
        match = re.search(r"(上海证券交易所|深圳证券交易所|北京证券交易所)", option_text)
        return match.group(1) if match else None
    if field_type == "document_type":
        if "面向专业投资者" in option_text and "募集说明书" in option_text:
            return "professional_investor_bond_prospectus"
        if "发行股份购买资产" in option_text and "报告书" in option_text:
            return "share_purchase_transaction_report"
    if field_type == "transaction_structure":
        return "share_purchase_with_supporting_funds" if (
            "发行股份购买资产" in option_text and "募集配套资金" in option_text
        ) else None
    if field_type == "payment_date":
        match = re.search(r"(20\d{2})\s*年(?:\s*(\d{1,2})\s*月)?(?:\s*(\d{1,2})\s*日)?", option_text)
        if match:
            date_value = (
                f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3) or 1):02d}"
                if match.group(2)
                else f"{int(match.group(1)):04d}"
            )
            semantic = (
                "put_redemption_payment_date"
                if any(token in option_text for token in ("回售", "赎回"))
                else "bond_variety_one_maturity_payment_date"
                if "品种一" in option_text
                else "generic_payment_date"
            )
            return {"semantic": semantic, "date": date_value}
    if field_type == "notification_deadline_days":
        match = re.search(r"(\d+|一|二|三|四|五|六|七|八|九|十)\s*(?:个)?(?:工作日|交易日|日)", option_text)
        if match:
            mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
            return int(match.group(1)) if match.group(1).isdigit() else mapping[match.group(1)]
    if field_type == "default_interest_formula":
        return "principal_and_interest" if "本金和利息" in option_text else None
    if field_type in {"asset_impairment_compensation_clause", "market_price_floor_clause"}:
        return True
    if field_type == "issuer_category" and "证券公司" in option_text:
        return "securities_company"
    return None


def detect_cross_doc_claim_spec(option_text: str, required_doc_ids: Sequence[str]) -> dict[str, Any]:
    docs = [_canonical_doc_id(value) for value in required_doc_ids]
    text = _compact(option_text)
    quantifier = "all" if _has_all_quantifier(text) else "any" if _has_any_quantifier(text) else "pairwise"
    field_type = ""
    relation_type = "all_presence"
    expected_value: Any = None

    if "股票代码" in text or "证券代码" in text:
        field_type = "stock_code"
    elif "股票简称" in text or "证券简称" in text:
        field_type = "stock_short_name"
    elif any(token in text for token in ("发行人名称", "发行人是", "发行人名称中包含")):
        field_type = "issuer_name"
    elif "受托管理人" in text:
        field_type = "trustee_institution"
    elif "上市地点" in text or "证券上市地点" in text:
        field_type = "listing_venue"
    elif "文件类型" in text:
        field_type = "document_type"
    elif "发行股份购买资产" in text and "募集配套资金" in text:
        field_type = "transaction_structure"
    elif "兑付日" in text:
        field_type = "payment_date"
    elif "通知" in text and ("日内" in text or "期限" in text):
        field_type = "notification_deadline_days"
    elif "资产减值补偿" in text:
        field_type = "asset_impairment_compensation_clause"
    elif "初始转股价格" in text and "不低于" in text:
        field_type = "market_price_floor_clause"
    elif "违约" in text and "本金和利息" in text:
        field_type = "default_interest_formula"
    elif "发行日期" in text or "发行日" in text:
        field_type = "issue_date"
    elif "主体信用评级" in text or "主体评级" in text or "主体信用等级" in text:
        field_type = "subject_credit_rating"
    elif "债项信用评级" in text or "债券信用评级" in text or "债券信用等级" in text:
        field_type = "bond_credit_rating"
    elif any(token in text for token in ("注册金额", "注册规模", "注册额度")):
        field_type = "registration_amount_wording"
    elif "注册批复" in text or "注册上限" in text:
        field_type = "registration_approval_ceiling"
    elif "违约罚息" in text or ("违约" in text and "%" in text):
        field_type = "penalty_interest_multiplier"
    elif "主承销商" in text:
        field_type = "lead_underwriter"
    elif "保荐机构" in text or "保荐人" in text:
        field_type = "sponsor_institution"
    elif "初始转股" in text:
        field_type = "initial_conversion_price"
    elif "全部转股后" in text and "资产负债率" in text:
        field_type = "post_conversion_debt_ratio"
    elif "发行金额" in text or "发行规模" in text:
        field_type = "issue_scale_cap"
    elif "最近三年" in text and "净利润" in text:
        field_type = "net_profit_three_year_series"
    elif "资产负债率" in text:
        field_type = "debt_asset_ratio"
    elif "证券公司" in text:
        field_type = "issuer_category"

    target_doc_id = None
    explicit_docs = [_canonical_doc_id(match.group(0)) for match in _DOC_TOKEN_RE.finditer(option_text)]
    explicit_docs = [doc for doc in explicit_docs if doc in docs]
    if len(set(explicit_docs)) == 1:
        target_doc_id = explicit_docs[0]
    elif "第一份文档" in text and "第二份文档" not in text and docs:
        target_doc_id = docs[0]
    elif "第二份文档" in text and "第一份文档" not in text and len(docs) >= 2:
        target_doc_id = docs[1]

    range_match = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*(?:至|到|[-~—])\s*(\d+(?:\.\d+)?)\s*%", option_text.replace("％", "%"))
    expected_value = (
        {"lower": float(range_match.group(1)), "upper": float(range_match.group(2))}
        if field_type == "debt_asset_ratio" and range_match
        else _expected_field_value(field_type, option_text)
    )
    if target_doc_id and field_type == "debt_asset_ratio" and range_match:
        relation_type = "document_scoped_field_range_all"
        docs = [target_doc_id]
    elif "第一份文档" in text and "第二份文档" in text and any(token in text for token in ("不一致", "不同")):
        relation_type = "field_distinct"
    elif "第二份文档" in text and "第一份文档" in text and "高于" in text:
        relation_type = "field_compare_gt"
    elif "第二份文档" in text and "第一份文档" in text and "低于" in text:
        relation_type = "field_compare_lt"
    elif target_doc_id and field_type == "issuer_name" and "包含" in text:
        relation_type = "document_scoped_field_contains"
        docs = [target_doc_id]
    elif target_doc_id:
        relation_type = "document_scoped_field_equals" if field_type and expected_value is not None else "document_scoped_presence"
        docs = [target_doc_id]
    elif quantifier == "any" and field_type and expected_value is not None:
        relation_type = "any_field_equals"
    elif field_type in {
        "issue_date",
        "subject_credit_rating",
        "bond_credit_rating",
        "issuer_category",
        "lead_underwriter",
        "sponsor_institution",
        "initial_conversion_price",
        "post_conversion_debt_ratio",
        "penalty_interest_multiplier",
        "registration_amount_wording",
        "registration_approval_ceiling",
        "issuer_name",
        "trustee_institution",
        "listing_venue",
        "document_type",
        "transaction_structure",
        "payment_date",
        "notification_deadline_days",
        "default_interest_formula",
        "asset_impairment_compensation_clause",
        "market_price_floor_clause",
    } and expected_value is not None:
        relation_type = "all_field_equals"

    return {
        "schema_version": "cross_doc_conjunctive_claim_v1",
        "option_text": option_text,
        "quantifier": quantifier,
        "required_doc_ids": docs,
        "question_doc_ids": [_canonical_doc_id(value) for value in required_doc_ids],
        "target_doc_id": target_doc_id,
        "relation_type": relation_type,
        "field_type": field_type,
        "expected_value": expected_value,
    }


def _role_institution_values(text: str, role_tokens: Sequence[str]) -> list[str]:
    """Return the nearest legal entity bound to an issuance-role label.

    HTML table cells and cover-page line breaks are preserved so an issuer or
    rating agency several rows away cannot be mistaken for the underwriter.
    Table-of-contents declaration headings are not role facts.
    """
    company_re = re.compile(
        r"([\u4e00-\u9fff]{2,40}?(?:股份有限公司|有限责任公司|有限公司))"
    )
    normalised = re.sub(
        r"</td>\s*<td[^>]*>|</tr>\s*<tr[^>]*>|<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    normalised = re.sub(r"<[^>]+>", " ", normalised)
    lines = [re.sub(r"\s+", "", line) for line in normalised.splitlines() if line.strip()]
    values: list[str] = []
    for index, line in enumerate(lines):
        if not any(token in line for token in role_tokens):
            continue
        if any(token in line for token in ("目录", "声明", "董事长", "总经理")) and not company_re.search(line):
            continue
        # A role label may be followed by a brand alias and then the full legal
        # name.  Stop at the first legal entity; do not scan into later rows.
        for offset, candidate_line in enumerate(lines[index : index + 4]):
            if offset > 0 and any(
                boundary in candidate_line
                for boundary in (
                    "签署日期",
                    "募集说明书",
                    "发行人声明",
                    "重大事项提示",
                )
            ):
                break
            match = company_re.search(candidate_line)
            if match is None:
                continue
            value = match.group(1)
            for prefix in ("CICC中金公司", "中金公司"):
                if value.startswith(prefix):
                    value = value[len(prefix):]
            if value and value not in values:
                values.append(value)
            break
    return values


def _field_values(field_type: str, text: str) -> list[Any]:
    values: list[Any] = []
    if field_type == "stock_code":
        values.extend(match.group(1) for match in _STOCK_CODE_RE.finditer(text))
    elif field_type == "stock_short_name":
        values.extend(match.group(1).strip() for match in _STOCK_SHORT_NAME_RE.finditer(text))
    elif field_type == "issue_date":
        for match in _ISSUE_DATE_RE.finditer(text):
            values.append(f"{int(match.group(1)):04d}-{int(match.group(2)):02d}")
        for match in _ISSUE_SCHEDULE_T_DAY_RE.finditer(text):
            values.append(f"{int(match.group(1)):04d}-{int(match.group(2)):02d}")
    elif field_type == "subject_credit_rating":
        values.extend(match.group(1).upper() for match in _RATING_RE.finditer(text))
    elif field_type == "bond_credit_rating":
        values.extend(match.group(1).upper() for match in _BOND_RATING_RE.finditer(text))
        compact = _compact(text)
        if "无债项评级" in compact or "本期债券无评级" in compact or "未进行评级" in compact:
            values.append("NO_RATING")
    elif field_type == "issue_scale_cap":
        scale_values = [float(match.group(1).replace(",", "")) for match in _ISSUE_SCALE_RE.finditer(text)]
        if scale_values:
            values.append(scale_values[-1])
    elif field_type == "debt_asset_ratio":
        values.extend(float(match.group(1).replace(",", "")) for match in _DEBT_ASSET_RATIO_RE.finditer(text.replace("％", "%")))
    elif field_type == "initial_conversion_price":
        values.extend(float(match.group(1).replace(",", "")) for match in _INITIAL_CONVERSION_PRICE_RE.finditer(text))
    elif field_type == "post_conversion_debt_ratio":
        values.extend(float(match.group(1).replace(",", "")) for match in _POST_CONVERSION_DEBT_RATIO_RE.finditer(text.replace("％", "%")))
    elif field_type == "penalty_interest_multiplier":
        values.extend(float(match.group(1).replace(",", "")) for match in _PENALTY_MULTIPLIER_RE.finditer(text.replace("％", "%")))
    elif field_type == "lead_underwriter":
        values.extend(_role_institution_values(text, ("主承销商", "牵头主承销商")))
    elif field_type == "sponsor_institution":
        values.extend(_role_institution_values(text, ("保荐机构", "保荐人")))
    elif field_type == "registration_amount_wording":
        values.extend(
            {"semantic": "registration_amount", "value": float(match.group(1).replace(",", ""))}
            for match in _REGISTRATION_AMOUNT_RE.finditer(text)
        )
        values.extend(
            {"semantic": "registration_approval_ceiling", "value": float(match.group(1).replace(",", ""))}
            for match in _REGISTRATION_APPROVAL_RE.finditer(text)
        )
    elif field_type == "registration_approval_ceiling":
        values.extend(
            {"semantic": "registration_approval_ceiling", "value": float(match.group(1).replace(",", ""))}
            for match in _REGISTRATION_APPROVAL_RE.finditer(text)
        )
    elif field_type == "issuer_name":
        for match in re.finditer(
            r"(?:发行人(?:名称)?|公司名称|中文名称)(?:为|是|：|:|</td><td[^>]*>)*\s*"
            r"([\u4e00-\u9fffA-Za-z（）()·]{4,60}?(?:股份有限公司|有限责任公司|集团有限公司|有限公司))",
            text,
        ):
            values.append(match.group(1).strip())
        values.extend(
            match.group(1).strip()
            for match in re.finditer(
                r"^\s*#\s*([\u4e00-\u9fffA-Za-z（）()·]{4,60}?(?:股份有限公司|有限责任公司|集团有限公司|有限公司))\s*$",
                text,
                re.MULTILINE,
            )
        )
    elif field_type == "trustee_institution":
        values.extend(_role_institution_values(text, ("债券受托管理人", "受托管理人名称", "受托管理人")))
    elif field_type == "listing_venue":
        values.extend(match.group(1) for match in _LISTING_VENUE_RE.finditer(text))
    elif field_type == "document_type":
        compact = _compact(text)
        if "面向专业投资者" in compact and "募集说明书" in compact:
            values.append("professional_investor_bond_prospectus")
        if "发行股份购买资产" in compact and "报告书" in compact:
            values.append("share_purchase_transaction_report")
        if "重大资产重组" in compact and "报告书" in compact:
            values.append("major_asset_restructuring_report")
    elif field_type == "transaction_structure":
        compact = _compact(text)
        if "发行股份购买资产" in compact and "募集配套资金" in compact:
            values.append("share_purchase_with_supporting_funds")
    elif field_type == "payment_date":
        for match in re.finditer(
            r"品种一的兑付日为\s*(20\d{2})年(\d{1,2})月(\d{1,2})日",
            text,
        ):
            values.append({
                "semantic": "bond_variety_one_maturity_payment_date",
                "date": f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}",
            })
        for match in re.finditer(
            r"(?:回售|赎回)[^。；\n]{0,80}?部分债券的兑付日为\s*"
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
            text,
        ):
            values.append({
                "semantic": "put_redemption_payment_date",
                "date": f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}",
            })
        if not values:
            for match in _PAYMENT_DATE_RE.finditer(text):
                values.append({
                    "semantic": "generic_payment_date",
                    "date": f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3) or 1):02d}",
                })
    elif field_type == "notification_deadline_days":
        mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        for match in _NOTIFICATION_DAYS_RE.finditer(text):
            token = match.group(1)
            values.append(int(token) if token.isdigit() else mapping[token])
    elif field_type == "default_interest_formula":
        compact = _compact(text)
        if "违约" in compact and "本金和利息" in compact:
            values.append("principal_and_interest")
    elif field_type == "asset_impairment_compensation_clause":
        if "资产减值补偿" in _compact(text):
            values.append(True)
    elif field_type == "market_price_floor_clause":
        compact = _compact(text)
        if "初始转股价格" in compact and "不低于" in compact and "募集说明书公告" in compact:
            values.append(True)
    elif field_type == "issuer_category":
        compact = _compact(text)
        if (
            "发行人为证券公司" in compact
            or "发行人属于证券公司" in compact
            or re.search(r"^\s*#\s*[^#。\n]{0,40}证券股份有限公司", text)
            or re.search(r"(?:公司名称|中文名称|发行主体)[：:]?[^。\n]{0,40}证券股份有限公司", text)
        ):
            values.append("securities_company")
        if (
            "发行人为融资租赁公司" in compact
            or "发行人属于融资租赁公司" in compact
            or re.search(r"(?:公司名称|中文名称|发行主体)[：:]?[^。\n]{0,50}融资租赁[^。\n]{0,20}(?:有限公司|集团)", text)
            or re.search(r"^\s*#\s*[^#。\n]{0,40}融资租赁[^#。\n]{0,20}(?:有限公司|集团)", text)
        ):
            values.append("financing_lease_company")
    output: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = repr(value)
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output


def _evidence_score(item: Mapping[str, Any], field_type: str) -> float:
    source = str(item.get("canonical_source") or item.get("source") or "")
    window = str(item.get("local_window") or item.get("text") or "")
    score = float(item.get("score") or 0.0)
    if source.endswith("page_0001.md"):
        score += 20.0
    if field_type and _field_values(field_type, window):
        score += 30.0
    if field_type == "stock_code" and any(label in window for label in ("股票代码", "证券代码")):
        score += 20.0
    if field_type == "stock_short_name" and any(label in window for label in ("股票简称", "证券简称")):
        score += 20.0
    if field_type == "issue_date" and any(label in window for label in ("发行日期", "发行日", "发行时间", "T 日", "T日")):
        score += 20.0
    if field_type in {"lead_underwriter", "sponsor_institution"} and any(
        label in window for label in ("主承销商", "保荐机构", "保荐人")
    ):
        score += 20.0
    if field_type in {"registration_amount_wording", "registration_approval_ceiling"} and "注册" in window:
        score += 20.0
    return score


def _field_segments(text: str, field_type: str) -> list[str]:
    if field_type == "debt_asset_ratio":
        parts = re.split(r"[。；;，\n]+", text)
    elif field_type in {
        "issue_scale_cap",
        "stock_code",
        "stock_short_name",
        "lead_underwriter",
        "sponsor_institution",
        "registration_amount_wording",
        "registration_approval_ceiling",
        "issue_date",
        "issuer_name",
        "trustee_institution",
        "listing_venue",
        "document_type",
        "transaction_structure",
        "payment_date",
        "notification_deadline_days",
        "default_interest_formula",
        "asset_impairment_compensation_clause",
        "market_price_floor_clause",
    }:
        # Preserve table-like fields whose label and value are separated by line
        # breaks or HTML cells.  Sentence boundaries still constrain matching.
        parts = re.split(r"[。；;]+", text)
    else:
        parts = re.split(r"[。；;\n]+", text)
    segments = [part.strip() for part in parts if part.strip()]
    return segments or [text]


def _claim_alignment_score(field_type: str, claim_text: str, segment: str) -> float:
    claim = _compact(claim_text)
    unit = _compact(segment)
    score = 0.0
    if field_type == "debt_asset_ratio":
        if "发行人" in claim or "合并口径" in claim:
            if any(marker in unit for marker in ("发行人资产负债率", "上市公司资产负债率", "发行人（合并）", "合并口径资产负债率")):
                score += 140.0
            if any(marker in unit for marker in ("控股股东", "间接控股股东", "标的公司")):
                score -= 180.0
            if "合并口径" in claim and "合并" not in unit:
                score -= 120.0
        claim_indirect = "间接控股股东" in claim
        unit_indirect = "间接控股股东" in unit
        if claim_indirect == unit_indirect:
            score += 40.0
        else:
            score -= 40.0
        if "控股股东" in claim and "控股股东" in unit:
            score += 15.0
        entity_match = re.search(r"控股股东([\u4e00-\u9fffA-Za-z0-9（）()·]{2,20})", claim)
        if entity_match and entity_match.group(1) in unit:
            score += 20.0
    if field_type == "issue_scale_cap":
        if "本期" in claim and "本期" in unit:
            score += 30.0
        if "上限" in claim and ("不超过" in unit or "上限" in unit):
            score += 15.0
    if field_type == "issuer_category":
        if any(marker in unit for marker in ("公司名称", "中文名称", "发行主体", "（一）发行人", "一、发行人")):
            score += 100.0
        elif "发行人" in unit:
            score += 60.0
        if any(marker in unit for marker in ("主承销商", "簿记管理人", "受托管理人", "联席主承销商")):
            score -= 120.0
    if field_type == "issuer_name":
        expected = _expected_field_value(field_type, claim_text)
        if expected and _compact(expected) in unit:
            score += 180.0
        if any(marker in unit for marker in ("发行人名称", "公司名称", "中文名称", "#")):
            score += 80.0
        if any(marker in unit for marker in ("主承销商", "受托管理人", "保荐机构", "评级机构")):
            score -= 120.0
    if field_type == "trustee_institution":
        if "受托管理人" in unit:
            score += 120.0
        if any(marker in unit for marker in ("前次", "时任", "历史")):
            score -= 100.0
    if field_type in {"listing_venue", "stock_short_name"} and any(
        marker in unit for marker in ("上市地点", "股票简称", "证券简称")
    ):
        score += 120.0
    if field_type == "document_type" and any(marker in unit for marker in ("募集说明书", "报告书（草案）", "报告书(草案)")):
        score += 100.0
    if field_type == "transaction_structure" and "发行股份购买资产" in unit and "募集配套资金" in unit:
        score += 120.0
    if field_type == "payment_date" and "兑付日" in unit:
        score += 120.0
    if field_type == "notification_deadline_days" and any(marker in unit for marker in ("通知", "告知", "披露")):
        score += 100.0
    return score


def _bounded_field_window(segment: str, field_type: str, value: Any, radius: int = 320) -> str:
    text = str(segment or "")
    value_text = str(value)
    value_index = text.find(value_text)
    labels = {
        "stock_code": ("股票代码", "证券代码"),
        "stock_short_name": ("股票简称", "证券简称"),
        "issue_date": ("发行日期", "发行日", "发行时间"),
        "subject_credit_rating": ("主体信用评级", "主体评级", "主体信用等级"),
        "bond_credit_rating": ("债项信用评级", "债券信用评级", "债券信用等级", "无债项评级"),
        "issue_scale_cap": ("本期债券发行规模", "发行规模", "发行金额", "发行总额"),
        "debt_asset_ratio": ("资产负债率",),
        "initial_conversion_price": ("初始转股价格", "初始转股价"),
        "post_conversion_debt_ratio": ("全部转股后", "资产负债率"),
        "penalty_interest_multiplier": ("违约金", "违约罚息", "罚息"),
        "lead_underwriter": ("主承销商", "牵头主承销商"),
        "sponsor_institution": ("保荐机构", "保荐人"),
        "registration_amount_wording": ("注册金额", "注册规模", "注册额度", "同意注册"),
        "registration_approval_ceiling": ("同意注册", "注册的批复"),
        "issuer_name": ("发行人名称", "公司名称", "中文名称", "发行人"),
        "trustee_institution": ("债券受托管理人", "受托管理人名称", "受托管理人"),
        "listing_venue": ("上市地点", "证券上市地点", "上市地"),
        "document_type": ("募集说明书", "报告书（草案）", "报告书(草案)"),
        "transaction_structure": ("发行股份购买资产", "募集配套资金"),
        "payment_date": ("到期兑付日", "本金兑付日", "兑付日"),
        "notification_deadline_days": ("通知", "告知", "披露"),
        "default_interest_formula": ("违约金具体计算方式", "本金和利息"),
        "asset_impairment_compensation_clause": ("资产减值补偿", "减值补偿"),
        "market_price_floor_clause": ("初始转股价格", "不低于募集说明书公告"),
        "issuer_category": ("发行人", "证券股份有限公司"),
    }.get(field_type, ())
    label_positions = [text.rfind(label, 0, value_index + 1) for label in labels] if value_index >= 0 else []
    label_index = max(label_positions, default=-1)
    anchor = label_index if label_index >= 0 and value_index - label_index <= 800 else value_index
    if anchor < 0:
        anchor = 0
    start = max(0, anchor - radius)
    end_anchor = value_index + len(value_text) if value_index >= 0 else anchor
    end = min(len(text), end_anchor + radius)
    return text[start:end].strip()


def _best_field_fact(
    doc_id: str,
    field_type: str,
    evidence: Sequence[Mapping[str, Any]],
    claim_text: str = "",
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for raw in evidence:
        item = dict(raw)
        source = str(item.get("canonical_source") or item.get("source") or "")
        window = str(item.get("local_window") or item.get("text") or "")
        for segment in _field_segments(window, field_type):
            for value in _field_values(field_type, segment):
                value_alignment = 0.0
                expected = _expected_field_value(field_type, claim_text)
                if isinstance(value, Mapping) and isinstance(expected, Mapping):
                    value_alignment += 240.0 if value.get("semantic") == expected.get("semantic") else -240.0
                candidates.append({
                    "value": value,
                    "canonical_source": source,
                    "local_window": _bounded_field_window(segment, field_type, value),
                    "score": (
                        _evidence_score(item, field_type)
                        + _claim_alignment_score(field_type, claim_text, segment)
                        + value_alignment
                    ),
                })
    if not candidates:
        return {
            "doc_id": doc_id,
            "status": "ambiguous",
            "value": None,
            "canonical_source": "",
            "local_window": "",
            "certification_basis": f"no explicit {field_type} field found in this document",
        }
    expected = _expected_field_value(field_type, claim_text)
    if expected is not None:
        exact = [row for row in candidates if _field_values_equal(row.get("value"), expected)]
        if exact:
            candidates = exact
    candidates.sort(key=lambda row: (float(row["score"]), -len(str(row["local_window"]))), reverse=True)
    best_score = float(candidates[0]["score"])
    top = [row for row in candidates if float(row["score"]) == best_score]
    values = {repr(row["value"]) for row in top}
    if len(values) > 1:
        return {
            "doc_id": doc_id,
            "status": "ambiguous",
            "value": None,
            "canonical_source": "",
            "local_window": "",
            "certification_basis": f"conflicting top-ranked {field_type} values in one document",
        }
    best = top[0]
    return {
        "doc_id": doc_id,
        "status": "supported",
        "value": best["value"],
        "canonical_source": best["canonical_source"],
        "local_window": best["local_window"],
        "certification_basis": f"explicit document-local {field_type} field with claim-role binding",
    }


def _single_doc_option_text(option_text: str, doc_id: str) -> str:
    text = str(option_text or "")
    for marker in _ALL_MARKERS:
        text = text.replace(marker, f"文档 {doc_id}")
    text = re.sub(r"两份(?:文档|文件)的?", f"文档 {doc_id} 的", text)
    text = re.sub(r"两个(?:文档|文件)的?", f"文档 {doc_id} 的", text)
    text = text.replace("两家发行人的", f"文档 {doc_id} 发行人的")
    text = text.replace("两家发行人", f"文档 {doc_id} 发行人")
    text = text.replace("均", "").replace("都", "")
    if "文档 " + doc_id not in text:
        text = f"文档 {doc_id} {text}"
    return text


def _certify_presence_subclaim(
    doc_id: str,
    option_text: str,
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    supported: list[dict[str, Any]] = []
    contradicted: list[dict[str, Any]] = []
    subclaim_text = _single_doc_option_text(option_text, doc_id)
    for raw in evidence:
        source = str(raw.get("canonical_source") or raw.get("source") or "")
        window = str(raw.get("local_window") or raw.get("text") or "")
        if not source or not window:
            continue
        payload = {
            "option_text": subclaim_text,
            "question_doc_ids": [doc_id],
            "resolved_evidence_refs": [source],
            "evidence_refs": [source],
            "source_resolution": [{
                "canonical_ref": source,
                "resolved_path": source,
                "read_status": "read",
                "bounded_context": window,
                "page_or_lineage": source,
            }],
        }
        result = certify_typed_option_claim(payload, replacement_effect="keep_baseline")
        row = {
            "result": result,
            "score": float(raw.get("score") or 0.0) + len(result.get("matched_atoms") or []),
        }
        status = _canonical_answer_status(result.get("claim_certification_status"))
        if status == "supported":
            supported.append(row)
        elif status == "contradicted":
            contradicted.append(row)
    if supported and contradicted:
        return {
            "doc_id": doc_id,
            "claim": subclaim_text,
            "status": "ambiguous",
            "canonical_source": "",
            "local_window": "",
            "certification_basis": "both supporting and contradicting typed certifications exist in this document",
        }
    pool = supported or contradicted
    if not pool:
        return {
            "doc_id": doc_id,
            "claim": subclaim_text,
            "status": "ambiguous",
            "canonical_source": "",
            "local_window": "",
            "certification_basis": "no document-local typed certification for the required relation",
        }
    pool.sort(key=lambda row: float(row["score"]), reverse=True)
    result = pool[0]["result"]
    return {
        "doc_id": doc_id,
        "claim": subclaim_text,
        "status": _canonical_answer_status(result.get("claim_certification_status")),
        "canonical_source": str(result.get("canonical_source") or ""),
        "local_window": str(result.get("local_window") or ""),
        "certification_basis": str(result.get("certification_basis") or ""),
    }


def _aggregate_all(subclaims: Sequence[Mapping[str, Any]], required_docs: Sequence[str]) -> tuple[str, str, list[str], list[str]]:
    statuses = {str(row.get("doc_id")): str(row.get("status") or "ambiguous") for row in subclaims}
    contradicted = [doc for doc in required_docs if statuses.get(doc) == "contradicted"]
    missing = [doc for doc in required_docs if statuses.get(doc) != "supported" and doc not in contradicted]
    if contradicted:
        return "contradicted", "at least one required document explicitly contradicts the same relation or field", missing, contradicted
    if not missing and required_docs:
        return "supported", "all required documents independently support their local subclaim", [], []
    return "ambiguous", "not all required document subclaims are independently certified", missing, []


def _field_values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= 1e-9
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        if actual.get("semantic") != expected.get("semantic"):
            return False
        actual_date = str(actual.get("date") or "")
        expected_date = str(expected.get("date") or "")
        if expected_date and actual_date:
            return actual_date.startswith(expected_date)
        return actual == expected
    return actual == expected


def _has_complete_scan(evidence: Sequence[Mapping[str, Any]]) -> bool:
    return any(item.get("complete_document_scan") is True for item in evidence)


def _all_numeric_field_values(
    field_type: str, evidence: Sequence[Mapping[str, Any]], claim_text: str
) -> tuple[list[float], str, str]:
    rows: list[tuple[float, str, str, float]] = []
    for raw in evidence:
        source = str(raw.get("canonical_source") or raw.get("source") or "")
        window = str(raw.get("local_window") or raw.get("text") or "")
        for segment in _field_segments(window, field_type):
            alignment = _claim_alignment_score(field_type, claim_text, segment)
            for value in _field_values(field_type, segment):
                if isinstance(value, (int, float)):
                    rows.append((float(value), source, segment, alignment + float(raw.get("score") or 0.0)))
    if not rows:
        return [], "", ""
    rows.sort(key=lambda item: item[3], reverse=True)
    best = rows[0][3]
    selected = [item for item in rows if item[3] >= best - 1e-9]
    values = list(dict.fromkeys(item[0] for item in selected))
    return values, selected[0][1], selected[0][2]


def _certify_strict_presence_field(
    doc_id: str,
    field_type: str,
    option_text: str,
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    complete = _has_complete_scan(evidence)
    if field_type == "net_profit_three_year_series":
        for raw in evidence:
            text = str(raw.get("local_window") or raw.get("text") or "")
            for segment in re.split(r"[。；;\n]+", text):
                compact = _compact(segment)
                years = {year for year in ("2022", "2023", "2024") if year in compact}
                numbers = re.findall(r"(?<!\d)-?\d[\d,]*(?:\.\d+)?", segment)
                if "净利润" in compact and len(years) == 3 and len(numbers) >= 6:
                    return {
                        "doc_id": doc_id,
                        "claim": option_text,
                        "status": "supported",
                        "canonical_source": str(raw.get("canonical_source") or ""),
                        "local_window": segment[:1600],
                        "certification_basis": "one document-local table or sentence binds 2022-2024 net-profit values",
                    }
        status = "contradicted" if complete else "ambiguous"
        return {
            "doc_id": doc_id,
            "claim": option_text,
            "status": status,
            "canonical_source": str(next((x.get("canonical_source") for x in evidence if x.get("complete_document_scan")), "")),
            "local_window": "",
            "certification_basis": (
                "complete declared-document scan found no coherent 2022-2024 net-profit series"
                if complete
                else "three-year net-profit series not found in available evidence"
            ),
        }
    if field_type == "debt_asset_ratio" and "合并口径" in option_text:
        for raw in evidence:
            text = str(raw.get("local_window") or raw.get("text") or "")
            for segment in re.split(r"[。；;\n]+", text):
                compact = _compact(segment)
                if "资产负债率" not in compact or not re.search(r"\d+(?:\.\d+)?%", compact):
                    continue
                issuer_role = any(
                    marker in compact
                    for marker in (
                        "发行人资产负债率",
                        "上市公司资产负债率",
                        "发行人（合并）",
                        "合并口径资产负债率",
                    )
                )
                explicit_consolidated = "合并" in compact
                wrong_role = any(
                    marker in compact for marker in ("控股股东", "间接控股股东", "标的公司")
                )
                if issuer_role and explicit_consolidated and not wrong_role:
                    return {
                        "doc_id": doc_id,
                        "claim": option_text,
                        "status": "supported",
                        "canonical_source": str(raw.get("canonical_source") or ""),
                        "local_window": segment[:1600],
                        "certification_basis": "document-local issuer consolidated debt-ratio disclosure",
                    }
        status = "contradicted" if complete else "ambiguous"
        return {
            "doc_id": doc_id,
            "claim": option_text,
            "status": status,
            "canonical_source": str(next((x.get("canonical_source") for x in evidence if x.get("complete_document_scan")), "")),
            "local_window": "",
            "certification_basis": (
                "complete declared-document scan found no explicit issuer consolidated debt-ratio disclosure"
                if complete
                else "issuer consolidated debt-ratio scope unresolved"
            ),
        }
    fact = _best_field_fact(doc_id, field_type, evidence, option_text)
    if fact["status"] == "supported":
        return {
            "doc_id": doc_id,
            "claim": option_text,
            "status": "supported",
            "canonical_source": fact["canonical_source"],
            "local_window": fact["local_window"],
            "certification_basis": fact["certification_basis"],
        }
    return {
        "doc_id": doc_id,
        "claim": option_text,
        "status": "contradicted" if complete else "ambiguous",
        "canonical_source": str(next((x.get("canonical_source") for x in evidence if x.get("complete_document_scan")), "")),
        "local_window": "",
        "certification_basis": (
            f"complete declared-document scan found no explicit {field_type} field"
            if complete
            else fact["certification_basis"]
        ),
    }

def certify_cross_doc_option(
    *,
    option_label: str,
    option_text: str,
    required_doc_ids: Sequence[str],
    evidence_by_doc: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    spec = detect_cross_doc_claim_spec(option_text, required_doc_ids)
    required_docs = list(spec["required_doc_ids"])
    normalised_evidence = {
        _canonical_doc_id(doc): list(rows or [])
        for doc, rows in evidence_by_doc.items()
    }
    relation_type = str(spec["relation_type"])
    field_type = str(spec["field_type"] or "")
    subclaims: list[dict[str, Any]] = []

    if relation_type == "document_scoped_field_equals":
        doc = required_docs[0] if required_docs else ""
        fact = _best_field_fact(doc, field_type, normalised_evidence.get(doc, []), option_text)
        status = "ambiguous"
        basis = fact["certification_basis"]
        if fact["status"] == "supported" and spec.get("expected_value") is not None:
            actual = fact.get("value")
            expected = spec.get("expected_value")
            equal = _field_values_equal(actual, expected)
            status = "supported" if equal else "contradicted"
            basis = f"document-local {field_type} equals expected value" if equal else f"document-local {field_type} explicitly differs from expected value"
        subclaims = [{
            "doc_id": doc,
            "claim": f"{field_type} equals {spec.get('expected_value')}",
            "status": status,
            "value": fact.get("value"),
            "canonical_source": fact["canonical_source"],
            "local_window": fact["local_window"],
            "certification_basis": basis,
        }]
        aggregate_status, aggregate_basis, missing, conflicting = _aggregate_all(subclaims, required_docs)
    elif relation_type == "document_scoped_field_range_all":
        doc = required_docs[0] if required_docs else ""
        rows = normalised_evidence.get(doc, [])
        values, source, window = _all_numeric_field_values(field_type, rows, option_text)
        bounds = dict(spec.get("expected_value") or {})
        lower = float(bounds.get("lower", 0.0))
        upper = float(bounds.get("upper", 0.0))
        if values:
            holds = all(lower <= value <= upper for value in values)
            status = "supported" if holds else "contradicted"
            basis = "all document-local historical field values fall within the stated range" if holds else "at least one document-local historical field value falls outside the stated range"
        else:
            status = "contradicted" if _has_complete_scan(rows) else "ambiguous"
            basis = "complete declared-document scan found no historical field series" if status == "contradicted" else "historical field series unresolved"
        subclaims = [{
            "doc_id": doc, "claim": f"all {field_type} values within [{lower}, {upper}]",
            "status": status, "value": values, "canonical_source": source,
            "local_window": window[:1600], "certification_basis": basis,
        }]
        aggregate_status, aggregate_basis, missing, conflicting = _aggregate_all(subclaims, required_docs)
    elif relation_type == "document_scoped_field_contains":
        doc = required_docs[0] if required_docs else ""
        fact = _best_field_fact(doc, field_type, normalised_evidence.get(doc, []), option_text)
        expected = str(spec.get("expected_value") or "")
        status = "ambiguous"
        basis = fact["certification_basis"]
        if fact["status"] == "supported" and expected:
            contains = _compact(expected) in _compact(fact.get("value"))
            status = "supported" if contains else "contradicted"
            basis = (
                f"document-local {field_type} contains expected value"
                if contains
                else f"document-local {field_type} does not contain expected value"
            )
        subclaims = [{
            "doc_id": doc,
            "claim": f"{field_type} contains {expected}",
            "status": status,
            "value": fact.get("value"),
            "canonical_source": fact["canonical_source"],
            "local_window": fact["local_window"],
            "certification_basis": basis,
        }]
        aggregate_status, aggregate_basis, missing, conflicting = _aggregate_all(subclaims, required_docs)
    elif relation_type == "any_field_equals":
        expected = spec.get("expected_value")
        matched_docs: list[str] = []
        explicit_docs: list[str] = []
        missing = []
        conflicting = []
        for doc in required_docs:
            fact = _best_field_fact(doc, field_type, normalised_evidence.get(doc, []), option_text)
            status = "ambiguous"
            basis = fact["certification_basis"]
            if fact["status"] == "supported" and expected is not None:
                explicit_docs.append(doc)
                equal = _field_values_equal(fact.get("value"), expected)
                status = "supported" if equal else "contradicted"
                basis = (
                    f"document-local {field_type} equals expected value"
                    if equal
                    else f"document-local {field_type} explicitly differs from expected value"
                )
                if equal:
                    matched_docs.append(doc)
                else:
                    conflicting.append(doc)
            else:
                missing.append(doc)
            subclaims.append({
                "doc_id": doc,
                "claim": f"{field_type} equals {expected}",
                "status": status,
                "value": fact.get("value"),
                "canonical_source": fact["canonical_source"],
                "local_window": fact["local_window"],
                "certification_basis": basis,
            })
        if matched_docs:
            aggregate_status = "supported"
            aggregate_basis = "at least one required document explicitly matches the exact field value"
            missing = []
            conflicting = []
        elif len(explicit_docs) == len(required_docs) and required_docs:
            aggregate_status = "contradicted"
            aggregate_basis = "every required document has an explicit incompatible exact field value"
            missing = []
        else:
            aggregate_status = "ambiguous"
            aggregate_basis = "no required document explicitly matches and at least one exact field remains unresolved"
    elif relation_type == "field_distinct":
        facts = [_best_field_fact(doc, field_type, normalised_evidence.get(doc, []), option_text) for doc in required_docs]
        subclaims = [
            {
                "doc_id": row["doc_id"],
                "claim": f"extract {field_type} for pairwise distinct comparison",
                "status": row["status"],
                "value": row.get("value"),
                "canonical_source": row["canonical_source"],
                "local_window": row["local_window"],
                "certification_basis": row["certification_basis"],
            }
            for row in facts
        ]
        missing = [row["doc_id"] for row in facts if row["status"] != "supported"]
        if missing:
            aggregate_status = "ambiguous"
            aggregate_basis = "pairwise field comparison requires explicit values from every required document"
            conflicting: list[str] = []
        else:
            values = [row["value"] for row in facts]
            aggregate_status = "supported" if len({repr(value) for value in values}) > 1 else "contradicted"
            aggregate_basis = "all document-local field values are present and differ" if aggregate_status == "supported" else "all document-local field values are present but equal"
            conflicting = required_docs if aggregate_status == "contradicted" else []
    elif relation_type in {"field_compare_gt", "field_compare_lt"}:
        facts = [_best_field_fact(doc, field_type, normalised_evidence.get(doc, []), option_text) for doc in required_docs]
        subclaims = [
            {
                "doc_id": row["doc_id"],
                "claim": f"extract {field_type} for ordered comparison",
                "status": row["status"],
                "value": row.get("value"),
                "canonical_source": row["canonical_source"],
                "local_window": row["local_window"],
                "certification_basis": row["certification_basis"],
            }
            for row in facts
        ]
        missing = [row["doc_id"] for row in facts if row["status"] != "supported"]
        conflicting = []
        if missing or len(facts) < 2:
            aggregate_status = "ambiguous"
            aggregate_basis = "ordered field comparison requires explicit values from both documents"
        else:
            first, second = facts[0]["value"], facts[1]["value"]
            numeric = isinstance(first, (int, float)) and isinstance(second, (int, float))
            if not numeric:
                aggregate_status = "ambiguous"
                aggregate_basis = "ordered comparison requires numeric values"
            else:
                comparison_holds = (
                    float(second) > float(first)
                    if relation_type == "field_compare_gt"
                    else float(second) < float(first)
                )
                aggregate_status = "supported" if comparison_holds else "contradicted"
                direction = "greater than" if relation_type == "field_compare_gt" else "less than"
                aggregate_basis = (
                    f"second document field value is {direction} first"
                    if aggregate_status == "supported"
                    else f"second document field value is not {direction} first"
                )
                if aggregate_status == "contradicted":
                    conflicting = required_docs
    elif relation_type == "all_field_equals":
        expected = spec.get("expected_value")
        for doc in required_docs:
            fact = _best_field_fact(doc, field_type, normalised_evidence.get(doc, []), option_text)
            status = "ambiguous"
            basis = fact["certification_basis"]
            if fact["status"] == "supported" and expected is not None:
                status = "supported" if _field_values_equal(fact.get("value"), expected) else "contradicted"
                basis = f"document-local {field_type} equals expected value" if status == "supported" else f"document-local {field_type} explicitly differs from expected value"
            subclaims.append({
                "doc_id": doc,
                "claim": f"{field_type} equals {expected}",
                "status": status,
                "value": fact.get("value"),
                "canonical_source": fact["canonical_source"],
                "local_window": fact["local_window"],
                "certification_basis": basis,
            })
        aggregate_status, aggregate_basis, missing, conflicting = _aggregate_all(subclaims, required_docs)
    else:
        subclaims = [
            (
                _certify_strict_presence_field(doc, field_type, option_text, normalised_evidence.get(doc, []))
                if field_type
                else _certify_presence_subclaim(doc, option_text, normalised_evidence.get(doc, []))
            )
            for doc in required_docs
        ]
        aggregate_status, aggregate_basis, missing, conflicting = _aggregate_all(subclaims, required_docs)

    refs = [str(row.get("canonical_source") or "") for row in subclaims if row.get("canonical_source")]
    return {
        "schema_version": "cross_doc_conjunctive_claim_v1",
        "option_label": str(option_label).upper(),
        "option_text": option_text,
        "quantifier": spec["quantifier"],
        "required_doc_ids": required_docs,
        "relation_type": relation_type,
        "field_type": field_type,
        "expected_value": spec.get("expected_value"),
        "subclaims": subclaims,
        "aggregate_status": aggregate_status,
        "aggregate_basis": aggregate_basis,
        "missing_docs": missing,
        "conflicting_docs": conflicting,
        "evidence_refs": list(dict.fromkeys(refs)),
        "trusted_for_option_gate": bool(
            (aggregate_status == "supported" and not missing)
            or (aggregate_status == "contradicted" and conflicting)
        ),
    }


def cross_doc_contract_to_option_payload(contract: Mapping[str, Any], *, replacement_effect: str) -> dict[str, Any]:
    status = str(contract.get("aggregate_status") or "ambiguous")
    refs = [str(value) for value in contract.get("evidence_refs") or []]
    payload: dict[str, Any] = {
        "option_text": str(contract.get("option_text") or ""),
        "status": status if status in {"supported", "contradicted"} else "unresolved",
        "claim_route": "exact_clause" if status == "supported" else "contradiction" if status == "contradicted" else "weak_related",
        "evidence_refs": refs,
        "resolved_evidence_refs": refs,
        "term_equivalence": "confirmed" if status == "supported" else "not_required" if status == "contradicted" else "unknown",
        "term_equivalence_confirmed": status == "supported",
        "term_equivalence_required": status == "supported",
        "factual_statement_true": True if status == "supported" else False if status == "contradicted" else None,
        "question_scope_binding": "in_scope",
        "replacement_effect": replacement_effect,
        "unresolved_reason": "" if status in {"supported", "contradicted"} else str(contract.get("aggregate_basis") or "cross-document subclaims unresolved"),
        "cross_doc_claim": dict(contract),
        "certification_basis": str(contract.get("aggregate_basis") or ""),
    }
    return payload
