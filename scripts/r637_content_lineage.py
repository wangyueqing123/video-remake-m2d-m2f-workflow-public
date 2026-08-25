#!/usr/bin/env python3
"""R6.37+ P2/P3 lineage rules for M2-D provider packages."""

from __future__ import annotations

from typing import Any


CONTENT_LINEAGE_VERSIONS = frozenset({"R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"})
CONTENT_LINEAGE_ROUTES = frozenset({"M2_D_SHARE_FIRST"})
LEGACY_LINEAGE_KEYS = (
    "route_analysis_relative_path",
    "route_analysis_sha256",
    "p3_blueprint_relative_path",
    "p3_blueprint_sha256",
)


def uses_content_lineage(state: dict[str, Any], job: dict[str, Any]) -> bool:
    return (
        str(state.get("skill_version", "")) in CONTENT_LINEAGE_VERSIONS
        and str(job.get("route_id", "")) in CONTENT_LINEAGE_ROUTES
    )


def content_lineage_specs(route_id: str) -> list[tuple[str, str, str, str, str]]:
    specs = [
        (
            "source_content",
            "SOURCE_CONTENT_CONTRACT",
            "source_content_contract_relative_path",
            "source_content_contract_sha256",
            "artifacts/P2/SOURCE_CONTENT_CONTRACT.json",
        ),
        (
            "copy_fidelity",
            "COPY_FIDELITY_AUDIT",
            "copy_fidelity_audit_relative_path",
            "copy_fidelity_audit_sha256",
            "artifacts/P3/COPY_FIDELITY_AUDIT.json",
        ),
        (
            "narration_plan",
            "NARRATION_PLAN",
            "narration_plan_relative_path",
            "narration_plan_sha256",
            "artifacts/P3/NARRATION_PLAN.json",
        ),
    ]
    return specs


def validate_content_lineage_shape(lineage: dict[str, Any], route_id: str) -> list[str]:
    issues: list[str] = []
    for _, label, path_key, hash_key, _ in content_lineage_specs(route_id):
        if not isinstance(lineage.get(path_key), str) or not str(lineage.get(path_key)).strip():
            issues.append(f"{label}_PATH_MISSING")
        if not isinstance(lineage.get(hash_key), str) or not str(lineage.get(hash_key)).strip():
            issues.append(f"{label}_HASH_MISSING")
    if any(lineage.get(key) for key in LEGACY_LINEAGE_KEYS):
        issues.append("R637_CONTENT_CALL_PACKAGE_FORBIDS_LEGACY_ROUTE_OR_P3_LINEAGE")
    return sorted(set(issues))
