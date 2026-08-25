#!/usr/bin/env python3
"""Validate that P4 inherits the R6.15 copy, obligations, and action deadlines exactly."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from r62_project import load_json, normalize_project_relative, sha256_file
from validate_r62_scene_plan import validate_scene_plan
from validate_r615_narration_plan import validate as validate_narration_plan
from release_contract import KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS
from r639_keyframe_contract import validate_plan as validate_r639_keyframes


TOLERANCE = 0.011


def canonical(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate(project: Path, job_path: Path, evidence_path: Path, scene_path: Path, narration_path: Path) -> list[str]:
    issues = [f"NARRATION:{item}" for item in validate_narration_plan(project, narration_path)]
    job, evidence, scene, narration = map(load_json, (job_path, evidence_path, scene_path, narration_path))
    issues.extend(f"SCENE:{item}" for item in validate_scene_plan(job, evidence, scene))
    manifest = load_json(project / "R62_PROJECT.json")
    if manifest.get("skill_version") in KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS:
        issues.extend(f"KEYFRAME:{item}" for item in validate_r639_keyframes(scene, require=True))
    ref = scene.get("narration_timing") if isinstance(scene.get("narration_timing"), dict) else {}
    if ref.get("relative_path") != "artifacts/P3/NARRATION_PLAN.json" or ref.get("sha256") != sha256_file(narration_path):
        issues.append("P4_NARRATION_PLAN_LINEAGE_MISMATCH")
    scene_rows = scene.get("video_segments") if isinstance(scene.get("video_segments"), list) else []
    narration_rows = narration.get("segments") if isinstance(narration.get("segments"), list) else []
    if len(scene_rows) != len(narration_rows):
        issues.append("P4_SEGMENT_COUNT_DIFFERS_FROM_NARRATION")
    grid_rows = scene.get("grids") if isinstance(scene.get("grids"), list) else []
    grids = {row.get("segment_id"): row for row in grid_rows if isinstance(row, dict)}
    for index, narration_row in enumerate(narration_rows):
        if index >= len(scene_rows) or not isinstance(narration_row, dict) or not isinstance(scene_rows[index], dict):
            continue
        row = scene_rows[index]
        segment_id = str(narration_row.get("segment_id", f"SEGMENT_{index + 1}"))
        for key in ("segment_id", "segment_order", "target_start_s", "target_end_s"):
            expected_key = {"target_start_s": "start_s", "target_end_s": "end_s"}.get(key, key)
            if row.get(key) != narration_row.get(expected_key):
                issues.append(f"{segment_id}_P4_{key.upper()}_DIFFERS_FROM_NARRATION")
        if row.get("planned_narration_duration_s") != narration_row.get("creative_target_duration_s"):
            issues.append(f"{segment_id}_P4_DURATION_DIFFERS_FROM_NARRATION")
        obligation_ids = {
            f"{utterance.get('utterance_id')}:{number}"
            for utterance in narration_row.get("utterances", []) if isinstance(utterance, dict)
            for number, obligation in enumerate(utterance.get("visual_obligations", []), start=1) if isinstance(obligation, dict)
        }
        if set(row.get("narration_obligation_ids", [])) != obligation_ids:
            issues.append(f"{segment_id}_P4_OBLIGATION_SET_DIFFERS_FROM_NARRATION")
        if canonical(row.get("action_nodes")) != canonical(narration_row.get("action_nodes")):
            issues.append(f"{segment_id}_P4_ACTION_NODES_DIFFER_FROM_NARRATION")
        grid = grids.get(segment_id, {})
        fulfilled = {
            ref_id
            for cell in grid.get("cells", []) if isinstance(cell, dict)
            for ref_id in cell.get("fulfills_obligation_ids", []) if isinstance(ref_id, str)
        }
        if obligation_ids - fulfilled:
            issues.append(f"{segment_id}_GRID_CELLS_DO_NOT_COVER_NARRATION_OBLIGATIONS")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--job", default="artifacts/P1/JOB.json")
    parser.add_argument("--evidence", default="artifacts/P2/TIMELINE_EVIDENCE.json")
    parser.add_argument("--scene-plan", default="artifacts/P4/SCENE_PLAN.json")
    parser.add_argument("--narration-plan", default="artifacts/P3/NARRATION_PLAN.json")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        paths = [(project / normalize_project_relative(value)).resolve() for value in (args.job, args.evidence, args.scene_plan, args.narration_plan)]
        issues = validate(project, *paths)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
