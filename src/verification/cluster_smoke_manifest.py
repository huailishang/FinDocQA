"""Offline cluster smoke manifest validation utilities."""
from __future__ import annotations

from typing import Any, Mapping

REQUIRED_TOP_LEVEL = {
    "manifest_version",
    "cluster",
    "cases",
    "no_model_call",
    "no_full100_run",
    "no_leaderboard_upload",
    "no_candidate_build",
}
REQUIRED_CASE_FIELDS = {"qid", "expected_state", "purpose", "rollback_rule"}


class ClusterSmokeManifestError(ValueError):
    """Raised when an offline smoke manifest is unsafe or incomplete."""


def validate_offline_smoke_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_TOP_LEVEL - set(manifest))
    if missing:
        raise ClusterSmokeManifestError("missing_top_level:" + ",".join(missing))
    for flag in ("no_model_call", "no_full100_run", "no_leaderboard_upload", "no_candidate_build"):
        if manifest.get(flag) is not True:
            raise ClusterSmokeManifestError(f"unsafe_flag:{flag}")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ClusterSmokeManifestError("cases_empty_or_not_list")
    qids: list[str] = []
    for idx, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ClusterSmokeManifestError(f"case_not_mapping:{idx}")
        missing_case = sorted(REQUIRED_CASE_FIELDS - set(case))
        if missing_case:
            raise ClusterSmokeManifestError(f"case_{idx}_missing:" + ",".join(missing_case))
        qid = str(case.get("qid") or "")
        if not qid:
            raise ClusterSmokeManifestError(f"case_{idx}_empty_qid")
        qids.append(qid)
        expected_state = str(case.get("expected_state") or "")
        if expected_state not in {"complete_unique_match", "zero_match", "unresolved_variables_or_coverage_gap", "multi_match"}:
            raise ClusterSmokeManifestError(f"case_{idx}_unsupported_expected_state:{expected_state}")
    if len(qids) != len(set(qids)):
        raise ClusterSmokeManifestError("duplicate_qids")
    return {"valid": True, "case_count": len(cases), "qids": qids, "cluster": str(manifest.get("cluster"))}
