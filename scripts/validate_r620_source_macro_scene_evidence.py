#!/usr/bin/env python3
"""Validate M2-F source-video macro-scene evidence without granting pixel authority."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from r62_project import load_json, normalize_project_relative, sha256_file
from validate_r62_job import validate_job


TOLERANCE = 0.08
GRANTED = [
    "MACRO_SCENE", "ACTION_SUBJECT", "ACTION_OBJECT", "ACTION_PATH", "CAUSAL_ORDER", "VISIBLE_RESULT",
]
DENIED = [
    "SOURCE_PIXELS", "SOURCE_COMPOSITION", "SOURCE_CAMERA", "SOURCE_CUTS", "GENERATION_REFERENCE_IMAGE",
]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _project_file(project: Path, relative: Any, label: str, issues: list[str]) -> Path | None:
    try:
        normalized = normalize_project_relative(str(relative or ""))
    except ValueError:
        issues.append(f"{label}_PATH_INVALID")
        return None
    path = (project / normalized).resolve()
    try:
        path.relative_to(project)
    except ValueError:
        issues.append(f"{label}_PATH_ESCAPES_PROJECT")
        return None
    if not path.is_file():
        issues.append(f"{label}_MISSING")
        return None
    return path


def validate(project: Path, evidence_path: Path, job_path: Path) -> list[str]:
    issues: list[str] = []
    job = load_json(job_path)
    evidence = load_json(evidence_path)
    issues.extend(f"JOB:{issue}" for issue in validate_job(job))
    if evidence.get("schema_version") != "R6.20-SOURCE-MACRO-SCENE-EVIDENCE-1.0":
        issues.append("SOURCE_MACRO_SCENE_SCHEMA_INVALID")
    if evidence.get("job_id") != job.get("job_id"):
        issues.append("SOURCE_MACRO_SCENE_JOB_MISMATCH")
    if evidence.get("route_id") != "M2_F_SOURCE_AUDIO_RESTYLE" or job.get("route_id") != evidence.get("route_id"):
        issues.append("SOURCE_MACRO_SCENE_ROUTE_INVALID")
    if evidence.get("status") != "VERIFIED":
        issues.append("SOURCE_MACRO_SCENE_NOT_VERIFIED")

    source = evidence.get("source_video") if isinstance(evidence.get("source_video"), dict) else {}
    job_source = job.get("source") if isinstance(job.get("source"), dict) else {}
    if source.get("relative_path") != job_source.get("video_path") or source.get("sha256") != job_source.get("video_sha256"):
        issues.append("SOURCE_MACRO_SCENE_VIDEO_DIFFERS_FROM_P1")
    source_path = _project_file(project, source.get("relative_path"), "SOURCE_VIDEO", issues)
    if source_path is not None and sha256_file(source_path) != _text(source.get("sha256")).lower():
        issues.append("SOURCE_MACRO_SCENE_VIDEO_HASH_MISMATCH")
    duration = _number(source.get("duration_s"))
    job_duration = _number(job_source.get("duration_s"))
    if duration is None or job_duration is None or abs(duration - job_duration) > TOLERANCE:
        issues.append("SOURCE_MACRO_SCENE_DURATION_MISMATCH")

    policy = evidence.get("observation_policy") if isinstance(evidence.get("observation_policy"), dict) else {}
    start = _number(policy.get("coverage_start_s"))
    end = _number(policy.get("coverage_end_s"))
    interval = _number(policy.get("maximum_sampling_interval_s"))
    if start is None or abs(start) > TOLERANCE or duration is None or end is None or abs(end - duration) > TOLERANCE:
        issues.append("SOURCE_MACRO_SCENE_COVERAGE_INCOMPLETE")
    if interval is None or interval <= 0 or interval > 1.0:
        issues.append("SOURCE_MACRO_SCENE_SAMPLING_TOO_SPARSE")
    if policy.get("event_boundary_reviewed") is not True:
        issues.append("SOURCE_MACRO_SCENE_EVENT_BOUNDARIES_NOT_REVIEWED")
    uncertainties = policy.get("critical_uncertainties")
    if not isinstance(uncertainties, list) or uncertainties:
        issues.append("SOURCE_MACRO_SCENE_UNCERTAINTY_BLOCKS_P3")

    scope = evidence.get("authority_scope") if isinstance(evidence.get("authority_scope"), dict) else {}
    if scope.get("granted") != GRANTED or scope.get("denied") != DENIED:
        issues.append("SOURCE_MACRO_SCENE_AUTHORITY_SCOPE_INVALID")

    scenes = evidence.get("macro_scenes")
    if not isinstance(scenes, list) or not scenes:
        issues.append("SOURCE_MACRO_SCENES_MISSING")
        scenes = []
    seen: set[str] = set()
    previous_start = -1.0
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            issues.append(f"SOURCE_MACRO_SCENE_{index}_INVALID")
            continue
        scene_id = _text(scene.get("source_scene_id"))
        if not scene_id or scene_id in seen:
            issues.append(f"SOURCE_MACRO_SCENE_{index}_ID_INVALID_OR_DUPLICATE")
        seen.add(scene_id)
        scene_start = _number(scene.get("source_start_s"))
        scene_end = _number(scene.get("source_end_s"))
        if scene_start is None or scene_end is None or scene_end <= scene_start or scene_start < -TOLERANCE or (duration is not None and scene_end > duration + TOLERANCE):
            issues.append(f"{scene_id or index}_SOURCE_INTERVAL_INVALID")
        elif scene_start < previous_start:
            issues.append(f"{scene_id}_SOURCE_SCENES_NOT_CHRONOLOGICAL")
        if scene_start is not None:
            previous_start = scene_start
        if not _text(scene.get("narrative_function")):
            issues.append(f"{scene_id}_NARRATIVE_FUNCTION_MISSING")
        contract = scene.get("action_contract") if isinstance(scene.get("action_contract"), dict) else {}
        for field in ("action_subject", "action_object", "action_path", "causal_order", "visible_result"):
            if not _text(contract.get(field)):
                issues.append(f"{scene_id}_{field.upper()}_MISSING")
        forbidden = contract.get("forbidden_substitutions")
        if not isinstance(forbidden, list) or not forbidden or not all(_text(item) for item in forbidden):
            issues.append(f"{scene_id}_FORBIDDEN_SUBSTITUTIONS_MISSING")
        evidence_times = scene.get("evidence_times_s")
        if not isinstance(evidence_times, list) or not evidence_times:
            issues.append(f"{scene_id}_EVIDENCE_TIMES_MISSING")
        elif scene_start is not None and scene_end is not None and any(_number(value) is None or float(value) < scene_start - TOLERANCE or float(value) > scene_end + TOLERANCE for value in evidence_times):
            issues.append(f"{scene_id}_EVIDENCE_TIME_OUT_OF_SCENE")
        if scene.get("confidence") not in {"HIGH", "MEDIUM"}:
            issues.append(f"{scene_id}_CONFIDENCE_TOO_LOW")
        if _text(scene.get("uncertainty")):
            issues.append(f"{scene_id}_UNRESOLVED_UNCERTAINTY")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--evidence", default="artifacts/P2/SOURCE_MACRO_SCENE_EVIDENCE.json")
    parser.add_argument("--job", default="artifacts/P1/JOB.json")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        evidence_path = _project_file(project, args.evidence, "SOURCE_MACRO_SCENE_EVIDENCE", [])
        job_path = _project_file(project, args.job, "JOB", [])
        if evidence_path is None or job_path is None:
            raise ValueError("SOURCE_MACRO_SCENE_EVIDENCE_OR_JOB_MISSING")
        issues = validate(project, evidence_path, job_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
