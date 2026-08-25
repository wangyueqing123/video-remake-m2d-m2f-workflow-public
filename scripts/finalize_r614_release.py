#!/usr/bin/env python3
"""Validate an R6.14 narration-aligned master, emit its certificate, and close the project."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

sys.dont_write_bytecode = True


STATE_NAME = "R62_PROJECT.json"
CERTIFICATE_RELATIVE_PATH = "artifacts/P9/FINAL_RELEASE_CERTIFICATE_R614.json"
FINAL_THRESHOLD = 85.0
SEGMENT_FLOOR = 80
REQUIRED_TRUE_CHECKS = {
    "all_segments_present_in_order",
    "hard_visual_failures_absent",
    "model_audio_replaced",
    "post_dub_audio_present",
    "audio_video_duration_aligned",
    "uniform_narration_voice_speed",
    "video_follows_narration_timeline",
    "black_frames_introduced_by_assembly",
    "grid_or_split_screen_absent",
    "unwanted_text_absent",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def project_file(project: Path, value: str) -> tuple[str, Path]:
    text = value.strip().replace("\\", "/")
    pure = PurePosixPath(text)
    if not text or text.startswith("/") or text.startswith("//") or (len(text) > 1 and text[1] == ":") or ".." in pure.parts:
        raise ValueError(f"PROJECT_PATH_NOT_PORTABLE:{value}")
    path = (project / Path(pure.as_posix())).resolve()
    try:
        path.relative_to(project)
    except ValueError as exc:
        raise ValueError(f"PROJECT_PATH_ESCAPES_ROOT:{value}") from exc
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{pure.as_posix()}")
    return pure.as_posix(), path


def validate_release(project: Path, state: dict[str, Any], qc: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    issues: list[str] = []
    if state.get("current_phase") not in {"P8", "P9"} or state.get("status") not in {"ACTIVE", "COMPLETE"}:
        issues.append("PROJECT_NOT_READY_FOR_FINAL_RELEASE")
    if qc.get("schema_version") != "R6.14-P9-FINAL-MASTER-QC-1.0":
        issues.append("FINAL_MASTER_QC_SCHEMA_INVALID")
    if state.get("pending_qc_submission_id") is not None:
        issues.append("PENDING_P6_QC_EXISTS")
    if state.get("resume_contract", {}).get("provider_call_authorized") is not False:
        issues.append("PROVIDER_CALL_PERMISSION_MUST_BE_FALSE")
    if qc.get("status") != "REVIEWED" or qc.get("decision") != "PASSED" or qc.get("test_run_complete") is not True:
        issues.append("FINAL_MASTER_QC_NOT_PASSED")
    if qc.get("additional_external_call_required") is not False or qc.get("automatic_retry_allowed") is not False:
        issues.append("FINAL_MASTER_MUST_NOT_REQUIRE_EXTERNAL_CALL_OR_RETRY")

    output = qc.get("output") if isinstance(qc.get("output"), dict) else {}
    output_rel = ""
    output_path: Path | None = None
    try:
        output_rel, output_path = project_file(project, str(output.get("relative_path", "")))
        if sha256_file(output_path) != str(output.get("sha256", "")).lower():
            issues.append("FINAL_MASTER_HASH_MISMATCH")
        if output_path.stat().st_size != output.get("bytes"):
            issues.append("FINAL_MASTER_SIZE_MISMATCH")
    except (OSError, ValueError) as exc:
        issues.append(str(exc))

    rows = qc.get("segment_visual_qc") if isinstance(qc.get("segment_visual_qc"), list) else []
    scores: list[int] = []
    segment_ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            issues.append("SEGMENT_QC_ROW_INVALID")
            continue
        score = row.get("visual_score")
        segment_id = row.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id or segment_id in segment_ids:
            issues.append("SEGMENT_ID_INVALID_OR_DUPLICATE")
        else:
            segment_ids.append(segment_id)
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            issues.append("SEGMENT_VISUAL_SCORE_INVALID")
        else:
            scores.append(score)
            if score < SEGMENT_FLOOR:
                issues.append("SEGMENT_VISUAL_SCORE_BELOW_80")
        if row.get("decision") != "PASSED":
            issues.append("SEGMENT_QC_NOT_PASSED")
    if not scores or len(scores) != len(rows):
        issues.append("SEGMENT_QC_SET_INCOMPLETE")
        calculated_mean = 0.0
    else:
        calculated_mean = round(sum(scores) / len(scores), 2)
    declared_mean = qc.get("aggregate_visual_score_mean")
    if isinstance(declared_mean, bool) or not isinstance(declared_mean, (int, float)) or abs(float(declared_mean) - calculated_mean) > 0.001:
        issues.append("AGGREGATE_VISUAL_SCORE_MISMATCH")
    if calculated_mean < FINAL_THRESHOLD:
        issues.append("FINAL_MASTER_SCORE_BELOW_85")

    hard_failures = qc.get("hard_failures")
    if hard_failures != []:
        issues.append("FINAL_MASTER_HARD_FAILURES_PRESENT")
    checks = qc.get("checks") if isinstance(qc.get("checks"), dict) else {}
    for name in REQUIRED_TRUE_CHECKS:
        expected = False if name == "black_frames_introduced_by_assembly" else True
        if checks.get(name) is not expected:
            issues.append(f"FINAL_MASTER_CHECK_FAILED:{name}")
    post_dub = qc.get("post_dub") if isinstance(qc.get("post_dub"), dict) else {}
    if not str(post_dub.get("status", "")).startswith("PASSED") or post_dub.get("model_audio_preserved") is not False:
        issues.append("POST_DUB_FINAL_STATUS_INVALID")
    try:
        timing_rel, timing_path = project_file(project, str(post_dub.get("timing_manifest_relative_path", "")))
        if sha256_file(timing_path) != str(post_dub.get("timing_manifest_sha256", "")).lower():
            issues.append("FINAL_NARRATION_TIMING_HASH_MISMATCH")
        else:
            from validate_r614_narration_timing import validate as validate_narration_timing

            issues.extend(f"NARRATION_TIMING:{item}" for item in validate_narration_timing(project, timing_path))
    except (OSError, ValueError) as exc:
        timing_rel = ""
        issues.append(str(exc))
    try:
        alignment_rel, alignment_path = project_file(project, str(post_dub.get("editing_alignment_relative_path", "")))
        if sha256_file(alignment_path) != str(post_dub.get("editing_alignment_sha256", "")).lower():
            issues.append("FINAL_EDITING_ALIGNMENT_HASH_MISMATCH")
        else:
            from validate_r614_editing_alignment import validate as validate_editing_alignment

            issues.extend(f"EDITING_ALIGNMENT:{item}" for item in validate_editing_alignment(project, alignment_path))
    except (OSError, ValueError) as exc:
        alignment_rel = ""
        issues.append(str(exc))
    technical = qc.get("technical_evidence") if isinstance(qc.get("technical_evidence"), dict) else {}
    for key in ("assembly_receipt_relative_path", "probe_relative_path", "contact_sheet_relative_path"):
        try:
            project_file(project, str(technical.get(key, "")))
        except (OSError, ValueError) as exc:
            issues.append(str(exc))

    summary = {
        "final_master_relative_path": output_rel,
        "final_master_sha256": str(output.get("sha256", "")).lower(),
        "aggregate_visual_score": calculated_mean,
        "final_threshold": FINAL_THRESHOLD,
        "segment_floor": SEGMENT_FLOOR,
        "segment_scores": dict(zip(segment_ids, scores)),
        "narration_timing_relative_path": timing_rel,
        "editing_alignment_relative_path": alignment_rel,
    }
    return sorted(set(issues)), summary


def finalize(project: Path, qc_relative_path: str, release_name: str, commit: bool) -> dict[str, Any]:
    project = project.resolve()
    state_path = project / STATE_NAME
    state = load_json(state_path)
    qc_rel, qc_path = project_file(project, qc_relative_path)
    qc = load_json(qc_path)
    issues, summary = validate_release(project, state, qc)
    if issues:
        return {"status": "BLOCKED_P0", "issues": issues, **summary}
    certificate = {
        "schema_version": "R6.14-FINAL-RELEASE-CERTIFICATE-1.0",
        "status": "PASSED",
        "release_name": release_name,
        "project_id": state.get("project_id"),
        "source_skill_version": state.get("skill_version"),
        "certification_tool_version": "R6.14",
        "final_master_qc_relative_path": qc_rel,
        "final_master_qc_sha256": sha256_file(qc_path),
        **summary,
        "hard_failures": [],
        "provider_call_authorized": False,
        "automatic_retry_allowed": False,
        "next_extension": "JIANying_CAPCUT_DRAFT_ADAPTER",
        "certified_at": now(),
    }
    if not commit:
        return {"status": "PASSED_DRY_RUN", "issues": [], "certificate": certificate}

    cert_path = project / CERTIFICATE_RELATIVE_PATH
    if cert_path.exists() or state.get("status") == "COMPLETE":
        raise ValueError("PROJECT_ALREADY_FINALIZED")
    write_json_atomic(cert_path, certificate)
    cert_hash = sha256_file(cert_path)
    state.setdefault("artifacts", {}).setdefault("P9", {})["FINAL_RELEASE_CERTIFICATE_R614"] = {
        "relative_path": CERTIFICATE_RELATIVE_PATH,
        "sha256": cert_hash,
        "bytes": cert_path.stat().st_size,
        "validator": "finalize_r614_release.py",
        "validation_status": "VALIDATED",
        "bound_at": now(),
    }
    state["status"] = "COMPLETE"
    state["current_phase"] = "P9"
    state.setdefault("resume_contract", {})["provider_call_authorized"] = False
    state["resume_contract"]["first_safe_action"] = "READY_FOR_JIANYING_CAPCUT_DRAFT_EXPORT"
    state["completion"] = {
        "release_name": release_name,
        "certificate_relative_path": CERTIFICATE_RELATIVE_PATH,
        "certificate_sha256": cert_hash,
        "aggregate_visual_score": summary["aggregate_visual_score"],
        "threshold": FINAL_THRESHOLD,
        "completed_at": now(),
    }
    state.setdefault("events", []).append({
        "at": now(),
        "type": "PROJECT_FINAL_RELEASE_COMPLETED",
        "detail": {"certificate_sha256": cert_hash, "release_name": release_name},
    })
    state["updated_at"] = now()
    write_json_atomic(state_path, state)
    return {"status": "COMPLETE", "issues": [], "certificate_relative_path": CERTIFICATE_RELATIVE_PATH, "certificate_sha256": cert_hash, **summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--final-qc", default="artifacts/P9/FINAL_MASTER_QC.json")
    parser.add_argument("--release-name", default="Video Remake Workflow R6.14 Narration-Master Timing Lock")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    try:
        result = finalize(args.project_dir, args.final_qc, args.release_name, args.commit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "BLOCKED_P0", "issues": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"PASSED_DRY_RUN", "COMPLETE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
