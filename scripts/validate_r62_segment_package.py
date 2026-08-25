#!/usr/bin/env python3
"""Validate one R6.2 scene-scoped grid to video-segment package."""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file
from validate_r614_narration_timing import validate as validate_narration_timing
from validate_r615_narration_plan import validate as validate_narration_plan
from validate_r617_source_audio_plan import validate as validate_source_audio_plan
from r628_p6_qc_authority import resolve_authoritative_passed_qc


HEX64 = re.compile(r"^[0-9a-f]{64}$")
LAYOUT_SIDE = {"2x2": 2, "3x3": 3, "4x4": 4, "5x5": 5}


def _png_dimensions(path: Path) -> list[int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"PNG_HEADER_INVALID:{path.name}")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError(f"PNG_DIMENSIONS_INVALID:{path.name}")
    return [width, height]


def _file(project: Path, block: dict[str, Any], path_key: str, hash_key: str, label: str, issues: list[str]) -> Path | None:
    try:
        relative = normalize_project_relative(str(block.get(path_key, "")))
    except ValueError:
        issues.append(f"{label}_PATH_INVALID")
        return None
    path = (project / relative).resolve()
    if not path.is_file():
        issues.append(f"{label}_MISSING")
        return None
    expected = str(block.get(hash_key, "")).lower()
    if not HEX64.fullmatch(expected) or sha256_file(path) != expected:
        issues.append(f"{label}_HASH_INVALID_OR_MISMATCH")
    return path


def validate(project: Path, package_path: Path) -> list[str]:
    issues: list[str] = []
    package = load_json(package_path)
    manifest = load_json(project / STATE_NAME)
    if package.get("schema_version") != "R6.2-P7-SEGMENT-PACKAGE-1.0":
        issues.append("SEGMENT_PACKAGE_SCHEMA_INVALID")
    if package.get("job_id") != manifest.get("project_id") or package.get("phase") != "P7":
        issues.append("SEGMENT_PACKAGE_PROJECT_OR_PHASE_INVALID")
    if package.get("status") != "WAIT_REVIEW_BEFORE_P8" or package.get("p8_submission_authorized") is not False:
        issues.append("SEGMENT_PACKAGE_STATUS_INVALID")
    segment_id = str(package.get("segment_id", ""))
    grid_id = str(package.get("grid_id", ""))
    if not segment_id or not grid_id or package.get("grid_role") != "SEGMENT_ACTION_AUTHORITY":
        issues.append("SEGMENT_GRID_BINDING_INVALID")
    if package.get("duration_authority") != "COMPLETE_ACTION_AND_SPEECH_SPAN" or package.get("fixed_time_slice") is not False:
        issues.append("SEGMENT_DURATION_AUTHORITY_INVALID")
    start = package.get("target_start_s")
    end = package.get("target_end_s")
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
        issues.append("SEGMENT_TIME_RANGE_INVALID")
    grid_path = _file(project, package.get("grid_asset", {}), "relative_path", "sha256", "GRID_ASSET", issues)
    derivative = package.get("start_frame_derivative") if isinstance(package.get("start_frame_derivative"), dict) else {}
    start_frame_path = _file(project, derivative, "relative_path", "sha256", "START_FRAME", issues)
    source_grid = _file(project, derivative, "source_grid_relative_path", "source_grid_sha256", "START_FRAME_SOURCE_GRID", issues)
    if grid_path is not None and source_grid is not None and grid_path != source_grid:
        issues.append("START_FRAME_SOURCE_GRID_DIFFERS_FROM_SEGMENT_GRID")
    if derivative.get("source_cell") != 1 or derivative.get("method") != "DETERMINISTIC_CROP_NO_GENERATION":
        issues.append("START_FRAME_DERIVATION_INVALID")
    crop_receipt_path = _file(
        project,
        derivative,
        "crop_receipt_relative_path",
        "crop_receipt_sha256",
        "START_FRAME_CROP_RECEIPT",
        issues,
    )
    video_prompt = package.get("video_prompt") if isinstance(package.get("video_prompt"), dict) else {}
    _file(project, video_prompt, "relative_path", "sha256", "VIDEO_PROMPT", issues)
    action = package.get("action_contract") if isinstance(package.get("action_contract"), dict) else {}
    for key in ("initial_state", "large_action_path", "unique_interaction_channel", "decisive_action", "visible_result", "camera", "continuity_in", "continuity_out"):
        if not isinstance(action.get(key), str) or not action.get(key).strip():
            issues.append(f"ACTION_CONTRACT_{key.upper()}_MISSING")
    if not isinstance(action.get("forbidden_alternatives"), list) or not action.get("forbidden_alternatives"):
        issues.append("ACTION_CONTRACT_FORBIDDEN_ALTERNATIVES_MISSING")
    lineage = package.get("lineage") if isinstance(package.get("lineage"), dict) else {}
    job_path = _file(project, lineage, "job_relative_path", "job_sha256", "JOB", issues)
    plan_path = _file(project, lineage, "scene_plan_relative_path", "scene_plan_sha256", "SCENE_PLAN", issues)
    qc_path = _file(project, lineage, "p6_qc_relative_path", "p6_qc_sha256", "P6_QC", issues)
    selected_grid: dict[str, Any] | None = None
    if plan_path is not None:
        plan = load_json(plan_path)
        segments = [row for row in plan.get("video_segments", []) if row.get("segment_id") == segment_id and row.get("grid_id") == grid_id]
        grids = [row for row in plan.get("grids", []) if row.get("grid_id") == grid_id and row.get("segment_id") == segment_id]
        if len(segments) != 1 or len(grids) != 1 or segments[0].get("scene_ids") != package.get("scene_ids"):
            issues.append("P4_SEGMENT_GRID_LINEAGE_MISMATCH")
        else:
            selected_grid = grids[0]
            if segments[0].get("target_start_s") != start or segments[0].get("target_end_s") != end:
                issues.append("P4_SEGMENT_TIME_RANGE_MISMATCH")
    if crop_receipt_path is not None:
        receipt = load_json(crop_receipt_path)
        if (
            receipt.get("schema_version") != "R6.2-START-CELL-CROP-RECEIPT-1.0"
            or receipt.get("status") != "PASSED"
            or receipt.get("method") != "DETERMINISTIC_CROP_NO_GENERATION"
            or receipt.get("source_cell") != 1
        ):
            issues.append("START_FRAME_CROP_RECEIPT_INVALID")
        if (
            receipt.get("source_grid_relative_path") != derivative.get("source_grid_relative_path")
            or receipt.get("source_grid_sha256") != derivative.get("source_grid_sha256")
            or receipt.get("output_relative_path") != derivative.get("relative_path")
            or receipt.get("output_sha256") != derivative.get("sha256")
        ):
            issues.append("START_FRAME_CROP_RECEIPT_LINEAGE_MISMATCH")
        if selected_grid is not None:
            layout = selected_grid.get("layout")
            side = LAYOUT_SIDE.get(layout)
            source_dimensions = receipt.get("source_dimensions_px")
            output_dimensions = receipt.get("output_dimensions_px")
            crop_box = receipt.get("crop_box_px")
            if receipt.get("layout") != layout or side is None:
                issues.append("START_FRAME_CROP_LAYOUT_MISMATCH")
            elif (
                not isinstance(source_dimensions, list)
                or len(source_dimensions) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in source_dimensions)
            ):
                issues.append("START_FRAME_SOURCE_DIMENSIONS_INVALID")
            else:
                expected_dimensions = [source_dimensions[0] // side, source_dimensions[1] // side]
                expected_crop = [0, 0, expected_dimensions[0], expected_dimensions[1]]
                expected_remainder = [source_dimensions[0] % side, source_dimensions[1] % side]
                if output_dimensions != expected_dimensions or crop_box != expected_crop:
                    issues.append("START_FRAME_CROP_GEOMETRY_INVALID")
                if (
                    receipt.get("division_policy") != "FLOOR_FROM_TOP_LEFT_ORIGIN"
                    or receipt.get("division_remainder_px") != expected_remainder
                    or receipt.get("remainder_placement") != "RIGHT_AND_BOTTOM_OUTSIDE_CELL1"
                    or receipt.get("resized") is not False
                ):
                    issues.append("START_FRAME_CROP_REMAINDER_CONTRACT_INVALID")
                try:
                    if grid_path is not None and _png_dimensions(grid_path) != source_dimensions:
                        issues.append("START_FRAME_SOURCE_ACTUAL_DIMENSIONS_MISMATCH")
                    if start_frame_path is not None and _png_dimensions(start_frame_path) != output_dimensions:
                        issues.append("START_FRAME_OUTPUT_ACTUAL_DIMENSIONS_MISMATCH")
                except ValueError as exc:
                    issues.append(str(exc))
    if qc_path is not None:
        qc = load_json(qc_path)
        if qc.get("decision") != "PASSED" or qc.get("grid_id") != grid_id or qc.get("segment_id") != segment_id:
            issues.append("P6_QC_NOT_PASSED_FOR_THIS_SEGMENT_GRID")
        if grid_path is not None:
            try:
                authoritative_rel, authoritative_path, _, _ = resolve_authoritative_passed_qc(
                    project, manifest, grid_id=grid_id, segment_id=segment_id,
                    grid_relative_path=str(package.get("grid_asset", {}).get("relative_path", "")),
                    grid_sha256=sha256_file(grid_path),
                )
                if qc_path != authoritative_path or lineage.get("p6_qc_relative_path") != authoritative_rel:
                    issues.append("P6_QC_NOT_AUTHORITATIVE_FOR_SEGMENT")
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                issues.append(f"P6_QC_AUTHORITY_INVALID:{exc}")
    audio = package.get("audio_contract") if isinstance(package.get("audio_contract"), dict) else {}
    variant = audio.get("variant")
    if variant not in {"POST_DUB_NARRATION", "A_NARRATION", "B_ONSCREEN_SPEECH", "SOURCE_AUDIO_REUSE"} or video_prompt.get("audio_variant") != variant:
        issues.append("SEGMENT_AUDIO_VARIANT_INVALID_OR_MISMATCH")
    if not isinstance(audio.get("spoken_copy"), str) or not audio.get("spoken_copy").strip():
        issues.append("SEGMENT_SPOKEN_COPY_MISSING")
    if audio.get("target_start_s") != start or audio.get("target_end_s") != end:
        issues.append("SEGMENT_AUDIO_TIME_RANGE_MISMATCH")
    if variant == "A_NARRATION" and (audio.get("speaker_mode"), audio.get("lip_sync_policy")) != ("VOICEOVER", "NOT_REQUIRED"):
        issues.append("NARRATION_AUDIO_POLICY_INVALID")
    if variant == "POST_DUB_NARRATION" and (
        (audio.get("speaker_mode"), audio.get("lip_sync_policy")) != ("POST_PRODUCTION_VOICEOVER", "NOT_APPLICABLE")
        or audio.get("generated_audio_policy") != "IGNORE_OR_REPLACE"
    ):
        issues.append("POST_DUB_AUDIO_POLICY_INVALID")
    if variant == "POST_DUB_NARRATION":
        narration_duration = audio.get("planned_duration_s", audio.get("measured_duration_s"))
        if (
            isinstance(narration_duration, bool)
            or not isinstance(narration_duration, (int, float))
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or abs(float(narration_duration) - (float(end) - float(start))) > 0.06
        ):
            issues.append("POST_DUB_NARRATION_DURATION_MISMATCH")
        timing_block = audio.get("timing_manifest") if isinstance(audio.get("timing_manifest"), dict) else {}
        timing_path = _file(project, timing_block, "relative_path", "sha256", "NARRATION_TIMING", issues)
        if (
            lineage.get("narration_timing_relative_path") != timing_block.get("relative_path")
            or lineage.get("narration_timing_sha256") != timing_block.get("sha256")
        ):
            issues.append("NARRATION_TIMING_LINEAGE_MISMATCH")
        if timing_path is not None:
            timing = load_json(timing_path)
            is_r615_plan = timing.get("schema_version") == "R6.15-NARRATION-PLAN-1.0"
            issues.extend(validate_narration_plan(project, timing_path) if is_r615_plan else validate_narration_timing(project, timing_path))
            timing_rows = [
                row for row in timing.get("segments", [])
                if isinstance(row, dict) and row.get("segment_id") == segment_id
            ]
            if len(timing_rows) != 1:
                issues.append("NARRATION_TIMING_SEGMENT_MISSING_OR_DUPLICATE")
            else:
                timing_row = timing_rows[0]
                voice = timing.get("voice_lock") if is_r615_plan and isinstance(timing.get("voice_lock"), dict) else timing.get("voice") if isinstance(timing.get("voice"), dict) else {}
                duration_key = "creative_target_duration_s" if is_r615_plan else "duration_s"
                if (
                    timing_row.get("spoken_copy") != audio.get("spoken_copy")
                    or timing_row.get("start_s") != start
                    or timing_row.get("end_s") != end
                    or timing_row.get(duration_key) != narration_duration
                ):
                    issues.append("SEGMENT_AUDIO_DIFFERS_FROM_NARRATION_PLAN")
                if (
                    audio.get("timing_authority") != timing.get("timing_authority")
                    or audio.get("voice_provider") != voice.get("provider")
                    or audio.get("voice_id") != voice.get("voice_id")
                    or audio.get("voice_speed") != voice.get("global_speed")
                ):
                    issues.append("SEGMENT_VOICE_LOCK_DIFFERS_FROM_TIMING_MANIFEST")
    if variant == "SOURCE_AUDIO_REUSE":
        if (
            (audio.get("speaker_mode"), audio.get("lip_sync_policy")) != ("ORIGINAL_SOURCE_AUDIO", "NOT_APPLICABLE")
            or audio.get("generated_audio_policy") != "MUTE"
            or audio.get("playback_speed") != 1.0
            or audio.get("timing_authority") != "SOURCE_AUDIO_MASTER"
        ):
            issues.append("SOURCE_AUDIO_REUSE_POLICY_INVALID")
        timing_block = audio.get("timing_manifest") if isinstance(audio.get("timing_manifest"), dict) else {}
        timing_path = _file(project, timing_block, "relative_path", "sha256", "SOURCE_AUDIO_PLAN", issues)
        source_audio_block = audio.get("source_audio") if isinstance(audio.get("source_audio"), dict) else {}
        source_audio_path = _file(project, source_audio_block, "relative_path", "sha256", "SOURCE_AUDIO", issues)
        if timing_path is not None and job_path is not None:
            issues.extend(validate_source_audio_plan(project, timing_path, job_path))
            timing = load_json(timing_path)
            timing_rows = [
                row for row in timing.get("segments", [])
                if isinstance(row, dict) and row.get("segment_id") == segment_id
            ]
            if len(timing_rows) != 1:
                issues.append("SOURCE_AUDIO_PLAN_SEGMENT_MISSING_OR_DUPLICATE")
            else:
                timing_row = timing_rows[0]
                if (
                    timing_row.get("exact_copy") != audio.get("spoken_copy")
                    or timing_row.get("start_s") != start
                    or timing_row.get("end_s") != end
                    or source_audio_block.get("relative_path") != timing.get("source_audio", {}).get("relative_path")
                    or source_audio_block.get("sha256") != timing.get("source_audio", {}).get("sha256")
                ):
                    issues.append("SEGMENT_AUDIO_DIFFERS_FROM_SOURCE_AUDIO_PLAN")
        if source_audio_path is None:
            issues.append("SOURCE_AUDIO_REUSE_ASSET_MISSING")
    if variant == "B_ONSCREEN_SPEECH" and (audio.get("speaker_mode"), audio.get("lip_sync_policy")) != ("ONSCREEN_PROTAGONIST", "MODEL_NATURAL_LIP_SYNC"):
        issues.append("ONSCREEN_SPEECH_AUDIO_POLICY_INVALID")
    if job_path is not None:
        job = load_json(job_path)
        runtime_policy = manifest.get("runtime_policy") if isinstance(manifest.get("runtime_policy"), dict) else {}
        job_variant = job.get("target", {}).get("audio_variant")
        runtime_pipeline = runtime_policy.get("audio_pipeline")
        if job_variant == "SOURCE_AUDIO_REUSE" and runtime_pipeline not in {None, "SOURCE_AUDIO_REUSE"}:
            issues.append("RUNTIME_AUDIO_POLICY_DIFFERS_FROM_LOCKED_P1")
        if job_variant == "POST_DUB_NARRATION" and runtime_pipeline not in {None, "POST_DUB_NARRATION"}:
            issues.append("RUNTIME_AUDIO_POLICY_DIFFERS_FROM_LOCKED_P1")
        allowed = {"POST_DUB_NARRATION"} if job_variant == "POST_DUB_NARRATION" else {"SOURCE_AUDIO_REUSE"} if job_variant == "SOURCE_AUDIO_REUSE" else {"A_NARRATION"} if job_variant == "A_NARRATION" else {"B_ONSCREEN_SPEECH"} if job_variant == "B_ONSCREEN_SPEECH" else {"A_NARRATION", "B_ONSCREEN_SPEECH"}
        if variant not in allowed:
            issues.append("SEGMENT_AUDIO_VARIANT_DIFFERS_FROM_P1")
        if variant == "POST_DUB_NARRATION" and audio.get("timing_authority") != job.get("target", {}).get("timing_authority"):
            issues.append("SEGMENT_TIMING_AUTHORITY_DIFFERS_FROM_P1")
        if variant == "SOURCE_AUDIO_REUSE" and audio.get("timing_authority") != job.get("target", {}).get("timing_authority"):
            issues.append("SEGMENT_TIMING_AUTHORITY_DIFFERS_FROM_P1")
    mode_lock = manifest.get("mode_lock") if isinstance(manifest.get("mode_lock"), dict) else {}
    if package.get("provider_adapter_profile") != mode_lock.get("provider_adapter_profile"):
        issues.append("SEGMENT_ADAPTER_DIFFERS_FROM_P1")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        package = (project / normalize_project_relative(args.package)).resolve()
        issues = validate(project, package)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
