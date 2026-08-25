#!/usr/bin/env python3
"""Validate evidence that a timed-out video request created no provider task."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file


HEX64 = re.compile(r"^[0-9a-f]{64}$")


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
        return None
    if not HEX64.fullmatch(digest) or sha256_file(path) != digest:
        issues.append(f"{label}_HASH_INVALID_OR_MISMATCH")
    return path


def validate(project: Path, reconciliation_path: Path) -> list[str]:
    issues: list[str] = []
    evidence = load_json(reconciliation_path)
    state = load_json(project / STATE_NAME)
    if evidence.get("schema_version") != "R6.5-P8-NO-PROVIDER-TASK-RECONCILIATION-1.0":
        issues.append("RECONCILIATION_SCHEMA_INVALID")
    if evidence.get("status") != "VERIFIED" or evidence.get("review_method") != "HUMAN_VISUAL_REVIEW_OF_AUTHENTICATED_PROVIDER_LOGS":
        issues.append("RECONCILIATION_REVIEW_STATUS_INVALID")
    segment_id = str(evidence.get("segment_id", "")).strip()
    prior_submission_id = str(evidence.get("prior_submission_id", "")).strip()
    if not segment_id or not prior_submission_id:
        issues.append("RECONCILIATION_SEGMENT_OR_SUBMISSION_MISSING")
    submissions = state.get("submissions") if isinstance(state.get("submissions"), list) else []
    prior = [
        row for row in submissions
        if isinstance(row, dict)
        and row.get("call_kind") == "VIDEO_API"
        and row.get("segment_id") == segment_id
        and row.get("submission_id") == prior_submission_id
    ]
    if len(prior) != 1:
        issues.append("RECONCILIATION_PRIOR_VIDEO_SUBMISSION_NOT_UNIQUE")
    timeout = evidence.get("timeout_evidence") if isinstance(evidence.get("timeout_evidence"), dict) else {}
    timeout_path = _project_file(project, timeout.get("relative_path"), timeout.get("sha256"), "TIMEOUT_EVIDENCE", issues)
    if timeout_path is not None:
        payload = load_json(timeout_path)
        if (
            payload.get("segment_id") != segment_id
            or payload.get("submission_id") != prior_submission_id
            or payload.get("task_creation_http_attempts") != 1
            or payload.get("http_response_received") is not False
            or payload.get("task_id_received") is not False
            or payload.get("provider_acceptance_state") != "UNKNOWN"
            or payload.get("automatic_retry_allowed") is not False
        ):
            issues.append("TIMEOUT_EVIDENCE_NOT_ELIGIBLE")
    screenshot = evidence.get("provider_logs_screenshot") if isinstance(evidence.get("provider_logs_screenshot"), dict) else {}
    _project_file(project, screenshot.get("relative_path"), screenshot.get("sha256"), "PROVIDER_LOGS_SCREENSHOT", issues)
    observation = evidence.get("provider_log_observation") if isinstance(evidence.get("provider_log_observation"), dict) else {}
    observed_ids = observation.get("visible_task_ids")
    if (
        not isinstance(observed_ids, list)
        or any(not str(value).strip() for value in observed_ids)
        or observation.get("target_segment_task_present") is not False
        or observation.get("new_task_after_attempt_present") is not False
        or observation.get("new_charge_after_attempt_detected") is not False
        or not str(observation.get("reviewed_at", "")).strip()
    ):
        issues.append("PROVIDER_LOG_OBSERVATION_INVALID")
    if evidence.get("conclusion") != "PROVIDER_TASK_NOT_CREATED":
        issues.append("RECONCILIATION_CONCLUSION_INVALID")
    recovery = evidence.get("recovery_contract") if isinstance(evidence.get("recovery_contract"), dict) else {}
    if (
        recovery.get("eligibility") != "ONE_NEW_HUMAN_REVIEWED_RECOVERY_CALL"
        or recovery.get("maximum_recovery_calls") != 1
        or recovery.get("automatic_retry_allowed") is not False
        or recovery.get("reuse_prior_package_seal_or_approval") is not False
        or recovery.get("new_package_seal_and_human_approval_required") is not True
    ):
        issues.append("RECOVERY_CONTRACT_INVALID")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--reconciliation", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        path = (project / normalize_project_relative(args.reconciliation)).resolve()
        issues = validate(project, path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
