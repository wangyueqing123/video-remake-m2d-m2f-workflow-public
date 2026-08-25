#!/usr/bin/env python3
"""Prove R6.23 hardening did not change the approved R6.22 production path."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file, write_json_atomic

sys.dont_write_bytecode = True


ADAPTER = "GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND"


def project_file(project: Path, relative: str) -> Path:
    normalized = normalize_project_relative(relative)
    path = (project / normalized).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{normalized}")
    return path


def external_ledger_projection(state: dict[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for key in ("call_seals", "approvals", "submissions"):
        rows = state.get(key) if isinstance(state.get(key), list) else []
        external = [row for row in rows if isinstance(row, dict) and row.get("call_kind") in {"ASSET_UPLOAD", "VIDEO_API", "VIDEO_API_RECOVERY", "GRID_BASELINE", "GRID_CORRECTION"}]
        raw = json.dumps(external, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        projection[key] = {"count": len(external), "sha256": hashlib.sha256(raw).hexdigest()}
    return projection


def validate_internal(project: Path) -> tuple[list[str], dict[str, Any]]:
    issues: list[str] = []
    job_path = project_file(project, "artifacts/P1/JOB.json")
    scene_path = project_file(project, "artifacts/P4/SCENE_PLAN.json")
    state_path = project_file(project, STATE_NAME)
    job = load_json(job_path)
    scene_plan = load_json(scene_path)
    state = load_json(state_path)
    if job.get("provider_adapter_profile") != ADAPTER:
        issues.append("R623_PROVIDER_ADAPTER_CHANGED")
    grid_strategy = job.get("grid_strategy") if isinstance(job.get("grid_strategy"), dict) else {}
    if grid_strategy.get("scope") != "ONE_GRID_PER_VIDEO_SEGMENT" or grid_strategy.get("fixed_time_slicing") is not False:
        issues.append("R623_SEGMENT_GRID_OR_SCENE_POLICY_CHANGED")
    segments = scene_plan.get("video_segments") if isinstance(scene_plan.get("video_segments"), list) else []
    segment_ids = [row.get("segment_id") for row in segments if isinstance(row, dict)]
    grid_ids = [row.get("grid_id") for row in segments if isinstance(row, dict)]
    if not segments or len(set(segment_ids)) != len(segments) or len(set(grid_ids)) != len(segments):
        issues.append("R623_ONE_SEGMENT_ONE_GRID_CARDINALITY_FAILED")

    segment_proofs: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        sid = str(segment.get("segment_id", ""))
        legacy_rel = f"artifacts/P7/{sid}_SEGMENT_PACKAGE.json"
        upgraded_rel = f"artifacts/P7/{sid}_SEGMENT_PACKAGE_R623.json"
        if not (project / legacy_rel).is_file() or not (project / upgraded_rel).is_file():
            issues.append(f"R623_SEGMENT_PACKAGE_PAIR_MISSING:{sid}")
            continue
        legacy = load_json(project / legacy_rel)
        upgraded = load_json(project / upgraded_rel)
        comparisons = {
            "segment_id": legacy.get("segment_id") == upgraded.get("segment_id") == segment.get("segment_id"),
            "grid_id": legacy.get("grid_id") == upgraded.get("grid_id") == segment.get("grid_id"),
            "target_start_s": legacy.get("target_start_s") == upgraded.get("target_start_s") == segment.get("target_start_s"),
            "target_end_s": legacy.get("target_end_s") == upgraded.get("target_end_s") == segment.get("target_end_s"),
            "grid_asset": legacy.get("grid_asset") == upgraded.get("grid_asset"),
            "start_frame_derivative": legacy.get("start_frame_derivative") == upgraded.get("start_frame_derivative"),
            "action_contract": legacy.get("action_contract") == upgraded.get("action_contract"),
            "audio_contract": legacy.get("audio_contract") == upgraded.get("audio_contract"),
            "adapter": legacy.get("provider_adapter_profile") == upgraded.get("provider_adapter_profile") == ADAPTER,
        }
        if not all(comparisons.values()):
            issues.extend(f"R623_SEGMENT_FLOW_CHANGED:{sid}:{key}" for key, passed in comparisons.items() if not passed)
        span = float(segment.get("target_end_s")) - float(segment.get("target_start_s"))
        segment_proofs.append({
            "segment_id": sid,
            "grid_id": segment.get("grid_id"),
            "comparisons": comparisons,
            "derived_request_duration_s": max(1, math.ceil(span - 1e-9)),
            "legacy_grid_sha256": legacy.get("grid_asset", {}).get("sha256"),
            "r623_grid_sha256": upgraded.get("grid_asset", {}).get("sha256"),
            "legacy_start_sha256": legacy.get("start_frame_derivative", {}).get("sha256"),
            "r623_start_sha256": upgraded.get("start_frame_derivative", {}).get("sha256"),
        })
    proof = {
        "job_sha256": sha256_file(job_path),
        "scene_plan_sha256": sha256_file(scene_path),
        "segment_count": len(segments),
        "segment_proofs": segment_proofs,
        "external_ledger_projection": external_ledger_projection(state),
    }
    return sorted(set(issues)), proof


def compare_baseline(project: Path, baseline: Path, proof: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for relative in ("artifacts/P1/JOB.json", "artifacts/P2/SOURCE_MACRO_SCENE_EVIDENCE.json", "artifacts/P3/SOURCE_AUDIO_PLAN.json", "artifacts/P4/SCENE_PLAN.json"):
        current = project_file(project, relative)
        old = project_file(baseline, relative)
        if sha256_file(current) != sha256_file(old):
            issues.append(f"R623_UPSTREAM_ARTIFACT_CHANGED:{relative}")
    current_state = load_json(project_file(project, STATE_NAME))
    baseline_state = load_json(project_file(baseline, STATE_NAME))
    if external_ledger_projection(current_state) != external_ledger_projection(baseline_state):
        issues.append("R623_EXTERNAL_CALL_LEDGER_CHANGED")
    for row in proof.get("segment_proofs", []):
        sid = row["segment_id"]
        for relative in (
            f"artifacts/P7/{sid}_START_CELL01.png",
            f"artifacts/P7/{sid}_START_CELL01_CROP_RECEIPT.json",
        ):
            if sha256_file(project_file(project, relative)) != sha256_file(project_file(baseline, relative)):
                issues.append(f"R623_EXISTING_P7_ASSET_CHANGED:{relative}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--baseline-project-dir", type=Path)
    parser.add_argument("--report")
    args = parser.parse_args()
    try:
        project = args.project_dir.resolve()
        issues, proof = validate_internal(project)
        if args.baseline_project_dir:
            issues.extend(compare_baseline(project, args.baseline_project_dir.resolve(), proof))
        issues = sorted(set(issues))
        result = {
            "schema_version": "R6.23-SAME-FLOW-MIGRATION-PROOF-1.0",
            "status": "PASSED" if not issues else "FAILED",
            "project_dir": str(project),
            "baseline_project_dir": str(args.baseline_project_dir.resolve()) if args.baseline_project_dir else None,
            "proof": proof,
            "issues": issues,
            "external_calls_during_validation": 0,
        }
        if args.report:
            report_relative = normalize_project_relative(args.report)
            write_json_atomic(project / report_relative, result)
        code = 0 if not issues else 1
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        result = {"status": "FAILED", "issues": [str(exc)], "external_calls_during_validation": 0}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
