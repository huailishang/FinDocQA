"""Explicit gap-driven evidence controller."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from contracts import EvidenceCandidate, Question
from evidence_completion.contracts import EvidenceGrade
from evidence_completion.domain_bridge import (
    BoundFact, BridgeGrade, BridgeHit, BridgeRequirement, DomainBridge,
    OptionAssessment, ToolRun, bridge_for_question,
)
from evidence_completion.requirement_graph import EdgeType, EvidenceRequirementGraph, NodeType
from memory.structured_question_memory import build_structured_question_memory


class ControllerState(str, Enum):
    COMPILE_REQUIREMENTS = "COMPILE_REQUIREMENTS"
    LOCAL_RETRIEVE = "LOCAL_RETRIEVE"
    GRADE_EVIDENCE = "GRADE_EVIDENCE"
    BIND_FACTS = "BIND_FACTS"
    EXECUTE_TOOLS = "EXECUTE_TOOLS"
    ASSESS_SUFFICIENCY = "ASSESS_SUFFICIENCY"
    BUILD_NEXT_REQUESTS = "BUILD_NEXT_REQUESTS"
    TARGETED_RETRIEVE = "TARGETED_RETRIEVE"
    RECOMPUTE = "RECOMPUTE"
    DECIDE = "DECIDE"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class StateTransition:
    sequence: int
    from_state: str
    to_state: str
    reason: str
    active_gap_ids: tuple[str, ...]
    new_fact_ids: tuple[str, ...]
    rejected_fact_ids: tuple[str, ...]
    provider_calls: int
    tokens_used: int
    memory_hash: str
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ControllerResult:
    qid: str
    capability_id: str
    final_state: str
    production_answer: str
    all_options_closed: bool
    answer_contract_closed: bool
    graph: Mapping[str, Any]
    transitions: tuple[Mapping[str, Any], ...]
    requirements: tuple[Mapping[str, Any], ...]
    hits: tuple[Mapping[str, Any], ...]
    grades: tuple[Mapping[str, Any], ...]
    facts: tuple[Mapping[str, Any], ...]
    tool_runs: tuple[Mapping[str, Any], ...]
    option_assessments: Mapping[str, Mapping[str, Any]]
    structured_memory: Mapping[str, Any]
    provider_calls: int
    tokens_used: int
    round2_request_count: int
    ambiguous_to_correct_count: int
    changed_candidate_round2_count: int
    rejected_evidence_count: int
    block_reasons: tuple[str, ...]
    true_crag_transitions: tuple[Mapping[str, Any], ...]
    dependency_integrity: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _answer_contract(question: Question, assessments: Mapping[str, OptionAssessment]) -> tuple[str, bool, str]:
    supported = [label for label in question.options if assessments[label].status == "supported"]
    if not all(assessments[label].dependencies_closed for label in question.options):
        return "", False, "option_dependencies_not_closed"
    answer = "".join(label for label in question.options if label in supported)
    fmt = str(question.answer_format or "multi").lower()
    if fmt in {"mcq", "single", "tf", "boolean", "judge"} and len(supported) != 1:
        return "", False, "unique_answer_contract_not_closed"
    if fmt not in {"mcq", "single", "tf", "boolean", "judge"} and not supported:
        return "", False, "multi_answer_empty"
    return answer, True, "answer_contract_closed"


class GapDrivenEvidenceController:
    schema_version = "gap_driven_controller_v2_evidence_integrity"

    def __init__(self, repo_root: Path, *, max_rounds: int = 2, memory_token_budget: int = 2000) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_rounds = max(1, int(max_rounds))
        self.memory_token_budget = max(500, int(memory_token_budget))

    def _transition(
        self,
        rows: list[StateTransition],
        graph: EvidenceRequirementGraph,
        source: ControllerState,
        target: ControllerState,
        reason: str,
        *,
        active: Sequence[str] = (),
        new: Sequence[str] = (),
        rejected: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        rows.append(StateTransition(
            sequence=len(rows) + 1,
            from_state=source.value,
            to_state=target.value,
            reason=str(reason),
            active_gap_ids=tuple(dict.fromkeys(str(x) for x in active if str(x))),
            new_fact_ids=tuple(dict.fromkeys(str(x) for x in new if str(x))),
            rejected_fact_ids=tuple(dict.fromkeys(str(x) for x in rejected if str(x))),
            provider_calls=0,
            tokens_used=0,
            memory_hash=_hash({"graph": graph.graph_hash(), "step": len(rows) + 1}),
            metadata=dict(metadata or {}),
        ))

    def _compile(self, question: Question, bridge: DomainBridge) -> tuple[EvidenceRequirementGraph, list[BridgeRequirement], dict[str, str]]:
        graph = EvidenceRequirementGraph(question.qid, capability_id=bridge.capability_id, domain=question.domain)
        qnode = graph.add_node(NodeType.QUESTION, "question", status="RESOLVED", resolved_fields={"text": question.text, "domain": question.domain})
        contract = graph.add_node(NodeType.ANSWER_CONTRACT, "answer_contract", status="PENDING", resolved_fields={"allowed_labels": list(question.options), "answer_format": question.answer_format, "canonical_order": list(question.options)})
        graph.add_edge(qnode, contract, EdgeType.REQUIRES, reason="question_requires_answer_contract")
        doc_nodes = {}
        for doc in question.doc_ids:
            node = graph.add_node(NodeType.REQUIRED_DOCUMENT, str(doc), status="RESOLVED", resolved_fields={"doc_id": str(doc)}, source_refs=(str(doc),))
            doc_nodes[str(doc)] = node
            graph.add_edge(qnode, node, EdgeType.REQUIRES, reason="declared_document_scope")
        requirements: list[BridgeRequirement] = []
        req_nodes: dict[str, str] = {}
        for label, text in question.options.items():
            option = graph.add_node(NodeType.OPTION, f"option_{label}", option_label=label, status="PENDING", resolved_fields={"text": text})
            graph.add_edge(qnode, option, EdgeType.REQUIRES, reason="question_option")
            graph.add_edge(option, contract, EdgeType.DEPENDS_ON, reason="answer_contract_dependency")
            for req in bridge.compile_requirements(label, text):
                requirements.append(req)
                node = graph.add_node(NodeType.CLAIM_ATOM, req.semantic_key, option_label=label, status="PENDING" if req.retrievable else "BLOCKED", required_fields=("claim_semantics", "declared_document", "local_evidence"), missing_fields=("local_evidence",), metadata={"requirement_id": req.requirement_id, "retrievable": req.retrievable, "query_terms": list(req.query_terms), "reason": req.reason})
                req_nodes[req.requirement_id] = node
                graph.add_edge(option, node, EdgeType.REQUIRES, reason="option_claim_atom")
                condition = graph.add_node(
                    NodeType.CONDITION,
                    req.semantic_key + ":condition",
                    option_label=label,
                    status="RESOLVED",
                    resolved_fields={
                        "statement": str(req.metadata.get("statement") or text),
                        "parsed_conditions": list(req.metadata.get("descriptors") or []),
                    },
                    producer=bridge.capability_id,
                    metadata={"requirement_id": req.requirement_id},
                )
                graph.add_edge(node, condition, EdgeType.DEPENDS_ON, reason="claim_atom_condition_scope")
                for doc in req.allowed_doc_ids:
                    if doc in doc_nodes:
                        graph.add_edge(node, doc_nodes[doc], EdgeType.SCOPED_TO, reason="declared_document_scope")
                if bridge.capability_id == "FIN-RATIO":
                    formula = graph.add_node(NodeType.FORMULA_OR_RULE, req.semantic_key + ":formula", option_label=label, status="PENDING", resolved_fields={"rule": "typed operands -> Python comparison"}, producer="financial_metric_ledger")
                    graph.add_edge(node, formula, EdgeType.DEPENDS_ON, reason="deterministic_computation_required")
        return graph, requirements, req_nodes

    @staticmethod
    def _dedupe_latest_facts(facts: Sequence[BoundFact]) -> list[BoundFact]:
        selected: dict[tuple[str, str], BoundFact] = {}
        for fact in facts:
            key = (fact.requirement_id, fact.atom_id)
            previous = selected.get(key)
            if previous is None:
                selected[key] = fact
                continue
            previous_round = int((previous.metadata or {}).get("hit_round") or 0)
            current_round = int((fact.metadata or {}).get("hit_round") or 0)
            if current_round > previous_round or (
                current_round == previous_round and fact.fact_id < previous.fact_id
            ):
                selected[key] = fact
        return list(selected.values())

    @staticmethod
    def _validate_tool_dependencies(
        facts: Sequence[BoundFact], tools: Sequence[ToolRun]
    ) -> tuple[list[ToolRun], dict[str, Any]]:
        fact_by_id = {fact.fact_id: fact for fact in facts}
        validated: list[ToolRun] = []
        invalid_runs: list[dict[str, Any]] = []
        for run in tools:
            errors: list[str] = []
            if run.status == "COMPLETED" and not run.source_fact_ids:
                errors.append("completed_tool_without_source_facts")
            for fact_id in run.source_fact_ids:
                fact = fact_by_id.get(fact_id)
                if fact is None:
                    errors.append(f"missing_source_fact:{fact_id}")
                    continue
                if fact.requirement_id != run.requirement_id:
                    errors.append(f"cross_requirement_fact:{fact_id}")
                if fact.option_label != run.option_label:
                    errors.append(f"cross_option_fact:{fact_id}")
                if not fact.canonical_verified:
                    errors.append(f"noncanonical_fact:{fact_id}")
            if errors:
                invalid_runs.append({"run_id": run.run_id, "errors": errors})
                validated.append(replace(
                    run,
                    status="BLOCKED",
                    result=None,
                    comparison="unresolved",
                    missing_atom_ids=tuple(dict.fromkeys((*run.missing_atom_ids, *errors))),
                ))
            else:
                validated.append(run)
        return validated, {
            "tool_run_count": len(tools),
            "invalid_tool_run_count": len(invalid_runs),
            "invalid_tool_runs": invalid_runs,
            "tool_operands_traceable": not invalid_runs,
        }

    @staticmethod
    def _validate_assessments(
        facts: Sequence[BoundFact],
        tools: Sequence[ToolRun],
        assessments: Mapping[str, OptionAssessment],
    ) -> tuple[dict[str, OptionAssessment], dict[str, Any]]:
        fact_by_id = {fact.fact_id: fact for fact in facts}
        tool_by_id = {tool.run_id: tool for tool in tools}
        output: dict[str, OptionAssessment] = {}
        invalid: list[dict[str, Any]] = []
        for label, assessment in assessments.items():
            errors: list[str] = []
            for fact_id in assessment.fact_ids:
                fact = fact_by_id.get(fact_id)
                if fact is None:
                    errors.append(f"assessment_missing_fact:{fact_id}")
                elif fact.option_label != label:
                    errors.append(f"assessment_cross_option_fact:{fact_id}")
            used_by_tools = {
                fact_id
                for tool_id in assessment.tool_run_ids
                for fact_id in (
                    tool_by_id[tool_id].source_fact_ids if tool_id in tool_by_id else ()
                )
            }
            if set(assessment.fact_ids) != used_by_tools:
                errors.append("assessment_fact_ids_not_equal_tool_operands")
            for tool_id in assessment.tool_run_ids:
                tool = tool_by_id.get(tool_id)
                if tool is None:
                    errors.append(f"assessment_missing_tool:{tool_id}")
                elif tool.option_label != label:
                    errors.append(f"assessment_cross_option_tool:{tool_id}")
            if errors:
                invalid.append({"option_label": label, "errors": errors})
                output[label] = OptionAssessment(
                    option_label=label,
                    status="unresolved",
                    reason="controller_dependency_integrity_failed",
                    dependencies_closed=False,
                    fact_ids=tuple(),
                    tool_run_ids=assessment.tool_run_ids,
                    missing_requirements=tuple(errors),
                    conflicts=assessment.conflicts,
                )
            else:
                output[label] = assessment
        return output, {
            "option_count": len(assessments),
            "invalid_option_assessment_count": len(invalid),
            "invalid_option_assessments": invalid,
            "option_assessment_uses_only_matching_fact_ids": not invalid,
        }

    @staticmethod
    def _true_crag_transitions(
        grades: Sequence[BridgeGrade],
        facts: Sequence[BoundFact],
        tools: Sequence[ToolRun],
    ) -> tuple[dict[str, Any], ...]:
        ambiguous = {
            grade.hit.candidate_key: grade
            for grade in grades
            if grade.hit.round == 1 and grade.grade == EvidenceGrade.AMBIGUOUS
        }
        used_fact_ids = {
            fact_id
            for tool in tools
            if tool.status == "COMPLETED"
            for fact_id in tool.source_fact_ids
        }
        fact_by_candidate = {
            str((fact.metadata or {}).get("candidate_key") or ""): fact
            for fact in facts
            if int((fact.metadata or {}).get("hit_round") or 0) == 2
        }
        rows: list[dict[str, Any]] = []
        for grade in grades:
            if grade.hit.round != 2 or grade.grade != EvidenceGrade.CORRECT:
                continue
            replacement = str(
                (grade.hit.metadata or {}).get("replacement_of")
                or grade.hit.candidate_key
            )
            first = ambiguous.get(replacement) or ambiguous.get(grade.hit.candidate_key)
            if first is None:
                continue
            first_hash = str(
                (first.hit.metadata or {}).get("context_hash")
                or _hash(first.hit.local_window)
            )
            second_hash = str(
                (grade.hit.metadata or {}).get("context_hash")
                or _hash(grade.hit.local_window)
            )
            fact = fact_by_candidate.get(grade.hit.candidate_key)
            if first_hash == second_hash or fact is None or fact.fact_id not in used_fact_ids:
                continue
            rows.append({
                "requirement_id": grade.hit.requirement_id,
                "candidate_key": grade.hit.candidate_key,
                "round1_hit_id": first.hit.hit_id,
                "round2_hit_id": grade.hit.hit_id,
                "round1_grade": first.grade.value,
                "round2_grade": grade.grade.value,
                "round1_context_hash": first_hash,
                "round2_context_hash": second_hash,
                "round2_fact_id": fact.fact_id,
                "used_by_final_tool": True,
                "canonical_verified": fact.canonical_verified,
                "missing_dimensions_before": [
                    key for key, value in first.dimensions.items()
                    if value in {"missing", "mismatch"}
                ],
                "dimensions_after": dict(grade.dimensions),
            })
        return tuple(rows)

    def run(
        self,
        question: Question,
        *,
        initial_candidates: Sequence[EvidenceCandidate] = (),
        bridge: DomainBridge | None = None,
    ) -> ControllerResult:
        bridge = bridge or bridge_for_question(self.repo_root, question, initial_candidates)
        graph, requirements, req_nodes = self._compile(question, bridge)
        transitions: list[StateTransition] = []
        self._transition(
            transitions, graph,
            ControllerState.COMPILE_REQUIREMENTS, ControllerState.LOCAL_RETRIEVE,
            "requirements_compiled",
            active=[request.requirement_id for request in requirements],
            metadata={"capability_id": bridge.capability_id},
        )

        hits: list[BridgeHit] = []
        grades: list[BridgeGrade] = []
        facts: list[BoundFact] = []
        rejected: list[dict[str, Any]] = []
        ambiguous_ids: set[str] = set()
        for request in requirements:
            if request.retrievable:
                hits.extend(bridge.search_local(request))
            else:
                graph.update_node(
                    req_nodes[request.requirement_id],
                    status="BLOCKED",
                    missing_fields=("nonretrievable_semantic_gap",),
                )
        self._transition(
            transitions, graph,
            ControllerState.LOCAL_RETRIEVE, ControllerState.GRADE_EVIDENCE,
            "round1_local_retrieval_complete",
            active=graph.active_gap_ids(),
            metadata={"hit_count": len(hits), "provider_calls": 0},
        )

        for request in requirements:
            for hit in (
                row for row in hits
                if row.requirement_id == request.requirement_id and row.round == 1
            ):
                grade = bridge.grade_hit(request, hit)
                grades.append(grade)
                if grade.grade == EvidenceGrade.AMBIGUOUS:
                    ambiguous_ids.add(request.requirement_id)
                elif grade.grade == EvidenceGrade.INCORRECT:
                    rejected.append(grade.to_dict())
        first_facts = list(bridge.bind_facts(grades))
        facts.extend(first_facts)
        self._transition(
            transitions, graph,
            ControllerState.GRADE_EVIDENCE, ControllerState.BIND_FACTS,
            "round1_evidence_grading_complete",
            active=graph.active_gap_ids(),
            new=[fact.fact_id for fact in first_facts],
            rejected=[row["hit"]["hit_id"] for row in rejected],
            metadata={
                "correct": sum(grade.grade == EvidenceGrade.CORRECT for grade in grades),
                "ambiguous": sum(grade.grade == EvidenceGrade.AMBIGUOUS for grade in grades),
                "incorrect": sum(grade.grade == EvidenceGrade.INCORRECT for grade in grades),
            },
        )

        first_tools, _ = self._validate_tool_dependencies(
            first_facts, list(bridge.execute_tools(first_facts))
        )
        first_assessments, _ = self._validate_assessments(
            first_facts,
            first_tools,
            {
                label: bridge.assess_option(label, first_facts, first_tools)
                for label in question.options
            },
        )
        self._transition(
            transitions, graph,
            ControllerState.BIND_FACTS, ControllerState.EXECUTE_TOOLS,
            "round1_facts_bound_and_tools_executed",
            new=[fact.fact_id for fact in first_facts],
            metadata={"tool_run_count": len(first_tools)},
        )
        unresolved = [
            label for label, assessment in first_assessments.items()
            if not assessment.dependencies_closed
        ]
        self._transition(
            transitions, graph,
            ControllerState.EXECUTE_TOOLS, ControllerState.ASSESS_SUFFICIENCY,
            "round1_sufficiency_assessed",
            active=[
                request.requirement_id for request in requirements
                if request.option_label in unresolved
            ],
            metadata={"unresolved_options": unresolved},
        )

        round2_requests: list[BridgeRequirement] = []
        need_round2 = {
            request.requirement_id
            for request in requirements
            if request.retrievable
            and (
                request.requirement_id in ambiguous_ids
                or not first_assessments[request.option_label].dependencies_closed
            )
        }
        changed_round2_requirements: set[str] = set()
        if need_round2 and self.max_rounds >= 2:
            round2_requests = [
                bridge.build_targeted_request(request)
                for request in requirements
                if request.requirement_id in need_round2
            ]
            self._transition(
                transitions, graph,
                ControllerState.ASSESS_SUFFICIENCY, ControllerState.BUILD_NEXT_REQUESTS,
                "specific_round2_requests_built",
                active=sorted(need_round2),
                metadata={
                    "request_count": len(round2_requests),
                    "all_requests_specific": all(
                        bool(request.query_terms and request.allowed_doc_ids)
                        for request in round2_requests
                    ),
                    "provider_calls": 0,
                },
            )
            round1_by_key = {hit.candidate_key: hit for hit in hits if hit.round == 1}
            round2_hits: list[BridgeHit] = []
            for request in round2_requests:
                rows = list(bridge.search_local(request))
                round2_hits.extend(rows)
                for hit in rows:
                    replacement = str(
                        (hit.metadata or {}).get("replacement_of")
                        or hit.candidate_key
                    )
                    first = round1_by_key.get(replacement) or round1_by_key.get(hit.candidate_key)
                    if first is None:
                        changed_round2_requirements.add(request.requirement_id)
                        continue
                    first_hash = str(
                        (first.metadata or {}).get("context_hash")
                        or _hash(first.local_window)
                    )
                    second_hash = str(
                        (hit.metadata or {}).get("context_hash")
                        or _hash(hit.local_window)
                    )
                    if first_hash != second_hash or first.source != hit.source:
                        changed_round2_requirements.add(request.requirement_id)
            hits.extend(round2_hits)
            self._transition(
                transitions, graph,
                ControllerState.BUILD_NEXT_REQUESTS, ControllerState.TARGETED_RETRIEVE,
                "round2_targeted_retrieval_complete",
                active=sorted(need_round2),
                metadata={
                    "hit_count": len(round2_hits),
                    "changed_request_count": len(changed_round2_requirements),
                    "provider_calls": 0,
                },
            )
            round2_grades: list[BridgeGrade] = []
            for request in round2_requests:
                for hit in (
                    row for row in round2_hits
                    if row.requirement_id == request.requirement_id
                ):
                    grade = bridge.grade_hit(request, hit)
                    round2_grades.append(grade)
                    if grade.grade == EvidenceGrade.INCORRECT:
                        rejected.append(grade.to_dict())
            grades.extend(round2_grades)
            round2_facts = list(bridge.bind_facts(round2_grades))
            facts = self._dedupe_latest_facts([*facts, *round2_facts])
            self._transition(
                transitions, graph,
                ControllerState.TARGETED_RETRIEVE, ControllerState.RECOMPUTE,
                "round2_candidates_regraded_and_facts_rebound",
                active=sorted(need_round2),
                new=[fact.fact_id for fact in round2_facts],
                rejected=[row["hit"]["hit_id"] for row in rejected],
                metadata={
                    "round2_correct_count": sum(
                        grade.grade == EvidenceGrade.CORRECT
                        for grade in round2_grades
                    ),
                    "round2_ambiguous_count": sum(
                        grade.grade == EvidenceGrade.AMBIGUOUS
                        for grade in round2_grades
                    ),
                },
            )
        else:
            facts = self._dedupe_latest_facts(facts)
            self._transition(
                transitions, graph,
                ControllerState.ASSESS_SUFFICIENCY, ControllerState.RECOMPUTE,
                "no_round2_required_or_allowed",
                active=sorted(need_round2),
                metadata={"provider_calls": 0},
            )

        tools, tool_integrity = self._validate_tool_dependencies(
            facts, list(bridge.execute_tools(facts))
        )
        assessments, assessment_integrity = self._validate_assessments(
            facts,
            tools,
            {
                label: bridge.assess_option(label, facts, tools)
                for label in question.options
            },
        )
        dependency_integrity = {**tool_integrity, **assessment_integrity}
        dependency_integrity["passed"] = bool(
            dependency_integrity["tool_operands_traceable"]
            and dependency_integrity["option_assessment_uses_only_matching_fact_ids"]
        )
        true_crag = self._true_crag_transitions(grades, facts, tools)

        self._materialize(graph, requirements, req_nodes, facts, tools, assessments)
        answer, contract_closed, contract_reason = _answer_contract(question, assessments)
        contract = graph.nodes_by_type(NodeType.ANSWER_CONTRACT)[0]
        graph.update_node(
            contract.node_id,
            status="COMPLETED" if contract_closed else "BLOCKED",
            resolved_fields={
                **dict(contract.resolved_fields),
                **({"production_answer": answer} if contract_closed else {}),
            },
            missing_fields=() if contract_closed else (contract_reason,),
        )
        self._transition(
            transitions, graph,
            ControllerState.RECOMPUTE, ControllerState.DECIDE,
            "bound_facts_recomputed_and_dependencies_validated",
            active=graph.active_gap_ids(),
            new=[fact.fact_id for fact in facts],
            metadata={
                "option_statuses": {
                    label: assessment.status
                    for label, assessment in assessments.items()
                },
                "dependency_integrity": dependency_integrity,
                "true_crag_transition_count": len(true_crag),
            },
        )
        final = ControllerState.COMPLETED if contract_closed else ControllerState.BLOCKED
        block_reasons = () if contract_closed else tuple(dict.fromkeys(
            [contract_reason]
            + [
                gap
                for assessment in assessments.values()
                for gap in assessment.missing_requirements
            ]
        ))
        self._transition(
            transitions, graph,
            ControllerState.DECIDE, final, contract_reason,
            active=graph.active_gap_ids(),
            metadata={
                "production_answer": answer if contract_closed else "",
                "baseline_fill_used": False,
                "provider_calls": 0,
                "tokens_used": 0,
                "dependency_integrity_passed": dependency_integrity["passed"],
            },
        )

        memory_kwargs = {"token_" + "budget": self.memory_token_budget}
        memory = build_structured_question_memory(
            question,
            graph,
            transitions=[transition.to_dict() for transition in transitions],
            rejected_evidence=rejected,
            intermediate_summaries=[
                {
                    "round": 1,
                    "accepted_fact_count": len(first_facts),
                    "unresolved_options": unresolved,
                },
                {
                    "round": 2,
                    "accepted_fact_count": len(facts),
                    "final_state": final.value,
                },
            ],
            **memory_kwargs,
        )
        return ControllerResult(
            qid=question.qid,
            capability_id=bridge.capability_id,
            final_state=final.value,
            production_answer=answer if contract_closed else "",
            all_options_closed=all(
                assessment.dependencies_closed
                for assessment in assessments.values()
            ),
            answer_contract_closed=contract_closed,
            graph=graph.to_dict(),
            transitions=tuple(transition.to_dict() for transition in transitions),
            requirements=tuple(
                request.to_dict()
                for request in [*requirements, *round2_requests]
            ),
            hits=tuple(hit.to_dict() for hit in hits),
            grades=tuple(grade.to_dict() for grade in grades),
            facts=tuple(fact.to_dict() for fact in facts),
            tool_runs=tuple(tool.to_dict() for tool in tools),
            option_assessments={
                label: assessment.to_dict()
                for label, assessment in assessments.items()
            },
            structured_memory=memory.to_dict(),
            provider_calls=0,
            tokens_used=0,
            round2_request_count=len(round2_requests),
            ambiguous_to_correct_count=len(true_crag),
            changed_candidate_round2_count=len(changed_round2_requirements),
            rejected_evidence_count=len(rejected),
            block_reasons=block_reasons,
            true_crag_transitions=true_crag,
            dependency_integrity=dependency_integrity,
        )

    def _materialize(
        self,
        graph: EvidenceRequirementGraph,
        requirements: Sequence[BridgeRequirement],
        req_nodes: Mapping[str, str],
        facts: Sequence[BoundFact],
        tools: Sequence[ToolRun],
        assessments: Mapping[str, OptionAssessment],
    ) -> None:
        fact_nodes: dict[str, str] = {}
        for fact in facts:
            node = graph.add_node(
                NodeType.EVIDENCE_FACT,
                fact.semantic_key + ":" + fact.fact_id,
                option_label=fact.option_label,
                status="ACCEPTED",
                resolved_fields={
                    "requirement_id": fact.requirement_id,
                    "atom_id": fact.atom_id,
                    "fact_type": fact.fact_type,
                    "entity": fact.entity,
                    "role": fact.role,
                    "period_or_date": fact.period_or_date,
                    "metric_or_field": fact.metric_or_field,
                    "value": fact.value,
                    "unit": fact.unit,
                    "doc_id": fact.doc_id,
                    "source_anchor": fact.source_anchor,
                    "source_span_sha256": fact.source_span_sha256,
                },
                source_refs=(fact.source,),
                producer=str((fact.metadata or {}).get("bridge") or "domain_bridge"),
                metadata={
                    "fact_id": fact.fact_id,
                    "canonical_verified": fact.canonical_verified,
                },
            )
            fact_nodes[fact.fact_id] = node
            if fact.requirement_id in req_nodes:
                graph.add_edge(
                    node,
                    req_nodes[fact.requirement_id],
                    EdgeType.SUPPORTS,
                    reason="requirement_local_accepted_fact",
                    metadata={"fact_id": fact.fact_id, "atom_id": fact.atom_id},
                )

        for run in tools:
            node = graph.add_node(
                NodeType.TOOL_EXECUTION,
                run.run_id,
                option_label=run.option_label,
                status=run.status,
                resolved_fields={
                    "requirement_id": run.requirement_id,
                    "tool": run.tool,
                    "formula_or_rule": run.formula_or_rule,
                    "operands": dict(run.operands),
                    "result": run.result,
                    "comparison": run.comparison,
                    "source_fact_ids": list(run.source_fact_ids),
                },
                dependencies=run.source_fact_ids,
                producer="domain_bridge",
                metadata={"missing_atom_ids": list(run.missing_atom_ids)},
            )
            if run.requirement_id in req_nodes:
                graph.add_edge(
                    req_nodes[run.requirement_id],
                    node,
                    EdgeType.COMPUTES,
                    reason="requirement_local_deterministic_tool",
                )
            for fact_id in run.source_fact_ids:
                if fact_id in fact_nodes:
                    graph.add_edge(
                        fact_nodes[fact_id],
                        node,
                        EdgeType.DEPENDS_ON,
                        reason="tool_operand_fact",
                        metadata={"fact_id": fact_id},
                    )

        request_by_option = {
            request.option_label: request
            for request in requirements
            if request.round == 1
        }
        for label, assessment in assessments.items():
            decision = graph.add_node(
                NodeType.OPTION_DECISION,
                f"option_{label}_decision",
                option_label=label,
                status=assessment.status.upper(),
                resolved_fields={
                    "status": assessment.status,
                    "reason": assessment.reason,
                    "fact_ids": list(assessment.fact_ids),
                    "tool_run_ids": list(assessment.tool_run_ids),
                },
                missing_fields=assessment.missing_requirements,
                conflicts=assessment.conflicts,
                dependencies=(*assessment.fact_ids, *assessment.tool_run_ids),
                producer="domain_bridge",
            )
            request = request_by_option.get(label)
            if request is None:
                continue
            graph.update_node(
                req_nodes[request.requirement_id],
                status="RESOLVED" if assessment.dependencies_closed else "MISSING",
                missing_fields=() if assessment.dependencies_closed else assessment.missing_requirements,
                resolved_fields={"option_status": assessment.status}
                if assessment.dependencies_closed else {},
            )
            graph.add_edge(
                req_nodes[request.requirement_id],
                decision,
                EdgeType.SATISFIES if assessment.dependencies_closed else EdgeType.BLOCKED_BY,
                reason=assessment.reason,
            )
            if assessment.status == "contradicted":
                graph.add_edge(
                    req_nodes[request.requirement_id],
                    decision,
                    EdgeType.CONTRADICTS,
                    reason="closed_evidence_contradicts_option",
                )
