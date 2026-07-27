from contracts import ClassificationResult, Question, QuestionLabel
from retrieval.document_catalog import DocumentCatalog, DocumentCatalogEntry
from retrieval.document_scope import DocumentScopeResolver


def _entry(
    doc_id: str,
    domain: str,
    title: str,
    identity: str | None = None,
    aliases: tuple[str, ...] | None = None,
) -> DocumentCatalogEntry:
    return DocumentCatalogEntry(
        doc_id=doc_id,
        domain=domain,
        retrieval_dir=f"/tmp/{domain}/{doc_id}",
        source_paths=(f"/tmp/{domain}/{doc_id}",),
        title=title,
        title_aliases=aliases or (title,),
        identity_text=identity or title,
    )


def _classification(*labels: QuestionLabel) -> ClassificationResult:
    return ClassificationResult(labels=labels or (QuestionLabel.FACT_LOOKUP,))


def test_entity_and_year_rank_single_document_first() -> None:
    catalog = DocumentCatalog(
        [
            _entry("annual_byd_2024_report", "financial_reports", "比亚迪股份有限公司 2024 年年度报告"),
            _entry("annual_midea_2024_report", "financial_reports", "美的集团股份有限公司 2024 年年度报告"),
        ]
    )
    resolver = DocumentScopeResolver(catalog, top_k=2)
    question = Question(
        qid="b1",
        domain="financial_reports",
        text="根据比亚迪 2024 年年度报告，公司的营业收入是多少？",
        options={"A": "甲", "B": "乙"},
        answer_format="mcq",
        doc_ids=(),
        raw={"type": "财务数据查询"},
    )

    first = resolver.resolve(question, _classification(QuestionLabel.FACT_LOOKUP))
    second = resolver.resolve(question, _classification(QuestionLabel.FACT_LOOKUP))

    assert first.candidate_doc_ids[0] == "annual_byd_2024_report"
    assert first == second
    assert first.provider_calls == 0
    assert "2024" in first.query_terms


def test_cross_document_question_recalls_both_entities() -> None:
    catalog = DocumentCatalog(
        [
            _entry("annual_byd_2024_report", "financial_reports", "比亚迪股份有限公司 2024 年年度报告"),
            _entry("annual_midea_2025_report", "financial_reports", "美的集团股份有限公司 2025 年年度报告"),
            _entry("annual_catl_2025_report", "financial_reports", "宁德时代新能源科技股份有限公司 2025 年年度报告"),
        ]
    )
    resolver = DocumentScopeResolver(catalog, top_k=3)
    question = Question(
        qid="b2",
        domain="financial_reports",
        text="对比比亚迪 2024 年与美的集团 2025 年年度报告，两家公司现金流表现如何？",
        options={"A": "甲", "B": "乙"},
        answer_format="mcq",
        doc_ids=(),
        raw={"type": "跨公司财务指标对比"},
    )

    result = resolver.resolve(question, _classification(QuestionLabel.CROSS_DOC))

    assert {"annual_byd_2024_report", "annual_midea_2025_report"} <= set(result.candidate_doc_ids)


def test_financial_report_identity_ignores_low_information_ocr_aliases() -> None:
    catalog = DocumentCatalog(
        [
            _entry(
                "annual_cscec_2025_report",
                "financial_reports",
                "中国建筑股份有限公司 2025 年年度报告",
                aliases=("中国建筑股份有限公司", "中国建筑"),
            ),
            _entry(
                "annual_midea_2025_report",
                "financial_reports",
                "2025 年年 的集团股份有限公司",
                aliases=("年年", "年年的集团"),
            ),
        ]
    )
    resolver = DocumentScopeResolver(catalog, top_k=2)
    question = Question(
        qid="report-identity-noise",
        domain="financial_reports",
        text="根据中国建筑 2025 年年度报告，现金分红金额是多少？",
        options={},
        answer_format="free_text",
        doc_ids=(),
    )

    result = resolver.resolve(question, _classification(QuestionLabel.FACT_LOOKUP))

    assert result.candidate_doc_ids[0] == "annual_cscec_2025_report"
    assert "中国建筑" in result.matched_identity_terms
    assert "年年" not in result.matched_identity_terms


def test_financial_contract_identity_preserves_multiple_named_issuers() -> None:
    catalog = DocumentCatalog(
        [
            _entry(
                "text04",
                "financial_contracts",
                "安克创新募集说明书",
                aliases=("安克创新", "募集说明书"),
            ),
            _entry(
                "text05",
                "financial_contracts",
                "本川智能募集说明书",
                aliases=("本川智", "募集说明书"),
            ),
            _entry(
                "text11",
                "financial_contracts",
                "普联软件募集说明书",
                aliases=("普联软件", "募集说明书"),
            ),
            _entry("noise", "financial_contracts", "其他公司募集说明书"),
        ]
    )
    resolver = DocumentScopeResolver(catalog, top_k=3, max_top_k=5)
    question = Question(
        qid="contract-multi-issuer",
        domain="financial_contracts",
        text="关于募投项目新增折旧摊销，以下哪些公司披露了定量测算？",
        options={"A": "普联软件", "B": "本川智能", "C": "安克创新"},
        answer_format="multi",
        doc_ids=(),
    )

    result = resolver.resolve(question, _classification(QuestionLabel.CROSS_DOC))

    assert {"text04", "text05", "text11"} <= set(result.candidate_doc_ids[:3])
    assert {"安克创新", "本川智", "普联软件"} <= set(result.matched_identity_terms)


def test_title_alias_can_recall_insurance_product() -> None:
    catalog = DocumentCatalog(
        [
            _entry("1", "insurance", "平安智盈金生专属商业养老保险"),
            _entry("2", "insurance", "平安盛世金越尊享版终身寿险"),
        ]
    )
    resolver = DocumentScopeResolver(catalog, top_k=2)
    question = Question(
        qid="b3",
        domain="insurance",
        text="平安智盈金生专属商业养老保险的犹豫期是多少天？",
        options={"A": "10日", "B": "20日"},
        answer_format="mcq",
        doc_ids=(),
    )

    result = resolver.resolve(question, _classification(QuestionLabel.CLAUSE_LOOKUP))

    assert result.candidate_doc_ids[0] == "1"
    assert result.candidates[0].matched_title_terms


def test_resolver_ignores_declared_doc_ids_for_leakage_safety() -> None:
    catalog = DocumentCatalog(
        [
            _entry("wanted", "research", "新能源汽车行业研究"),
            _entry("secret_ground_truth", "research", "银行业研究"),
        ]
    )
    resolver = DocumentScopeResolver(catalog, top_k=2)
    base = dict(
        qid="masked",
        domain="research",
        text="新能源汽车行业未来市场空间如何？",
        options={"A": "增长", "B": "下降"},
        answer_format="mcq",
    )
    with_truth = Question(**base, doc_ids=("secret_ground_truth",))
    without_truth = Question(**base, doc_ids=())

    assert resolver.resolve(with_truth, _classification()) == resolver.resolve(without_truth, _classification())


def test_unrelated_query_fails_closed_instead_of_returning_whole_domain() -> None:
    catalog = DocumentCatalog([_entry("doc1", "insurance", "完全无关标题")])
    resolver = DocumentScopeResolver(catalog, top_k=5, min_score=1.0)
    question = Question(
        qid="no-hit",
        domain="insurance",
        text="xyzqv 987654321",
        options={},
        answer_format="mcq",
        doc_ids=(),
    )

    result = resolver.resolve(question, _classification())

    assert result.candidate_doc_ids == ()
    assert "no_candidate_above_threshold" in result.warnings
