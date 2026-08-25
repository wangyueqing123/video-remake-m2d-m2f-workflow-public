#!/usr/bin/env python3
"""Bind every declared M2-F source action change to real pre/post source frames."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from r62_project import load_json, normalize_project_relative, sha256_file

sys.dont_write_bytecode = True

SCHEMA = "R6.24-P2-SOURCE-EVENT-EVIDENCE-1.0"
TOLERANCE = 0.08


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def project_file(project: Path, relative: Any, label: str, issues: list[str]) -> Path | None:
    try:
        normalized = normalize_project_relative(str(relative or ""))
        path = (project / normalized).resolve()
        path.relative_to(project)
    except ValueError:
        issues.append(f"{label}_PATH_INVALID")
        return None
    if not path.is_file():
        issues.append(f"{label}_MISSING")
        return None
    return path


def frame_ref(project: Path, value: Any, frame_map: dict[str, dict[str, Any]], label: str, issues: list[str]) -> tuple[float | None, Path | None]:
    if not isinstance(value, dict):
        issues.append(f"{label}_INVALID")
        return None, None
    when = number(value.get("time_s"))
    path = project_file(project, value.get("relative_path"), label, issues)
    if path is None or value.get("sha256") != sha256_file(path):
        issues.append(f"{label}_HASH_MISMATCH")
    indexed = frame_map.get(str(value.get("relative_path", "")))
    if indexed is None:
        issues.append(f"{label}_NOT_IN_FRAME_INDEX")
    elif when is None or abs(float(indexed["decoded_time_s"]) - when) > TOLERANCE or indexed.get("sha256") != value.get("sha256"):
        issues.append(f"{label}_FRAME_INDEX_BINDING_MISMATCH")
    return when, path


def validate(project: Path, event_path: Path, macro_path: Path, job_path: Path) -> list[str]:
    issues: list[str] = []
    event_doc = load_json(event_path)
    macro = load_json(macro_path)
    job = load_json(job_path)
    if event_doc.get("schema_version") != SCHEMA or event_doc.get("status") != "VERIFIED":
        issues.append("R624_SOURCE_EVENT_SCHEMA_OR_STATUS_INVALID")
    if event_doc.get("job_id") != job.get("job_id") or macro.get("job_id") != job.get("job_id"):
        issues.append("R624_SOURCE_EVENT_JOB_MISMATCH")
    source = event_doc.get("source_video") if isinstance(event_doc.get("source_video"), dict) else {}
    job_source = job.get("source") if isinstance(job.get("source"), dict) else {}
    if source.get("relative_path") != job_source.get("video_path") or source.get("sha256") != job_source.get("video_sha256"):
        issues.append("R624_SOURCE_EVENT_VIDEO_DIFFERS_FROM_P1")

    index_ref = event_doc.get("frame_index") if isinstance(event_doc.get("frame_index"), dict) else {}
    index_path = project_file(project, index_ref.get("relative_path"), "R624_FRAME_INDEX", issues)
    frame_map: dict[str, dict[str, Any]] = {}
    frames: list[dict[str, Any]] = []
    if index_path is not None:
        if index_ref.get("sha256") != sha256_file(index_path):
            issues.append("R624_FRAME_INDEX_HASH_MISMATCH")
        index = load_json(index_path)
        if index.get("source_sha256") != job_source.get("video_sha256"):
            issues.append("R624_FRAME_INDEX_SOURCE_MISMATCH")
        frames = [row for row in index.get("frames", []) if isinstance(row, dict)]
        frame_map = {str(row.get("relative_path")): row for row in frames}
    review = event_doc.get("review") if isinstance(event_doc.get("review"), dict) else {}
    if not frames or review.get("reviewed_frame_count") != len(frames) or review.get("all_visible_state_changes_captured") is not True:
        issues.append("R624_FULL_FRAME_REVIEW_NOT_PROVEN")
    if review.get("reviewer_basis") != "FRAME_INDEX_PLUS_EVENT_BOUNDARIES":
        issues.append("R624_REVIEW_BASIS_INVALID")
    relation = review.get("claim_visual_relationship")
    contradictions = review.get("claim_visual_contradictions")
    if relation not in {"ALIGNED", "CONTRADICTION_PRESERVED"} or not isinstance(contradictions, list):
        issues.append("R624_CLAIM_VISUAL_REVIEW_INVALID")
    elif bool(contradictions) != (relation == "CONTRADICTION_PRESERVED"):
        issues.append("R624_CLAIM_VISUAL_CONTRADICTION_STATUS_MISMATCH")

    events = event_doc.get("events") if isinstance(event_doc.get("events"), list) else []
    event_map: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            issues.append(f"R624_EVENT_{index}_INVALID")
            continue
        event_id = text(event.get("event_id"))
        event_time = number(event.get("event_time_s"))
        if not event_id or event_id in event_map or event_time is None:
            issues.append(f"R624_EVENT_{index}_ID_OR_TIME_INVALID")
            continue
        event_map[event_id] = event
        if not all(text(event.get(key)) for key in ("action_subject", "action_object", "prior_state", "result_state")):
            issues.append(f"{event_id}_SEMANTIC_FIELDS_MISSING")
        if event.get("prior_state") == event.get("result_state"):
            issues.append(f"{event_id}_STATE_DOES_NOT_CHANGE")
        pre_time, _ = frame_ref(project, event.get("pre_frame"), frame_map, f"{event_id}_PRE_FRAME", issues)
        post_time, _ = frame_ref(project, event.get("post_frame"), frame_map, f"{event_id}_POST_FRAME", issues)
        if pre_time is None or post_time is None or event_time < pre_time - TOLERANCE or event_time > post_time + TOLERANCE or post_time <= pre_time:
            issues.append(f"{event_id}_PRE_POST_TIME_ORDER_INVALID")

    macro_scenes = [row for row in macro.get("macro_scenes", []) if isinstance(row, dict)]
    duration = number(job_source.get("duration_s"))
    previous_end = 0.0
    referenced_events: list[str] = []
    for index, scene in enumerate(macro_scenes, start=1):
        scene_id = text(scene.get("source_scene_id")) or f"SCENE_{index}"
        start = number(scene.get("source_start_s"))
        end = number(scene.get("source_end_s"))
        if start is None or end is None or abs(start - previous_end) > TOLERANCE:
            issues.append(f"{scene_id}_R624_SCENE_GAP_OR_OVERLAP")
        if end is not None:
            previous_end = end
        policy = scene.get("change_policy")
        event_ids = scene.get("source_event_ids") if isinstance(scene.get("source_event_ids"), list) else []
        if policy not in {"HOLD", "TRANSITION"} or (policy == "TRANSITION") != bool(event_ids):
            issues.append(f"{scene_id}_R624_CHANGE_POLICY_OR_EVENTS_INVALID")
        for event_id in event_ids:
            referenced_events.append(str(event_id))
            event = event_map.get(str(event_id))
            event_time = number(event.get("event_time_s")) if event else None
            if event is None or start is None or end is None or event_time is None or event_time < start - TOLERANCE or event_time > end + TOLERANCE:
                issues.append(f"{scene_id}_R624_EVENT_BINDING_INVALID:{event_id}")
    if duration is None or abs(previous_end - duration) > TOLERANCE:
        issues.append("R624_MACRO_SCENES_DO_NOT_COVER_FULL_SOURCE")
    if sorted(referenced_events) != sorted(event_map) or len(referenced_events) != len(set(referenced_events)):
        issues.append("R624_EVENTS_NOT_REFERENCED_EXACTLY_ONCE")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--events", default="artifacts/P2/SOURCE_EVENT_EVIDENCE_R624.json")
    parser.add_argument("--macro", default="artifacts/P2/SOURCE_MACRO_SCENE_EVIDENCE.json")
    parser.add_argument("--job", default="artifacts/P1/JOB.json")
    args = parser.parse_args()
    try:
        project = args.project_dir.resolve()
        event_path = project_file(project, args.events, "R624_SOURCE_EVENT_EVIDENCE", [])
        macro_path = project_file(project, args.macro, "SOURCE_MACRO_SCENE_EVIDENCE", [])
        job_path = project_file(project, args.job, "JOB", [])
        if event_path is None or macro_path is None or job_path is None:
            raise ValueError("R624_REQUIRED_P2_FILE_MISSING")
        issues = validate(project, event_path, macro_path, job_path)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
