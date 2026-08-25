#!/usr/bin/env python3
"""Accept a P6 grid at the R6.41 80-point core-expression threshold, without a provider call."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from r62_project import (
    STATE_NAME,
    append_event,
    exclusive_project_lock,
    load_json,
    now,
    sha256_file,
    write_json_atomic,
)
from r634_integrity_contract import resolve_effective_inputs
from r641_expression_contract import validate_expression_review, validate_plan


def _project_path(project: Path, relative: str, *, must_exist: bool = True) -> Path:
    path = (project / relative).resolve()
    path.relative_to(project)
    if must_exist and not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{relative}")
    return path


def accept(project: Path, rejected_rel: str, review_rel: str, amended_rel: str) -> dict[str, Any]:
    project = project.resolve()
    with exclusive_project_lock(project):
        state_path = project / STATE_NAME
        state = load_json(state_path)
        if state.get("skill_version") != "R6.41":
            raise ValueError("PROJECT_MUST_BE_R641")
        if state.get("status") != "BLOCKED_P0" or state.get("resume_contract", {}).get("first_safe_action") != "APPLY_R641_EXPRESSION_ACCEPTANCE":
            raise ValueError("PROJECT_NOT_WAITING_FOR_R641_EXPRESSION_ACCEPTANCE")
        if state.get("pending_qc_submission_id") is not None:
            raise ValueError("PENDING_QC_BLOCKS_EXPRESSION_ACCEPTANCE")
        if state.get("resume_contract", {}).get("provider_call_authorized") is not False:
            raise ValueError("LIVE_PROVIDER_AUTHORITY_BLOCKS_EXPRESSION_ACCEPTANCE")

        rejected_path = _project_path(project, rejected_rel)
        review_path = _project_path(project, review_rel)
        amended_path = _project_path(project, amended_rel, must_exist=False)
        if amended_path.exists():
            raise ValueError("AMENDED_QC_OUTPUT_ALREADY_EXISTS")
        rejected = load_json(rejected_path)
        review = load_json(review_path)
        rejected_hash = sha256_file(rejected_path)
        review_hash = sha256_file(review_path)

        _, plan_path, _ = resolve_effective_inputs(project, state)
        plan_issues = validate_plan(load_json(plan_path), require=True)
        if plan_issues:
            raise ValueError("R641_EFFECTIVE_PLAN_INVALID:" + ",".join(plan_issues))
        review_issues = validate_expression_review(review)
        if review_issues:
            raise ValueError("R641_EXPRESSION_REVIEW_INVALID:" + ",".join(review_issues))

        binding = {
            "project_id": state.get("project_id"),
            "grid_id": rejected.get("grid_id"),
            "segment_id": rejected.get("segment_id"),
            "rejected_qc_relative_path": rejected_rel,
            "rejected_qc_sha256": rejected_hash,
        }
        if any(review.get(key) != value for key, value in binding.items()):
            raise ValueError("R641_EXPRESSION_REVIEW_BINDING_INVALID")
        blocking = rejected.get("blocking_failure_codes")
        if not isinstance(blocking, list) or not blocking or rejected.get("decision") != "REJECTED":
            raise ValueError("R641_ACCEPTANCE_REQUIRES_REJECTED_QC")
        deviation_codes = [row.get("code") for row in review.get("soft_deviations", [])]
        if len(deviation_codes) != len(set(deviation_codes)) or set(deviation_codes) != set(blocking):
            raise ValueError("R641_SOFT_DEVIATIONS_MUST_COVER_BLOCKING_CODES_EXACTLY")
        if any(value == "FAILED" for value in rejected.get("cross_grid_checks", {}).values()):
            raise ValueError("R641_CROSS_GRID_FAILURE_IS_HARD")
        promotion = rejected.get("reference_promotion") if isinstance(rejected.get("reference_promotion"), dict) else {}
        if promotion.get("support_topology_passed") is not True or promotion.get("critical_contacts_verifiable") is not True:
            raise ValueError("R641_TOPOLOGY_OR_CONTACT_FAILURE_IS_HARD")
        if any(row.get("topology_result") != "PASSED" for row in rejected.get("cells", []) if isinstance(row, dict)):
            raise ValueError("R641_CELL_TOPOLOGY_FAILURE_IS_HARD")

        records = [
            row for row in state.get("qc_records", [])
            if isinstance(row, dict) and row.get("qc_sha256") == rejected_hash and row.get("decision") == "REJECTED"
        ]
        if len(records) != 1:
            raise ValueError("R641_ACCEPTANCE_REQUIRES_ONE_RECORDED_REJECTED_QC")

        amended = copy.deepcopy(rejected)
        for key in ("chronology_correct", "state_ledger_satisfied", "unwanted_text_absent"):
            if key in amended.get("global_checks", {}):
                amended["global_checks"][key] = True
        for cell in amended.get("cells", []):
            if isinstance(cell, dict):
                cell["result"] = "PASSED"
                cell["blocking_failure_codes"] = []
        amended["blocking_failure_codes"] = []
        amended["failure_class"] = "NONE"
        amended["decision"] = "PASSED"
        amended["correction_eligible"] = False
        amended["automatic_retry_allowed"] = False
        amended.setdefault("reference_promotion", {})["eligible"] = True
        amended["reference_promotion"]["end_state_safe_for_next_segment"] = True
        amended["r641_expression_acceptance"] = {
            "score": review["score"],
            "threshold": 80,
            "decision": "ACCEPT_AT_80_PLUS",
            "review_relative_path": review_rel,
            "review_sha256": review_hash,
            "original_qc_relative_path": rejected_rel,
            "original_qc_sha256": rejected_hash,
            "soft_deviations": copy.deepcopy(review["soft_deviations"]),
            "core_expression_checks": copy.deepcopy(review["core_expression_checks"]),
            "provider_calls_added": 0,
        }
        if not all(amended.get("global_checks", {}).values()):
            raise ValueError("R641_AMENDED_QC_HAS_UNRESOLVED_GLOBAL_FAILURE")
        write_json_atomic(amended_path, amended)
        amended_hash = sha256_file(amended_path)

        output = rejected.get("generated_output") if isinstance(rejected.get("generated_output"), dict) else {}
        matches = [
            row for phase in state.get("artifacts", {}).values() if isinstance(phase, dict)
            for row in phase.values() if isinstance(row, dict)
            and row.get("relative_path") == output.get("relative_path") and row.get("sha256") == output.get("sha256")
        ]
        if len(matches) != 1:
            raise ValueError("R641_OUTPUT_ARTIFACT_NOT_UNIQUE")
        matches[0]["validation_status"] = "VALIDATED"
        matches[0]["validator"] = "accept_r641_expression_qc.py"
        state.setdefault("artifacts", {}).setdefault("P6", {})[f"{rejected.get('grid_id')}_EXPRESSION_QC_AMENDED_R641"] = {
            "relative_path": amended_rel,
            "sha256": amended_hash,
            "bytes": amended_path.stat().st_size,
            "validator": "accept_r641_expression_qc.py",
            "validation_status": "VALIDATED",
            "bound_at": now(),
        }
        records[0].update({
            "qc_relative_path": amended_rel,
            "qc_sha256": amended_hash,
            "decision": "PASSED",
            "failure_class": "NONE",
            "correction_eligible": False,
            "original_qc_relative_path": rejected_rel,
            "original_qc_sha256": rejected_hash,
            "expression_review_relative_path": review_rel,
            "expression_review_sha256": review_hash,
            "amended_at": now(),
        })
        acceptance = {
            "grid_id": rejected.get("grid_id"),
            "segment_id": rejected.get("segment_id"),
            "submission_id": rejected.get("submission_id"),
            "score": review["score"],
            "threshold": 80,
            "accepted_failure_codes": blocking,
            "review_relative_path": review_rel,
            "review_sha256": review_hash,
            "amended_qc_relative_path": amended_rel,
            "amended_qc_sha256": amended_hash,
            "provider_calls_added": 0,
            "accepted_at": now(),
        }
        state.setdefault("expression_acceptances", []).append(acceptance)
        plan = load_json(plan_path)
        ordered = [row.get("grid_id") for row in plan.get("grids", []) if isinstance(row, dict)]
        is_final_grid = bool(ordered) and rejected.get("grid_id") == ordered[-1]
        state["status"] = "ACTIVE"
        state["current_phase"] = "P6"
        state["resume_contract"]["provider_call_authorized"] = False
        state["resume_contract"]["first_safe_action"] = (
            "PROCEED_TO_P7_VIDEO_PROMPT_PACKAGE" if is_final_grid else "PROMOTE_ACCEPTED_GRID_AND_PREPARE_NEXT_GRID"
        )
        append_event(state, "P6_R641_EXPRESSION_ACCEPTED", acceptance)
        write_json_atomic(state_path, state)
        return {
            "status": "PASSED",
            "grid_id": rejected.get("grid_id"),
            "score": review["score"],
            "amended_qc_relative_path": amended_rel,
            "amended_qc_sha256": amended_hash,
            "provider_calls_added": 0,
            "next_action": state["resume_contract"]["first_safe_action"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--rejected-qc", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--amended-qc", required=True)
    args = parser.parse_args()
    try:
        result = accept(args.project_dir, args.rejected_qc, args.review, args.amended_qc)
        code = 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result = {"status": "BLOCKED_P0", "error": str(exc), "provider_calls_added": 0}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
