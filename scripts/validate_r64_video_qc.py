#!/usr/bin/env python3
"""Validate R6.4 visual-score QC with post-dub audio excluded from scoring."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file


HEX64 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_CRITERIA = {
    "IDENTITY_AND_ENVIRONMENT_CONTINUITY": 20,
    "CORE_ACTION_AND_NARRATIVE_RESULT": 25,
    "PHYSICAL_TOPOLOGY_AND_SAFETY": 20,
    "COMPOSITION_STYLE_AND_USABILITY": 15,
    "REFERENCE_START_AND_SEQUENCE_ADHERENCE": 20,
}


def validate(project: Path, qc_path: Path) -> list[str]:
    issues: list[str] = []
    qc = load_json(qc_path)
    manifest = load_json(project / STATE_NAME)
    if qc.get("schema_version") != "R6.4-P8-VIDEO-OUTPUT-QC-1.0" or qc.get("status") != "REVIEWED":
        issues.append("VIDEO_QC_SCHEMA_OR_STATUS_INVALID")
    for key in ("job_id", "segment_id", "task_id"):
        if not isinstance(qc.get(key), str) or not qc.get(key).strip():
            issues.append(f"VIDEO_QC_{key.upper()}_MISSING")
    output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
    try:
        relative = normalize_project_relative(str(output.get("relative_path", "")))
        path = (project / relative).resolve()
        path.relative_to(project)
        expected_hash = str(output.get("sha256", "")).lower()
        if not path.is_file() or not HEX64.fullmatch(expected_hash) or sha256_file(path) != expected_hash:
            issues.append("VIDEO_QC_OUTPUT_HASH_INVALID_OR_MISMATCH")
    except ValueError:
        issues.append("VIDEO_QC_OUTPUT_PATH_INVALID")

    if qc.get("visual_score_threshold") != 80:
        issues.append("VISUAL_SCORE_THRESHOLD_MUST_BE_80")
    breakdown = qc.get("visual_score_breakdown") if isinstance(qc.get("visual_score_breakdown"), list) else []
    rows: dict[str, dict[str, Any]] = {}
    for row in breakdown:
        if isinstance(row, dict) and isinstance(row.get("criterion"), str):
            rows[row["criterion"]] = row
    if set(rows) != set(EXPECTED_CRITERIA):
        issues.append("VISUAL_SCORE_CRITERIA_INCOMPLETE_OR_DUPLICATE")
    calculated = 0
    for criterion, maximum in EXPECTED_CRITERIA.items():
        row = rows.get(criterion, {})
        score = row.get("score")
        if row.get("maximum") != maximum or isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= maximum:
            issues.append(f"VISUAL_SCORE_ROW_INVALID:{criterion}")
        else:
            calculated += score
        if not isinstance(row.get("evidence"), str) or not row.get("evidence").strip():
            issues.append(f"VISUAL_SCORE_EVIDENCE_MISSING:{criterion}")
    visual_score = qc.get("visual_score")
    if isinstance(visual_score, bool) or not isinstance(visual_score, int) or visual_score != calculated:
        issues.append("VISUAL_SCORE_TOTAL_INVALID")

    hard_failures = qc.get("hard_visual_failures")
    if not isinstance(hard_failures, list) or any(not isinstance(value, str) or not value.strip() for value in hard_failures):
        issues.append("HARD_VISUAL_FAILURES_INVALID")
        hard_failures = []
    audio = qc.get("audio_policy") if isinstance(qc.get("audio_policy"), dict) else {}
    mode_lock = manifest.get("mode_lock") if isinstance(manifest.get("mode_lock"), dict) else {}
    target = mode_lock.get("target") if isinstance(mode_lock.get("target"), dict) else {}
    if target.get("audio_variant") == "SOURCE_AUDIO_REUSE":
        source = audio.get("source_audio") if isinstance(audio.get("source_audio"), dict) else {}
        try:
            source_relative = normalize_project_relative(str(source.get("relative_path", "")))
            source_path = (project / source_relative).resolve()
            expected_source_hash = str(source.get("sha256", "")).lower()
            if not source_path.is_file() or not HEX64.fullmatch(expected_source_hash) or sha256_file(source_path) != expected_source_hash:
                issues.append("SOURCE_AUDIO_QC_ASSET_INVALID_OR_MISMATCH")
        except ValueError:
            issues.append("SOURCE_AUDIO_QC_ASSET_PATH_INVALID")
        if (
            audio.get("pipeline") != "SOURCE_AUDIO_REUSE"
            or audio.get("excluded_from_visual_score") is not True
            or audio.get("post_dub_required") is not False
            or audio.get("generated_audio_action") != "MUTE"
            or audio.get("protagonist_lip_sync_required") is not False
            or audio.get("playback_speed") != 1.0
            or audio.get("timing_authority") != "SOURCE_AUDIO_MASTER"
        ):
            issues.append("SOURCE_AUDIO_REUSE_QC_POLICY_INVALID")
    elif (
        audio.get("pipeline") != "POST_DUB_NARRATION"
        or audio.get("excluded_from_visual_score") is not True
        or audio.get("post_dub_required") is not True
        or audio.get("generated_audio_action") != "IGNORE_OR_REPLACE"
        or audio.get("protagonist_lip_sync_required") is not False
    ):
        issues.append("POST_DUB_AUDIO_POLICY_INVALID")

    should_pass = isinstance(visual_score, int) and not isinstance(visual_score, bool) and visual_score >= 80 and not hard_failures
    expected_decision = "PASSED" if should_pass else "REJECTED"
    if qc.get("decision") != expected_decision:
        issues.append("VIDEO_QC_DECISION_DIFFERS_FROM_SCORE_OR_HARD_FAILURES")
    if qc.get("automatic_retry_allowed") is not False:
        issues.append("VIDEO_QC_AUTOMATIC_RETRY_FORBIDDEN")
    if not isinstance(qc.get("next_action"), str) or not qc.get("next_action").strip():
        issues.append("VIDEO_QC_NEXT_ACTION_MISSING")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--qc", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        qc_path = (project / normalize_project_relative(args.qc)).resolve()
        issues = validate(project, qc_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
