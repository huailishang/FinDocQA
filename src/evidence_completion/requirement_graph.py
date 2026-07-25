"""Domain-neutral evidence requirement graph.

The graph records what a question needs, what facts were accepted or rejected,
and why an option can or cannot be decided.  It intentionally has no baseline,
expected-answer, or oracle fields.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


class NodeType(str, Enum):
    QUESTION = "QUESTION"
    OPTION = "OPTION"
    REQUIRED_DOCUMENT = "REQUIRED_DOCUMENT"
    CLAIM_ATOM = "CLAIM_ATOM"
    EVIDENCE_FACT = "EVIDENCE_FACT"
    CONDITION = "CONDITION"
    FORMULA_OR_RULE = "FORMULA_OR_RULE"
    TOOL_EXECUTION = "TOOL_EXECUTION"
    OPTION_DECISION = "OPTION_DECISION"
    ANSWER_CONTRACT = "ANSWER_CONTRACT"


class EdgeType(str, Enum):
    REQUIRES = "REQUIRES"
    SCOPED_TO = "SCOPED_TO"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    COMPUTES = "COMPUTES"
    DEPENDS_ON = "DEPENDS_ON"
    BLOCKED_BY = "BLOCKED_BY"
    SATISFIES = "SATISFIES"


_FORBIDDEN_DECISION_KEYS = {
    "expected_answer", "oracle_answer", "old_production_answer",
    "baseline_answer", "gold_answer", "label_answer",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def semantic_slug(value: str) -> str:
    compact = re.sub(r"\s+", "_", str(value or "").strip().lower())
    compact = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "_", compact).strip("_")
    if compact:
        return compact[:96]
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def stable_node_id(qid: str, option_label: str, node_type: NodeType | str, semantic_key: str) -> str:
    kind = node_type.value if isinstance(node_type, NodeType) else str(node_type)
    label = str(option_label or "_")
    return f"{qid}:{label}:{kind}:{semantic_slug(semantic_key)}"


@dataclass(frozen=True)
class RequirementNode:
    node_id: str
    node_type: str
    semantic_key: str
    option_label: str = ""
    status: str = "PENDING"
    required_fields: tuple[str, ...] = ()
    resolved_fields: Mapping[str, Any] = field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    producer: str = "requirement_graph"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequirementEdge:
    edge_id: str
    source_id: str
    target_id: str
    edge_type: str
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceRequirementGraph:
    """Mutable builder with deterministic serialization and validation."""

    schema_version = "evidence_requirement_graph_v1"

    def __init__(self, qid: str, *, capability_id: str, domain: str) -> None:
        self.qid = str(qid)
        self.capability_id = str(capability_id)
        self.domain = str(domain)
        self._nodes: dict[str, RequirementNode] = {}
        self._edges: dict[str, RequirementEdge] = {}

    def add_node(
        self,
        node_type: NodeType | str,
        semantic_key: str,
        *,
        option_label: str = "",
        status: str = "PENDING",
        required_fields: Iterable[str] = (),
        resolved_fields: Mapping[str, Any] | None = None,
        missing_fields: Iterable[str] = (),
        conflicts: Iterable[str] = (),
        source_refs: Iterable[str] = (),
        dependencies: Iterable[str] = (),
        producer: str = "requirement_graph",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        kind = node_type.value if isinstance(node_type, NodeType) else str(node_type)
        node_id = stable_node_id(self.qid, option_label, kind, semantic_key)
        candidate = RequirementNode(
            node_id=node_id,
            node_type=kind,
            semantic_key=str(semantic_key),
            option_label=str(option_label),
            status=str(status),
            required_fields=tuple(dict.fromkeys(str(x) for x in required_fields if str(x))),
            resolved_fields=dict(resolved_fields or {}),
            missing_fields=tuple(dict.fromkeys(str(x) for x in missing_fields if str(x))),
            conflicts=tuple(dict.fromkeys(str(x) for x in conflicts if str(x))),
            source_refs=tuple(dict.fromkeys(str(x) for x in source_refs if str(x))),
            dependencies=tuple(dict.fromkeys(str(x) for x in dependencies if str(x))),
            producer=str(producer),
            metadata=dict(metadata or {}),
        )
        existing = self._nodes.get(node_id)
        if existing is not None and existing.semantic_key != candidate.semantic_key:
            raise ValueError(f"stable node id collision: {node_id}")
        self._nodes[node_id] = candidate
        return node_id

    def update_node(self, node_id: str, **changes: Any) -> None:
        node = self._nodes[node_id]
        allowed = {
            "status", "resolved_fields", "missing_fields", "conflicts",
            "source_refs", "dependencies", "producer", "metadata",
        }
        unexpected = set(changes) - allowed
        if unexpected:
            raise ValueError(f"unsupported node changes: {sorted(unexpected)}")
        normalized = dict(changes)
        for key in ("missing_fields", "conflicts", "source_refs", "dependencies"):
            if key in normalized:
                normalized[key] = tuple(dict.fromkeys(str(x) for x in normalized[key] if str(x)))
        for key in ("resolved_fields", "metadata"):
            if key in normalized:
                normalized[key] = dict(normalized[key] or {})
        self._nodes[node_id] = replace(node, **normalized)

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType | str,
        *,
        reason: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if source_id not in self._nodes or target_id not in self._nodes:
            raise KeyError("edge endpoint missing")
        kind = edge_type.value if isinstance(edge_type, EdgeType) else str(edge_type)
        edge_id = _hash((source_id, target_id, kind, reason))[:24]
        self._edges[edge_id] = RequirementEdge(
            edge_id=edge_id,
            source_id=source_id,
            target_id=target_id,
            edge_type=kind,
            reason=str(reason),
            metadata=dict(metadata or {}),
        )
        return edge_id

    @property
    def nodes(self) -> tuple[RequirementNode, ...]:
        return tuple(self._nodes[key] for key in sorted(self._nodes))

    @property
    def edges(self) -> tuple[RequirementEdge, ...]:
        return tuple(self._edges[key] for key in sorted(self._edges))

    def nodes_by_type(self, node_type: NodeType | str) -> tuple[RequirementNode, ...]:
        kind = node_type.value if isinstance(node_type, NodeType) else str(node_type)
        return tuple(node for node in self.nodes if node.node_type == kind)

    def active_gap_ids(self) -> tuple[str, ...]:
        return tuple(
            node.node_id for node in self.nodes
            if node.node_type in {NodeType.CLAIM_ATOM.value, NodeType.CONDITION.value}
            and node.status not in {"RESOLVED", "SUPPORTED", "CONTRADICTED"}
        )

    def validate(self) -> dict[str, Any]:
        errors: list[str] = []
        allowed_nodes = {item.value for item in NodeType}
        allowed_edges = {item.value for item in EdgeType}
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            errors.append("duplicate_node_ids")
        for node in self.nodes:
            if node.node_type not in allowed_nodes:
                errors.append(f"unknown_node_type:{node.node_type}")
            if node.node_id != stable_node_id(self.qid, node.option_label, node.node_type, node.semantic_key):
                errors.append(f"unstable_node_id:{node.node_id}")
            payload = node.to_dict()
            lowered = {str(key).lower() for key in payload}
            forbidden = lowered & _FORBIDDEN_DECISION_KEYS
            if forbidden:
                errors.append(f"forbidden_decision_key:{node.node_id}:{','.join(sorted(forbidden))}")
            metadata_keys = {str(key).lower() for key in dict(node.metadata or {})}
            forbidden_meta = metadata_keys & _FORBIDDEN_DECISION_KEYS
            if forbidden_meta:
                errors.append(f"forbidden_metadata_key:{node.node_id}:{','.join(sorted(forbidden_meta))}")
        for edge in self.edges:
            if edge.edge_type not in allowed_edges:
                errors.append(f"unknown_edge_type:{edge.edge_type}")
            if edge.source_id not in self._nodes or edge.target_id not in self._nodes:
                errors.append(f"dangling_edge:{edge.edge_id}")
        required_types = {
            NodeType.QUESTION.value, NodeType.OPTION.value,
            NodeType.ANSWER_CONTRACT.value,
        }
        present = {node.node_type for node in self.nodes}
        for missing in sorted(required_types - present):
            errors.append(f"missing_required_node_type:{missing}")
        return {
            "valid": not errors,
            "errors": errors,
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "graph_hash": self.graph_hash(),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "qid": self.qid,
            "domain": self.domain,
            "capability_id": self.capability_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }
        payload["validation"] = self.validate()
        return payload

    def graph_hash(self) -> str:
        return _hash({
            "schema_version": self.schema_version,
            "qid": self.qid,
            "domain": self.domain,
            "capability_id": self.capability_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        })
