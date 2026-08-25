#!/usr/bin/env python3
"""Build and validate an R6.28 zero-call source-event re-audit overlay."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from r62_project import load_json, normalize_project_relative, sha256_file, write_json_atomic
from validate_r624_source_event_evidence import validate as validate_r624_events


INPUT_SCHEMA = "R6.28-SOURCE-EVENT-REAUDIT-INPUT-1.0"


def _path(project: Path, relative: object, label: str) -> tuple[str, Path]:
    try:
        normalized = normalize_project_relative(str(relative or ""))
    except ValueError as exc:
        raise ValueError(f"{label}_PATH_INVALID") from exc
    path = (project / normalized).resolve()
    try:
        path.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}_PATH_ESCAPES_PROJECT") from exc
    if not path.is_file():
        raise ValueError(f"{label}_MISSING")
    return normalized, path


def _strip_event_fields(macro: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(macro)
    for scene in cleaned.get("macro_scenes", []):
        if isinstance(scene, dict):
            scene.pop("change_policy", None)
            scene.pop("source_event_ids", None)
    return cleaned


def build_bridge(project: Path, input_path: Path) -> dict[str, Any]:
    spec = load_json(input_path)
    job_rel, job_path = _path(project, "artifacts/P1/JOB.json", "JOB")
    macro_rel, macro_path = _path(project, "artifacts/P2/SOURCE_MACRO_SCENE_EVIDENCE.json", "SOURCE_MACRO")
    p3_rel, p3_path = _path(project, "artifacts/P3/SOURCE_AUDIO_PLAN.json", "SOURCE_AUDIO_PLAN")
    frame_rel, frame_path = _path(project, "artifacts/P2/source_evidence/FRAME_INDEX.json", "FRAME_INDEX")
    job = load_json(job_path)
    macro = load_json(macro_path)
    frame_index = load_json(frame_path)
    if spec.get("schema_version") != INPUT_SCHEMA or spec.get("status") != "REVIEWED":
        raise ValueError("R628_SOURCE_EVENT_INPUT_SCHEMA_OR_STATUS_INVALID")
    if spec.get("job_id") != job.get("job_id"):
        raise ValueError("R628_SOURCE_EVENT_INPUT_JOB_MISMATCH")
    bindings = spec.get("scene_event_bindings") if isinstance(spec.get("scene_event_bindings"), list) else []
    binding_map = {row.get("source_scene_id"): row for row in bindings if isinstance(row, dict)}
    scenes = [row for row in macro.get("macro_scenes", []) if isinstance(row, dict)]
    scene_ids = [row.get("source_scene_id") for row in scenes]
    if len(binding_map) != len(bindings) or sorted(binding_map) != sorted(scene_ids):
        raise ValueError("R628_SOURCE_EVENT_SCENE_BINDINGS_NOT_EXACT")
    overlay = copy.deepcopy(macro)
    for scene in overlay["macro_scenes"]:
        binding = binding_map[scene["source_scene_id"]]
        scene["change_policy"] = binding.get("change_policy")
        scene["source_event_ids"] = binding.get("source_event_ids")
    if _strip_event_fields(overlay) != macro:
        raise ValueError("R628_MACRO_OVERLAY_CHANGED_NON_EVENT_FIELDS")

    review = spec.get("review") if isinstance(spec.get("review"), dict) else {}
    event_doc = {
        "schema_version": "R6.24-P2-SOURCE-EVENT-EVIDENCE-1.0",
        "status": "VERIFIED",
        "job_id": job.get("job_id"),
        "source_video": {
            "relative_path": job.get("source", {}).get("video_path"),
            "sha256": job.get("source", {}).get("video_sha256"),
        },
        "frame_index": {"relative_path": frame_rel, "sha256": sha256_file(frame_path)},
        "review": review,
        "events": spec.get("events"),
    }
    overlay_rel = "artifacts/P2/SOURCE_MACRO_SCENE_EVIDENCE_R628_AUDITED.json"
    event_rel = "artifacts/P2/SOURCE_EVENT_EVIDENCE_R628.json"
    write_json_atomic(project / overlay_rel, overlay)
    write_json_atomic(project / event_rel, event_doc)
    issues = validate_r624_events(project, project / event_rel, project / overlay_rel, job_path)
    if issues:
        raise ValueError("R628_SOURCE_EVENT_REAUDIT_INVALID:" + ",".join(issues))
    return {
        "schema_version": "R6.28-SOURCE-EVENT-REAUDIT-BRIDGE-1.0",
        "status": "PASSED",
        "source_input_sha256": sha256_file(input_path),
        "source_macro_relative_path": macro_rel,
        "source_macro_sha256": sha256_file(macro_path),
        "audited_macro_relative_path": overlay_rel,
        "audited_macro_sha256": sha256_file(project / overlay_rel),
        "event_evidence_relative_path": event_rel,
        "event_evidence_sha256": sha256_file(project / event_rel),
        "source_audio_plan_relative_path": p3_rel,
        "source_audio_plan_sha256": sha256_file(p3_path),
        "job_relative_path": job_rel,
        "job_sha256": sha256_file(job_path),
        "allowed_overlay_fields": ["change_policy", "source_event_ids"],
        "provider_calls": 0,
    }


def validate_bridge(project: Path, state: dict[str, Any], plan_path: Path, macro_path: Path, job_path: Path) -> list[str]:
    issues: list[str] = []
    historical = state.get("historical_revision") if isinstance(state.get("historical_revision"), dict) else {}
    record = historical.get("r628_source_event_reaudit") if isinstance(historical.get("r628_source_event_reaudit"), dict) else {}
    try:
        if record.get("status") != "PASSED" or record.get("provider_calls") != 0:
            raise ValueError("R628_SOURCE_EVENT_BRIDGE_STATUS_INVALID")
        if record.get("source_audio_plan_sha256") != sha256_file(plan_path) or record.get("source_macro_sha256") != sha256_file(macro_path) or record.get("job_sha256") != sha256_file(job_path):
            raise ValueError("R628_SOURCE_EVENT_BRIDGE_UPSTREAM_HASH_MISMATCH")
        _, overlay_path = _path(project, record.get("audited_macro_relative_path"), "R628_AUDITED_MACRO")
        _, event_path = _path(project, record.get("event_evidence_relative_path"), "R628_EVENT_EVIDENCE")
        if record.get("audited_macro_sha256") != sha256_file(overlay_path) or record.get("event_evidence_sha256") != sha256_file(event_path):
            raise ValueError("R628_SOURCE_EVENT_BRIDGE_ARTIFACT_HASH_MISMATCH")
        original = load_json(macro_path)
        overlay = load_json(overlay_path)
        if _strip_event_fields(overlay) != original:
            raise ValueError("R628_MACRO_OVERLAY_CHANGED_NON_EVENT_FIELDS")
        event_issues = validate_r624_events(project, event_path, overlay_path, job_path)
        if event_issues:
            raise ValueError("R628_SOURCE_EVENT_REAUDIT_INVALID:" + ",".join(event_issues))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        issues.append(str(exc))
    return issues
