#!/usr/bin/env python3
"""Validate M2-F source audio/copy plus source-video macro-scene inheritance."""

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
from validate_r620_source_macro_scene_evidence import validate as validate_macro_scene_evidence
from validate_r624_source_event_evidence import validate as validate_r624_source_events
from r628_source_event_bridge import validate_bridge as validate_r628_source_event_bridge


ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "assets" / "m2-f-source-audio-restyle-profile.json"
TOLERANCE = 0.08


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


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


def _duration_with_pyav(path: Path) -> float:
    try:
        import av
    except ImportError as exc:
        raise ValueError("PYAV_REQUIRED_FOR_SOURCE_AUDIO_DURATION") from exc
    with av.open(str(path)) as container:
        if container.duration is not None:
            return float(container.duration) / 1_000_000.0
        streams = [stream for stream in container.streams if stream.type == "audio"]
        if not streams or streams[0].duration is None or streams[0].time_base is None:
            raise ValueError("SOURCE_AUDIO_DURATION_UNAVAILABLE")
        return float(streams[0].duration * streams[0].time_base)


def validate(project: Path, plan_path: Path, job_path: Path, scene_plan_path: Path | None = None) -> list[str]:
    issues: list[str] = []
    profile = load_json(PROFILE_PATH)
    job = load_json(job_path)
    plan = load_json(plan_path)

    issues.extend(f"JOB:{issue}" for issue in validate_job(job))
    if profile.get("profile_id") != "M2_F_SOURCE_AUDIO_SCENE_RESTYLE_V2":
        issues.append("M2F_PROFILE_INVALID")
    capability = profile.get("imagegen_capability_contract") if isinstance(profile.get("imagegen_capability_contract"), dict) else {}
    if capability != {
        "tool": "BUILT_IN_IMAGEGEN",
        "profile_id": "CODEX_BUILT_IN_IMAGEGEN_PROMPT_ONLY",
        "geometry_enforcement": "FLEXIBLE_REFERENCE",
        "prompt_only_control_allowed": True,
        "exact_pixels_claimed": False,
        "canvas_and_cell_aspect_are_composition_guidance": True,
        "downstream_start_cell_crop_is_deterministic": True,
        "post_generation_geometry_verification_required": True,
    }:
        issues.append("M2F_IMAGEGEN_CAPABILITY_CONTRACT_INVALID")
    if plan.get("schema_version") != "R6.20-SOURCE-AUDIO-SCENE-PLAN-2.0":
        issues.append("SOURCE_AUDIO_PLAN_SCHEMA_INVALID")
    if plan.get("status") != "LOCKED":
        issues.append("SOURCE_AUDIO_PLAN_NOT_LOCKED")
    if plan.get("job_id") != job.get("job_id"):
        issues.append("SOURCE_AUDIO_PLAN_JOB_MISMATCH")
    if plan.get("route_id") != "M2_F_SOURCE_AUDIO_RESTYLE" or job.get("route_id") != plan.get("route_id"):
        issues.append("SOURCE_AUDIO_PLAN_ROUTE_INVALID")
    if plan.get("timing_authority") != "SOURCE_AUDIO_MASTER":
        issues.append("SOURCE_AUDIO_TIMING_AUTHORITY_INVALID")

    contract = job.get("source_audio_copy_contract") if isinstance(job.get("source_audio_copy_contract"), dict) else {}
    audio = plan.get("source_audio") if isinstance(plan.get("source_audio"), dict) else {}
    copy_block = plan.get("source_copy") if isinstance(plan.get("source_copy"), dict) else {}
    visual = plan.get("visual_authority") if isinstance(plan.get("visual_authority"), dict) else {}
    alignment = plan.get("alignment") if isinstance(plan.get("alignment"), dict) else {}
    macro_lineage = plan.get("source_macro_scene_evidence") if isinstance(plan.get("source_macro_scene_evidence"), dict) else {}

    if audio.get("relative_path") != contract.get("audio_relative_path") or audio.get("sha256") != contract.get("audio_sha256"):
        issues.append("SOURCE_AUDIO_PLAN_DIFFERS_FROM_P1")
    if copy_block.get("relative_path") != contract.get("copy_relative_path") or copy_block.get("sha256") != contract.get("copy_sha256"):
        issues.append("SOURCE_COPY_PLAN_DIFFERS_FROM_P1")
    if audio.get("playback_speed") != 1.0 or audio.get("asset_usage") != "FULL_TRACK_UNCHANGED":
        issues.append("SOURCE_AUDIO_MUST_BE_FULL_TRACK_AT_1X")
    if copy_block.get("encoding") != "UTF-8" or copy_block.get("policy") != "VERBATIM_NO_REWRITE":
        issues.append("SOURCE_COPY_POLICY_INVALID")
    if visual != {
        "derivation": "SOURCE_AUDIO_COPY_PLUS_SOURCE_VIDEO_MACRO_SCENES",
        "source_video_semantics": "MACRO_SCENE_ACTION_CAUSAL_ONLY",
        "source_video_pixels_camera_cuts": "NO_AUTHORITY",
        "source_video_keyframes_as_generation_input": False,
    }:
        issues.append("SOURCE_VIDEO_SEMANTIC_AUTHORITY_SCOPE_INVALID")
    if alignment.get("method") != "LOCAL_ASR_PLUS_VERBATIM_COPY_ALIGNMENT" or alignment.get("status") != "VERIFIED":
        issues.append("SOURCE_AUDIO_ALIGNMENT_NOT_VERIFIED")
    uncertainties = alignment.get("uncertainties")
    if not isinstance(uncertainties, list) or uncertainties:
        issues.append("SOURCE_AUDIO_ALIGNMENT_UNCERTAINTY_BLOCKS_PLAN")

    audio_path = _project_file(project, audio.get("relative_path"), "SOURCE_AUDIO", issues)
    copy_path = _project_file(project, copy_block.get("relative_path"), "SOURCE_COPY", issues)
    if audio_path is not None:
        if sha256_file(audio_path) != str(audio.get("sha256", "")).lower():
            issues.append("SOURCE_AUDIO_HASH_MISMATCH")
        try:
            actual_duration = _duration_with_pyav(audio_path)
        except (OSError, ValueError) as exc:
            issues.append(str(exc))
            actual_duration = None
    else:
        actual_duration = None
    planned_duration = _number(audio.get("duration_s"))
    if planned_duration is None or planned_duration <= 0:
        issues.append("SOURCE_AUDIO_DURATION_INVALID")
    if actual_duration is not None and planned_duration is not None and abs(actual_duration - planned_duration) > TOLERANCE:
        issues.append("SOURCE_AUDIO_DURATION_MISMATCH")
    target_duration = _number(job.get("target", {}).get("duration_s"))
    if planned_duration is None or target_duration is None or abs(planned_duration - target_duration) > TOLERANCE:
        issues.append("TARGET_DURATION_MUST_EQUAL_SOURCE_AUDIO")

    copy_text = ""
    if copy_path is not None:
        try:
            copy_text = copy_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append("SOURCE_COPY_NOT_UTF8")
        if sha256_file(copy_path) != str(copy_block.get("sha256", "")).lower():
            issues.append("SOURCE_COPY_HASH_MISMATCH")
    if not copy_text:
        issues.append("SOURCE_COPY_EMPTY")

    macro_path = _project_file(project, macro_lineage.get("relative_path"), "SOURCE_MACRO_SCENE_EVIDENCE", issues)
    macro_evidence: dict[str, Any] = {}
    macro_scene_map: dict[str, dict[str, Any]] = {}
    if macro_path is not None:
        if macro_lineage.get("sha256") != sha256_file(macro_path):
            issues.append("SOURCE_MACRO_SCENE_LINEAGE_HASH_MISMATCH")
        else:
            macro_evidence = load_json(macro_path)
            issues.extend(f"P2_MACRO:{item}" for item in validate_macro_scene_evidence(project, macro_path, job_path))
            macro_scene_map = {
                row.get("source_scene_id"): row
                for row in macro_evidence.get("macro_scenes", [])
                if isinstance(row, dict) and _text(row.get("source_scene_id"))
            }
    state_path = project / "R62_PROJECT.json"
    state = load_json(state_path) if state_path.is_file() else {}
    if state.get("skill_version") in {"R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
        event_lineage = plan.get("source_event_evidence") if isinstance(plan.get("source_event_evidence"), dict) else {}
        if state.get("skill_version") in {"R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"} and not event_lineage and macro_path is not None:
            issues.extend(validate_r628_source_event_bridge(project, state, plan_path, macro_path, job_path))
        else:
            event_path = _project_file(project, event_lineage.get("relative_path"), "R624_SOURCE_EVENT_EVIDENCE", issues)
            if event_path is None or macro_path is None:
                issues.append("R624_SOURCE_EVENT_LINEAGE_REQUIRED")
            else:
                if event_lineage.get("sha256") != sha256_file(event_path):
                    issues.append("R624_SOURCE_EVENT_LINEAGE_HASH_MISMATCH")
                issues.extend(f"P2_EVENT:{item}" for item in validate_r624_source_events(project, event_path, macro_path, job_path))

    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        issues.append("SOURCE_AUDIO_SEGMENTS_MISSING")
        segments = []
    previous_end = 0.0
    previous_char_end = 0
    seen_ids: set[str] = set()
    segment_map: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(segments, start=1):
        if not isinstance(row, dict):
            issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_INVALID")
            continue
        segment_id = _text(row.get("segment_id")).strip()
        if not segment_id or segment_id in seen_ids:
            issues.append(f"SOURCE_AUDIO_SEGMENT_{index}_ID_INVALID_OR_DUPLICATE")
            continue
        seen_ids.add(segment_id)
        segment_map[segment_id] = row
        start = _number(row.get("start_s"))
        end = _number(row.get("end_s"))
        if start is None or end is None or end <= start:
            issues.append(f"{segment_id}_AUDIO_RANGE_INVALID")
        else:
            if abs(start - previous_end) > TOLERANCE:
                issues.append(f"{segment_id}_AUDIO_TIMELINE_GAP_OR_OVERLAP")
            previous_end = end
        char_start = row.get("copy_char_start")
        char_end = row.get("copy_char_end")
        if isinstance(char_start, bool) or isinstance(char_end, bool) or not isinstance(char_start, int) or not isinstance(char_end, int):
            issues.append(f"{segment_id}_COPY_RANGE_INVALID")
        elif char_start != previous_char_end or char_end <= char_start or char_end > len(copy_text):
            issues.append(f"{segment_id}_COPY_RANGE_GAP_OVERLAP_OR_BOUNDS")
        else:
            if row.get("exact_copy") != copy_text[char_start:char_end]:
                issues.append(f"{segment_id}_COPY_NOT_VERBATIM")
            previous_char_end = char_end
        if not isinstance(row.get("visual_obligations"), list) or not row.get("visual_obligations") or not all(_text(item).strip() for item in row.get("visual_obligations", [])):
            issues.append(f"{segment_id}_VISUAL_OBLIGATIONS_MISSING")
        source_scene_ids = row.get("source_scene_ids")
        if not isinstance(source_scene_ids, list) or not source_scene_ids or not all(_text(item) for item in source_scene_ids):
            issues.append(f"{segment_id}_SOURCE_SCENE_IDS_MISSING")
            source_scene_ids = []
        expected_contracts = [
            macro_scene_map[scene_id].get("action_contract")
            for scene_id in source_scene_ids
            if scene_id in macro_scene_map
        ]
        if len(expected_contracts) != len(source_scene_ids):
            issues.append(f"{segment_id}_UNKNOWN_SOURCE_SCENE_ID")
        if row.get("inherited_action_contracts") != expected_contracts:
            issues.append(f"{segment_id}_SOURCE_ACTION_CONTRACT_NOT_EXACTLY_INHERITED")
        for key in ("large_action", "visible_result"):
            if not _text(row.get(key)).strip():
                issues.append(f"{segment_id}_{key.upper()}_MISSING")
    if planned_duration is not None and segments and abs(previous_end - planned_duration) > TOLERANCE:
        issues.append("SOURCE_AUDIO_SEGMENTS_DO_NOT_COVER_FULL_TRACK")
    if copy_text and segments and previous_char_end != len(copy_text):
        issues.append("SOURCE_AUDIO_SEGMENTS_DO_NOT_COVER_FULL_COPY")

    if scene_plan_path is not None:
        scene_plan = load_json(scene_plan_path)
        if scene_plan.get("route_id") != "M2_F_SOURCE_AUDIO_RESTYLE" or scene_plan.get("timing_authority") != "SOURCE_AUDIO_MASTER":
            issues.append("P4_M2F_ROUTE_OR_TIMING_INVALID")
        source_timing = scene_plan.get("source_audio_timing") if isinstance(scene_plan.get("source_audio_timing"), dict) else {}
        try:
            plan_relative = plan_path.resolve().relative_to(project).as_posix()
        except ValueError:
            plan_relative = ""
        if source_timing.get("relative_path") != plan_relative or source_timing.get("sha256") != sha256_file(plan_path):
            issues.append("P4_SOURCE_AUDIO_PLAN_LINEAGE_MISMATCH")
        scenes = {row.get("scene_id"): row for row in scene_plan.get("scenes", []) if isinstance(row, dict)}
        p4_segments = scene_plan.get("video_segments") if isinstance(scene_plan.get("video_segments"), list) else []
        if len(p4_segments) != len(segment_map):
            issues.append("P4_SOURCE_AUDIO_SEGMENT_COUNT_MISMATCH")
        for row in p4_segments:
            if not isinstance(row, dict):
                continue
            source_id = row.get("source_audio_segment_id")
            source_row = segment_map.get(source_id)
            segment_id = _text(row.get("segment_id")).strip() or "UNKNOWN"
            if source_row is None or segment_id != source_id:
                issues.append(f"{segment_id}_P4_SOURCE_AUDIO_SEGMENT_ID_MISMATCH")
                continue
            start = _number(row.get("target_start_s"))
            end = _number(row.get("target_end_s"))
            duration = _number(row.get("source_audio_duration_s"))
            expected_duration = float(source_row["end_s"]) - float(source_row["start_s"])
            if start != source_row.get("start_s") or end != source_row.get("end_s") or duration is None or abs(duration - expected_duration) > TOLERANCE:
                issues.append(f"{segment_id}_P4_SOURCE_AUDIO_TIME_MISMATCH")
            scene_ids = row.get("scene_ids") if isinstance(row.get("scene_ids"), list) else []
            if len(scene_ids) != 1 or scene_ids[0] not in scenes:
                issues.append(f"{segment_id}_P4_SCENE_BINDING_INVALID")
                continue
            scene = scenes[scene_ids[0]]
            if scene.get("copy_or_audio") != source_row.get("exact_copy") or scene.get("authority") != "SOURCE_AUDIO_SCENE_SEMANTIC_BLUEPRINT":
                issues.append(f"{segment_id}_P4_COPY_OR_AUTHORITY_DRIFT")
            if scene.get("source_scene_ids") != source_row.get("source_scene_ids"):
                issues.append(f"{segment_id}_P4_SOURCE_SCENE_IDS_DRIFT")
            expected_intervals = [
                [macro_scene_map[scene_id].get("source_start_s"), macro_scene_map[scene_id].get("source_end_s")]
                for scene_id in source_row.get("source_scene_ids", [])
                if scene_id in macro_scene_map
            ]
            if scene.get("source_evidence_intervals") != expected_intervals:
                issues.append(f"{segment_id}_P4_SOURCE_SCENE_INTERVALS_DRIFT")
            if scene.get("inherited_action_contracts") != source_row.get("inherited_action_contracts"):
                issues.append(f"{segment_id}_P4_SOURCE_ACTION_CONTRACT_DRIFT")
            if scene.get("large_action") != source_row.get("large_action") or scene.get("visible_result") != source_row.get("visible_result"):
                issues.append(f"{segment_id}_P4_SOURCE_ACTION_OR_RESULT_DRIFT")
            forbidden = scene.get("forbidden_alternatives") if isinstance(scene.get("forbidden_alternatives"), list) else []
            required_forbidden = [
                item
                for contract_row in source_row.get("inherited_action_contracts", [])
                if isinstance(contract_row, dict)
                for item in contract_row.get("forbidden_substitutions", [])
            ]
            if any(item not in forbidden for item in required_forbidden):
                issues.append(f"{segment_id}_P4_FORBIDDEN_SUBSTITUTION_MISSING")
            obligations = row.get("content_obligations") if isinstance(row.get("content_obligations"), list) else []
            if any(item not in obligations for item in source_row.get("visual_obligations", [])):
                issues.append(f"{segment_id}_P4_VISUAL_OBLIGATION_MISSING")

    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--plan", default="artifacts/P3/SOURCE_AUDIO_PLAN.json")
    parser.add_argument("--job", default="artifacts/P1/JOB.json")
    parser.add_argument("--scene-plan")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        plan_path = _project_file(project, args.plan, "SOURCE_AUDIO_PLAN", [])
        job_path = _project_file(project, args.job, "JOB", [])
        if plan_path is None or job_path is None:
            raise ValueError("SOURCE_AUDIO_PLAN_OR_JOB_MISSING")
        scene_path = _project_file(project, args.scene_plan, "SCENE_PLAN", []) if args.scene_plan else None
        issues = validate(project, plan_path, job_path, scene_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
