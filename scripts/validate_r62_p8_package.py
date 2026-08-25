#!/usr/bin/env python3
"""Validate R6.2 upload-only and video API packages without exposing secrets."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file
from validate_r62_segment_package import validate as validate_segment_package


HEX64 = re.compile(r"^[0-9a-f]{64}$")
SECRET_MARKERS = ("api_key", "apikey", "authorization:", "bearer ", "secret_key")


def derive_provider_duration_s(start_s: Any, end_s: Any) -> int | None:
    if isinstance(start_s, bool) or isinstance(end_s, bool) or not isinstance(start_s, (int, float)) or not isinstance(end_s, (int, float)):
        return None
    span = float(end_s) - float(start_s)
    if span <= 0:
        return None
    return max(1, math.ceil(span - 1e-9))


def _https(value: Any) -> bool:
    parsed = urlparse(str(value))
    return parsed.scheme == "https" and bool(parsed.netloc)


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str):
        yield value


def _project_file(project: Path, relative: Any, expected_hash: Any, label: str, issues: list[str]) -> Path | None:
    try:
        normalized = normalize_project_relative(str(relative))
    except ValueError:
        issues.append(f"{label}_PATH_INVALID")
        return None
    path = (project / normalized).resolve()
    digest = str(expected_hash).lower()
    if not path.is_file():
        issues.append(f"{label}_MISSING")
    elif not HEX64.fullmatch(digest) or sha256_file(path) != digest:
        issues.append(f"{label}_HASH_INVALID_OR_MISMATCH")
    return path if path.is_file() else None


def validate(project: Path, package_path: Path, *, expected_call_kind: str | None = None) -> list[str]:
    issues: list[str] = []
    package = load_json(package_path)
    manifest = load_json(project / STATE_NAME)
    call_kind = package.get("call_kind")
    if expected_call_kind is not None and call_kind != expected_call_kind:
        issues.append("P8_CALL_KIND_MISMATCH")
    if package.get("job_id") != manifest.get("project_id") or package.get("phase") != "P8" or package.get("status") != "WAIT_REVIEW":
        issues.append("P8_PROJECT_PHASE_OR_STATUS_INVALID")
    expected_ordinal = 2 if call_kind == "VIDEO_API_RECOVERY" else 1
    if package.get("call_ordinal") != expected_ordinal:
        issues.append("P8_CALL_ORDINAL_INVALID")
    if package.get("automatic_retry_allowed") is not False or package.get("human_approval_required") is not True or package.get("one_approval_one_submission") is not True:
        issues.append("P8_EXACTLY_ONCE_POLICY_INVALID")
    if any(any(marker in value.lower() for marker in SECRET_MARKERS) for value in _walk(package)):
        issues.append("P8_PACKAGE_MAY_CONTAIN_SECRET")

    if call_kind == "ASSET_UPLOAD":
        if package.get("schema_version") != "R6.2-P8-ASSET-UPLOAD-PACKAGE-1.0":
            issues.append("ASSET_UPLOAD_SCHEMA_INVALID")
        for key in ("batch_id", "segment_id", "grid_id", "provider", "provider_adapter_profile"):
            if not str(package.get(key, "")).strip():
                issues.append(f"ASSET_UPLOAD_{key.upper()}_MISSING")
        mode_lock = manifest.get("mode_lock") if isinstance(manifest.get("mode_lock"), dict) else {}
        if package.get("provider_adapter_profile") != mode_lock.get("provider_adapter_profile"):
            issues.append("ASSET_UPLOAD_ADAPTER_DIFFERS_FROM_P1")
        if not _https(package.get("endpoint")) or "createtask" in str(package.get("endpoint", "")).lower():
            issues.append("ASSET_UPLOAD_ENDPOINT_INVALID")
        segment_block = package.get("segment_package") if isinstance(package.get("segment_package"), dict) else {}
        segment_path = _project_file(project, segment_block.get("relative_path"), segment_block.get("sha256"), "SEGMENT_PACKAGE", issues)
        segment: dict[str, Any] = {}
        if segment_path is not None:
            issues.extend(validate_segment_package(project, segment_path))
            if manifest.get("skill_version") in {"R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
                from validate_r623_video_prompt_audit import validate as validate_r623_prompt
                issues.extend(validate_r623_prompt(project, segment_path))
            segment = load_json(segment_path)
            if segment.get("segment_id") != package.get("segment_id") or segment.get("grid_id") != package.get("grid_id"):
                issues.append("ASSET_UPLOAD_SEGMENT_GRID_MISMATCH")
        assets = package.get("assets")
        if not isinstance(assets, list) or not assets:
            issues.append("ASSET_UPLOAD_ASSETS_MISSING")
            assets = []
        asset_ids: set[str] = set()
        for index, asset in enumerate(assets, start=1):
            if not isinstance(asset, dict):
                issues.append(f"ASSET_{index}_INVALID")
                continue
            asset_id = str(asset.get("asset_id", ""))
            if not asset_id or asset_id in asset_ids:
                issues.append(f"ASSET_{index}_ID_INVALID_OR_DUPLICATE")
            asset_ids.add(asset_id)
            _project_file(project, asset.get("relative_path"), asset.get("sha256"), f"ASSET_{index}", issues)
            if asset.get("role") not in {"SEGMENT_ACTION_GRID", "SEGMENT_START_PLACEHOLDER", "ORDERED_KEYFRAME"}:
                issues.append(f"ASSET_{index}_ROLE_INVALID")
            if not str(asset.get("upload_path", "")).strip() or not str(asset.get("remote_file_name", "")).strip():
                issues.append(f"ASSET_{index}_REMOTE_TARGET_MISSING")
        if package.get("provider_adapter_profile") == "GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND":
            expected_roles = ["SEGMENT_ACTION_GRID", "SEGMENT_START_PLACEHOLDER"]
            roles = [row.get("role") for row in assets if isinstance(row, dict)]
            if roles != expected_roles:
                issues.append("ASSET_UPLOAD_GROK_KIE_ROLE_OR_ORDER_INVALID")
            if segment:
                expected_hashes = [
                    segment.get("grid_asset", {}).get("sha256"),
                    segment.get("start_frame_derivative", {}).get("sha256"),
                ]
                actual_hashes = [row.get("sha256") for row in assets if isinstance(row, dict)]
                if actual_hashes != expected_hashes:
                    issues.append("ASSET_UPLOAD_SOURCE_HASH_DIFFERS_FROM_SEGMENT_PACKAGE")
        if package.get("estimated_cost_usd") != 0.0 or package.get("video_task_creation_count") != 0 or package.get("forbidden_create_task") is not True:
            issues.append("ASSET_UPLOAD_NO_VIDEO_OR_COST_CONTRACT_INVALID")
        submissions = manifest.get("submissions") if isinstance(manifest.get("submissions"), list) else []
        if any(
            isinstance(row, dict)
            and row.get("call_kind") == "ASSET_UPLOAD"
            and row.get("segment_id") == package.get("segment_id")
            for row in submissions
        ):
            issues.append("SEGMENT_ASSET_UPLOAD_ALREADY_CONSUMED")
    elif call_kind in {"VIDEO_API", "VIDEO_API_RECOVERY"}:
        expected_schema = "R6.5-P8-VIDEO-RECOVERY-REQUEST-1.0" if call_kind == "VIDEO_API_RECOVERY" else "R6.2-P8-VIDEO-REQUEST-1.0"
        if package.get("schema_version") != expected_schema:
            issues.append("VIDEO_REQUEST_SCHEMA_INVALID")
        for key in ("request_id", "segment_id", "grid_id", "provider", "model", "provider_adapter_profile", "idempotency_key"):
            if not str(package.get(key, "")).strip():
                issues.append(f"VIDEO_REQUEST_{key.upper()}_MISSING")
        if not _https(package.get("endpoint")):
            issues.append("VIDEO_REQUEST_ENDPOINT_INVALID")
        mode_lock = manifest.get("mode_lock") if isinstance(manifest.get("mode_lock"), dict) else {}
        if package.get("provider_adapter_profile") != mode_lock.get("provider_adapter_profile"):
            issues.append("VIDEO_REQUEST_ADAPTER_DIFFERS_FROM_P1")
        segment_block = package.get("segment_package") if isinstance(package.get("segment_package"), dict) else {}
        segment_path = _project_file(project, segment_block.get("relative_path"), segment_block.get("sha256"), "SEGMENT_PACKAGE", issues)
        segment: dict[str, Any] = {}
        if segment_path is not None:
            issues.extend(validate_segment_package(project, segment_path))
            if manifest.get("skill_version") in {"R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
                from validate_r623_video_prompt_audit import validate as validate_r623_prompt
                issues.extend(validate_r623_prompt(project, segment_path))
            segment = load_json(segment_path)
            if segment.get("segment_id") != package.get("segment_id") or segment.get("grid_id") != package.get("grid_id"):
                issues.append("VIDEO_REQUEST_SEGMENT_GRID_MISMATCH")
        request = package.get("request") if isinstance(package.get("request"), dict) else {}
        _project_file(project, request.get("prompt_relative_path"), request.get("prompt_sha256"), "P8_PROMPT", issues)
        if segment and (
            request.get("prompt_relative_path") != segment.get("video_prompt", {}).get("relative_path")
            or request.get("prompt_sha256") != segment.get("video_prompt", {}).get("sha256")
        ):
            issues.append("VIDEO_REQUEST_PROMPT_DIFFERS_FROM_APPROVED_SEGMENT_PACKAGE")
        inputs = request.get("visual_inputs")
        if not isinstance(inputs, list):
            issues.append("VIDEO_REQUEST_VISUAL_INPUTS_INVALID")
            inputs = []
        if package.get("provider_adapter_profile") == "GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND":
            expected_roles = ["SEGMENT_ACTION_GRID", "SEGMENT_START_PLACEHOLDER"]
            if len(inputs) != 2 or [row.get("role") for row in inputs if isinstance(row, dict)] != expected_roles:
                issues.append("GROK_KIE_INPUT_ROLE_OR_ORDER_INVALID")
            if segment:
                expected_hashes = [
                    segment.get("grid_asset", {}).get("sha256"),
                    segment.get("start_frame_derivative", {}).get("sha256"),
                ]
                if [row.get("source_sha256") for row in inputs if isinstance(row, dict)] != expected_hashes:
                    issues.append("VIDEO_INPUT_SOURCE_HASH_DIFFERS_FROM_SEGMENT_PACKAGE")
        for index, row in enumerate(inputs):
            if not isinstance(row, dict) or row.get("index") != index or not _https(row.get("remote_url")) or not HEX64.fullmatch(str(row.get("source_sha256", "")).lower()):
                issues.append(f"VIDEO_INPUT_{index}_INVALID")
        duration = request.get("duration_s")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            issues.append("VIDEO_REQUEST_DURATION_MUST_BE_INTEGER_SECONDS")
        expected_duration = derive_provider_duration_s(segment.get("target_start_s"), segment.get("target_end_s")) if segment else None
        if expected_duration is None or duration != expected_duration:
            issues.append("VIDEO_REQUEST_DURATION_NOT_DERIVED_FROM_APPROVED_SEGMENT_SPAN")
        derivation = request.get("duration_derivation") if isinstance(request.get("duration_derivation"), dict) else {}
        expected_span = (
            round(float(segment.get("target_end_s")) - float(segment.get("target_start_s")), 6)
            if segment and isinstance(segment.get("target_start_s"), (int, float)) and isinstance(segment.get("target_end_s"), (int, float))
            else None
        )
        if (
            derivation.get("policy") != "CEIL_APPROVED_SEGMENT_SPAN"
            or derivation.get("approved_segment_span_s") != expected_span
            or derivation.get("derived_duration_s") != expected_duration
        ):
            issues.append("VIDEO_REQUEST_DURATION_DERIVATION_INVALID")
        if request.get("aspect_ratio") not in {"9:16", "16:9", "1:1", "2:3", "3:2"} or request.get("resolution") not in {"480p", "720p"}:
            issues.append("VIDEO_REQUEST_FORMAT_INVALID")
        cost = package.get("cost") if isinstance(package.get("cost"), dict) else {}
        if not isinstance(cost.get("estimated_cost"), (int, float)) or cost.get("estimated_cost", 0) < 0 or cost.get("maximum_authorized_cost") != cost.get("estimated_cost"):
            issues.append("VIDEO_REQUEST_COST_INVALID")
        unit_price = cost.get("unit_price_per_second")
        if (
            isinstance(unit_price, bool)
            or not isinstance(unit_price, (int, float))
            or unit_price < 0
            or not isinstance(duration, int)
            or round(float(unit_price) * duration, 6) != round(float(cost.get("estimated_cost", -1)), 6)
        ):
            issues.append("VIDEO_REQUEST_COST_NOT_DERIVED_FROM_DURATION")
        expected_timeout_action = (
            "BLOCK_AND_RECONCILE_PROVIDER_LOGS_NO_AUTO_RESUBMIT"
            if call_kind == "VIDEO_API_RECOVERY"
            else "QUERY_STATUS_WITH_SAME_TASK_OR_IDEMPOTENCY_KEY_NO_RESUBMIT"
        )
        if package.get("maximum_task_creation_attempts") != 1 or package.get("timeout_action") != expected_timeout_action:
            issues.append("VIDEO_REQUEST_RETRY_OR_TIMEOUT_POLICY_INVALID")
        upload_batch_id = str(package.get("upload_batch_id", "")).strip()
        upload_submission_id = str(package.get("upload_submission_id", "")).strip()
        submissions = manifest.get("submissions") if isinstance(manifest.get("submissions"), list) else []
        if not upload_batch_id or not upload_submission_id or not any(
            isinstance(row, dict)
            and row.get("call_kind") == "ASSET_UPLOAD"
            and row.get("resource_id") == upload_batch_id
            and row.get("submission_id") == upload_submission_id
            and row.get("segment_id") == package.get("segment_id")
            for row in submissions
        ):
            issues.append("VIDEO_REQUEST_REQUIRES_RECORDED_SAME_SEGMENT_UPLOAD")
        prior_video = [
            row for row in submissions
            if isinstance(row, dict)
            and row.get("call_kind") == "VIDEO_API"
            and row.get("segment_id") == package.get("segment_id")
        ]
        prior_recovery = [
            row for row in submissions
            if isinstance(row, dict)
            and row.get("call_kind") == "VIDEO_API_RECOVERY"
            and row.get("segment_id") == package.get("segment_id")
        ]
        if call_kind == "VIDEO_API":
            if prior_video or prior_recovery:
                issues.append("SEGMENT_VIDEO_API_ALREADY_CONSUMED")
        else:
            recovery = package.get("recovery") if isinstance(package.get("recovery"), dict) else {}
            reconciliation_path = _project_file(
                project,
                recovery.get("reconciliation_relative_path"),
                recovery.get("reconciliation_sha256"),
                "RECOVERY_RECONCILIATION",
                issues,
            )
            if reconciliation_path is not None:
                from validate_r65_no_task_reconciliation import validate as validate_reconciliation

                issues.extend(validate_reconciliation(project, reconciliation_path))
            if len(prior_video) != 1 or prior_recovery:
                issues.append("VIDEO_RECOVERY_PRIOR_SUBMISSION_CARDINALITY_INVALID")
            elif recovery.get("prior_submission_id") != prior_video[0].get("submission_id"):
                issues.append("VIDEO_RECOVERY_PRIOR_SUBMISSION_MISMATCH")
            contracts = manifest.get("recovery_contracts") if isinstance(manifest.get("recovery_contracts"), list) else []
            eligible = [
                row for row in contracts
                if isinstance(row, dict)
                and row.get("segment_id") == package.get("segment_id")
                and row.get("prior_submission_id") == recovery.get("prior_submission_id")
                and row.get("reconciliation_sha256") == recovery.get("reconciliation_sha256")
                and row.get("status") == "ELIGIBLE"
                and row.get("maximum_recovery_calls") == 1
                and row.get("recovery_calls_consumed") == 0
            ]
            if len(eligible) != 1:
                issues.append("VIDEO_RECOVERY_ELIGIBLE_CONTRACT_MISSING")
            if (
                recovery.get("provider_task_absence_verified") is not True
                or recovery.get("maximum_recovery_calls") != 1
                or recovery.get("reuse_prior_authority") is not False
                or package.get("maximum_task_creation_attempts") != 1
                or package.get("timeout_action") != "BLOCK_AND_RECONCILE_PROVIDER_LOGS_NO_AUTO_RESUBMIT"
            ):
                issues.append("VIDEO_RECOVERY_SAFETY_POLICY_INVALID")
    else:
        issues.append("P8_CALL_KIND_INVALID")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--package", required=True)
    parser.add_argument("--expected-call-kind", choices=["ASSET_UPLOAD", "VIDEO_API", "VIDEO_API_RECOVERY"])
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        package = (project / normalize_project_relative(args.package)).resolve()
        issues = validate(project, package, expected_call_kind=args.expected_call_kind)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
