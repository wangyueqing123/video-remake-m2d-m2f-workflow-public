#!/usr/bin/env python3
"""Validate R6.2 baseline/correction packages, lineage, geometry, and budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from r62_project import (
    SKILL_ROOT,
    STATE_NAME,
    load_json,
    normalize_project_relative,
    sha256_file,
    skill_tree_fingerprint,
)
from release_contract import (
    CANONICAL_P5_VERSIONS,
    COMPATIBLE_P6_QC_SCHEMAS,
    KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS,
    SEMANTIC_PROMPT_AUDIT_VERSIONS,
    canonical_p5_suffix,
)
from r639_keyframe_contract import prompt_proof as r639_prompt_proof, validate_plan as validate_r639_keyframes
from r634_integrity_contract import require_state_flow_receipt, resolve_effective_inputs
from r635_source_content_lock import validate_project as validate_source_content_project
from r637_content_lineage import content_lineage_specs, uses_content_lineage, validate_content_lineage_shape
from validate_r619_state_contract import semantic_prompt_audit
from validate_r62_imagegen_capability import validate_capability
from validate_r62_timeline_evidence import canonical_fingerprint
from validate_r66_end_state_receipt import validate as validate_end_state_receipt
from validate_r66_visual_anchor import validate as validate_visual_anchor


HEX64 = re.compile(r"^[0-9a-f]{64}$")
CREATIVE_ROUTES = {"M2_D_SHARE_FIRST", "M2_F_SOURCE_AUDIO_RESTYLE"}


def _hash(value: Any) -> str:
    return value.lower() if isinstance(value, str) else ""


def _project_file(project: Path, relative: Any, label: str, issues: list[str]) -> Path | None:
    try:
        normalized = normalize_project_relative(str(relative))
    except ValueError:
        issues.append(f"{label}_PATH_NOT_PROJECT_RELATIVE")
        return None
    path = (project / normalized).resolve()
    try:
        path.relative_to(project)
    except ValueError:
        issues.append(f"{label}_PATH_ESCAPES_PROJECT")
        return None
    if not path.is_file():
        issues.append(f"{label}_FILE_MISSING")
        return None
    return path


def _verify_project_binding(
    project: Path,
    container: dict[str, Any],
    path_key: str,
    hash_key: str,
    label: str,
    issues: list[str],
) -> Path | None:
    path = _project_file(project, container.get(path_key), label, issues)
    expected = _hash(container.get(hash_key))
    if not HEX64.fullmatch(expected):
        issues.append(f"{label}_HASH_INVALID")
    elif path is not None and sha256_file(path) != expected:
        issues.append(f"{label}_HASH_MISMATCH")
    return path


def _walk_strings(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value


def _require_validated_artifact(manifest: dict[str, Any], relative: Any, expected_hash: Any, label: str, issues: list[str]) -> None:
    path_value = str(relative or "")
    hash_value = _hash(expected_hash)
    matches = []
    for rows in manifest.get("artifacts", {}).values():
        if not isinstance(rows, dict):
            continue
        matches.extend(
            record for record in rows.values()
            if isinstance(record, dict)
            and record.get("relative_path") == path_value
            and _hash(record.get("sha256")) == hash_value
        )
    if not matches:
        issues.append(f"{label}_NOT_BOUND_IN_ARTIFACT_LEDGER")
    elif not any(record.get("validation_status") == "VALIDATED" for record in matches):
        issues.append(f"{label}_ARTIFACT_NOT_VALIDATED")


def validate(project: Path, package_path: Path) -> list[str]:
    issues: list[str] = []
    manifest_path = project / STATE_NAME
    if not manifest_path.is_file():
        return ["PROJECT_MANIFEST_MISSING"]
    manifest = load_json(manifest_path)
    package = load_json(package_path)
    if package.get("schema_version") != "R6.2-P6-CALL-PACKAGE-1.0":
        issues.append("CALL_PACKAGE_SCHEMA_INVALID")
    if package.get("job_id") != manifest.get("project_id"):
        issues.append("CALL_PACKAGE_PROJECT_ID_MISMATCH")
    if package.get("phase") != "P6" or package.get("status") != "WAIT_REVIEW":
        issues.append("CALL_PACKAGE_PHASE_OR_STATUS_INVALID")
    call_kind = package.get("call_kind")
    call_ordinal = package.get("call_ordinal")
    if (call_kind, call_ordinal) not in {("GRID_BASELINE", 1), ("GRID_CORRECTION", 2)}:
        issues.append("CALL_PACKAGE_KIND_OR_ORDINAL_INVALID")
    if package.get("human_approval_required") is not True or package.get("one_approval_one_submission") is not True:
        issues.append("CALL_PACKAGE_HUMAN_OR_EXACTLY_ONCE_RULE_MISSING")
    if package.get("automatic_retry_forbidden") is not True:
        issues.append("CALL_PACKAGE_AUTO_RETRY_NOT_FORBIDDEN")

    request = package.get("request") if isinstance(package.get("request"), dict) else {}
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    budget = package.get("cost_and_retry") if isinstance(package.get("cost_and_retry"), dict) else {}
    if budget.get("planned_calls") != 1 or budget.get("auto_retry") is not False or budget.get("per_cell_calls") != 0:
        issues.append("CALL_PACKAGE_COST_OR_RETRY_CONTRACT_INVALID")

    job_path = _verify_project_binding(project, lineage, "job_relative_path", "job_sha256", "JOB", issues)
    job: dict[str, Any] = {}
    if job_path is not None:
        job = load_json(job_path)
        if job.get("job_id") != package.get("job_id"):
            issues.append("JOB_ID_MISMATCH")
    evidence_path = _verify_project_binding(
        project, lineage, "timeline_evidence_relative_path", "timeline_evidence_file_sha256", "TIMELINE_EVIDENCE", issues
    )
    content_lineage_paths: dict[str, Path] = {}
    if job.get("route_id") == "M2_F_SOURCE_AUDIO_RESTYLE":
        _verify_project_binding(
            project, lineage, "source_audio_plan_relative_path", "source_audio_plan_sha256", "SOURCE_AUDIO_PLAN", issues
        )
        if any(lineage.get(key) for key in ("route_analysis_relative_path", "route_analysis_sha256", "p3_blueprint_relative_path", "p3_blueprint_sha256")):
            issues.append("M2F_CALL_PACKAGE_FORBIDS_LEGACY_ROUTE_OR_P3_LINEAGE")
    elif uses_content_lineage(manifest, job):
        issues.extend(validate_content_lineage_shape(lineage, str(job.get("route_id", ""))))
        for resolved_key, label, path_key, hash_key, _ in content_lineage_specs(str(job.get("route_id", ""))):
            resolved_path = _verify_project_binding(project, lineage, path_key, hash_key, label, issues)
            if resolved_path is not None:
                content_lineage_paths[resolved_key] = resolved_path
            _require_validated_artifact(manifest, lineage.get(path_key), lineage.get(hash_key), label, issues)
        try:
            issues.extend(
                f"R637_SOURCE_CONTENT_{issue}"
                for issue in validate_source_content_project(project, "p3")
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            issues.append(f"R637_SOURCE_CONTENT_PREFLIGHT_FAILED:{exc}")
    else:
        _verify_project_binding(project, lineage, "route_analysis_relative_path", "route_analysis_sha256", "ROUTE_ANALYSIS", issues)
        _verify_project_binding(project, lineage, "p3_blueprint_relative_path", "p3_blueprint_sha256", "P3_BLUEPRINT", issues)
    plan_path = _verify_project_binding(project, lineage, "scene_plan_relative_path", "scene_plan_sha256", "SCENE_PLAN", issues)
    audit_path = _verify_project_binding(project, lineage, "prompt_audit_relative_path", "prompt_audit_sha256", "PROMPT_AUDIT", issues)
    _verify_project_binding(project, lineage, "p5_review_relative_path", "p5_review_sha256", "P5_REVIEW", issues)
    prompt_path = _verify_project_binding(project, request, "prompt_relative_path", "prompt_sha256", "PROMPT", issues)
    capability_block = package.get("capability") if isinstance(package.get("capability"), dict) else {}
    capability_path = _verify_project_binding(
        project, capability_block, "capability_relative_path", "capability_sha256", "IMAGEGEN_CAPABILITY", issues
    )

    manifest_version = str(manifest.get("skill_version", ""))
    if manifest_version in {"R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
        try:
            effective_job_path, effective_plan_path, fact_lineage = resolve_effective_inputs(project, manifest)
            if job_path != effective_job_path:
                issues.append("R634_CALL_PACKAGE_JOB_IS_NOT_EFFECTIVE_AUTHORITY")
            if plan_path != effective_plan_path:
                issues.append("R634_CALL_PACKAGE_SCENE_PLAN_IS_NOT_EFFECTIVE_AUTHORITY")
            if lineage.get("accepted_deviation_fact_contracts") != fact_lineage:
                issues.append("R634_CALL_PACKAGE_FACT_LINEAGE_MISMATCH")
            expected_flow_receipt = require_state_flow_receipt(project, manifest, effective_plan_path)
            if lineage.get("segment_state_flow_audit") != expected_flow_receipt:
                issues.append("R634_CALL_PACKAGE_STATE_FLOW_RECEIPT_MISMATCH")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            issues.append(f"R634_INTEGRITY_PREFLIGHT_FAILED:{exc}")

    if evidence_path is not None:
        evidence = load_json(evidence_path)
        if _hash(lineage.get("timeline_evidence_fingerprint")) != canonical_fingerprint(evidence):
            issues.append("TIMELINE_EVIDENCE_FINGERPRINT_MISMATCH")
    if plan_path is not None:
        plan = load_json(plan_path)
        if manifest_version in KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS:
            issues.extend(validate_r639_keyframes(plan, require=True))
        if content_lineage_paths:
            narration_path = content_lineage_paths.get("narration_plan")
            if narration_path is None or _hash(plan.get("p3_blueprint_sha256")) != sha256_file(narration_path):
                issues.append("R637_SCENE_PLAN_NARRATION_LINEAGE_MISMATCH")
        target = package.get("target") if isinstance(package.get("target"), dict) else {}
        grids = [row for row in plan.get("grids", []) if isinstance(row, dict) and row.get("grid_id") == target.get("grid_id")]
        if len(grids) != 1 or grids[0].get("layout") != target.get("layout"):
            issues.append("TARGET_GRID_OR_LAYOUT_MISMATCH")
        elif grids[0].get("grid_order") != target.get("grid_order"):
            issues.append("TARGET_GRID_ORDER_MISMATCH")
        elif grids[0].get("segment_id") != target.get("segment_id"):
            issues.append("TARGET_SEGMENT_MISMATCH")
        elif grids[0].get("grid_role") != target.get("grid_role") or target.get("grid_role") != "SEGMENT_ACTION_AUTHORITY":
            issues.append("TARGET_GRID_ROLE_MISMATCH")
        elif grids[0].get("target_canvas_aspect_ratio") != target.get("whole_grid_aspect_ratio"):
            issues.append("TARGET_CANVAS_ASPECT_MISMATCH")
        elif grids[0].get("target_cell_aspect_ratio") != target.get("cell_aspect_ratio"):
            issues.append("TARGET_CELL_ASPECT_MISMATCH")

    try:
        style_relative = normalize_project_relative(str(lineage.get("style_registry_skill_relative_path", "")))
    except ValueError:
        issues.append("STYLE_REGISTRY_PATH_INVALID")
    else:
        style_path = (SKILL_ROOT / style_relative).resolve()
        try:
            style_path.relative_to(SKILL_ROOT)
        except ValueError:
            issues.append("STYLE_REGISTRY_PATH_ESCAPES_SKILL")
        else:
            if not style_path.is_file():
                issues.append("STYLE_REGISTRY_MISSING")
            elif sha256_file(style_path) != _hash(lineage.get("style_registry_sha256")):
                issues.append("STYLE_REGISTRY_HASH_MISMATCH")

    tree_sha256, _ = skill_tree_fingerprint()
    if _hash(lineage.get("skill_tree_sha256")) != tree_sha256:
        issues.append("CALL_PACKAGE_SKILL_TREE_HASH_MISMATCH")
    if manifest.get("skill", {}).get("tree_sha256") != tree_sha256:
        issues.append("PROJECT_SKILL_TREE_BINDING_STALE")

    if audit_path is not None:
        audit = load_json(audit_path)
        if prompt_path is not None and _hash(audit.get("prompt_sha256")) != sha256_file(prompt_path):
            issues.append("PROMPT_AUDIT_HASH_MISMATCH")
        for audit_key, package_key in (
            ("job_sha256", "job_sha256"),
            ("timeline_evidence_file_sha256", "timeline_evidence_file_sha256"),
            ("scene_plan_sha256", "scene_plan_sha256"),
            ("style_registry_sha256", "style_registry_sha256"),
        ):
            if _hash(audit.get(audit_key)) != _hash(lineage.get(package_key)):
                issues.append(f"PROMPT_AUDIT_{audit_key.upper()}_MISMATCH")

        if manifest_version in SEMANTIC_PROMPT_AUDIT_VERSIONS:
            if plan_path is None or prompt_path is None:
                issues.append("SEMANTIC_PROMPT_AUDIT_INPUT_MISSING")
            else:
                target_grid_id = str(package.get("target", {}).get("grid_id", "")) if isinstance(package.get("target"), dict) else ""
                matching_grids = [
                    row for row in plan.get("grids", [])
                    if isinstance(row, dict) and row.get("grid_id") == target_grid_id
                ]
                if len(matching_grids) != 1:
                    issues.append("SEMANTIC_PROMPT_AUDIT_GRID_NOT_UNIQUE")
                else:
                    recomputed_semantic = semantic_prompt_audit(
                        job,
                        matching_grids[0],
                        prompt_path.read_text(encoding="utf-8"),
                    )
                    if recomputed_semantic.get("status") != "PASSED":
                        issues.append("SEMANTIC_PROMPT_AUDIT_RECOMPUTE_FAILED")
                    if audit.get("state_semantic_audit") != recomputed_semantic:
                        issues.append("SEMANTIC_PROMPT_AUDIT_RECEIPT_MISMATCH")
                    if manifest_version in KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS:
                        recomputed_keyframe_proof = r639_prompt_proof(
                            matching_grids[0],
                            prompt_path.read_text(encoding="utf-8"),
                        )
                        if recomputed_keyframe_proof.get("status") != "PASSED":
                            issues.append("R639_KEYFRAME_PROMPT_PROOF_RECOMPUTE_FAILED")
                        if audit.get("keyframe_snapshot_prompt_proof") != recomputed_keyframe_proof:
                            issues.append("R639_KEYFRAME_PROMPT_PROOF_RECEIPT_MISMATCH")

    target_grid_id = str(package.get("target", {}).get("grid_id", "")) if isinstance(package.get("target"), dict) else ""
    if manifest_version in CANONICAL_P5_VERSIONS and call_kind == "GRID_BASELINE":
        suffix = canonical_p5_suffix(manifest_version)
        canonical_review = f"artifacts/P5/{target_grid_id}_{suffix}_PENDING_GRID_REVIEW.json"
        canonical_prompt = f"artifacts/P5/{target_grid_id}_GRID_PROMPT_{suffix}.txt"
        canonical_audit = f"artifacts/P5/{target_grid_id}_GRID_PROMPT_{suffix}_AUDIT.json"
        if lineage.get("p5_review_relative_path") != canonical_review:
            issues.append(f"{suffix}_CANONICAL_P5_REVIEW_PATH_MISMATCH")
        if request.get("prompt_relative_path") != canonical_prompt:
            issues.append(f"{suffix}_CANONICAL_PROMPT_PATH_MISMATCH")
        if lineage.get("prompt_audit_relative_path") != canonical_audit:
            issues.append(f"{suffix}_CANONICAL_PROMPT_AUDIT_PATH_MISMATCH")
        _require_validated_artifact(manifest, canonical_review, lineage.get("p5_review_sha256"), f"{suffix}_CANONICAL_P5_REVIEW", issues)
        _require_validated_artifact(manifest, canonical_prompt, request.get("prompt_sha256"), f"{suffix}_CANONICAL_PROMPT", issues)
        _require_validated_artifact(manifest, canonical_audit, lineage.get("prompt_audit_sha256"), f"{suffix}_CANONICAL_PROMPT_AUDIT", issues)

    capability_registry_path = SKILL_ROOT / "assets" / "imagegen-capability-registry.json"
    if not capability_registry_path.is_file():
        issues.append("CAPABILITY_REGISTRY_MISSING")
        capability_registry: dict[str, Any] = {}
    else:
        capability_registry = load_json(capability_registry_path)
        if sha256_file(capability_registry_path) != _hash(lineage.get("capability_registry_sha256")):
            issues.append("CAPABILITY_REGISTRY_HASH_MISMATCH")
        if lineage.get("capability_registry_skill_relative_path") != "assets/imagegen-capability-registry.json":
            issues.append("CAPABILITY_REGISTRY_PATH_INVALID")
    target = package.get("target") if isinstance(package.get("target"), dict) else {}
    if capability_block.get("requested_canvas_aspect_ratio") != target.get("whole_grid_aspect_ratio"):
        issues.append("CAPABILITY_REQUESTED_CANVAS_ASPECT_MISMATCH")
    if capability_block.get("requested_cell_aspect_ratio") != target.get("cell_aspect_ratio"):
        issues.append("CAPABILITY_REQUESTED_CELL_ASPECT_MISMATCH")
    if capability_block.get("preflight_decision") != "PASSED":
        issues.append("CAPABILITY_PREFLIGHT_NOT_PASSED")
    if capability_path is not None:
        capability_artifact = load_json(capability_path)
        if isinstance(job, dict) and job and capability_artifact.get("profile_id") != job.get("imagegen_capability_profile_id"):
            issues.append("CAPABILITY_PROFILE_DIFFERS_FROM_JOB")
        issues.extend(validate_capability(
            capability_artifact,
            requested_aspect_ratio=str(target.get("whole_grid_aspect_ratio", "")),
            enforcement=str(target.get("geometry_enforcement", "")),
            expected_tool=str(package.get("tool", "")),
            registry=capability_registry,
        ))
    if isinstance(job, dict) and job:
        geometry = job.get("grid_geometry_contract") if isinstance(job.get("grid_geometry_contract"), dict) else {}
        if target.get("whole_grid_aspect_ratio") != geometry.get("canvas_aspect_ratio"):
            issues.append("PACKAGE_CANVAS_ASPECT_DIFFERS_FROM_JOB")
        if target.get("cell_aspect_ratio") != geometry.get("cell_aspect_ratio"):
            issues.append("PACKAGE_CELL_ASPECT_DIFFERS_FROM_JOB")
        if target.get("geometry_enforcement") != geometry.get("enforcement"):
            issues.append("PACKAGE_GEOMETRY_ENFORCEMENT_DIFFERS_FROM_JOB")
        job_budget = job.get("generation_budget") if isinstance(job.get("generation_budget"), dict) else {}
        for key in (
            "per_grid_baseline_calls",
            "per_grid_consolidated_corrections",
            "project_max_grid_baselines",
            "project_max_grid_corrections",
            "pilot_gate_after_first_grid",
        ):
            if budget.get(key) != job_budget.get(key):
                issues.append(f"PACKAGE_{key.upper()}_DIFFERS_FROM_JOB")

    references = package.get("reference_roles")
    if not isinstance(references, list):
        issues.append("REFERENCE_ROLES_INVALID")
        references = []
    submitted = request.get("referenced_image_paths")
    if not isinstance(submitted, list):
        issues.append("REQUEST_REFERENCE_PATHS_INVALID")
        submitted = []
    input_mode = request.get("input_mode")
    if input_mode == "TEXT_ONLY_WHOLE_GRID":
        if references or submitted or request.get("include_recent_conversation_images") is not False:
            issues.append("TEXT_ONLY_PACKAGE_CONTAINS_VISUAL_REFERENCE")
        if job.get("route_id") not in CREATIVE_ROUTES:
            issues.append("TEXT_ONLY_PACKAGE_FOR_SOURCE_BOUND_ROUTE")
    elif input_mode not in {"REFERENCED_WHOLE_GRID", "ANCHORED_WHOLE_GRID"}:
        issues.append("REQUEST_INPUT_MODE_INVALID")
    if len(references) != len(submitted):
        issues.append("REFERENCE_ROLE_AND_SUBMITTED_PATH_COUNT_MISMATCH")
    for index, reference in enumerate(references, start=1):
        if not isinstance(reference, dict):
            issues.append(f"REFERENCE_{index}_INVALID")
            continue
        _verify_project_binding(project, reference, "relative_path", "sha256", f"REFERENCE_{index}", issues)
        _require_validated_artifact(manifest, reference.get("relative_path"), reference.get("sha256"), f"REFERENCE_{index}", issues)
        if index <= len(submitted):
            try:
                submitted_relative = normalize_project_relative(str(submitted[index - 1]))
                role_relative = normalize_project_relative(str(reference.get("relative_path", "")))
                if submitted_relative != role_relative:
                    issues.append(f"REFERENCE_{index}_REQUEST_PATH_DIFFERS_FROM_ROLE_BINDING")
            except ValueError:
                issues.append(f"REFERENCE_{index}_REQUEST_OR_ROLE_PATH_INVALID")
        if reference.get("role") not in {
            "STRUCTURE_REFERENCE",
            "IDENTITY_STYLE_REFERENCE",
            "CONTENT_EVIDENCE",
            "PROJECT_VISUAL_ANCHOR",
            "PREVIOUS_SEGMENT_END_STATE",
        }:
            issues.append(f"REFERENCE_{index}_ROLE_INVALID")

    anchor_contract = job.get("visual_anchor_contract") if isinstance(job.get("visual_anchor_contract"), dict) else {}
    anchor_required = anchor_contract.get("required") is True
    grid_order = target.get("grid_order")
    anchor_roles = [row for row in references if isinstance(row, dict) and row.get("role") == "PROJECT_VISUAL_ANCHOR"]
    previous_roles = [row for row in references if isinstance(row, dict) and row.get("role") == "PREVIOUS_SEGMENT_END_STATE"]
    if request.get("include_recent_conversation_images") is not False:
        issues.append("RECENT_CONVERSATION_IMAGES_FORBIDDEN_AS_ANCHOR")
    if anchor_required and grid_order == anchor_contract.get("anchor_grid_order", 1):
        if input_mode != "TEXT_ONLY_WHOLE_GRID" or anchor_roles or previous_roles:
            issues.append("ANCHOR_ORIGIN_GRID_MUST_BE_TEXT_ONLY_WITHOUT_FUTURE_REFERENCES")
    if anchor_required and isinstance(grid_order, int) and grid_order >= anchor_contract.get("project_anchor_required_from_grid_order", 2):
        if input_mode != "ANCHORED_WHOLE_GRID":
            issues.append("GRID_TWO_PLUS_REQUIRES_ANCHORED_WHOLE_GRID")
        if len(anchor_roles) != 1:
            issues.append("EXACTLY_ONE_PROJECT_VISUAL_ANCHOR_REQUIRED")
        if anchor_contract.get("previous_segment_end_state_required") is True and len(previous_roles) != 1:
            issues.append("EXACTLY_ONE_PREVIOUS_SEGMENT_END_STATE_REQUIRED")
    if input_mode == "ANCHORED_WHOLE_GRID" and not anchor_required:
        issues.append("ANCHORED_MODE_REQUIRES_JOB_ANCHOR_CONTRACT")
    if input_mode == "ANCHORED_WHOLE_GRID":
        if [row.get("role") for row in references if isinstance(row, dict)] != ["PROJECT_VISUAL_ANCHOR", "PREVIOUS_SEGMENT_END_STATE"]:
            issues.append("ANCHORED_REFERENCE_ORDER_MUST_BE_PROJECT_ANCHOR_THEN_PREVIOUS_END_STATE")

    for reference in anchor_roles:
        contract_path = _verify_project_binding(
            project, reference, "anchor_contract_relative_path", "anchor_contract_sha256", "PROJECT_ANCHOR_CONTRACT", issues
        )
        _require_validated_artifact(
            manifest,
            reference.get("anchor_contract_relative_path"),
            reference.get("anchor_contract_sha256"),
            "PROJECT_ANCHOR_CONTRACT",
            issues,
        )
        if set(reference.get("controls", [])) != {"person_identity", "animal_identity", "visual_style", "core_environment"}:
            issues.append("PROJECT_ANCHOR_REFERENCE_CONTROLS_INVALID")
        if contract_path is not None:
            contract = load_json(contract_path)
            issues.extend(f"PROJECT_ANCHOR_{issue}" for issue in validate_visual_anchor(project, contract_path))
            if contract.get("schema_version") != "R6.6-PROJECT-VISUAL-ANCHOR-1.0" or contract.get("status") != "VALIDATED":
                issues.append("PROJECT_ANCHOR_CONTRACT_INVALID")
            if contract.get("job_id") != package.get("job_id"):
                issues.append("PROJECT_ANCHOR_JOB_ID_MISMATCH")
            anchor_asset = contract.get("anchor_asset") if isinstance(contract.get("anchor_asset"), dict) else {}
            if anchor_asset.get("relative_path") != reference.get("relative_path") or _hash(anchor_asset.get("sha256")) != _hash(reference.get("sha256")):
                issues.append("PROJECT_ANCHOR_ASSET_BINDING_MISMATCH")
            if contract.get("source_grid_order") != anchor_contract.get("anchor_grid_order", 1):
                issues.append("PROJECT_ANCHOR_SOURCE_GRID_ORDER_INVALID")
            required_locks = {"person_identity", "animal_identity", "visual_style", "core_environment"}
            locks = contract.get("locks") if isinstance(contract.get("locks"), dict) else {}
            if any(locks.get(key) is not True for key in required_locks) or locks.get("action_structure") is not False:
                issues.append("PROJECT_ANCHOR_LOCK_SCOPE_INVALID")

    for reference in previous_roles:
        receipt_path = _verify_project_binding(
            project, reference, "crop_receipt_relative_path", "crop_receipt_sha256", "PREVIOUS_END_STATE_RECEIPT", issues
        )
        _require_validated_artifact(
            manifest,
            reference.get("crop_receipt_relative_path"),
            reference.get("crop_receipt_sha256"),
            "PREVIOUS_END_STATE_RECEIPT",
            issues,
        )
        if set(reference.get("controls", [])) != {"continuity_entry_state"}:
            issues.append("PREVIOUS_END_STATE_REFERENCE_CONTROLS_INVALID")
        if receipt_path is not None:
            receipt = load_json(receipt_path)
            issues.extend(f"PREVIOUS_END_STATE_{issue}" for issue in validate_end_state_receipt(project, receipt_path))
            if receipt.get("schema_version") != "R6.6-PREVIOUS-END-STATE-CROP-1.0":
                issues.append("PREVIOUS_END_STATE_RECEIPT_INVALID")
            if receipt.get("job_id") != package.get("job_id"):
                issues.append("PREVIOUS_END_STATE_JOB_ID_MISMATCH")
            if receipt.get("source_grid_order") != grid_order - 1:
                issues.append("PREVIOUS_END_STATE_NOT_FROM_IMMEDIATE_PRIOR_GRID")
            if plan_path is not None:
                prior_grids = [
                    row for row in plan.get("grids", [])
                    if isinstance(row, dict) and row.get("grid_order") == grid_order - 1
                ]
                if len(prior_grids) != 1 or receipt.get("source_grid_id") != prior_grids[0].get("grid_id") or receipt.get("layout") != prior_grids[0].get("layout"):
                    issues.append("PREVIOUS_END_STATE_SOURCE_DIFFERS_FROM_SCENE_PLAN")
            if receipt.get("output_relative_path") != reference.get("relative_path") or _hash(receipt.get("output_sha256")) != _hash(reference.get("sha256")):
                issues.append("PREVIOUS_END_STATE_ASSET_BINDING_MISMATCH")

    manifest_submissions = manifest.get("submissions") if isinstance(manifest.get("submissions"), list) else []
    manifest_qc = manifest.get("qc_records") if isinstance(manifest.get("qc_records"), list) else []
    grid_id = target.get("grid_id")
    grid_baselines = [
        row for row in manifest_submissions
        if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE" and row.get("resource_id") == grid_id
    ]
    grid_corrections = [
        row for row in manifest_submissions
        if isinstance(row, dict) and row.get("call_kind") == "GRID_CORRECTION" and row.get("resource_id") == grid_id
    ]
    all_baselines = [row for row in manifest_submissions if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE"]
    all_corrections = [row for row in manifest_submissions if isinstance(row, dict) and row.get("call_kind") == "GRID_CORRECTION"]
    if call_kind == "GRID_BASELINE":
        replacement_scope = manifest.get("generation_budget", {}).get("r66_replacement_scope")
        if isinstance(replacement_scope, list) and grid_id not in replacement_scope:
            issues.append("GRID_OUTSIDE_R66_REPLACEMENT_SCOPE")
        if grid_baselines:
            issues.append("GRID_BASELINE_BUDGET_ALREADY_CONSUMED")
        if isinstance(job, dict) and job:
            max_baselines = job.get("generation_budget", {}).get("project_max_grid_baselines")
            if isinstance(max_baselines, int) and len(all_baselines) >= max_baselines:
                issues.append("PROJECT_BASELINE_BUDGET_ALREADY_CONSUMED")
        if target.get("grid_order", 0) > 1 and budget.get("pilot_gate_after_first_grid") is True:
            pilot_submission_ids = {
                row.get("submission_id") for row in all_baselines if row.get("grid_order") == 1
            }
            live_pilot_passed = any(
                row.get("submission_id") in pilot_submission_ids and row.get("decision") == "PASSED"
                for row in manifest_qc if isinstance(row, dict)
            )
            historical = manifest.get("historical_revision") if isinstance(manifest.get("historical_revision"), dict) else {}
            imported_qc = historical.get("anchor_origin_historical_qc") if isinstance(historical.get("anchor_origin_historical_qc"), dict) else {}
            imported_anchor_passed = (
                imported_qc.get("decision") == "PASSED"
                and imported_qc.get("grid_id") == "G01"
                and imported_qc.get("grid_order") == 1
                and len(anchor_roles) == 1
                and not any(issue.startswith("PROJECT_ANCHOR_") for issue in issues)
            )
            if not live_pilot_passed and not imported_anchor_passed:
                issues.append("PILOT_GRID_QC_MUST_PASS_BEFORE_SCALE")
        if package.get("correction_scope") not in ([], None):
            issues.append("BASELINE_PACKAGE_FORBIDS_CORRECTION_SCOPE")
    elif call_kind == "GRID_CORRECTION":
        baselines = grid_baselines
        if len(baselines) != 1:
            issues.append("CORRECTION_REQUIRES_EXACTLY_ONE_SAME_GRID_BASELINE")
        if grid_corrections:
            issues.append("SAME_GRID_CORRECTION_BUDGET_ALREADY_CONSUMED")
        if isinstance(job, dict) and job:
            max_corrections = job.get("generation_budget", {}).get("project_max_grid_corrections")
            if isinstance(max_corrections, int) and len(all_corrections) >= max_corrections:
                issues.append("PROJECT_CORRECTION_BUDGET_ALREADY_CONSUMED")
        prior_qc_path = _verify_project_binding(project, lineage, "prior_qc_relative_path", "prior_qc_sha256", "PRIOR_QC", issues)
        prior_output_path = _verify_project_binding(project, lineage, "prior_output_relative_path", "prior_output_sha256", "PRIOR_OUTPUT", issues)
        if prior_qc_path is not None:
            prior_qc = load_json(prior_qc_path)
            if prior_qc.get("schema_version") not in COMPATIBLE_P6_QC_SCHEMAS or prior_qc.get("decision") != "REJECTED":
                issues.append("CORRECTION_PRIOR_QC_NOT_REJECTED_COMPATIBLE_SCHEMA")
            if prior_qc.get("correction_eligible") is not True:
                issues.append("CORRECTION_PRIOR_QC_NOT_ELIGIBLE")
            if baselines and prior_qc.get("submission_id") != baselines[0].get("submission_id"):
                issues.append("CORRECTION_PRIOR_QC_SUBMISSION_MISMATCH")
            if not any(row.get("qc_sha256") == sha256_file(prior_qc_path) for row in manifest_qc if isinstance(row, dict)):
                issues.append("CORRECTION_PRIOR_QC_NOT_RECORDED_IN_PROJECT")
        if prior_output_path is not None:
            prior_output_relative = normalize_project_relative(str(lineage.get("prior_output_relative_path", "")))
            if prior_output_relative in submitted or request.get("failed_output_used_as_reference") is not False:
                issues.append("FAILED_BASELINE_MUST_NOT_BE_VISUAL_REFERENCE")
        correction_scope = package.get("correction_scope")
        if not isinstance(correction_scope, list) or not correction_scope or any(not isinstance(item, str) or not item.strip() for item in correction_scope):
            issues.append("CORRECTION_SCOPE_MISSING")

    secret_markers = ("api_key", "apikey", "authorization:", "bearer ", "secret_key")
    if any(any(marker in value.lower() for marker in secret_markers) for value in _walk_strings(package)):
        issues.append("CALL_PACKAGE_MAY_CONTAIN_SECRET")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    package_path = _project_file(project, args.package, "PACKAGE", [])
    if package_path is None:
        result = {"status": "FAILED", "issues": ["PACKAGE_PATH_INVALID_OR_MISSING"]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    try:
        issues = validate(project, package_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    result = {
        "status": "PASSED" if not issues else "FAILED",
        "package_sha256": sha256_file(package_path),
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
