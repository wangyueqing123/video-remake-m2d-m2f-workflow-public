#!/usr/bin/env python3
"""Build and bind all R6.25 P7 packages from current validated project artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

from compile_r623_video_prompt import compile_segment
from r62_project import STATE_NAME, command_bind, load_json, sha256_file, write_json_atomic
from r625_p7_timing import canonical_sha256, derive_timing_nodes
from r628_p6_qc_authority import resolve_authoritative_passed_qc
from r634_integrity_contract import require_state_flow_receipt, resolve_effective_inputs
from validate_r623_video_prompt_audit import validate as validate_prompt_audit
from validate_r62_segment_package import validate as validate_segment_package

sys.dont_write_bytecode = True


def clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def exactly_one(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise ValueError(f"{label}_MISSING_OR_DUPLICATE")
    return rows[0]


def project_file(project: Path, relative: str) -> Path:
    path = (project / relative).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{relative}")
    return path


def bind(project: Path, name: str, relative: str, validator: str) -> None:
    command_bind(Namespace(
        project_dir=project,
        phase="P7",
        name=name,
        path=relative,
        validator=validator,
        validation_status="VALIDATED",
    ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        state = load_json(project / STATE_NAME)
        if state.get("skill_version") not in {"R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
            raise ValueError("R625_TO_R628_PROJECT_VERSION_REQUIRED")
        if state.get("pending_qc_submission_id") is not None:
            raise ValueError("P6_PENDING_QC_BLOCKS_P7")
        if state.get("runtime_policy", {}).get("audio_pipeline") != "SOURCE_AUDIO_REUSE":
            raise ValueError("P7_RUNTIME_AUDIO_POLICY_NOT_RECONCILED")

        effective_job_path, effective_plan_path, fact_lineage = resolve_effective_inputs(project, state)
        job_rel = effective_job_path.relative_to(project).as_posix()
        plan_rel = effective_plan_path.relative_to(project).as_posix()
        job_path = project_file(project, job_rel)
        timing_path = project_file(project, "artifacts/P3/SOURCE_AUDIO_PLAN.json")
        plan_path = project_file(project, plan_rel)
        job = load_json(job_path)
        timing = load_json(timing_path)
        plan = load_json(plan_path)
        state_flow_receipt = (
            require_state_flow_receipt(project, state, effective_plan_path)
            if state.get("skill_version") in {"R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}
            else None
        )
        if job.get("route_id") != "M2_F_SOURCE_AUDIO_RESTYLE":
            raise ValueError("R625_P7_BUILDER_ROUTE_INVALID")
        if job.get("target", {}).get("audio_variant") != "SOURCE_AUDIO_REUSE":
            raise ValueError("R625_P7_BUILDER_AUDIO_VARIANT_INVALID")

        source_audio = timing.get("source_audio") if isinstance(timing.get("source_audio"), dict) else {}
        source_audio_path = project_file(project, clean(source_audio.get("relative_path")))
        if source_audio.get("sha256") != sha256_file(source_audio_path):
            raise ValueError("SOURCE_AUDIO_HASH_MISMATCH")
        timing_rows = {
            clean(row.get("segment_id")): row
            for row in timing.get("segments", [])
            if isinstance(row, dict) and clean(row.get("segment_id"))
        }
        scene_rows = {
            clean(row.get("scene_id")): row
            for row in plan.get("scenes", [])
            if isinstance(row, dict) and clean(row.get("scene_id"))
        }
        grids = [row for row in plan.get("grids", []) if isinstance(row, dict)]
        p7 = project / "artifacts/P7"
        p7.mkdir(parents=True, exist_ok=True)
        outputs: list[dict[str, Any]] = []
        staged_bindings: list[tuple[str, str, str]] = []

        for segment in plan.get("video_segments", []):
            if not isinstance(segment, dict):
                raise ValueError("P4_VIDEO_SEGMENT_INVALID")
            segment_id = clean(segment.get("segment_id"))
            grid_id = clean(segment.get("grid_id"))
            scene_ids = segment.get("scene_ids") if isinstance(segment.get("scene_ids"), list) else []
            if len(scene_ids) != 1 or scene_ids[0] not in scene_rows:
                raise ValueError(f"{segment_id}_SCENE_BINDING_INVALID")
            scene = scene_rows[scene_ids[0]]
            grid = exactly_one(
                [row for row in grids if row.get("grid_id") == grid_id and row.get("segment_id") == segment_id],
                f"{segment_id}_GRID",
            )
            timing_row = timing_rows.get(segment_id)
            if timing_row is None:
                raise ValueError(f"{segment_id}_SOURCE_AUDIO_TIMING_MISSING")
            start = float(segment["target_start_s"])
            end = float(segment["target_end_s"])
            if timing_row.get("start_s") != segment.get("target_start_s") or timing_row.get("end_s") != segment.get("target_end_s"):
                raise ValueError(f"{segment_id}_P3_P4_TIME_MISMATCH")
            timing_source, timing_nodes = derive_timing_nodes(plan, segment)

            grid_rel = f"artifacts/P6/{grid_id}_GRID_BASELINE.png"
            grid_path = project_file(project, grid_rel)
            qc_rel, qc_path, qc, qc_authority = resolve_authoritative_passed_qc(
                project, state, grid_id=grid_id, segment_id=segment_id,
                grid_relative_path=grid_rel, grid_sha256=sha256_file(grid_path),
            )

            start_rel = f"artifacts/P7/{segment_id}_START_CELL01.png"
            crop_rel = f"artifacts/P7/{segment_id}_START_CELL01_CROP_RECEIPT.json"
            source_prompt_rel = f"artifacts/P7/{segment_id}_VIDEO_PROMPT.txt"
            source_package_rel = f"artifacts/P7/{segment_id}_SEGMENT_PACKAGE.json"
            for relative in (start_rel, crop_rel, source_prompt_rel, source_package_rel,
                             f"artifacts/P7/{segment_id}_VIDEO_PROMPT_R623.txt",
                             f"artifacts/P7/{segment_id}_VIDEO_PROMPT_AUDIT_R623.json",
                             f"artifacts/P7/{segment_id}_SEGMENT_PACKAGE_R623.json"):
                if (project / relative).exists():
                    raise ValueError(f"P7_OUTPUT_ALREADY_EXISTS:{relative}")

            crop = subprocess.run([
                sys.executable, "-B", "-X", "utf8",
                str(Path(__file__).resolve().parent / "extract_r62_start_cell.py"),
                "--project-dir", str(project),
                "--grid", grid_rel,
                "--layout", str(grid.get("layout")),
                "--output", start_rel,
                "--receipt", crop_rel,
            ], capture_output=True, text=True, encoding="utf-8", check=False)
            if crop.returncode != 0:
                raise ValueError(f"{segment_id}_START_CELL_CROP_FAILED:{crop.stdout or crop.stderr}")
            crop_receipt = load_json(project_file(project, crop_rel))
            if crop_receipt.get("status") != "PASSED":
                raise ValueError(f"{segment_id}_START_CELL_CROP_RECEIPT_FAILED")

            source_prompt_path = project / source_prompt_rel
            source_prompt_path.write_text(
                "R6.25 PRECOMPILE CONTRACT ONLY. The provider Prompt must be generated by "
                "compile_r623_video_prompt.py from this package and current P4 evidence.\n",
                encoding="utf-8",
                newline="\n",
            )
            spatial = scene.get("spatial_contract") if isinstance(scene.get("spatial_contract"), dict) else {}
            camera_proofs = spatial.get("camera_proofs") if isinstance(spatial.get("camera_proofs"), list) else []
            cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
            if not cells:
                raise ValueError(f"{segment_id}_GRID_CELLS_MISSING")
            forbidden = scene.get("forbidden_alternatives") if isinstance(scene.get("forbidden_alternatives"), list) else []
            if not forbidden:
                raise ValueError(f"{segment_id}_FORBIDDEN_ALTERNATIVES_MISSING")
            package = {
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
                "grid_asset": {"relative_path": grid_rel, "sha256": sha256_file(grid_path)},
                "start_frame_derivative": {
                    "relative_path": start_rel,
                    "sha256": sha256_file(project_file(project, start_rel)),
                    "source_grid_relative_path": grid_rel,
                    "source_grid_sha256": sha256_file(grid_path),
                    "source_cell": 1,
                    "method": "DETERMINISTIC_CROP_NO_GENERATION",
                    "crop_receipt_relative_path": crop_rel,
                    "crop_receipt_sha256": sha256_file(project_file(project, crop_rel)),
                },
                "video_prompt": {
                    "relative_path": source_prompt_rel,
                    "sha256": sha256_file(source_prompt_path),
                    "audio_variant": "SOURCE_AUDIO_REUSE",
                },
                "action_contract": {
                    "initial_state": clean(cells[0].get("visual_statement")),
                    "large_action_path": clean(scene.get("large_action")),
                    "unique_interaction_channel": clean(spatial.get("action_path", {}).get("description")),
                    "decisive_action": clean(spatial.get("decisive_contact", {}).get("description")),
                    "visible_result": clean(scene.get("visible_result")),
                    "camera": "；".join(clean(item) for item in camera_proofs if clean(item)) + "；" + clean(cells[-1].get("camera")),
                    "continuity_in": clean(segment.get("continuity_in")),
                    "continuity_out": clean(segment.get("continuity_out")),
                    "forbidden_alternatives": [clean(item) for item in forbidden if clean(item)],
                },
                "audio_contract": {
                    "variant": "SOURCE_AUDIO_REUSE",
                    "spoken_copy": timing_row.get("exact_copy"),
                    "target_start_s": segment.get("target_start_s"),
                    "target_end_s": segment.get("target_end_s"),
                    "planned_duration_s": round(end - start, 6),
                    "timing_authority": "SOURCE_AUDIO_MASTER",
                    "voice_provider": "SOURCE_FILE",
                    "voice_id": "ORIGINAL_SOURCE_AUDIO",
                    "voice_speed": 1.0,
                    "playback_speed": 1.0,
                    "timing_manifest": {
                        "relative_path": "artifacts/P3/SOURCE_AUDIO_PLAN.json",
                        "sha256": sha256_file(timing_path),
                    },
                    "source_audio": {
                        "relative_path": source_audio.get("relative_path"),
                        "sha256": source_audio.get("sha256"),
                    },
                    "speaker_mode": "ORIGINAL_SOURCE_AUDIO",
                    "lip_sync_policy": "NOT_APPLICABLE",
                    "generated_audio_policy": "MUTE",
                },
                "lineage": {
                    "job_relative_path": job_rel,
                    "job_sha256": sha256_file(job_path),
                    "scene_plan_relative_path": plan_rel,
                    "scene_plan_sha256": sha256_file(plan_path),
                    "narration_timing_relative_path": "artifacts/P3/SOURCE_AUDIO_PLAN.json",
                    "narration_timing_sha256": sha256_file(timing_path),
                    "p6_qc_relative_path": qc_rel,
                    "p6_qc_sha256": sha256_file(qc_path),
                    "accepted_deviation_fact_contracts": fact_lineage,
                    "segment_state_flow_audit": state_flow_receipt,
                },
                "provider_adapter_profile": job.get("provider_adapter_profile"),
                "p8_submission_authorized": False,
            }
            package_path = project / source_package_rel
            write_json_atomic(package_path, package)
            source_issues = validate_segment_package(project, package_path)
            if source_issues:
                raise ValueError(f"{segment_id}_SOURCE_PACKAGE_INVALID:" + ",".join(source_issues))
            compiled = compile_segment(project, segment_id)
            upgraded_rel = compiled["upgraded_segment_package_relative_path"]
            upgraded_path = project_file(project, upgraded_rel)
            audit_issues = validate_prompt_audit(project, upgraded_path)
            if audit_issues:
                raise ValueError(f"{segment_id}_R625_PROMPT_AUDIT_INVALID:" + ",".join(audit_issues))

            output = {
                "segment_id": segment_id,
                "grid_id": grid_id,
                "timing_source": timing_source,
                "p6_qc_authority": qc_authority,
                "timing_nodes_sha256": canonical_sha256(timing_nodes),
                "timing_interval_count": len(timing_nodes),
                "start_frame_relative_path": start_rel,
                "start_frame_sha256": sha256_file(project_file(project, start_rel)),
                "segment_package_relative_path": upgraded_rel,
                "segment_package_sha256": sha256_file(upgraded_path),
                "video_prompt_relative_path": compiled["prompt_relative_path"],
                "video_prompt_sha256": compiled["prompt_sha256"],
                "video_prompt_character_count": compiled["prompt_character_count"],
                "video_prompt_audit_relative_path": compiled["audit_relative_path"],
                "video_prompt_audit_sha256": sha256_file(project_file(project, compiled["audit_relative_path"])),
            }
            outputs.append(output)
            staged_bindings.extend([
                (f"{segment_id}_START_CELL01", start_rel, "extract_r62_start_cell.py"),
                (f"{segment_id}_START_CELL01_CROP_RECEIPT", crop_rel, "extract_r62_start_cell.py"),
                (f"{segment_id}_VIDEO_PROMPT_R623", compiled["prompt_relative_path"], "compile_r623_video_prompt.py"),
                (f"{segment_id}_VIDEO_PROMPT_AUDIT_R623", compiled["audit_relative_path"], "validate_r623_video_prompt_audit.py"),
                (f"{segment_id}_SEGMENT_PACKAGE_R623", upgraded_rel, "validate_r623_video_prompt_audit.py"),
            ])

        receipt_rel = "artifacts/P7/P7_BUILD_R625.json"
        receipt = {
            "schema_version": "R6.25-P7-BUILD-1.0",
            "status": "PASSED",
            "job_id": state.get("project_id"),
            "route_id": job.get("route_id"),
            "audio_variant": "SOURCE_AUDIO_REUSE",
            "timing_authority": "SOURCE_AUDIO_MASTER",
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
        bind(project, "P7_BUILD_R625", receipt_rel, "build_r625_p7_packages.py")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "issues": [str(exc)], "external_calls": 0}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
