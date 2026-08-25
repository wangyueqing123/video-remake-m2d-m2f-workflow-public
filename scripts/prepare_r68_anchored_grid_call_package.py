#!/usr/bin/env python3
"""Compile one exact R6.8 ImageGen grid package from validated project artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from r62_project import (
    SKILL_ROOT,
    imported_anchor_origin_passed,
    normalize_project_relative,
    sha256_file,
    skill_tree_fingerprint,
    write_json_atomic,
)
from r634_integrity_contract import require_state_flow_receipt, resolve_effective_inputs
from r637_content_lineage import content_lineage_specs, uses_content_lineage
from r639_keyframe_contract import validate_plan as validate_r639_keyframes
from release_contract import CANONICAL_P5_VERSIONS, KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS, canonical_p5_schema_version, canonical_p5_suffix, required_core_qc_schema
from validate_r62_call_package import validate as validate_call_package
from validate_r62_timeline_evidence import canonical_fingerprint


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def project_file(project: Path, relative: str) -> tuple[str, Path]:
    normalized = normalize_project_relative(relative)
    path = (project / normalized).resolve()
    try:
        path.relative_to(project)
    except ValueError as exc:
        raise ValueError(f"PROJECT_PATH_ESCAPES_ROOT:{relative}") from exc
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{normalized}")
    return normalized, path


def artifact_is_validated(state: dict[str, Any], relative: str, digest: str) -> bool:
    artifact_root = state.get("artifacts")
    artifacts: list[dict[str, Any]] = []
    if isinstance(artifact_root, dict):
        for phase_rows in artifact_root.values():
            if isinstance(phase_rows, dict):
                artifacts.extend(row for row in phase_rows.values() if isinstance(row, dict))
    elif isinstance(artifact_root, list):
        artifacts = [row for row in artifact_root if isinstance(row, dict)]
    return any(
        isinstance(row, dict)
        and row.get("relative_path", row.get("path")) == relative
        and row.get("sha256") == digest
        and row.get("validation_status") == "VALIDATED"
        for row in artifacts
    )


def require_safe_prior_grid(project: Path, state: dict[str, Any], prior_grid_id: str) -> dict[str, Any]:
    records = [
        row for row in state.get("qc_records", [])
        if isinstance(row, dict) and row.get("grid_id") == prior_grid_id and row.get("decision") == "PASSED"
    ]
    if len(records) == 0 and prior_grid_id == "G01" and imported_anchor_origin_passed(project, state):
        historical = state.get("historical_revision") if isinstance(state.get("historical_revision"), dict) else {}
        historical_qc = historical.get("anchor_origin_historical_qc") if isinstance(historical.get("anchor_origin_historical_qc"), dict) else {}
        qc_relative, qc_path = project_file(project, str(historical_qc.get("relative_path", "")))
        if sha256_file(qc_path) != historical_qc.get("sha256"):
            raise SystemExit("IMPORTED_PRIOR_GRID_QC_BINDING_STALE")
        return {"relative_path": qc_relative, "sha256": sha256_file(qc_path), "origin": "IMPORTED_ZERO_CALL_REAUDIT"}
    if len(records) != 1:
        raise SystemExit("IMMEDIATE_PRIOR_GRID_REQUIRES_ONE_PASSED_QC")
    qc_relative, qc_path = project_file(project, str(records[0].get("qc_relative_path", "")))
    if sha256_file(qc_path) != records[0].get("qc_sha256"):
        raise SystemExit("IMMEDIATE_PRIOR_GRID_QC_BINDING_STALE")
    qc = load_json(qc_path)
    promotion = qc.get("reference_promotion") if isinstance(qc.get("reference_promotion"), dict) else {}
    required_schema = required_core_qc_schema(str(state.get("skill_version", "")))
    if qc.get("schema_version") != required_schema or not all(
        promotion.get(key) is True for key in (
            "eligible", "support_topology_passed", "critical_contacts_verifiable", "end_state_safe_for_next_segment"
        )
    ):
        raise SystemExit("IMMEDIATE_PRIOR_GRID_NOT_TOPOLOGY_SAFE_FOR_REFERENCE")
    return {"relative_path": qc_relative, "sha256": sha256_file(qc_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--grid-id", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--planned-output", required=True)
    args = parser.parse_args()

    project = args.project_dir.resolve()
    state = load_json(project / "R62_PROJECT.json")
    current_tree_sha256, _ = skill_tree_fingerprint()
    if state.get("skill", {}).get("tree_sha256") != current_tree_sha256:
        raise SystemExit("PROJECT_SKILL_TREE_BINDING_STALE: migrate again after the Skill tree is frozen")

    effective_job_path, effective_plan_path, fact_lineage = resolve_effective_inputs(project, state)
    fixed = {
        "job": effective_job_path.relative_to(project).as_posix(),
        "evidence": "artifacts/P2/TIMELINE_EVIDENCE.json",
        "plan": effective_plan_path.relative_to(project).as_posix(),
        "capability": "artifacts/P5/IMAGEGEN_CAPABILITY.json",
    }
    resolved = {key: project_file(project, value) for key, value in fixed.items()}
    job = load_json(resolved["job"][1])
    if job.get("route_id") == "M2_F_SOURCE_AUDIO_RESTYLE":
        resolved["source_audio_plan"] = project_file(project, "artifacts/P3/SOURCE_AUDIO_PLAN.json")
    elif uses_content_lineage(state, job):
        for resolved_key, _, _, _, relative in content_lineage_specs(str(job.get("route_id", ""))):
            resolved[resolved_key] = project_file(project, relative)
    else:
        resolved["route"] = project_file(project, "artifacts/P2/ROUTE_ANALYSIS.json")
        resolved["p3"] = project_file(project, "artifacts/P3/P3_BLUEPRINT.json")
    evidence = load_json(resolved["evidence"][1])
    plan = load_json(resolved["plan"][1])
    if state.get("skill_version") in KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS:
        keyframe_issues = validate_r639_keyframes(plan, require=True)
        if keyframe_issues:
            raise SystemExit("R639_KEYFRAME_CONTRACT_INVALID:" + ",".join(keyframe_issues))
    state_flow_receipt = (
        require_state_flow_receipt(project, state, resolved["plan"][1])
        if state.get("skill_version") in {"R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}
        else None
    )

    grids = [row for row in plan.get("grids", []) if isinstance(row, dict) and row.get("grid_id") == args.grid_id]
    if len(grids) != 1:
        raise SystemExit(f"GRID_ID_NOT_UNIQUE:{args.grid_id}")
    grid = grids[0]
    grid_order = grid.get("grid_order")
    if not isinstance(grid_order, int) or grid_order < 1:
        raise SystemExit("GRID_ORDER_INVALID")

    version = str(state.get("skill_version", ""))
    suffix = canonical_p5_suffix(version)
    current_review_relative = f"artifacts/P5/{args.grid_id}_{suffix}_PENDING_GRID_REVIEW.json"
    current_review_path = project / current_review_relative
    anchor_review: dict[str, Any] | None = None
    if grid_order > 1 or (version not in CANONICAL_P5_VERSIONS and not current_review_path.is_file()):
        resolved["anchor_review"] = project_file(project, "artifacts/P5/P5_R66_VISUAL_ANCHOR_REVIEW.json")
        anchor_review = load_json(resolved["anchor_review"][1])
    if version in CANONICAL_P5_VERSIONS:
        if not current_review_path.is_file():
            raise SystemExit(f"{suffix}_CANONICAL_P5_REVIEW_REQUIRED_NO_LEGACY_FALLBACK")
        review_row = load_json(current_review_path)
        if (
            review_row.get("schema_version") != f"{canonical_p5_schema_version(version)}-P5-PENDING-GRID-REVIEW-1.0"
            or review_row.get("status") != "PASSED"
            or review_row.get("grid_id") != args.grid_id
        ):
            raise SystemExit(f"P5_{suffix}_GRID_REVIEW_INVALID")
        if version in {"R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"} and review_row.get("accepted_deviation_fact_contracts") != fact_lineage:
            raise SystemExit("P5_R634_ACCEPTED_DEVIATION_FACT_LINEAGE_MISMATCH")
        p5_review_relative, p5_review_path = current_review_relative, current_review_path
        for relative, digest, label in (
            (p5_review_relative, sha256_file(p5_review_path), f"{suffix}_CANONICAL_P5_REVIEW"),
            (str(review_row.get("prompt_relative_path", "")), str(review_row.get("prompt_sha256", "")), f"{suffix}_CANONICAL_PROMPT"),
            (str(review_row.get("audit_relative_path", "")), str(review_row.get("audit_sha256", "")), f"{suffix}_CANONICAL_PROMPT_AUDIT"),
        ):
            if not artifact_is_validated(state, relative, digest):
                raise SystemExit(f"{label}_NOT_VALIDATED")
    elif current_review_path.is_file():
        review_row = load_json(current_review_path)
        if review_row.get("status") != "PASSED" or review_row.get("grid_id") != args.grid_id:
            raise SystemExit(f"P5_{suffix}_GRID_REVIEW_INVALID")
        p5_review_relative, p5_review_path = current_review_relative, current_review_path
    else:
        if anchor_review is None:
            raise SystemExit("P5_VISUAL_ANCHOR_REVIEW_REQUIRED_FOR_LEGACY_FALLBACK")
        review_rows = [row for row in anchor_review.get("affected_grids", []) if isinstance(row, dict) and row.get("grid_id") == args.grid_id]
        if len(review_rows) != 1:
            raise SystemExit(f"P5_ANCHOR_REVIEW_GRID_NOT_UNIQUE:{args.grid_id}")
        review_row = review_rows[0]
        p5_review_relative, p5_review_path = resolved["anchor_review"]
    prompt_relative, prompt_path = project_file(project, str(review_row.get("prompt_relative_path", "")))
    audit_relative, audit_path = project_file(project, str(review_row.get("audit_relative_path", "")))
    if sha256_file(prompt_path) != review_row.get("prompt_sha256") or sha256_file(audit_path) != review_row.get("audit_sha256"):
        raise SystemExit("P5_GRID_REVIEW_BINDING_STALE")

    geometry = job.get("grid_geometry_contract") if isinstance(job.get("grid_geometry_contract"), dict) else {}
    budget = job.get("generation_budget") if isinstance(job.get("generation_budget"), dict) else {}
    references: list[dict[str, Any]] = []
    submitted: list[str] = []
    input_mode = "TEXT_ONLY_WHOLE_GRID"

    if grid_order > 1:
        if anchor_review is None:
            raise SystemExit("P5_VISUAL_ANCHOR_REVIEW_REQUIRED_AFTER_G01")
        input_mode = "ANCHORED_WHOLE_GRID"
        contract_relative, contract_path = project_file(project, str(anchor_review.get("anchor_contract_relative_path", "")))
        contract = load_json(contract_path)
        if sha256_file(contract_path) != anchor_review.get("anchor_contract_sha256"):
            raise SystemExit("P5_ANCHOR_CONTRACT_BINDING_STALE")
        anchor = contract.get("anchor_asset") if isinstance(contract.get("anchor_asset"), dict) else {}
        anchor_relative, anchor_path = project_file(project, str(anchor.get("relative_path", "")))
        anchor_sha256 = sha256_file(anchor_path)
        if anchor_sha256 != anchor.get("sha256"):
            raise SystemExit("PROJECT_VISUAL_ANCHOR_ASSET_STALE")

        prior_grids = [row for row in plan.get("grids", []) if isinstance(row, dict) and row.get("grid_order") == grid_order - 1]
        if len(prior_grids) != 1:
            raise SystemExit("IMMEDIATE_PRIOR_GRID_NOT_UNIQUE")
        prior_grid_id = str(prior_grids[0].get("grid_id", ""))
        prior_qc_reference = require_safe_prior_grid(project, state, prior_grid_id)
        end_relative, end_path = project_file(project, f"artifacts/P6/{prior_grid_id}_END_STATE.png")
        receipt_relative, receipt_path = project_file(project, f"artifacts/P6/{prior_grid_id}_END_STATE_CROP_RECEIPT.json")
        end_sha256 = sha256_file(end_path)
        receipt_sha256 = sha256_file(receipt_path)

        for relative, digest, label in (
            (anchor_relative, anchor_sha256, "PROJECT_VISUAL_ANCHOR_ASSET"),
            (contract_relative, sha256_file(contract_path), "PROJECT_VISUAL_ANCHOR_CONTRACT"),
            (end_relative, end_sha256, "PREVIOUS_SEGMENT_END_STATE"),
            (receipt_relative, receipt_sha256, "PREVIOUS_SEGMENT_END_STATE_RECEIPT"),
        ):
            if not artifact_is_validated(state, relative, digest):
                raise SystemExit(f"{label}_NOT_VALIDATED")

        submitted = [anchor_relative, end_relative]
        references = [
            {
                "role": "PROJECT_VISUAL_ANCHOR", "relative_path": anchor_relative, "sha256": anchor_sha256,
                "anchor_contract_relative_path": contract_relative, "anchor_contract_sha256": sha256_file(contract_path),
                "controls": ["person_identity", "animal_identity", "visual_style", "core_environment"],
            },
            {
                "role": "PREVIOUS_SEGMENT_END_STATE", "relative_path": end_relative, "sha256": end_sha256,
                "crop_receipt_relative_path": receipt_relative, "crop_receipt_sha256": receipt_sha256,
                "controls": ["continuity_entry_state"],
            },
        ]

    capability_relative, capability_path = resolved["capability"]
    style_path = SKILL_ROOT / "assets" / "style-registry.json"
    registry_path = SKILL_ROOT / "assets" / "imagegen-capability-registry.json"
    planned_output = normalize_project_relative(args.planned_output)
    package_relative = normalize_project_relative(args.package)
    package_path = (project / package_relative).resolve()
    package = {
        "schema_version": "R6.2-P6-CALL-PACKAGE-1.0", "job_id": job.get("job_id"), "phase": "P6",
        "status": "WAIT_REVIEW", "call_kind": "GRID_BASELINE", "call_ordinal": 1, "tool": "BUILT_IN_IMAGEGEN",
        "request": {
            "input_mode": input_mode, "prompt_relative_path": prompt_relative, "prompt_sha256": sha256_file(prompt_path),
            "referenced_image_paths": submitted, "include_recent_conversation_images": False,
            "failed_output_used_as_reference": False, "planned_output_relative_path": planned_output,
        },
        "target": {
            "grid_id": args.grid_id, "grid_order": grid_order, "segment_id": grid.get("segment_id"),
            "grid_role": grid.get("grid_role"), "layout": grid.get("layout"),
            "whole_grid_aspect_ratio": grid.get("target_canvas_aspect_ratio"),
            "cell_aspect_ratio": grid.get("target_cell_aspect_ratio"), "geometry_enforcement": geometry.get("enforcement"),
            "row_major_chronology": True,
        },
        "capability": {
            "capability_relative_path": capability_relative, "capability_sha256": sha256_file(capability_path),
            "requested_canvas_aspect_ratio": geometry.get("canvas_aspect_ratio"),
            "requested_cell_aspect_ratio": geometry.get("cell_aspect_ratio"), "preflight_decision": "PASSED",
        },
        "lineage": {
            "job_relative_path": resolved["job"][0], "job_sha256": sha256_file(resolved["job"][1]),
            "timeline_evidence_relative_path": resolved["evidence"][0], "timeline_evidence_file_sha256": sha256_file(resolved["evidence"][1]),
            "timeline_evidence_fingerprint": canonical_fingerprint(evidence),
            "scene_plan_relative_path": resolved["plan"][0], "scene_plan_sha256": sha256_file(resolved["plan"][1]),
            "prompt_audit_relative_path": audit_relative, "prompt_audit_sha256": sha256_file(audit_path),
            "p5_review_relative_path": p5_review_relative, "p5_review_sha256": sha256_file(p5_review_path),
            "style_registry_skill_relative_path": "assets/style-registry.json", "style_registry_sha256": sha256_file(style_path),
            "capability_registry_skill_relative_path": "assets/imagegen-capability-registry.json", "capability_registry_sha256": sha256_file(registry_path),
            "skill_tree_sha256": current_tree_sha256, "prior_qc_relative_path": "", "prior_qc_sha256": "",
            "prior_output_relative_path": "", "prior_output_sha256": "",
            "accepted_deviation_fact_contracts": fact_lineage,
            "segment_state_flow_audit": state_flow_receipt,
        },
        "reference_roles": references, "correction_scope": [],
        "cost_and_retry": {
            "planned_calls": 1, "auto_retry": False, "per_cell_calls": 0,
            "per_grid_baseline_calls": budget.get("per_grid_baseline_calls"),
            "per_grid_consolidated_corrections": budget.get("per_grid_consolidated_corrections"),
            "project_max_grid_baselines": budget.get("project_max_grid_baselines"),
            "project_max_grid_corrections": budget.get("project_max_grid_corrections"),
            "pilot_gate_after_first_grid": budget.get("pilot_gate_after_first_grid"),
        },
        "human_approval_required": True, "one_approval_one_submission": True, "automatic_retry_forbidden": True,
    }
    if job.get("route_id") == "M2_F_SOURCE_AUDIO_RESTYLE":
        package["lineage"].update({
            "source_audio_plan_relative_path": resolved["source_audio_plan"][0],
            "source_audio_plan_sha256": sha256_file(resolved["source_audio_plan"][1]),
        })
    elif uses_content_lineage(state, job):
        for resolved_key, _, path_key, hash_key, _ in content_lineage_specs(str(job.get("route_id", ""))):
            package["lineage"].update({
                path_key: resolved[resolved_key][0],
                hash_key: sha256_file(resolved[resolved_key][1]),
            })
    else:
        package["lineage"].update({
            "route_analysis_relative_path": resolved["route"][0],
            "route_analysis_sha256": sha256_file(resolved["route"][1]),
            "p3_blueprint_relative_path": resolved["p3"][0],
            "p3_blueprint_sha256": sha256_file(resolved["p3"][1]),
        })
    if grid_order > 1:
        package["lineage"].update({
            "prior_grid_qc_relative_path": prior_qc_reference["relative_path"],
            "prior_grid_qc_sha256": prior_qc_reference["sha256"],
        })
    write_json_atomic(package_path, package)
    issues = validate_call_package(project, package_path)
    result = {
        "status": "PASSED" if not issues else "FAILED", "grid_id": args.grid_id,
        "package_relative_path": package_relative, "package_sha256": sha256_file(package_path),
        "planned_output_relative_path": planned_output, "input_mode": input_mode,
        "reference_roles": [row["role"] for row in references], "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
