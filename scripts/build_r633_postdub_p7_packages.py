#!/usr/bin/env python3
"""Build and bind P7 packages for narration-master M2-D projects."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from compile_r623_video_prompt import compile_segment
from r62_project import STATE_NAME, command_bind, load_json, normalize_project_relative, sha256_file, write_json_atomic
from r625_p7_timing import canonical_sha256, derive_timing_nodes
from r628_p6_qc_authority import resolve_authoritative_passed_qc
from r634_integrity_contract import require_state_flow_receipt, resolve_effective_inputs
from validate_r623_video_prompt_audit import validate as validate_prompt_audit
from validate_r62_segment_package import validate as validate_segment_package
from r635_source_content_lock import validate_project as validate_r635_source_content_project


SUPPORTED_ROUTES = {"M2_D_SHARE_FIRST"}
SUPPORTED_VERSIONS = {"R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}


def clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def exactly_one(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise ValueError(f"{label}_MISSING_OR_DUPLICATE")
    return rows[0]


def project_file(project: Path, relative: str) -> Path:
    normalized = normalize_project_relative(relative)
    path = (project / normalized).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{normalized}")
    return path


def validate_postdub_mode(state: dict[str, Any], job: dict[str, Any], narration: dict[str, Any]) -> None:
    route = job.get("route_id")
    if route not in SUPPORTED_ROUTES:
        raise ValueError("POSTDUB_P7_ROUTE_INVALID")
    if job.get("target", {}).get("audio_variant") != "POST_DUB_NARRATION":
        raise ValueError("POSTDUB_P7_JOB_AUDIO_VARIANT_INVALID")
    if state.get("runtime_policy", {}).get("audio_pipeline") != "POST_DUB_NARRATION":
        raise ValueError("POSTDUB_P7_RUNTIME_AUDIO_POLICY_NOT_RECONCILED")
    if narration.get("schema_version") != "R6.15-NARRATION-PLAN-1.0":
        raise ValueError("POSTDUB_P7_NARRATION_PLAN_SCHEMA_INVALID")
    if narration.get("audio_variant") != "POST_DUB_NARRATION" or narration.get("timing_authority") != "NARRATION_MASTER":
        raise ValueError("POSTDUB_P7_NARRATION_AUTHORITY_INVALID")


def bind(project: Path, name: str, relative: str, validator: str) -> None:
    command_bind(Namespace(
        project_dir=project,
        phase="P7",
        name=name,
        path=relative,
        validator=validator,
        validation_status="VALIDATED",
    ))


def passed_grid(state: dict[str, Any], project: Path, grid_id: str, segment_id: str) -> tuple[str, str, str, str]:
    rows = [
        row for row in state.get("qc_records", [])
        if isinstance(row, dict)
        and row.get("grid_id") == grid_id
        and row.get("segment_id") == segment_id
        and row.get("decision") == "PASSED"
    ]
    row = exactly_one(rows, f"{grid_id}_PASSED_QC")
    qc_rel = normalize_project_relative(row.get("qc_relative_path"))
    qc_path = project_file(project, qc_rel)
    if row.get("qc_sha256") != sha256_file(qc_path):
        raise ValueError(f"{grid_id}_PASSED_QC_LEDGER_HASH_MISMATCH")
    qc = load_json(qc_path)
    output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
    grid_rel = normalize_project_relative(output.get("relative_path"))
    grid_path = project_file(project, grid_rel)
    grid_sha = sha256_file(grid_path)
    if output.get("sha256") != grid_sha:
        raise ValueError(f"{grid_id}_PASSED_GRID_HASH_MISMATCH")
    authoritative_rel, authoritative_path, _, authority = resolve_authoritative_passed_qc(
        project,
        state,
        grid_id=grid_id,
        segment_id=segment_id,
        grid_relative_path=grid_rel,
        grid_sha256=grid_sha,
    )
    if authoritative_path != qc_path or authoritative_rel != qc_rel:
        raise ValueError(f"{grid_id}_PASSED_QC_NOT_AUTHORITATIVE")
    return grid_rel, grid_sha, qc_rel, authority


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        state_path = project / STATE_NAME
        state = load_json(state_path)
        if state.get("skill_version") not in SUPPORTED_VERSIONS:
            raise ValueError("R630_TO_R633_PROJECT_VERSION_REQUIRED")
        if state.get("pending_qc_submission_id") is not None:
            raise ValueError("P6_PENDING_QC_BLOCKS_P7")
        if state.get("resume_contract", {}).get("provider_call_authorized") is not False:
            raise ValueError("P7_BUILD_REQUIRES_NO_AUTHORIZED_PROVIDER_CALL")

        effective_job_path, effective_plan_path, fact_lineage = resolve_effective_inputs(project, state)
        job_rel = effective_job_path.relative_to(project).as_posix()
        plan_rel = effective_plan_path.relative_to(project).as_posix()
        narration_rel = "artifacts/P3/NARRATION_PLAN.json"
        job_path = project_file(project, job_rel)
        plan_path = project_file(project, plan_rel)
        narration_path = project_file(project, narration_rel)
        job = load_json(job_path)
        plan = load_json(plan_path)
        narration = load_json(narration_path)
        state_flow_receipt = (
            require_state_flow_receipt(project, state, effective_plan_path)
            if state.get("skill_version") in {"R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}
            else None
        )
        if state.get("skill_version") in {"R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
            content_issues = validate_r635_source_content_project(project, "p3")
            if content_issues:
                raise ValueError("R635_SOURCE_CONTENT_INVALID:" + ",".join(content_issues))
        validate_postdub_mode(state, job, narration)

        voice = narration.get("voice_lock") if isinstance(narration.get("voice_lock"), dict) else {}
        timing_rows = {
            clean(row.get("segment_id")): row
            for row in narration.get("segments", [])
            if isinstance(row, dict) and clean(row.get("segment_id"))
        }
        scenes = {
            clean(row.get("scene_id")): row
            for row in plan.get("scenes", [])
            if isinstance(row, dict) and clean(row.get("scene_id"))
        }
        grids = {
            clean(row.get("grid_id")): row
            for row in plan.get("grids", [])
            if isinstance(row, dict) and clean(row.get("grid_id"))
        }
        segments = sorted(
            [row for row in plan.get("video_segments", []) if isinstance(row, dict)],
            key=lambda row: row.get("segment_order", 0),
        )
        if not segments:
            raise ValueError("P7_VIDEO_SEGMENTS_MISSING")

        outputs: list[dict[str, Any]] = []
        staged_bindings: list[tuple[str, str, str]] = []
        extractor = Path(__file__).resolve().parent / "extract_r62_start_cell.py"
        for segment in segments:
            segment_id = clean(segment.get("segment_id"))
            grid_id = clean(segment.get("grid_id"))
            grid = grids.get(grid_id)
            timing = timing_rows.get(segment_id)
            scene_ids = segment.get("scene_ids") if isinstance(segment.get("scene_ids"), list) else []
            scene = scenes.get(clean(scene_ids[0])) if len(scene_ids) == 1 else None
            if not segment_id or not grid_id or grid is None or timing is None or scene is None:
                raise ValueError(f"{segment_id or 'UNKNOWN'}_P7_INPUT_BINDING_MISSING")
            if timing.get("start_s") != segment.get("target_start_s") or timing.get("end_s") != segment.get("target_end_s"):
                raise ValueError(f"{segment_id}_P3_P4_TIME_MISMATCH")

            grid_rel, grid_sha, qc_rel, qc_authority = passed_grid(state, project, grid_id, segment_id)
            qc_path = project_file(project, qc_rel)
            start_rel = f"artifacts/P7/{segment_id}_START_CELL01.png"
            crop_rel = f"artifacts/P7/{segment_id}_START_CELL01_CROP_RECEIPT.json"
            crop = subprocess.run(
                [
                    sys.executable, "-B", str(extractor), "--project-dir", str(project),
                    "--grid", grid_rel, "--layout", clean(grid.get("layout")),
                    "--output", start_rel, "--receipt", crop_rel,
                ],
                capture_output=True,
                check=False,
            )
            if crop.returncode != 0:
                raise ValueError(f"{segment_id}_START_CELL_CROP_FAILED:" + crop.stderr.decode("utf-8", errors="replace"))
            crop_receipt = load_json(project_file(project, crop_rel))

            spatial = scene.get("spatial_contract") if isinstance(scene.get("spatial_contract"), dict) else {}
            action_path = spatial.get("action_path") if isinstance(spatial.get("action_path"), dict) else {}
            decisive = spatial.get("decisive_contact") if isinstance(spatial.get("decisive_contact"), dict) else {}
            cameras = spatial.get("camera_proofs") if isinstance(spatial.get("camera_proofs"), list) else []
            grid_cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
            first_cell = exactly_one([row for row in grid_cells if isinstance(row, dict) and row.get("cell") == 1], f"{grid_id}_CELL_1")
            forbidden = [clean(item) for item in scene.get("forbidden_alternatives", []) if clean(item)]
            forbidden.append("不得把宫格、分屏、格线、字幕、编号、Logo或水印生成到视频中")
            timing_source, timing_nodes = derive_timing_nodes(plan, segment)

            bootstrap_rel = f"artifacts/P7/{segment_id}_VIDEO_PROMPT_BOOTSTRAP.txt"
            bootstrap = (
                f"Generate one full-frame 9:16 video for {segment_id}. "
                f"Initial state: {clean(first_cell.get('visual_statement'))} "
                f"Action: {clean(scene.get('large_action'))} "
                f"Visible result: {clean(scene.get('visible_result'))} "
                "No collage, split screen, text, subtitles, logo or watermark.\n"
            )
            write_json_atomic(project / f"artifacts/P7/{segment_id}_P7_BOOTSTRAP_AUDIT.json", {
                "schema_version": "R6.33-P7-BOOTSTRAP-1.0",
                "status": "PASSED",
                "segment_id": segment_id,
                "external_calls": 0,
            })
            (project / bootstrap_rel).write_text(bootstrap, encoding="utf-8", newline="\n")
            source_package_rel = f"artifacts/P7/{segment_id}_SEGMENT_PACKAGE.json"
            source_package = {
                "schema_version": "R6.2-P7-SEGMENT-PACKAGE-1.0",
                "job_id": state.get("project_id"),
                "phase": "P7",
                "status": "WAIT_REVIEW_BEFORE_P8",
                "segment_id": segment_id,
                "scene_ids": scene_ids,
                "grid_id": grid_id,
                "grid_role": "SEGMENT_ACTION_AUTHORITY",
                "target_start_s": segment.get("target_start_s"),
                "target_end_s": segment.get("target_end_s"),
                "duration_authority": "COMPLETE_ACTION_AND_SPEECH_SPAN",
                "fixed_time_slice": False,
                "grid_asset": {"relative_path": grid_rel, "sha256": grid_sha},
                "start_frame_derivative": {
                    "relative_path": start_rel,
                    "sha256": sha256_file(project_file(project, start_rel)),
                    "source_grid_relative_path": grid_rel,
                    "source_grid_sha256": grid_sha,
                    "source_cell": 1,
                    "method": "DETERMINISTIC_CROP_NO_GENERATION",
                    "crop_receipt_relative_path": crop_rel,
                    "crop_receipt_sha256": sha256_file(project_file(project, crop_rel)),
                },
                "video_prompt": {
                    "relative_path": bootstrap_rel,
                    "sha256": sha256_file(project_file(project, bootstrap_rel)),
                    "audio_variant": "POST_DUB_NARRATION",
                },
                "action_contract": {
                    "initial_state": clean(first_cell.get("visual_statement")),
                    "large_action_path": clean(scene.get("large_action")),
                    "unique_interaction_channel": clean(action_path.get("description")),
                    "decisive_action": clean(decisive.get("description")),
                    "visible_result": clean(scene.get("visible_result")),
                    "camera": clean(scene.get("setting")) + "；" + "；".join(clean(item) for item in cameras if clean(item)),
                    "continuity_in": clean(segment.get("continuity_in")),
                    "continuity_out": clean(segment.get("continuity_out")),
                    "forbidden_alternatives": forbidden,
                },
                "audio_contract": {
                    "variant": "POST_DUB_NARRATION",
                    "spoken_copy": timing.get("spoken_copy"),
                    "target_start_s": segment.get("target_start_s"),
                    "target_end_s": segment.get("target_end_s"),
                    "planned_duration_s": timing.get("creative_target_duration_s"),
                    "timing_authority": narration.get("timing_authority"),
                    "voice_provider": voice.get("provider"),
                    "voice_id": voice.get("voice_id"),
                    "voice_speed": voice.get("global_speed"),
                    "timing_manifest": {"relative_path": narration_rel, "sha256": sha256_file(narration_path)},
                    "speaker_mode": "POST_PRODUCTION_VOICEOVER",
                    "lip_sync_policy": "NOT_APPLICABLE",
                    "generated_audio_policy": "IGNORE_OR_REPLACE",
                },
                "lineage": {
                    "job_relative_path": job_rel,
                    "job_sha256": sha256_file(job_path),
                    "scene_plan_relative_path": plan_rel,
                    "scene_plan_sha256": sha256_file(plan_path),
                    "narration_timing_relative_path": narration_rel,
                    "narration_timing_sha256": sha256_file(narration_path),
                    "p6_qc_relative_path": qc_rel,
                    "p6_qc_sha256": sha256_file(qc_path),
                    "accepted_deviation_fact_contracts": fact_lineage,
                    "segment_state_flow_audit": state_flow_receipt,
                },
                "provider_adapter_profile": job.get("provider_adapter_profile"),
                "p8_submission_authorized": False,
            }
            source_path = project / source_package_rel
            write_json_atomic(source_path, source_package)
            source_issues = validate_segment_package(project, source_path)
            if source_issues:
                raise ValueError(f"{segment_id}_SOURCE_PACKAGE_INVALID:" + ",".join(source_issues))
            compiled = compile_segment(project, segment_id)
            upgraded_rel = compiled["upgraded_segment_package_relative_path"]
            upgraded_path = project_file(project, upgraded_rel)
            audit_issues = validate_prompt_audit(project, upgraded_path)
            if audit_issues:
                raise ValueError(f"{segment_id}_VIDEO_PROMPT_AUDIT_INVALID:" + ",".join(audit_issues))

            outputs.append({
                "segment_id": segment_id,
                "grid_id": grid_id,
                "p6_qc_authority": qc_authority,
                "timing_source": timing_source,
                "timing_nodes_sha256": canonical_sha256(timing_nodes),
                "start_frame_relative_path": start_rel,
                "start_frame_sha256": sha256_file(project_file(project, start_rel)),
                "segment_package_relative_path": upgraded_rel,
                "segment_package_sha256": sha256_file(upgraded_path),
                "video_prompt_relative_path": compiled["prompt_relative_path"],
                "video_prompt_sha256": compiled["prompt_sha256"],
                "video_prompt_character_count": compiled["prompt_character_count"],
                "video_prompt_audit_relative_path": compiled["audit_relative_path"],
                "video_prompt_audit_sha256": sha256_file(project_file(project, compiled["audit_relative_path"])),
            })
            staged_bindings.extend([
                (f"{segment_id}_START_CELL01", start_rel, "extract_r62_start_cell.py"),
                (f"{segment_id}_START_CELL01_CROP_RECEIPT", crop_rel, "extract_r62_start_cell.py"),
                (f"{segment_id}_VIDEO_PROMPT_R623", compiled["prompt_relative_path"], "compile_r623_video_prompt.py"),
                (f"{segment_id}_VIDEO_PROMPT_AUDIT_R623", compiled["audit_relative_path"], "validate_r623_video_prompt_audit.py"),
                (f"{segment_id}_SEGMENT_PACKAGE_R623", upgraded_rel, "validate_r623_video_prompt_audit.py"),
            ])

        receipt_rel = "artifacts/P7/P7_BUILD_R633_POSTDUB.json"
        receipt = {
            "schema_version": "R6.33-P7-POSTDUB-BUILD-1.0",
            "status": "PASSED",
            "job_id": state.get("project_id"),
            "route_id": job.get("route_id"),
            "audio_variant": "POST_DUB_NARRATION",
            "timing_authority": "NARRATION_MASTER",
            "segments": outputs,
            "accepted_deviation_fact_contracts": fact_lineage,
            "segment_state_flow_audit": state_flow_receipt,
            "external_calls": 0,
            "additional_images": 0,
            "additional_provider_tasks": 0,
        }
        write_json_atomic(project / receipt_rel, receipt)
        for name, relative, validator in staged_bindings:
            bind(project, name, relative, validator)
        bind(project, "P7_BUILD_R633_POSTDUB", receipt_rel, "build_r633_postdub_p7_packages.py")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "issues": [str(exc)], "external_calls": 0}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
