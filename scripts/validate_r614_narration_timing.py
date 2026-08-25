#!/usr/bin/env python3
"""Validate final narration timing or the M2-F source-audio timing authority."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file


HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOLERANCE = 0.002


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _project_file(project: Path, block: dict[str, Any], issues: list[str]) -> None:
    try:
        relative = normalize_project_relative(str(block.get("relative_path", "")))
    except ValueError:
        issues.append("NARRATION_MEASUREMENT_EVIDENCE_PATH_INVALID")
        return
    path = (project / relative).resolve()
    digest = str(block.get("sha256", "")).lower()
    if not path.is_file():
        issues.append("NARRATION_MEASUREMENT_EVIDENCE_MISSING")
    elif not HEX64.fullmatch(digest) or sha256_file(path) != digest:
        issues.append("NARRATION_MEASUREMENT_EVIDENCE_HASH_MISMATCH")


def validate(project: Path, timing_path: Path) -> list[str]:
    issues: list[str] = []
    timing = load_json(timing_path)
    manifest = load_json(project / STATE_NAME)
    mode_lock = manifest.get("mode_lock") if isinstance(manifest.get("mode_lock"), dict) else {}
    target = mode_lock.get("target") if isinstance(mode_lock.get("target"), dict) else {}
    source_audio_mode = target.get("audio_variant") == "SOURCE_AUDIO_REUSE"

    if source_audio_mode:
        if timing.get("schema_version") != "R6.19-SOURCE-AUDIO-TIMING-1.0":
            issues.append("SOURCE_AUDIO_TIMING_SCHEMA_INVALID")
        if timing.get("status") != "MEASURED_SOURCE_AUDIO":
            issues.append("SOURCE_AUDIO_TIMING_NOT_MEASURED")
        if timing.get("job_id") != manifest.get("project_id"):
            issues.append("SOURCE_AUDIO_TIMING_PROJECT_MISMATCH")
        if timing.get("audio_variant") != "SOURCE_AUDIO_REUSE" or timing.get("timing_authority") != "SOURCE_AUDIO_MASTER":
            issues.append("SOURCE_AUDIO_TIMING_AUTHORITY_INVALID")

        source = timing.get("source_audio") if isinstance(timing.get("source_audio"), dict) else {}
        _project_file(project, source, issues)
        source_speed = _number(source.get("playback_speed"))
        source_duration = _number(source.get("duration_s"))
        if source_speed is None or abs(source_speed - 1.0) > TOLERANCE:
            issues.append("SOURCE_AUDIO_PLAYBACK_SPEED_MUST_BE_ONE")
        if source_duration is None or source_duration <= 0:
            issues.append("SOURCE_AUDIO_DURATION_INVALID")
        measurement_method = source.get("measurement_method")
        if measurement_method not in {"MEDIA_PROBE_PYAV", "MEDIA_PROBE_PYAV_EDITOR_SAFE_FLOOR_MS"}:
            issues.append("SOURCE_AUDIO_MEASUREMENT_METHOD_INVALID")
        if measurement_method == "MEDIA_PROBE_PYAV_EDITOR_SAFE_FLOOR_MS":
            measured_media_duration = _number(source.get("measured_media_duration_s"))
            if measured_media_duration is None or source_duration is None or measured_media_duration < source_duration or measured_media_duration - source_duration >= 0.001 + TOLERANCE:
                issues.append("SOURCE_AUDIO_EDITOR_SAFE_DURATION_INVALID")

        copy_block = timing.get("source_copy") if isinstance(timing.get("source_copy"), dict) else {}
        _project_file(project, copy_block, issues)
        if copy_block.get("policy") != "VERBATIM_NO_REWRITE":
            issues.append("SOURCE_COPY_POLICY_INVALID")
        try:
            copy_relative = normalize_project_relative(str(copy_block.get("relative_path", "")))
            source_copy_text = (project / copy_relative).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError, ValueError):
            source_copy_text = ""
            issues.append("SOURCE_COPY_READ_FAILED")

        rows = timing.get("segments") if isinstance(timing.get("segments"), list) else []
        if not rows:
            issues.append("SOURCE_AUDIO_TIMING_SEGMENTS_MISSING")
        seen_ids: set[str] = set()
        previous_end = 0.0
        assembled_copy: list[str] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_INVALID")
                continue
            segment_id = str(row.get("segment_id", ""))
            if not segment_id or segment_id in seen_ids or row.get("segment_order") != index:
                issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_ID_OR_ORDER_INVALID")
            seen_ids.add(segment_id)
            exact_copy = str(row.get("exact_copy", ""))
            if not exact_copy:
                issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_COPY_MISSING")
            assembled_copy.append(exact_copy)
            start = _number(row.get("start_s"))
            end = _number(row.get("end_s"))
            duration = _number(row.get("duration_s"))
            if start is None or end is None or duration is None or end <= start:
                issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_TIME_INVALID")
                continue
            if abs(start - previous_end) > TOLERANCE:
                issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_TIMELINE_NOT_CONTIGUOUS")
            if abs((end - start) - duration) > TOLERANCE:
                issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_DURATION_MISMATCH")
            row_speed = _number(row.get("audio_speed"))
            if source_speed is None or row_speed is None or abs(row_speed - source_speed) > TOLERANCE:
                issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_SPEED_DIFFERS_FROM_MASTER")
            previous_end = end
        total = _number(timing.get("total_duration_s"))
        if total is None or abs(total - previous_end) > TOLERANCE:
            issues.append("SOURCE_AUDIO_TOTAL_DURATION_MISMATCH")
        if source_duration is not None and total is not None and abs(total - source_duration) > TOLERANCE:
            issues.append("SOURCE_AUDIO_TOTAL_DIFFERS_FROM_ASSET")
        if source_copy_text and "".join(assembled_copy) != source_copy_text:
            issues.append("SOURCE_AUDIO_SEGMENT_COPY_DOES_NOT_RECONSTRUCT_LOCKED_COPY")
        return sorted(set(issues))

    if timing.get("schema_version") != "R6.14-NARRATION-TIMING-1.0":
        issues.append("NARRATION_TIMING_SCHEMA_INVALID")
    if timing.get("status") != "MEASURED_FINAL_VOICE":
        issues.append("NARRATION_TIMING_NOT_MEASURED_WITH_FINAL_VOICE")
    if timing.get("job_id") != manifest.get("project_id"):
        issues.append("NARRATION_TIMING_PROJECT_MISMATCH")
    if timing.get("audio_variant") != "POST_DUB_NARRATION" or timing.get("timing_authority") not in {"NARRATION_MASTER", "SOURCE_TIMELINE"}:
        issues.append("NARRATION_TIMING_AUTHORITY_INVALID")

    voice = timing.get("voice") if isinstance(timing.get("voice"), dict) else {}
    if not str(voice.get("provider", "")).strip() or not str(voice.get("voice_id", "")).strip():
        issues.append("FINAL_NARRATION_VOICE_NOT_LOCKED")
    global_speed = _number(voice.get("global_speed"))
    if global_speed is None or not 0.5 <= global_speed <= 2.0:
        issues.append("GLOBAL_NARRATION_SPEED_INVALID")
    if voice.get("measurement_method") not in {"MEASURED_FINAL_VOICE_AUDIO", "MEASURED_FINAL_EDITOR_TTS"}:
        issues.append("NARRATION_MEASUREMENT_METHOD_INVALID")
    evidence = timing.get("measurement_evidence") if isinstance(timing.get("measurement_evidence"), dict) else {}
    _project_file(project, evidence, issues)

    rows = timing.get("segments") if isinstance(timing.get("segments"), list) else []
    if not rows:
        issues.append("NARRATION_TIMING_SEGMENTS_MISSING")
    seen_ids: set[str] = set()
    previous_end = 0.0
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(f"NARRATION_SEGMENT_{index}_INVALID")
            continue
        segment_id = str(row.get("segment_id", ""))
        if not segment_id or segment_id in seen_ids or row.get("segment_order") != index:
            issues.append(f"NARRATION_SEGMENT_{index}_ID_OR_ORDER_INVALID")
        seen_ids.add(segment_id)
        if not str(row.get("spoken_copy", "")).strip():
            issues.append(f"NARRATION_SEGMENT_{index}_COPY_MISSING")
        start = _number(row.get("start_s"))
        end = _number(row.get("end_s"))
        duration = _number(row.get("duration_s"))
        if start is None or end is None or duration is None or end <= start:
            issues.append(f"NARRATION_SEGMENT_{index}_TIME_INVALID")
            continue
        if abs(start - previous_end) > TOLERANCE:
            issues.append(f"NARRATION_SEGMENT_{index}_TIMELINE_NOT_CONTIGUOUS")
        if abs((end - start) - duration) > TOLERANCE:
            issues.append(f"NARRATION_SEGMENT_{index}_DURATION_MISMATCH")
        row_speed = _number(row.get("voice_speed"))
        if global_speed is None or row_speed is None or abs(row_speed - global_speed) > TOLERANCE:
            issues.append(f"NARRATION_SEGMENT_{index}_VOICE_SPEED_DIFFERS_FROM_GLOBAL")
        previous_end = end
    total = _number(timing.get("total_duration_s"))
    if total is None or abs(total - previous_end) > TOLERANCE:
        issues.append("NARRATION_TOTAL_DURATION_MISMATCH")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--timing", default="artifacts/P3/NARRATION_TIMING.json")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        timing_path = (project / normalize_project_relative(args.timing)).resolve()
        issues = validate(project, timing_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
