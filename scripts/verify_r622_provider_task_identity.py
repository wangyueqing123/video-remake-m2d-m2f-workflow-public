#!/usr/bin/env python3
"""Prove that one KIE provider task is the exact task sealed by this project."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, now, sha256_file, write_json_atomic


SCHEMA = "R6.22-P8-PROVIDER-TASK-IDENTITY-1.0"
VIDEO_KINDS = {"VIDEO_API", "VIDEO_API_RECOVERY"}


def _object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}_JSON_INVALID") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{label}_OBJECT_REQUIRED")


def _provider_param(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    data = record.get("data") if isinstance(record.get("data"), dict) else record
    param = _object(data.get("param"), "PROVIDER_PARAM")
    provider_input = _object(param.get("input"), "PROVIDER_INPUT")
    return data, {"model": param.get("model"), "input": provider_input}


def _utc_timestamp(value: Any, label: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return number
    text = str(value or "").strip()
    if text.isdigit():
        return _utc_timestamp(int(text), label)
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
        except ValueError as exc:
            raise ValueError(f"{label}_TIME_INVALID") from exc
    raise ValueError(f"{label}_TIME_MISSING")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify(project: Path, package_path: Path, record_path: Path, seal_sha256: str, approval_id: str) -> dict[str, Any]:
    package = load_json(package_path)
    state = load_json(project / STATE_NAME)
    provider_record = load_json(record_path)
    if state.get("skill_version") not in {"R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
        raise ValueError("R622_TO_R624_PROJECT_REQUIRED")
    if package.get("call_kind") not in VIDEO_KINDS:
        raise ValueError("VIDEO_PACKAGE_REQUIRED")
    package_sha256 = sha256_file(package_path)
    seal = next((row for row in state.get("review_seals", []) if isinstance(row, dict) and row.get("seal_sha256") == seal_sha256), None)
    approval = next((row for row in state.get("approvals", []) if isinstance(row, dict) and row.get("approval_id") == approval_id), None)
    if not seal or seal.get("package_sha256") != package_sha256 or seal.get("call_kind") != package.get("call_kind"):
        raise ValueError("SEALED_PACKAGE_BINDING_INVALID")
    if not approval or approval.get("seal_sha256") != seal_sha256 or approval.get("package_sha256") != package_sha256:
        raise ValueError("APPROVAL_BINDING_INVALID")

    provider_data, provider = _provider_param(provider_record)
    provider_input = provider["input"]
    request = package.get("request") if isinstance(package.get("request"), dict) else {}
    prompt_path = (project / normalize_project_relative(str(request.get("prompt_relative_path", "")))).resolve()
    if sha256_file(prompt_path) != request.get("prompt_sha256"):
        raise ValueError("PROMPT_FILE_HASH_MISMATCH")
    expected_urls = [str(row.get("remote_url", "")) for row in request.get("visual_inputs", []) if isinstance(row, dict)]
    actual_urls = provider_input.get("image_urls")
    if not isinstance(actual_urls, list):
        actual_urls = []
    actual_urls = [str(value) for value in actual_urls]
    actual_prompt = provider_input.get("prompt")
    if not isinstance(actual_prompt, str):
        actual_prompt = ""
    task_id = str(provider_data.get("taskId") or provider_data.get("task_id") or "").strip()
    provider_create_time = provider_data.get("createTime") or provider_data.get("create_time") or provider_data.get("createdAt")
    created_ts = _utc_timestamp(provider_create_time, "PROVIDER_CREATE")
    approved_ts = _utc_timestamp(approval.get("approved_at"), "APPROVAL")
    verified_ts = _utc_timestamp(now(), "VERIFIED")

    prompt_text = prompt_path.read_text(encoding="utf-8")
    expected = {
        "model": package.get("model"),
        "prompt_file_sha256": request.get("prompt_sha256"),
        "prompt_sha256": _sha256_text(prompt_text),
        "image_urls": expected_urls,
        "duration_s": request.get("duration_s"),
        "aspect_ratio": request.get("aspect_ratio"),
        "resolution": request.get("resolution"),
    }
    actual = {
        "model": provider.get("model"),
        "prompt_sha256": _sha256_text(actual_prompt),
        "image_urls": actual_urls,
        "duration_s": provider_input.get("duration"),
        "aspect_ratio": provider_input.get("aspect_ratio"),
        "resolution": provider_input.get("resolution"),
    }
    checks = {
        "task_id_present": bool(task_id),
        "model_exact": actual["model"] == expected["model"],
        "prompt_sha256_exact": actual["prompt_sha256"] == expected["prompt_sha256"],
        "ordered_image_urls_exact": actual["image_urls"] == expected["image_urls"],
        "duration_exact": actual["duration_s"] == expected["duration_s"],
        "aspect_ratio_exact": actual["aspect_ratio"] == expected["aspect_ratio"],
        "resolution_exact": actual["resolution"] == expected["resolution"],
        "created_not_before_approval": created_ts >= approved_ts,
        "created_not_in_future": created_ts <= verified_ts + 300,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "schema_version": SCHEMA,
        "status": "PASSED" if not failed else "REJECTED",
        "verified_at": now(),
        "project_id": state.get("project_id"),
        "segment_id": package.get("segment_id"),
        "resource_id": seal.get("resource_id"),
        "call_kind": package.get("call_kind"),
        "call_ordinal": package.get("call_ordinal"),
        "task_id": task_id,
        "seal_sha256": seal_sha256,
        "approval_id": approval_id,
        "package_relative_path": package_path.relative_to(project).as_posix(),
        "package_sha256": package_sha256,
        "provider_record_relative_path": record_path.relative_to(project).as_posix(),
        "provider_record_sha256": sha256_file(record_path),
        "provider_create_time": provider_create_time,
        "approval_time": approval.get("approved_at"),
        "expected": expected,
        "actual": actual,
        "checks": checks,
        "failed_checks": failed,
        "identity_rule": "TASK_ID_PLUS_EXACT_MODEL_PROMPT_ORDERED_URLS_DURATION_FORMAT_AND_POST_APPROVAL_TIME",
        "screenshot_or_duration_only_is_identity_proof": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--package", required=True)
    parser.add_argument("--provider-record", required=True)
    parser.add_argument("--seal-sha256", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        package_path = (project / normalize_project_relative(args.package)).resolve()
        record_path = (project / normalize_project_relative(args.provider_record)).resolve()
        receipt_path = (project / normalize_project_relative(args.receipt)).resolve()
        for path in (package_path, record_path):
            path.relative_to(project)
            if not path.is_file():
                raise ValueError(f"PROJECT_FILE_MISSING:{path.name}")
        receipt_path.relative_to(project)
        result = verify(project, package_path, record_path, args.seal_sha256, args.approval_id)
        write_json_atomic(receipt_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASSED" else 2
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_P0", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
