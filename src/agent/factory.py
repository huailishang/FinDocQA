"""Pipeline factory for the enhanced baseline.

This is the composition root. Keep object wiring here so individual modules stay
small and replaceable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agent.classifier import RuleBasedQuestionClassifier
from agent.workflow import EnhancedBaselineWorkflow
from data.loader import JsonQuestionLoader
from evidence.assembler import GroupedEvidenceAssembler
from evidence.enhanced_assembler import EnhancedEvidenceAssembler
from evidence.structure_aware import StructureAwareEvidenceAssembler
from evaluation.writer import SubmissionTemplate, CsvSubmissionWriter
from document.store import (
    AdaptedPageDocumentStore,
    FallbackDocumentStore,
    RawMineruDocumentStore,
)
from retrieval.canonical_lexical import (
    CanonicalDocumentRetriever,
    CanonicalLexicalEvidenceRetriever,
)
from retrieval.document_catalog import DocumentCatalog
from retrieval.document_scope import DocumentScopeResolver
from retrieval.hybrid import LexicalHybridRetriever
from retrieval.interfaces import StoreBoundEvidenceRetriever
from retrieval.scope_aware import ScopeAwareEvidenceRetriever
from solvers.calculation import CalculationSolver
from solvers.cross_doc import CrossDocSolver
from solvers.direct import DirectSolver
from solvers.multi_choice import MultiChoiceSolver
from solvers.router import RoutedSolver
from submission_contract import load_answer_slot_contracts
from utils.llm_client import OpenAICompatibleClient, build_fallback_client
from verification.verifier import HighRiskVerifier
from verification.self_check import OptionSelfCheckVerifier


class PipelineFactory:
    """Build the current enhanced-baseline workflow from config."""

    def __init__(
        self,
        config: Dict[str, Any],
        project_root: Path,
        artifact_mode: str = "standard",
    ) -> None:
        self.config = config
        self.project_root = project_root
        self.artifact_mode = str(artifact_mode or "standard").strip().lower()
        if self.artifact_mode not in CsvSubmissionWriter.VALID_ARTIFACT_MODES:
            raise ValueError(f"unsupported artifact_mode: {artifact_mode!r}")

    def build_loader(self) -> JsonQuestionLoader:
        submission_cfg = self.config.get("submission", {})
        if not isinstance(submission_cfg, dict):
            submission_cfg = {}
        submission_mode = str(
            submission_cfg.get("mode") or "legacy_single"
        ).strip().lower()
        slot_count_by_qid: Mapping[str, int] = {}
        slot_contracts_by_qid: Mapping[str, tuple[dict[str, Any], ...]] = {}
        require_slot_contract = submission_mode == "multi_slot"
        if require_slot_contract:
            template_value = self._path_config("submission_template", "").strip()
            if not template_value:
                raise ValueError("multi_slot loader requires paths.submission_template")
            template = SubmissionTemplate.load(self._resolve_path(template_value))
            slot_count_by_qid = template.slot_count_by_qid
            slot_contract_value = self._path_config(
                "answer_slot_contracts", "config/answer_slot_contracts.example.json"
            ).strip()
            if not slot_contract_value:
                raise ValueError("multi_slot loader requires paths.answer_slot_contracts")
            slot_contracts_by_qid = load_answer_slot_contracts(
                self._resolve_path(slot_contract_value)
            )
            if set(slot_contracts_by_qid) != set(slot_count_by_qid):
                missing = sorted(set(slot_count_by_qid) - set(slot_contracts_by_qid))
                extra = sorted(set(slot_contracts_by_qid) - set(slot_count_by_qid))
                raise ValueError(
                    "multi-slot slot contracts/template qid mismatch: "
                    f"missing={missing[:20]} extra={extra[:20]}"
                )

        explicit_questions_dir = self._path_config("questions_dir", "").strip()
        if explicit_questions_dir:
            resolved_explicit = self._resolve_path(explicit_questions_dir)
            if resolved_explicit.is_dir():
                return JsonQuestionLoader(
                    questions_dir=resolved_explicit,
                    submission_slot_count_by_qid=slot_count_by_qid,
                    submission_slot_contracts_by_qid=slot_contracts_by_qid,
                    require_submission_slot_contract=require_slot_contract,
                )

        raw_dataset = self._resolve_path(self._path_config("raw_dataset", "../data/raw_dataset"))
        question_group = self._path_config("question_group", "group_a").strip() or "group_a"
        questions_dir = raw_dataset / "questions" / question_group
        return JsonQuestionLoader(
            questions_dir=questions_dir,
            submission_slot_count_by_qid=slot_count_by_qid,
            submission_slot_contracts_by_qid=slot_contracts_by_qid,
            require_submission_slot_contract=require_slot_contract,
        )

    def build_classifier(self) -> RuleBasedQuestionClassifier:
        """Offline rule-based classifier (no LLM, no I/O)."""
        return RuleBasedQuestionClassifier()

    def build_document_scope_resolver(self) -> DocumentScopeResolver | None:
        """Build deterministic multi-slot candidate-document discovery.

        This resolver performs only local catalog/lexical work.  It never creates
        provider clients and therefore has a fixed provider-call count of zero.
        """
        scope_cfg = self.config.get("document_scope", {})
        if not isinstance(scope_cfg, dict) or not bool(scope_cfg.get("enabled", False)):
            return None

        retrieval_cfg = self.config.get("retrieval", {})
        fallback_roots = [
            self._resolve_path(str(value))
            for value in retrieval_cfg.get("fallback_processed_docs", [])
            if str(value).strip()
        ]
        processed_root = self._resolve_path(
            self._path_config("processed_docs", "../data/processed_pymupdf4llm")
        )
        raw_root = self._resolve_path(
            self._path_config("raw_pdfs", "../data/raw_dataset/raw")
        )
        catalog = DocumentCatalog.from_roots(
            processed_root,
            fallback_roots=fallback_roots,
            raw_root=raw_root,
        )
        insurance_product_catalog = self._resolve_path(
            str(
                scope_cfg.get("insurance_product_catalog")
                or "config/insurance_product_documents.json"
            )
        )
        return DocumentScopeResolver(
            catalog,
            top_k=int(scope_cfg.get("top_k", 5)),
            max_top_k=int(scope_cfg.get("max_top_k", 10)),
            recall_pool_size=int(scope_cfg.get("recall_pool_size", 10)),
            strategy=str(scope_cfg.get("strategy") or "deterministic_lexical_v1"),
            min_score=float(scope_cfg.get("min_score", 1.0)),
            insurance_product_catalog_path=insurance_product_catalog,
            weak_scope_min_score=float(scope_cfg.get("weak_scope_min_score", 18.0)),
            weak_scope_min_margin=float(scope_cfg.get("weak_scope_min_margin", 2.0)),
        )

    def build_retriever(self) -> StoreBoundEvidenceRetriever:
        """Build the configured offline evidence retriever.

        ``lexical_hybrid`` remains the production-compatible default.
        ``canonical_lexical`` is a parser-agnostic candidate that reuses the
        same DocumentScopeResolver and retriever-scope audit contract.
        """
        retrieval_cfg = self.config.get("retrieval", {})
        pipeline_cfg = self.config.get("pipeline", {})
        raw_mode = (
            pipeline_cfg.get("retriever") if isinstance(pipeline_cfg, dict) else ""
        )
        mode = str(raw_mode or "lexical_hybrid").strip().lower()

        if mode == "canonical_lexical":
            canonical_stores = [
                RawMineruDocumentStore(
                    self._resolve_path(
                        str(
                            retrieval_cfg.get("canonical_raw_root")
                            or "../data/processed_mineru"
                        )
                    )
                )
            ]
            adapted_roots = [
                self._resolve_path(
                    self._path_config(
                        "processed_docs", "../data/processed_mineru_retrieval"
                    )
                ),
                *[
                    self._resolve_path(str(value))
                    for value in retrieval_cfg.get("fallback_processed_docs", [])
                    if str(value).strip()
                ],
            ]
            seen_roots = {canonical_stores[0].root.resolve()}
            for root in adapted_roots:
                resolved = root.resolve()
                if resolved in seen_roots:
                    continue
                seen_roots.add(resolved)
                canonical_stores.append(AdaptedPageDocumentStore(root))
            store = FallbackDocumentStore(canonical_stores)
            delegate = CanonicalLexicalEvidenceRetriever(
                store=store,
                document_retriever=CanonicalDocumentRetriever(
                    top_k=int(retrieval_cfg.get("canonical_document_top_k", 8))
                ),
                top_k_per_doc=int(retrieval_cfg.get("canonical_top_k_per_doc", 5)),
                window_chars=int(retrieval_cfg.get("canonical_window_chars", 1800)),
                context_flank_chars=int(
                    retrieval_cfg.get("canonical_context_flank_chars", 600)
                ),
            )
            return ScopeAwareEvidenceRetriever(
                delegate=delegate,
                document_scope_resolver=self.build_document_scope_resolver(),
            )

        if mode != "lexical_hybrid":
            raise ValueError(f"unsupported pipeline retriever: {mode!r}")

        fallback_roots = [
            self._resolve_path(str(value))
            for value in retrieval_cfg.get("fallback_processed_docs", [])
            if str(value).strip()
        ]
        return LexicalHybridRetriever(
            processed_docs_dir=self._resolve_path(
                self._path_config("processed_docs", "../data/processed_pymupdf4llm")
            ),
            top_k_per_doc=int(retrieval_cfg.get("top_k_per_doc", 5)),
            windows_per_page=int(retrieval_cfg.get("windows_per_page", 3)),
            # P6e-7: optional per-domain top_k overrides. When absent or empty
            # the global ``top_k_per_doc`` is used for every domain, preserving
            # the pre-P6e-7 default behavior exactly. Coercion/validation is
            # performed inside the retriever.
            top_k_per_doc_by_domain=retrieval_cfg.get("top_k_per_doc_by_domain"),
            # P6g: optional before/after context flank chars. Committed default
            # is 600; absent/invalid values fall back to 600 inside the
            # retriever. Coercion reuses the same strict style as top_k.
            context_flank_chars=retrieval_cfg.get(
                "context_flank_chars",
                LexicalHybridRetriever.DEFAULT_CONTEXT_FLANK_CHARS,
            ),
            # P6g-3: optional per-domain context_flank_chars overrides. Modeled
            # on top_k_per_doc_by_domain. Invalid values fall back to the
            # global context_flank_chars for that domain.
            context_flank_chars_by_domain=retrieval_cfg.get(
                "context_flank_chars_by_domain",
            ),
            fallback_processed_docs_dirs=fallback_roots,
            document_scope_resolver=self.build_document_scope_resolver(),
        )

    def build_assembler(self) -> GroupedEvidenceAssembler:
        """Build the selected evidence assembler.

        Priority (highest first):
        1. ``evidence.enhanced.enabled: true`` → EnhancedEvidenceAssembler (Lane 2)
        2. ``evidence.structure_aware.enabled: true`` → StructureAwareEvidenceAssembler
        3. default → GroupedEvidenceAssembler

        All three support the same ``token_budgets`` config key.
        """
        evidence_cfg = self.config.get("evidence", {})
        enhanced_cfg = evidence_cfg.get("enhanced", {})
        structure_cfg = evidence_cfg.get("structure_aware", {})
        prompt_cfg = evidence_cfg.get("prompt_compaction", {})
        if not isinstance(prompt_cfg, dict):
            prompt_cfg = {}
        prompt_kwargs = {
            "enable_prompt_evidence_compaction": bool(prompt_cfg.get("enabled", False)),
            "prompt_evidence_policy": {
                key: prompt_cfg[key]
                for key in (
                    "max_context_chars",
                    "max_candidates",
                    "min_candidates_per_doc",
                    "main_doc_max_candidates",
                    "other_doc_max_candidates",
                    "near_duplicate_overlap",
                )
                if key in prompt_cfg
            },
            "prompt_budget_model": str(prompt_cfg.get("model") or "qwen3.7-max"),
        }
        table_cfg = evidence_cfg.get("structured_tables", {})
        # Package L separates verification-side evidence from solver prompt
        # injection. The legacy ``enabled`` key remains a verification-only
        # compatibility alias; it must never silently enable prompt injection.
        table_verification_enabled = bool(
            table_cfg.get("verification_enabled", table_cfg.get("enabled", False))
        )
        table_prompt_injection_enabled = bool(
            table_cfg.get("prompt_injection_enabled", False)
        )
        table_root = self._resolve_path(
            str(table_cfg.get("root") or "../data/processed_mineru")
        )
        insurance_clause_cfg = evidence_cfg.get("insurance_clauses", {})
        insurance_clause_full_root = self._resolve_path(
            str(insurance_clause_cfg.get("full_text_root") or "../data/processed_mineru")
        )
        insurance_clause_product_catalog = self._resolve_path(
            str(insurance_clause_cfg.get("product_catalog") or "config/insurance_product_documents.json")
        )
        insurance_calculation_cfg = evidence_cfg.get("insurance_calculations", {})
        insurance_calculation_full_root = self._resolve_path(
            str(insurance_calculation_cfg.get("full_text_root") or "../data/processed_mineru")
        )
        insurance_calculation_product_catalog = self._resolve_path(
            str(insurance_calculation_cfg.get("product_catalog") or "config/insurance_product_documents.json")
        )
        regulatory_cfg = evidence_cfg.get("regulatory_options", {})
        regulatory_data_root = self._resolve_path(
            str(regulatory_cfg.get("data_root") or "../data")
        )
        exact_field_cfg = evidence_cfg.get("contract_exact_fields", {})
        exact_field_full_root = self._resolve_path(
            str(exact_field_cfg.get("full_text_root") or "../data/processed_mineru")
        )
        exact_field_retrieval_root = self._resolve_path(
            str(exact_field_cfg.get("retrieval_root") or "../data/processed_mineru_retrieval")
        )
        table_kwargs = {
            "structured_table_root": table_root,
            "enable_structured_table_verification": table_verification_enabled,
            "enable_structured_table_prompt_injection": table_prompt_injection_enabled,
            "structured_table_max_rows_per_doc": int(
                table_cfg.get("max_rows_per_doc", 12)
            ),
            "contract_exact_field_full_text_root": exact_field_full_root,
            "contract_exact_field_retrieval_root": exact_field_retrieval_root,
            "enable_contract_exact_field_verification": bool(
                exact_field_cfg.get("verification_enabled", False)
            ),
            "contract_exact_field_max_windows_per_doc": int(
                exact_field_cfg.get("max_windows_per_field_doc", 3)
            ),
            "insurance_clause_full_text_root": insurance_clause_full_root,
            "insurance_clause_product_catalog_path": insurance_clause_product_catalog,
            "insurance_clause_registry_path": None,
            "allow_curated_insurance_fixture_for_offline_evaluation": False,
            "enable_insurance_clause_verification": bool(
                insurance_clause_cfg.get("verification_enabled", False)
            ),
            "insurance_calculation_full_text_root": insurance_calculation_full_root,
            "insurance_calculation_product_catalog_path": insurance_calculation_product_catalog,
            "enable_insurance_calculation_verification": bool(
                insurance_calculation_cfg.get("verification_enabled", False)
            ),
            "regulatory_data_root": regulatory_data_root,
            "enable_regulatory_option_verification": bool(
                regulatory_cfg.get("verification_enabled", False)
            ),
        }

        if bool(enhanced_cfg.get("enabled", False)):
            return EnhancedEvidenceAssembler(
                token_budgets=evidence_cfg.get("token_budgets"),
                enable_dedup=bool(enhanced_cfg.get("enable_dedup", True)),
                enable_source_order=bool(enhanced_cfg.get("enable_source_order", True)),
                enable_section_context=bool(enhanced_cfg.get("enable_section_context", True)),
                enable_table_grouping=bool(enhanced_cfg.get("enable_table_grouping", True)),
                **prompt_kwargs,
                **table_kwargs,
            )
        if bool(structure_cfg.get("enabled", False)):
            return StructureAwareEvidenceAssembler(
                token_budgets=evidence_cfg.get("token_budgets"),
                **prompt_kwargs,
                **table_kwargs,
            )
        return GroupedEvidenceAssembler(
            token_budgets=evidence_cfg.get("token_budgets"),
            **prompt_kwargs,
            **table_kwargs,
        )

    def build_workflow(self, writer: Optional[CsvSubmissionWriter] = None) -> EnhancedBaselineWorkflow:
        classifier = self.build_classifier()
        retriever = self.build_retriever()
        assembler = self.build_assembler()
        evidence_cfg = self.config.get("evidence", {})
        if not isinstance(evidence_cfg, dict):
            evidence_cfg = {}
        prompt_cfg = evidence_cfg.get("prompt_compaction", {})
        if not isinstance(prompt_cfg, dict):
            prompt_cfg = {}

        llm_client = OpenAICompatibleClient.from_env(self.config)
        fallback_llm_client = build_fallback_client(self.config)
        direct = DirectSolver(llm_client=llm_client, fallback_llm_client=fallback_llm_client)
        routed_solver = RoutedSolver(
            solvers={
                "multi_choice": MultiChoiceSolver(llm_client=llm_client, fallback_llm_client=fallback_llm_client),
                "calculation": CalculationSolver(llm_client=llm_client, fallback_llm_client=fallback_llm_client),
                "cross_doc": CrossDocSolver(llm_client=llm_client, fallback_llm_client=fallback_llm_client),
            },
            default_solver=direct,
        )

        verifier = HighRiskVerifier()

        # Lane 3: optional self-check verifier (default-off, evaluation only)
        verification_cfg = self.config.get("verification", {})
        self_check_cfg = verification_cfg.get("self_check", {})
        production_integrity_cfg = verification_cfg.get("production_integrity", {})
        runtime_cfg = self.config.get("runtime", {}) if isinstance(self.config.get("runtime", {}), dict) else {}
        fallback_cfg = self.config.get("fallback", {}) if isinstance(self.config.get("fallback", {}), dict) else {}
        self_check_verifier = (
            OptionSelfCheckVerifier(
                min_term_match_ratio=float(
                    self_check_cfg.get("min_term_match_ratio", 0.4)
                ),
                enable_correction_proposal=bool(
                    self_check_cfg.get("enable_correction_proposal", True)
                ),
            )
            if bool(self_check_cfg.get("enabled", False))
            else None
        )

        fallback_solver = direct

        return EnhancedBaselineWorkflow(
            classifier=classifier,
            retriever=retriever,
            assembler=assembler,
            solver=routed_solver,
            writer=writer,
            verifier=verifier,
            fallback_solver=fallback_solver,
            self_check_verifier=self_check_verifier,
            enforce_production_integrity=bool(
                production_integrity_cfg.get("enabled", False)
            ),
            apply_safe_self_check_corrections=bool(
                self_check_cfg.get("apply_safe_corrections", False)
            ),
            self_check_correction_routes=self_check_cfg.get(
                "safe_correction_routes",
                ["regulatory_exact_clause", "question_scope_exclusion"],
            ),
            fallback_enabled=bool(
                runtime_cfg.get("fallback_enabled", fallback_cfg.get("enabled", True))
            ),
            prompt_budget_enforced=bool(prompt_cfg.get("enabled", False)),
            prompt_budget_model=str(prompt_cfg.get("model") or "qwen3.7-max"),
            prompt_budget_target_total_tokens=int(
                prompt_cfg.get("target_total_tokens", 38_000)
            ),
            prompt_budget_hard_cap_tokens=int(
                prompt_cfg.get("hard_cap_tokens", 45_000)
            ),
        )

    def build_writer(self) -> CsvSubmissionWriter:
        output_dir = self._resolve_path(self._path_config("output_dir", "output"))
        submission_cfg = self.config.get("submission", {})
        if not isinstance(submission_cfg, dict):
            submission_cfg = {}
        submission_mode = str(
            submission_cfg.get("mode") or "legacy_single"
        ).strip().lower()
        template_value = self._path_config("submission_template", "").strip()
        template_path = self._resolve_path(template_value) if template_value else None
        return CsvSubmissionWriter(
            output_dir=output_dir,
            artifact_mode=self.artifact_mode,
            submission_mode=submission_mode,
            submission_template_path=template_path,
        )

    def _path_config(self, key: str, default: str) -> str:
        return str(self.config.get("paths", {}).get(key, default))

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (self.project_root / path).resolve()
