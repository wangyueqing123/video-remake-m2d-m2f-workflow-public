#!/usr/bin/env python3
"""Create one sealed KIE video task and book it only after exact identity proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from r62_project import (
    STATE_NAME,
    command_block,
    command_consume,
    load_json,
    normalize_project_relative,
    now,
    sha256_file,
    write_json_atomic,
)
from verify_r622_provider_task_identity import verify as verify_identity


def _post_json(url: str, body: bytes, api_key: str, timeout: int) -> tuple[int | None, dict[str, Any] | None, str | None]:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "video-remake-workflow-r622/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw), None
        except json.JSONDecodeError:
            return exc.code, None, raw[:2000]
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return None, None, f"{type(exc).__name__}:{exc}"


def _get_record(task_id: str, api_key: str, timeout: int) -> tuple[int | None, dict[str, Any] | None, str | None]:
    query = urllib.parse.urlencode({"taskId": task_id})
    request = urllib.request.Request(
        f"https://api.kie.ai/api/v1/jobs/recordInfo?{query}",
        method="GET",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json", "User-Agent": "video-remake-workflow-r622/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw), None
        except json.JSONDecodeError:
            return exc.code, None, raw[:2000]
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return None, None, f"{type(exc).__name__}:{exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--package", required=True)
    parser.add_argument("--seal-sha256", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        package_rel = normalize_project_relative(args.package)
        package_path = (project / package_rel).resolve()
        runtime_rel = normalize_project_relative(args.runtime_dir)
        runtime_dir = (project / runtime_rel).resolve()
        runtime_dir.relative_to(project)
        package = load_json(package_path)
        state = load_json(project / STATE_NAME)
        if state.get("skill_version") not in {"R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"} or package.get("call_kind") not in {"VIDEO_API", "VIDEO_API_RECOVERY"}:
            raise ValueError("R622_VIDEO_PACKAGE_REQUIRED")
        package_sha256 = sha256_file(package_path)
        seal = next((row for row in state.get("review_seals", []) if isinstance(row, dict) and row.get("seal_sha256") == args.seal_sha256), None)
        approval = next((row for row in state.get("approvals", []) if isinstance(row, dict) and row.get("approval_id") == args.approval_id), None)
        if not seal or seal.get("package_sha256") != package_sha256 or seal.get("call_kind") != package.get("call_kind"):
            raise ValueError("SEALED_PACKAGE_BINDING_INVALID")
        if not approval or approval.get("seal_sha256") != args.seal_sha256 or approval.get("package_sha256") != package_sha256:
            raise ValueError("APPROVAL_BINDING_INVALID")
        if any(isinstance(row, dict) and row.get("approval_id") == args.approval_id for row in state.get("submissions", [])):
            raise ValueError("APPROVAL_ALREADY_CONSUMED")
        api_key = os.environ.get("KIE_API_KEY", "").strip()
        if not api_key:
            raise ValueError("KIE_API_KEY_ENVIRONMENT_MISSING")
        request = package.get("request") if isinstance(package.get("request"), dict) else {}
        prompt_path = (project / normalize_project_relative(str(request.get("prompt_relative_path", "")))).resolve()
        if sha256_file(prompt_path) != request.get("prompt_sha256"):
            raise ValueError("PROMPT_HASH_MISMATCH")
        body = {
            "model": package.get("model"),
            "input": {
                "prompt": prompt_path.read_text(encoding="utf-8"),
                "image_urls": [row["remote_url"] for row in request.get("visual_inputs", [])],
                "aspect_ratio": request.get("aspect_ratio"),
                "resolution": request.get("resolution"),
                "duration": request.get("duration_s"),
                "nsfw_checker": request.get("nsfw_checker"),
            },
        }
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        intent_path = runtime_dir / "CREATE_TASK_ATTEMPT_INTENT.json"
        response_path = runtime_dir / "CREATE_TASK_RAW_RESPONSE.json"
        record_path = runtime_dir / "PROVIDER_TASK_RAW_RECORD.json"
        proof_path = runtime_dir / "PROVIDER_TASK_IDENTITY_PROOF.json"
        outcome_path = runtime_dir / "CREATE_TASK_OUTCOME.json"
        if any(path.exists() for path in (intent_path, response_path, record_path, proof_path, outcome_path)):
            raise ValueError("EXACT_EXTERNAL_ATTEMPT_ALREADY_STARTED_OR_RECORDED")
        intent = {
            "schema_version": "R6.22-P8-KIE-ATTEMPT-INTENT-1.0",
            "status": "STARTED_ONCE_NO_RETRY",
            "started_at": now(),
            "package_relative_path": package_rel,
            "package_sha256": package_sha256,
            "seal_sha256": args.seal_sha256,
            "approval_id": args.approval_id,
            "segment_id": package.get("segment_id"),
            "call_kind": package.get("call_kind"),
            "request_body_sha256": hashlib.sha256(body_bytes).hexdigest(),
            "http_attempt_ordinal": 1,
            "automatic_retry_allowed": False,
        }
        write_json_atomic(intent_path, intent)
        http_status, response_payload, error = _post_json(str(package.get("endpoint")), body_bytes, api_key, args.timeout_seconds)
        raw_response = {
            "schema_version": "R6.22-P8-KIE-CREATE-TASK-RAW-RESPONSE-1.0",
            "received_at": now(),
            "http_status": http_status,
            "provider": response_payload,
            "transport_error": error,
            "automatic_retry_allowed": False,
        }
        write_json_atomic(response_path, raw_response)
        data = response_payload.get("data") if isinstance(response_payload, dict) and isinstance(response_payload.get("data"), dict) else {}
        task_id = str(data.get("taskId") or "").strip()
        if http_status != 200 or not isinstance(response_payload, dict) or response_payload.get("code") != 200 or not task_id:
            outcome = {
                "schema_version": "R6.22-P8-KIE-CREATE-TASK-OUTCOME-1.0",
                "status": "BLOCKED_P0",
                "reason_code": "CREATE_TASK_RESPONSE_AMBIGUOUS_OR_NO_TASK_ID",
                "task_id": task_id or None,
                "http_attempts": 1,
                "automatic_retry_allowed": False,
                "raw_response_relative_path": response_path.relative_to(project).as_posix(),
                "raw_response_sha256": sha256_file(response_path),
            }
            write_json_atomic(outcome_path, outcome)
            command_block(argparse.Namespace(project_dir=project, phase="P8", reason_code=outcome["reason_code"], evidence=outcome_path.relative_to(project).as_posix()))
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            return 2
        record_http_status, record_payload, record_error = _get_record(task_id, api_key, args.timeout_seconds)
        raw_record = record_payload if isinstance(record_payload, dict) else {
            "code": None,
            "msg": record_error,
            "data": {"taskId": task_id},
            "http_status": record_http_status,
        }
        write_json_atomic(record_path, raw_record)
        proof = verify_identity(project, package_path, record_path, args.seal_sha256, args.approval_id)
        write_json_atomic(proof_path, proof)
        if proof.get("status") != "PASSED":
            outcome = {
                "schema_version": "R6.22-P8-KIE-CREATE-TASK-OUTCOME-1.0",
                "status": "BLOCKED_P0",
                "reason_code": "PROVIDER_TASK_IDENTITY_NOT_PROVEN",
                "task_id": task_id,
                "http_attempts": 1,
                "automatic_retry_allowed": False,
                "identity_proof_relative_path": proof_path.relative_to(project).as_posix(),
                "identity_proof_sha256": sha256_file(proof_path),
            }
            write_json_atomic(outcome_path, outcome)
            command_block(argparse.Namespace(project_dir=project, phase="P8", reason_code=outcome["reason_code"], evidence=outcome_path.relative_to(project).as_posix()))
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            return 2
        consumed = command_consume(argparse.Namespace(
            project_dir=project,
            seal_sha256=args.seal_sha256,
            approval_id=args.approval_id,
            submission_id=task_id,
            provider_task_proof=proof_path.relative_to(project).as_posix(),
        ))
        outcome = {
            "schema_version": "R6.22-P8-KIE-CREATE-TASK-OUTCOME-1.0",
            "status": "TASK_CREATED_AND_IDENTITY_PROVEN",
            "task_id": task_id,
            "http_attempts": 1,
            "automatic_retry_allowed": False,
            "identity_proof_relative_path": proof_path.relative_to(project).as_posix(),
            "identity_proof_sha256": sha256_file(proof_path),
            "submission": consumed,
        }
        write_json_atomic(outcome_path, outcome)
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_P0", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
