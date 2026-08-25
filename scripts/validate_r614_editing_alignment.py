#!/usr/bin/env python3
"""Validate that edited video follows narration or M2-F source audio."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file
from validate_r614_narration_timing import validate as validate_narration_timing


HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def validate(project: Path, alignment_path: Path) -> list[str]:
    issues: list[str] = []
    alignment = load_json(alignment_path)
    manifest = load_json(project / STATE_NAME)
    mode_lock = manifest.get("mode_lock") if isinstance(manifest.get("mode_lock"), dict) else {}
    target = mode_lock.get("target") if isinstance(mode_lock.get("target"), dict) else {}
    source_audio_mode = target.get("audio_variant") == "SOURCE_AUDIO_REUSE"
    if alignment.get("schema_version") != "R6.14-EDITING-ALIGNMENT-1.0" or alignment.get("status") != "REVIEWED":
        issues.append("EDITING_ALIGNMENT_SCHEMA_OR_STATUS_INVALID")
    if alignment.get("job_id") != manifest.get("project_id"):
        issues.append("EDITING_ALIGNMENT_PROJECT_MISMATCH")
    fps = _number(alignment.get("timeline_fps"))
    if fps is None or fps <= 0:
        issues.append("EDITING_TIMELINE_FPS_INVALID")
        tolerance = 0.002
    else:
        tolerance = 1.0 / fps + 0.002

    timing_block = alignment.get("timing_manifest") if isinstance(alignment.get("timing_manifest"), dict) else {}
    try:
        timing_rel = normalize_project_relative(str(timing_block.get("relative_path", "")))
        timing_path = (project / timing_rel).resolve()
    except ValueError:
        timing_path = project / "__invalid__"
        issues.append("EDITING_TIMING_MANIFEST_PATH_INVALID")
    timing_hash = str(timing_block.get("sha256", "")).lower()
    timing: dict[str, Any] = {}
    if not timing_path.is_file():
        issues.append("EDITING_TIMING_MANIFEST_MISSING")
    elif not HEX64.fullmatch(timing_hash) or sha256_file(timing_path) != timing_hash:
        issues.append("EDITING_TIMING_MANIFEST_HASH_MISMATCH")
    else:
        issues.extend(validate_narration_timing(project, timing_path))
        timing = load_json(timing_path)

    timing_rows = timing.get("segments") if isinstance(timing.get("segments"), list) else []
    rows = alignment.get("segments") if isinstance(alignment.get("segments"), list) else []
    if len(rows) != len(timing_rows) or not rows:
        issues.append("EDITING_SEGMENT_SET_DIFFERS_FROM_NARRATION")
    if source_audio_mode:
        global_speed = _number((timing.get("source_audio") or {}).get("playback_speed")) if isinstance(timing.get("source_audio"), dict) else None
    else:
        global_speed = _number((timing.get("voice") or {}).get("global_speed")) if isinstance(timing.get("voice"), dict) else None
    total = 0.0
    for index, (row, timing_row) in enumerate(zip(rows, timing_rows), start=1):
        if not isinstance(row, dict) or not isinstance(timing_row, dict):
            issues.append(f"EDITING_SEGMENT_{index}_INVALID")
            continue
        if row.get("segment_id") != timing_row.get("segment_id") or row.get("segment_order") != index:
            issues.append(f"EDITING_SEGMENT_{index}_ID_OR_ORDER_MISMATCH")
        narration = _number(row.get("audio_duration_s" if source_audio_mode else "narration_duration_s"))
        edited = _number(row.get("edited_video_duration_s"))
        source = _number(row.get("source_video_duration_s"))
        requested = row.get("requested_generation_s")
        expected_narration = _number(timing_row.get("duration_s"))
        if narration is None or edited is None or source is None or expected_narration is None or source <= 0:
            issues.append(f"EDITING_SEGMENT_{index}_DURATION_INVALID")
            continue
        if abs(narration - expected_narration) > tolerance:
            issues.append(f"EDITING_SEGMENT_{index}_NARRATION_DURATION_MISMATCH")
        if abs(edited - narration) > tolerance:
            issues.append(f"EDITING_SEGMENT_{index}_VIDEO_NOT_ALIGNED_TO_NARRATION")
        expected_request = max(1, math.ceil(expected_narration - 1e-9))
        if isinstance(requested, bool) or not isinstance(requested, int) or requested != expected_request:
            issues.append(f"EDITING_SEGMENT_{index}_REQUEST_DURATION_NOT_CEILING")
        row_speed = _number(row.get("source_audio_speed" if source_audio_mode else "voice_speed"))
        if global_speed is None or row_speed is None or abs(row_speed - global_speed) > 0.002:
            issues.append(f"EDITING_SEGMENT_{index}_VOICE_SPEED_DIFFERS_FROM_GLOBAL")
        video_speed = _number(row.get("video_speed"))
        expected_policy = "VIDEO_FOLLOWS_SOURCE_AUDIO" if source_audio_mode else "VIDEO_FOLLOWS_NARRATION"
        if video_speed is None or video_speed <= 0 or row.get("alignment_policy") != expected_policy:
            issues.append(f"EDITING_SEGMENT_{index}_VIDEO_ALIGNMENT_POLICY_INVALID")
        elif abs(video_speed - (source / edited)) > 0.02:
            issues.append(f"EDITING_SEGMENT_{index}_VIDEO_SPEED_DOES_NOT_MATCH_DURATION_RATIO")
        total += edited
    declared_total = _number(alignment.get("total_duration_s"))
    if declared_total is None or abs(declared_total - total) > tolerance:
        issues.append("EDITING_TOTAL_DURATION_MISMATCH")
    if alignment.get("per_segment_voice_speed_changes") is not False:
        issues.append("PER_SEGMENT_VOICE_SPEED_CHANGES_FORBIDDEN")
    if alignment.get("decision") != "PASSED":
        issues.append("EDITING_ALIGNMENT_NOT_PASSED")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--alignment", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        path = (project / normalize_project_relative(args.alignment)).resolve()
        issues = validate(project, path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
