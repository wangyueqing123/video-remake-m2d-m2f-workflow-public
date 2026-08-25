#!/usr/bin/env python3
"""Recompute the R6.23 P7 video-Prompt proof from project artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from r62_project import load_json, normalize_project_relative, sha256_file
from r625_p7_timing import derive_timing_nodes
from validate_r62_segment_package import validate as validate_segment_package

sys.dont_write_bytecode = True


SCHEMA = "R6.23-P7-VIDEO-PROMPT-AUDIT-1.0"
ADAPTER = "GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND"
COMPILER = "compile_r623_video_prompt.py"


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def project_file(project: Path, relative: object) -> Path:
    normalized = normalize_project_relative(str(relative or ""))
    path = (project / normalized).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{normalized}")
    return path


def find_segment(scene_plan: dict[str, Any], segment_id: str) -> dict[str, Any] | None:
    rows = [row for row in scene_plan.get("video_segments", []) if isinstance(row, dict) and row.get("segment_id") == segment_id]
    return rows[0] if len(rows) == 1 else None


def validate(project: Path, package_path: Path) -> list[str]:
    issues = list(validate_segment_package(project, package_path))
    package = load_json(package_path)
    prompt_block = package.get("video_prompt") if isinstance(package.get("video_prompt"), dict) else {}
    audit_relative = prompt_block.get("audit_relative_path")
    if not audit_relative:
        issues.append("R623_VIDEO_PROMPT_AUDIT_MISSING")
        return sorted(set(issues))
    try:
        audit_path = project_file(project, audit_relative)
        audit = load_json(audit_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append(f"R623_VIDEO_PROMPT_AUDIT_READ_ERROR:{exc}")
        return sorted(set(issues))
    if prompt_block.get("audit_sha256") != sha256_file(audit_path):
        issues.append("R623_VIDEO_PROMPT_AUDIT_HASH_MISMATCH")
    if audit.get("schema_version") != SCHEMA or audit.get("status") != "PASSED" or audit.get("compiler") != COMPILER:
        issues.append("R623_VIDEO_PROMPT_AUDIT_SCHEMA_STATUS_OR_COMPILER_INVALID")
    for key in ("job_id", "segment_id", "grid_id"):
        if audit.get(key) != package.get(key):
            issues.append(f"R623_VIDEO_PROMPT_AUDIT_{key.upper()}_MISMATCH")

    prompt_path = project_file(project, prompt_block.get("relative_path"))
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if prompt_block.get("sha256") != sha256_file(prompt_path):
        issues.append("R623_VIDEO_PROMPT_HASH_MISMATCH")
    prompt_proof = audit.get("prompt") if isinstance(audit.get("prompt"), dict) else {}
    if prompt_proof.get("relative_path") != prompt_block.get("relative_path") or prompt_proof.get("sha256") != sha256_file(prompt_path):
        issues.append("R623_AUDIT_PROMPT_BINDING_MISMATCH")
    if prompt_proof.get("character_count") != len(prompt_text):
        issues.append("R623_PROMPT_CHARACTER_COUNT_MISMATCH")
    if prompt_proof.get("budget_is_provider_claim") is not False:
        issues.append("R623_INTERNAL_PROMPT_BUDGET_MUST_NOT_BE_PROVIDER_CLAIM")
    budget = prompt_proof.get("internal_character_budget")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0 or len(prompt_text) > budget:
        issues.append("R623_INTERNAL_PROMPT_BUDGET_INVALID_OR_EXCEEDED")

    proofs = audit.get("proofs") if isinstance(audit.get("proofs"), dict) else {}
    required = proofs.get("required_literals") if isinstance(proofs.get("required_literals"), list) else []
    if not required or any(not isinstance(item, str) or item not in prompt_text for item in required):
        issues.append("R623_PROMPT_REQUIRED_LITERAL_PROOF_FAILED")
    if not prompt_text.startswith("OUTPUT LAYOUT — ABSOLUTE PRIORITY"):
        issues.append("R623_LAYOUT_CONTRACT_NOT_FIRST")
    if "@image1" not in prompt_text or "@image2" not in prompt_text or "No collage, split screen" not in prompt_text:
        issues.append("R623_INPUT_ROLE_OR_ANTI_COLLAGE_CLAUSE_MISSING")

    lineage = audit.get("lineage") if isinstance(audit.get("lineage"), dict) else {}
    for path_key, hash_key in (
        ("job_relative_path", "job_sha256"),
        ("scene_plan_relative_path", "scene_plan_sha256"),
        ("source_segment_package_relative_path", "source_segment_package_sha256"),
    ):
        try:
            path = project_file(project, lineage.get(path_key))
            if lineage.get(hash_key) != sha256_file(path):
                issues.append(f"R623_LINEAGE_HASH_MISMATCH:{path_key}")
        except (OSError, ValueError):
            issues.append(f"R623_LINEAGE_FILE_INVALID:{path_key}")

    state = load_json(project_file(project, lineage.get("project_state_relative_path")))
    if lineage.get("project_skill_version_at_compile") != state.get("skill_version") or lineage.get("project_id_at_compile") != state.get("project_id"):
        issues.append("R623_PROJECT_IDENTITY_OR_SKILL_VERSION_CHANGED")

    job = load_json(project_file(project, lineage.get("job_relative_path")))
    scene_plan = load_json(project_file(project, lineage.get("scene_plan_relative_path")))
    segment = find_segment(scene_plan, str(package.get("segment_id", "")))
    if segment is None:
        issues.append("R623_P4_SEGMENT_MISSING_OR_DUPLICATE")
    else:
        if proofs.get("p4_action_nodes_sha256") != canonical_sha256(segment.get("action_nodes", [])):
            issues.append("R623_P4_ACTION_NODE_PROOF_MISMATCH")
        try:
            timing_source, timing_nodes = derive_timing_nodes(scene_plan, segment)
            coverage = proofs.get("p4_timing_coverage") if isinstance(proofs.get("p4_timing_coverage"), dict) else {}
            if (
                proofs.get("p4_timing_source") != timing_source
                or proofs.get("p4_timing_nodes_sha256") != canonical_sha256(timing_nodes)
                or proofs.get("p4_timing_interval_count") != len(timing_nodes)
                or coverage.get("start_s") != timing_nodes[0]["start_s"]
                or coverage.get("end_s") != timing_nodes[-1]["end_s"]
            ):
                issues.append("R625_P4_TIMING_PROOF_MISMATCH")
            for node in timing_nodes:
                if str(node["action"]) not in prompt_text or str(node["visible_state_at_end"]) not in prompt_text:
                    issues.append("R625_P4_TIMING_NODE_NOT_COMPILED_IN_PROMPT")
                    break
        except (ValueError, KeyError, TypeError) as exc:
            issues.append(f"R625_P4_TIMING_INVALID:{exc}")
        if segment.get("grid_id") != package.get("grid_id"):
            issues.append("R623_P4_P7_GRID_CHANGED")
        if segment.get("target_start_s") != package.get("target_start_s") or segment.get("target_end_s") != package.get("target_end_s"):
            issues.append("R623_P4_P7_TIMING_CHANGED")
    if proofs.get("identity_sha256") != canonical_sha256(job.get("project_identity", {})):
        issues.append("R623_IDENTITY_PROOF_MISMATCH")
    if proofs.get("p7_action_contract_sha256") != canonical_sha256(package.get("action_contract", {})):
        issues.append("R623_P7_ACTION_CONTRACT_PROOF_MISMATCH")
    if proofs.get("p7_audio_contract_sha256") != canonical_sha256(package.get("audio_contract", {})):
        issues.append("R623_P7_AUDIO_CONTRACT_PROOF_MISMATCH")

    flow = audit.get("unchanged_flow_proof") if isinstance(audit.get("unchanged_flow_proof"), dict) else {}
    expected_duration = max(1, math.ceil(float(package["target_end_s"]) - float(package["target_start_s"]) - 1e-9))
    if (
        flow.get("provider_adapter_profile") != ADAPTER
        or flow.get("ordered_image_roles") != ["SEGMENT_ACTION_GRID", "DETERMINISTIC_START_PLACEHOLDER"]
        or flow.get("segment_id") != package.get("segment_id")
        or flow.get("grid_id") != package.get("grid_id")
        or flow.get("target_start_s") != package.get("target_start_s")
        or flow.get("target_end_s") != package.get("target_end_s")
        or flow.get("derived_request_duration_s") != expected_duration
        or flow.get("additional_images") != 0
        or flow.get("additional_provider_tasks") != 0
    ):
        issues.append("R623_UNCHANGED_FLOW_PROOF_FAILED")
    invariant = package.get("r623_same_flow_invariants") if isinstance(package.get("r623_same_flow_invariants"), dict) else {}
    if set(invariant) != {"adapter_unchanged", "ordered_image_roles_unchanged", "grid_asset_unchanged", "start_frame_unchanged", "target_timing_unchanged", "audio_contract_unchanged", "additional_calls"}:
        issues.append("R623_SEGMENT_INVARIANT_FIELDS_INCOMPLETE")
    elif any(invariant.get(key) is not True for key in invariant if key != "additional_calls") or invariant.get("additional_calls") != 0:
        issues.append("R623_SEGMENT_INVARIANT_FAILED")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    try:
        project = args.project_dir.resolve()
        package_path = project_file(project, args.package)
        issues = validate(project, package_path)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
