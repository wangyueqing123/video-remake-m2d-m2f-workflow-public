#!/usr/bin/env python3
"""Prepare and seal one fresh R6.22 recovery package per eligible segment, without external calls."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, command_seal, load_json, now, write_json_atomic
from validate_r62_p8_package import validate as validate_p8


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        state = load_json(project / STATE_NAME)
        if state.get("skill_version") not in {"R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"} or not isinstance(state.get("active_session"), dict):
            raise ValueError("R622_PROJECT_WITH_ACTIVE_SESSION_REQUIRED")
        submissions = [row for row in state.get("submissions", []) if isinstance(row, dict)]
        seals_by_hash = {row.get("seal_sha256"): row for row in state.get("review_seals", []) if isinstance(row, dict)}
        prepared: list[dict[str, Any]] = []
        for contract in sorted(
            (row for row in state.get("recovery_contracts", []) if isinstance(row, dict) and row.get("status") == "ELIGIBLE"),
            key=lambda row: str(row.get("segment_id")),
        ):
            segment_id = str(contract.get("segment_id"))
            prior = [
                row for row in submissions
                if row.get("call_kind") == "VIDEO_API"
                and row.get("segment_id") == segment_id
                and row.get("submission_id") == contract.get("prior_submission_id")
            ]
            if len(prior) != 1 or prior[0].get("provider_task_identity_status") != "REJECTED_TASK_MISATTRIBUTION":
                raise ValueError(f"RECOVERY_PRIOR_MISATTRIBUTION_NOT_UNIQUE:{segment_id}")
            old_seal = seals_by_hash.get(prior[0].get("seal_sha256"))
            if not old_seal:
                raise ValueError(f"PRIOR_SEAL_MISSING:{segment_id}")
            old_package = load_json(project / old_seal["package_relative_path"])
            package = copy.deepcopy(old_package)
            package.update({
                "schema_version": "R6.5-P8-VIDEO-RECOVERY-REQUEST-1.0",
                "status": "WAIT_REVIEW",
                "call_kind": "VIDEO_API_RECOVERY",
                "call_ordinal": 2,
                "request_id": f"R622-RECOVERY-{segment_id}-R2",
                "idempotency_key": f"r622-{state.get('project_id')}-{segment_id.lower()}-recovery-r2",
                "maximum_task_creation_attempts": 1,
                "automatic_retry_allowed": False,
                "timeout_action": "BLOCK_AND_RECONCILE_PROVIDER_LOGS_NO_AUTO_RESUBMIT",
                "recovery": {
                    "prior_submission_id": contract["prior_submission_id"],
                    "reconciliation_relative_path": contract["reconciliation_relative_path"],
                    "reconciliation_sha256": contract["reconciliation_sha256"],
                    "provider_task_absence_verified": True,
                    "maximum_recovery_calls": 1,
                    "reuse_prior_authority": False,
                },
                "r622_task_identity_gate": {
                    "provider_record_required_before_consume": True,
                    "exact_model_prompt_ordered_urls_duration_format_required": True,
                    "task_created_after_current_approval_required": True,
                    "screenshot_or_duration_only_forbidden": True,
                },
                "prepared_at": now(),
            })
            relative = f"reviews/P8_{segment_id}_VIDEO_RECOVERY_R622.json"
            write_json_atomic(project / relative, package)
            issues = validate_p8(project, project / relative, expected_call_kind="VIDEO_API_RECOVERY")
            if issues:
                raise ValueError(f"RECOVERY_PACKAGE_INVALID:{segment_id}:" + ",".join(issues))
            seal = command_seal(argparse.Namespace(
                project_dir=project, phase="P8", package=relative,
                call_kind="VIDEO_API_RECOVERY", call_ordinal=2,
            ))
            prepared.append({
                "segment_id": segment_id,
                "package_relative_path": relative,
                "package_sha256": seal["package_sha256"],
                "seal_sha256": seal["seal_sha256"],
                "maximum_authorized_cost_usd": package["cost"]["maximum_authorized_cost"],
                "duration_s": package["request"]["duration_s"],
                "status": "WAIT_REVIEW",
            })
        if len(prepared) != 3:
            raise ValueError(f"EXPECTED_THREE_RECOVERY_PACKAGES_GOT:{len(prepared)}")
        print(json.dumps({
            "schema_version": "R6.22-P8-RECOVERY-PACKAGE-PREPARATION-1.0",
            "status": "WAIT_REVIEW", "prepared": prepared,
            "total_maximum_authorized_cost_usd": round(sum(row["maximum_authorized_cost_usd"] for row in prepared), 6),
            "external_calls": 0, "automatic_retry_allowed": False,
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_P0", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
