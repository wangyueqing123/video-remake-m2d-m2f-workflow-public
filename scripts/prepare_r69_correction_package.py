#!/usr/bin/env python3
"""Compile one diagnosed, consolidated R6.9 grid correction package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from compile_r62_grid_prompt import CORRECTION_HEADROOM_CHARS, compile_prompt
from r634_integrity_contract import require_state_flow_receipt, resolve_effective_inputs
from r637_content_lineage import content_lineage_specs, uses_content_lineage
from r639_keyframe_contract import validate_plan as validate_r639_keyframes
from prepare_r68_anchored_grid_call_package import artifact_is_validated, load_json, project_file, require_safe_prior_grid
from r62_project import SKILL_ROOT, normalize_project_relative, sha256_file, skill_tree_fingerprint, write_json_atomic
from release_contract import COMPATIBLE_P6_QC_SCHEMAS, KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS, REFERENCE_COMPATIBILITY_VERSIONS
from validate_r62_call_package import validate as validate_call_package
from validate_r62_timeline_evidence import canonical_fingerprint


def load_anchor_review_for_grid(project: Path, grid_order: int) -> dict[str, Any] | None:
    """Require the approved project anchor only after the G01 origin grid."""
    if grid_order <= 1:
        return None
    _, review_path = project_file(project, "artifacts/P5/P5_R66_VISUAL_ANCHOR_REVIEW.json")
    return load_json(review_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--grid-id", required=True)
    parser.add_argument("--prior-qc", required=True)
    parser.add_argument("--diagnosis", required=True)
    parser.add_argument("--directive", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--planned-output", required=True)
    args = parser.parse_args()

    project = args.project_dir.resolve()
    state = load_json(project / "R62_PROJECT.json")
    tree_sha256, _ = skill_tree_fingerprint()
    if state.get("skill", {}).get("tree_sha256") != tree_sha256:
        raise SystemExit("PROJECT_SKILL_TREE_BINDING_STALE")

    effective_job_path, effective_plan_path, fact_lineage = resolve_effective_inputs(project, state)
    fixed = {
        "job": effective_job_path.relative_to(project).as_posix(), "evidence": "artifacts/P2/TIMELINE_EVIDENCE.json",
        "plan": effective_plan_path.relative_to(project).as_posix(), "capability": "artifacts/P5/IMAGEGEN_CAPABILITY.json",
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

    qc_relative, qc_path = project_file(project, args.prior_qc)
    diagnosis_relative, diagnosis_path = project_file(project, args.diagnosis)
    directive_relative, directive_path = project_file(project, args.directive)
    qc = load_json(qc_path)
    diagnosis = load_json(diagnosis_path)
    directive = load_json(directive_path)
    if qc.get("schema_version") not in COMPATIBLE_P6_QC_SCHEMAS or qc.get("grid_id") != args.grid_id or qc.get("decision") != "REJECTED":
        raise SystemExit("PRIOR_QC_NOT_REJECTED_FOR_GRID")
    if qc.get("failure_class") != "MODEL_RENDERING" or qc.get("correction_eligible") is not True:
        raise SystemExit("RECORDED_BASELINE_QC_MUST_BE_CORRECTION_ELIGIBLE")
    qc_hash = sha256_file(qc_path)
    if not any(isinstance(row, dict) and row.get("qc_sha256") == qc_hash for row in state.get("qc_records", [])):
        raise SystemExit("PRIOR_QC_NOT_RECORDED")
    if diagnosis.get("schema_version") not in {"R6.8-P6-FAILURE-DIAGNOSIS-1.0", "R6.9-P6-FAILURE-DIAGNOSIS-1.0"} or diagnosis.get("grid_id") != args.grid_id:
        raise SystemExit("DIAGNOSIS_SCHEMA_OR_GRID_INVALID")
    diagnosis_class = diagnosis.get("failure_class")
    ordinary_model_correction = diagnosis_class == "MODEL_RENDERING"
    semantic_revision = state.get("r621_semantic_prompt_revision") if isinstance(state.get("r621_semantic_prompt_revision"), dict) else {}
    resolved_compiler_correction = (
        diagnosis_class == "PROMPT_COMPILER"
        and state.get("skill_version") == "R6.21"
        and semantic_revision.get("target_grid_id") == args.grid_id
        and diagnosis.get("system_change_required") is True
        and diagnosis.get("system_change_resolution") == "RESOLVED_BY_R621_SEMANTIC_RECOMPILE"
    )
    if not (ordinary_model_correction or resolved_compiler_correction) or diagnosis.get("allowed_next_action") != "PREPARE_ONE_CONSOLIDATED_CORRECTION_PACKAGE":
        raise SystemExit("DIAGNOSIS_DOES_NOT_AUTHORIZE_CORRECTION")
    if diagnosis.get("system_change_required") not in {True, False}:
        raise SystemExit("DIAGNOSIS_SYSTEM_DECISION_MISSING")
    if directive.get("schema_version") not in {
        "R6.9-CONSOLIDATED-CORRECTION-DIRECTIVE-1.0",
        "R6.21-CONSOLIDATED-CORRECTION-DIRECTIVE-1.0",
    } or directive.get("grid_id") != args.grid_id:
        raise SystemExit("CORRECTION_DIRECTIVE_SCHEMA_OR_GRID_INVALID")
    if directive.get("prior_qc_sha256") != qc_hash or directive.get("diagnosis_sha256") != sha256_file(diagnosis_path):
        raise SystemExit("CORRECTION_DIRECTIVE_LINEAGE_STALE")
    scope = directive.get("scope") if isinstance(directive.get("scope"), list) else []
    if set(scope) != set(qc.get("blocking_failure_codes", [])):
        raise SystemExit("CORRECTION_SCOPE_MUST_COVER_ALL_AND_ONLY_BLOCKING_CODES")
    correction_block = str(directive.get("prompt_block", "")).strip()
    if not correction_block or len(correction_block) > 1400:
        raise SystemExit("CORRECTION_PROMPT_BLOCK_MISSING_OR_TOO_LONG")
    if directive.get("failed_output_may_be_visual_reference") is not False or directive.get("automatic_retry_allowed") is not False:
        raise SystemExit("CORRECTION_DIRECTIVE_SAFETY_FLAGS_INVALID")
    if state.get("skill_version") in REFERENCE_COMPATIBILITY_VERSIONS:
        compatibility = directive.get("reference_compatibility")
        required = {
            "preserves_project_anchor_identity_style_environment": True,
            "preserves_prior_end_entry_state": True,
            "requires_upstream_layout_change": False,
            "requires_reference_geometry_change": False,
        }
        if not isinstance(compatibility, dict) or any(compatibility.get(key) is not value for key, value in required.items()):
            raise SystemExit("R618_CORRECTION_CONFLICTS_WITH_REFERENCE_OR_UPSTREAM_PLAN")

    grids = [row for row in plan.get("grids", []) if isinstance(row, dict) and row.get("grid_id") == args.grid_id]
    if len(grids) != 1 or not isinstance(grids[0].get("grid_order"), int) or grids[0]["grid_order"] < 1:
        raise SystemExit("CORRECTION_GRID_MUST_BE_ONE_EXISTING_GRID")
    grid = grids[0]
    anchor_review = load_anchor_review_for_grid(project, grid["grid_order"])
    registry_path = SKILL_ROOT / "assets/style-registry.json"
    registry = load_json(registry_path)
    base_prompt, audit = compile_prompt(job, evidence, plan, registry, args.grid_id)
    parts = base_prompt.split("\n\n", 1)
    prompt_text = parts[0] + "\n\n唯一一次整张合并修正：" + correction_block + "\n\n" + (parts[1] if len(parts) == 2 else "")
    maximum = audit.get("maximum_character_budget")
    baseline_count = len(base_prompt)
    correction_delta = len(prompt_text) - baseline_count
    reserved = audit.get("reserved_correction_headroom")
    if reserved != CORRECTION_HEADROOM_CHARS or correction_delta > reserved:
        raise SystemExit("CORRECTION_PROMPT_EXCEEDS_RESERVED_HEADROOM")
    if not isinstance(maximum, int) or len(prompt_text) > maximum:
        raise SystemExit("CORRECTION_PROMPT_EXCEEDS_LAYOUT_BUDGET")

    prompt_relative = normalize_project_relative(args.prompt)
    audit_relative = normalize_project_relative(args.audit)
    review_relative = normalize_project_relative(args.review)
    package_relative = normalize_project_relative(args.package)
    planned_output = normalize_project_relative(args.planned_output)
    prompt_path = project / prompt_relative
    audit_path = project / audit_relative
    review_path = project / review_relative
    package_path = project / package_relative
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text, encoding="utf-8", newline="\n")
    audit.update({
        "schema_version": "R6.9-CORRECTION-PROMPT-AUDIT-1.0", "character_count": len(prompt_text),
        "baseline_character_count": baseline_count,
        "correction_character_delta": correction_delta,
        "reserved_correction_headroom": reserved,
        "correction_headroom_check": "PASSED",
        "job_sha256": sha256_file(resolved["job"][1]), "timeline_evidence_file_sha256": sha256_file(resolved["evidence"][1]),
        "scene_plan_sha256": sha256_file(resolved["plan"][1]), "style_registry_sha256": sha256_file(registry_path),
        "prompt_sha256": sha256_file(prompt_path),
        "accepted_deviation_fact_contracts": fact_lineage,
        "correction_compilation": {
            "prior_qc_relative_path": qc_relative, "prior_qc_sha256": qc_hash,
            "diagnosis_relative_path": diagnosis_relative, "diagnosis_sha256": sha256_file(diagnosis_path),
            "directive_relative_path": directive_relative, "directive_sha256": sha256_file(directive_path),
            "scope": scope, "failed_baseline_as_image_input": False,
            "failure_origin": diagnosis_class,
            "system_change_resolution": diagnosis.get("system_change_resolution"),
        },
    })
    write_json_atomic(audit_path, audit)
    review = {
        "schema_version": "R6.9-P5-CORRECTION-REVIEW-1.0", "status": "PASSED", "grid_id": args.grid_id,
        "prompt_relative_path": prompt_relative, "prompt_sha256": sha256_file(prompt_path),
        "audit_relative_path": audit_relative, "audit_sha256": sha256_file(audit_path),
        "prior_qc_relative_path": qc_relative, "prior_qc_sha256": qc_hash,
        "diagnosis_relative_path": diagnosis_relative, "diagnosis_sha256": sha256_file(diagnosis_path),
        "directive_relative_path": directive_relative, "directive_sha256": sha256_file(directive_path),
        "all_blocking_codes_covered": True, "failed_output_used_as_reference": False,
        "human_review_required_before_call": True,
        "accepted_deviation_fact_contracts": fact_lineage,
    }
    write_json_atomic(review_path, review)

    prior_output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
    prior_output_relative, prior_output_path = project_file(project, str(prior_output.get("relative_path", "")))
    if sha256_file(prior_output_path) != prior_output.get("sha256"):
        raise SystemExit("PRIOR_OUTPUT_BINDING_STALE")

    reference_roles: list[dict[str, Any]] = []
    referenced_image_paths: list[str] = []
    prior_grid_lineage: dict[str, Any] = {}
    input_mode = "TEXT_ONLY_WHOLE_GRID"
    if grid["grid_order"] > 1:
        if anchor_review is None:
            raise SystemExit("CORRECTION_ANCHOR_REVIEW_REQUIRED_FOR_GRID_TWO_PLUS")
        contract_relative, contract_path = project_file(project, str(anchor_review.get("anchor_contract_relative_path", "")))
        contract = load_json(contract_path)
        anchor = contract.get("anchor_asset") if isinstance(contract.get("anchor_asset"), dict) else {}
        anchor_relative, anchor_path = project_file(project, str(anchor.get("relative_path", "")))
        prior_grids = [row for row in plan.get("grids", []) if isinstance(row, dict) and row.get("grid_order") == grid["grid_order"] - 1]
        if len(prior_grids) != 1:
            raise SystemExit("IMMEDIATE_PRIOR_GRID_NOT_UNIQUE")
        prior_grid_id = str(prior_grids[0].get("grid_id"))
        prior_qc_reference = require_safe_prior_grid(project, state, prior_grid_id)
        end_relative, end_path = project_file(project, f"artifacts/P6/{prior_grid_id}_END_STATE.png")
        receipt_relative, receipt_path = project_file(project, f"artifacts/P6/{prior_grid_id}_END_STATE_CROP_RECEIPT.json")
        for relative, path in ((anchor_relative, anchor_path), (contract_relative, contract_path), (end_relative, end_path), (receipt_relative, receipt_path)):
            if not artifact_is_validated(state, relative, sha256_file(path)):
                raise SystemExit(f"CORRECTION_REFERENCE_NOT_VALIDATED:{relative}")
        input_mode = "ANCHORED_WHOLE_GRID"
        referenced_image_paths = [anchor_relative, end_relative]
        reference_roles = [
            {"role": "PROJECT_VISUAL_ANCHOR", "relative_path": anchor_relative, "sha256": sha256_file(anchor_path),
             "anchor_contract_relative_path": contract_relative, "anchor_contract_sha256": sha256_file(contract_path),
             "controls": ["person_identity", "animal_identity", "visual_style", "core_environment"]},
            {"role": "PREVIOUS_SEGMENT_END_STATE", "relative_path": end_relative, "sha256": sha256_file(end_path),
             "crop_receipt_relative_path": receipt_relative, "crop_receipt_sha256": sha256_file(receipt_path),
             "controls": ["continuity_entry_state"]},
        ]
        prior_grid_lineage = {
            "prior_grid_qc_relative_path": prior_qc_reference["relative_path"],
            "prior_grid_qc_sha256": prior_qc_reference["sha256"],
        }

    budget = job.get("generation_budget", {})
    geometry = job.get("grid_geometry_contract", {})
    capability_relative, capability_path = resolved["capability"]
    package = {
        "schema_version": "R6.2-P6-CALL-PACKAGE-1.0", "job_id": job.get("job_id"), "phase": "P6",
        "status": "WAIT_REVIEW", "call_kind": "GRID_CORRECTION", "call_ordinal": 2, "tool": "BUILT_IN_IMAGEGEN",
        "request": {
            "input_mode": input_mode, "prompt_relative_path": prompt_relative, "prompt_sha256": sha256_file(prompt_path),
            "referenced_image_paths": referenced_image_paths, "include_recent_conversation_images": False,
            "failed_output_used_as_reference": False, "planned_output_relative_path": planned_output,
        },
        "target": {
            "grid_id": args.grid_id, "grid_order": grid.get("grid_order"), "segment_id": grid.get("segment_id"),
            "grid_role": grid.get("grid_role"), "layout": grid.get("layout"),
            "whole_grid_aspect_ratio": grid.get("target_canvas_aspect_ratio"), "cell_aspect_ratio": grid.get("target_cell_aspect_ratio"),
            "geometry_enforcement": geometry.get("enforcement"), "row_major_chronology": True,
        },
        "capability": {
            "capability_relative_path": capability_relative, "capability_sha256": sha256_file(capability_path),
            "requested_canvas_aspect_ratio": geometry.get("canvas_aspect_ratio"), "requested_cell_aspect_ratio": geometry.get("cell_aspect_ratio"),
            "preflight_decision": "PASSED",
        },
        "lineage": {
            "job_relative_path": resolved["job"][0], "job_sha256": sha256_file(resolved["job"][1]),
            "timeline_evidence_relative_path": resolved["evidence"][0], "timeline_evidence_file_sha256": sha256_file(resolved["evidence"][1]),
            "timeline_evidence_fingerprint": canonical_fingerprint(evidence),
            "scene_plan_relative_path": resolved["plan"][0], "scene_plan_sha256": sha256_file(resolved["plan"][1]),
            "prompt_audit_relative_path": audit_relative, "prompt_audit_sha256": sha256_file(audit_path),
            "p5_review_relative_path": review_relative, "p5_review_sha256": sha256_file(review_path),
            "style_registry_skill_relative_path": "assets/style-registry.json", "style_registry_sha256": sha256_file(registry_path),
            "capability_registry_skill_relative_path": "assets/imagegen-capability-registry.json",
            "capability_registry_sha256": sha256_file(SKILL_ROOT / "assets/imagegen-capability-registry.json"),
            "skill_tree_sha256": tree_sha256, "prior_qc_relative_path": qc_relative, "prior_qc_sha256": qc_hash,
            "prior_output_relative_path": prior_output_relative, "prior_output_sha256": sha256_file(prior_output_path),
            "accepted_deviation_fact_contracts": fact_lineage,
            "segment_state_flow_audit": state_flow_receipt,
            **prior_grid_lineage,
        },
        "reference_roles": reference_roles,
        "correction_scope": scope,
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
    write_json_atomic(package_path, package)
    issues = validate_call_package(project, package_path)
    result = {
        "status": "PASSED" if not issues else "FAILED", "package_relative_path": package_relative,
        "package_sha256": sha256_file(package_path), "prompt_relative_path": prompt_relative,
        "prompt_sha256": sha256_file(prompt_path), "correction_scope": scope,
        "reference_roles": [row["role"] for row in package["reference_roles"]], "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
