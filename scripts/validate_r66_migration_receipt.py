#!/usr/bin/env python3
"""Independently verify an R6.5 to R6.7 visual-anchor migration."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from r62_project import canonical_sha256, sha256_file
from validate_r62_job import validate_job
from validate_r66_end_state_receipt import validate as validate_end_state
from validate_r66_visual_anchor import validate as validate_anchor


HEX64 = re.compile(r"^[0-9a-f]{64}$")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def file_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {"relative_path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(
            (
                item for item in root.rglob("*")
                if item.is_file() and "__pycache__" not in item.parts and item.suffix.lower() not in {".pyc", ".pyo"}
            ),
            key=lambda item: item.relative_to(root).as_posix(),
        )
    ]


def project_file(project: Path, relative: Any, label: str, issues: list[str]) -> Path | None:
    text = str(relative or "").replace("\\", "/")
    if not text or text.startswith("/") or (len(text) >= 2 and text[1] == ":") or ".." in Path(text).parts:
        issues.append(f"{label}_PATH_INVALID")
        return None
    path = (project / text).resolve()
    try:
        path.relative_to(project)
    except ValueError:
        issues.append(f"{label}_PATH_ESCAPES_PROJECT")
        return None
    if not path.is_file():
        issues.append(f"{label}_FILE_MISSING")
        return None
    return path


def verify_file(project: Path, relative: Any, expected_hash: Any, label: str, issues: list[str]) -> Path | None:
    path = project_file(project, relative, label, issues)
    digest = str(expected_hash or "").lower()
    if not HEX64.fullmatch(digest):
        issues.append(f"{label}_HASH_INVALID")
    elif path is not None and sha256_file(path) != digest:
        issues.append(f"{label}_HASH_MISMATCH")
    return path


def validate(project: Path, receipt_path: Path, source: Path | None = None) -> list[str]:
    issues: list[str] = []
    receipt = load_json(receipt_path)
    state = load_json(project / "R62_PROJECT.json")
    if receipt.get("schema_version") != "R6.6-VISUAL-ANCHOR-MIGRATION-1.0" or receipt.get("status") != "PASSED":
        issues.append("MIGRATION_RECEIPT_SCHEMA_OR_STATUS_INVALID")
    if receipt.get("source_skill_version") != "R6.5" or receipt.get("destination_skill_version") != "R6.7":
        issues.append("MIGRATION_VERSION_TRANSITION_INVALID")
    if receipt.get("external_calls_during_migration") != 0 or receipt.get("provider_calls_authorized") is not False:
        issues.append("MIGRATION_EXTERNAL_CALL_OR_AUTHORITY_INVALID")
    if receipt.get("old_live_authority_retired") is not True:
        issues.append("MIGRATION_OLD_AUTHORITY_NOT_RETIRED")
    if any(receipt.get(key) != 0 for key in ("live_review_seals_after_migration", "live_approvals_after_migration", "live_submissions_after_migration")):
        issues.append("MIGRATION_RECEIPT_LIVE_AUTHORITY_COUNT_NONZERO")
    if state.get("skill_version") != "R6.7" or state.get("current_phase") != "P5" or state.get("status") != "WAIT_REVIEW":
        issues.append("MIGRATED_PROJECT_PHASE_OR_VERSION_INVALID")
    if state.get("active_session") is not None or state.get("pending_qc_submission_id") is not None:
        issues.append("MIGRATED_PROJECT_SESSION_OR_PENDING_QC_NOT_CLEARED")
    if any(state.get(key) for key in ("review_seals", "approvals", "submissions", "qc_records")):
        issues.append("MIGRATED_PROJECT_LIVE_AUTHORITY_NOT_EMPTY")
    if state.get("resume_contract", {}).get("provider_call_authorized") is not False:
        issues.append("MIGRATED_PROJECT_PROVIDER_AUTHORITY_NOT_FALSE")

    historical = state.get("historical_revision") if isinstance(state.get("historical_revision"), dict) else {}
    history_state = verify_file(project, historical.get("source_state_relative_path"), historical.get("source_state_sha256"), "HISTORY_STATE", issues)
    history_job = verify_file(project, historical.get("source_job_relative_path"), historical.get("source_job_sha256"), "HISTORY_JOB", issues)
    inventory_path = verify_file(project, historical.get("source_inventory_relative_path"), historical.get("source_inventory_sha256"), "HISTORY_INVENTORY", issues)
    history_state_data: dict[str, Any] = {}
    if history_state is not None:
        if sha256_file(history_state) != receipt.get("source_history_state_sha256"):
            issues.append("HISTORY_STATE_DIFFERS_FROM_RECEIPT")
        history_state_data = load_json(history_state)
    if history_job is not None and sha256_file(history_job) != receipt.get("source_history_job_sha256"):
        issues.append("HISTORY_JOB_DIFFERS_FROM_RECEIPT")
    stored_files: list[dict[str, Any]] = []
    if inventory_path is not None:
        stored_inventory = load_json(inventory_path)
        candidate_files = stored_inventory.get("files") if isinstance(stored_inventory.get("files"), list) else []
        stored_files = [row for row in candidate_files if isinstance(row, dict)]
        if (
            stored_inventory.get("inventory_sha256") != receipt.get("source_inventory_sha256")
            or stored_inventory.get("file_count") != receipt.get("source_inventory_file_count")
            or len(stored_files) != receipt.get("source_inventory_file_count")
            or canonical_sha256(stored_files) != receipt.get("source_inventory_sha256")
        ):
            issues.append("HISTORY_INVENTORY_CONTENT_MISMATCH")
        for row in stored_files:
            relative = str(row.get("relative_path", ""))
            if relative == "R62_PROJECT.json":
                actual = history_state
            elif relative == "artifacts/P1/JOB.json":
                actual = history_job
            else:
                actual = project_file(project, relative, "HISTORY_SNAPSHOT", issues)
            expected_bytes = row.get("bytes")
            expected_hash = str(row.get("sha256", "")).lower()
            if actual is not None and (actual.stat().st_size != expected_bytes or sha256_file(actual) != expected_hash):
                issues.append(f"HISTORY_SNAPSHOT_FILE_MISMATCH:{relative}")
    if source is not None:
        source_state = source / "R62_PROJECT.json"
        source_job = source / "artifacts/P1/JOB.json"
        if not source_state.is_file() or sha256_file(source_state) != receipt.get("source_manifest_sha256"):
            issues.append("SOURCE_MANIFEST_CHANGED_SINCE_MIGRATION")
        if not source_job.is_file() or sha256_file(source_job) != receipt.get("source_job_sha256"):
            issues.append("SOURCE_JOB_CHANGED_SINCE_MIGRATION")
        external_inventory = file_inventory(source)
        if len(external_inventory) != receipt.get("source_inventory_file_count") or canonical_sha256(external_inventory) != receipt.get("source_inventory_sha256"):
            issues.append("SOURCE_INVENTORY_CHANGED_SINCE_MIGRATION")
    if historical.get("historical_authority_reusable") is not False:
        issues.append("HISTORICAL_AUTHORITY_REUSABLE_NOT_FALSE")
    source_seals = [row.get("seal_sha256") for row in history_state_data.get("review_seals", []) if isinstance(row, dict)]
    source_approvals = [row.get("approval_id") for row in history_state_data.get("approvals", []) if isinstance(row, dict)]
    if historical.get("retired_seal_sha256s") != source_seals:
        issues.append("RETIRED_SEALS_DIFFER_FROM_SOURCE_HISTORY")
    if historical.get("retired_approval_ids") != source_approvals:
        issues.append("RETIRED_APPROVALS_DIFFER_FROM_SOURCE_HISTORY")
    if historical.get("historical_submissions") != len(history_state_data.get("submissions", [])):
        issues.append("HISTORICAL_SUBMISSION_COUNT_DIFFERS_FROM_SOURCE_HISTORY")

    job_path = project / "artifacts/P1/JOB.json"
    if validate_job(load_json(job_path)):
        issues.append("MIGRATED_JOB_INVALID")
    anchor_path = project / "artifacts/P6/PROJECT_VISUAL_ANCHOR.json"
    if validate_anchor(project, anchor_path):
        issues.append("MIGRATED_PROJECT_ANCHOR_INVALID")
    end_receipt = project / "artifacts/P6/G01_END_STATE_CROP_RECEIPT.json"
    if validate_end_state(project, end_receipt):
        issues.append("MIGRATED_G01_END_STATE_INVALID")
    if sha256_file(anchor_path) != receipt.get("anchor_contract_sha256"):
        issues.append("ANCHOR_HASH_DIFFERS_FROM_MIGRATION_RECEIPT")

    p5_review = load_json(project / "artifacts/P5/P5_R66_VISUAL_ANCHOR_REVIEW.json")
    if p5_review.get("status") != "WAIT_REVIEW" or p5_review.get("provider_calls_authorized") is not False:
        issues.append("P5_REVIEW_GATE_INVALID")
    affected = p5_review.get("affected_grids") if isinstance(p5_review.get("affected_grids"), list) else []
    if [row.get("grid_id") for row in affected if isinstance(row, dict)] != receipt.get("affected_grids"):
        issues.append("P5_AFFECTED_GRIDS_DIFFER_FROM_RECEIPT")
    affected_ids = [row.get("grid_id") for row in affected if isinstance(row, dict)]
    budget = state.get("generation_budget") if isinstance(state.get("generation_budget"), dict) else {}
    job_budget = load_json(job_path).get("generation_budget", {})
    if budget.get("r66_replacement_scope") != affected_ids:
        issues.append("MIGRATED_REPLACEMENT_SCOPE_INVALID")
    if budget.get("project_max_grid_baselines") != len(affected_ids) or budget.get("project_max_grid_corrections") != len(affected_ids):
        issues.append("MIGRATED_LIVE_GRID_BUDGET_NOT_LIMITED_TO_AFFECTED_SCOPE")
    if job_budget.get("project_max_grid_baselines") != len(affected_ids) or job_budget.get("project_max_grid_corrections") != len(affected_ids):
        issues.append("MIGRATED_JOB_GRID_BUDGET_NOT_LIMITED_TO_AFFECTED_SCOPE")
    history_state_data = history_state_data if isinstance(history_state_data, dict) else {}
    historical_baselines = len([row for row in history_state_data.get("submissions", []) if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE"])
    if budget.get("historical_grid_baselines_consumed") != historical_baselines or budget.get("historical_calls_remain_charged") is not True:
        issues.append("HISTORICAL_GRID_COST_LEDGER_INVALID")
    for row in affected:
        if not isinstance(row, dict):
            issues.append("P5_AFFECTED_GRID_RECORD_INVALID")
            continue
        grid_id = str(row.get("grid_id", "UNKNOWN"))
        prompt_path = verify_file(project, row.get("prompt_relative_path"), row.get("prompt_sha256"), f"{grid_id}_PROMPT", issues)
        audit_path = verify_file(project, row.get("audit_relative_path"), row.get("audit_sha256"), f"{grid_id}_PROMPT_AUDIT", issues)
        if row.get("required_reference_roles") != ["PROJECT_VISUAL_ANCHOR", "PREVIOUS_SEGMENT_END_STATE"]:
            issues.append(f"{grid_id}_REFERENCE_ROLE_DECLARATION_INVALID")
        if prompt_path is not None:
            prompt = prompt_path.read_text(encoding="utf-8")
            required = ("PROJECT_VISUAL_ANCHOR", "PREVIOUS_SEGMENT_END_STATE", "逐格隔离规则", "硬状态锁", "全图禁止")
            if not all(token in prompt for token in required):
                issues.append(f"{grid_id}_PROMPT_MODEL_SEMANTICS_MISSING")
        if audit_path is not None:
            audit = load_json(audit_path)
            contract = audit.get("visual_anchor_contract") if isinstance(audit.get("visual_anchor_contract"), dict) else {}
            if audit.get("schema_version") != "R6.6-COMPILED-PROMPT-AUDIT-1.0" or audit.get("model_readable_language") != "zh-CN":
                issues.append(f"{grid_id}_PROMPT_AUDIT_SCHEMA_OR_LANGUAGE_INVALID")
            if not all(contract.get(key) is True for key in ("required", "project_anchor_role_expected", "project_anchor_role_compiled", "previous_end_state_role_expected", "previous_end_state_role_compiled")):
                issues.append(f"{grid_id}_PROMPT_AUDIT_ANCHOR_PROOF_INVALID")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", type=Path, help="Optional additional comparison with the still-existing R6.5 directory")
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--receipt", default="artifacts/P5/R65_TO_R66_VISUAL_ANCHOR_MIGRATION_RECEIPT.json")
    args = parser.parse_args()
    source = args.source_project.resolve() if args.source_project else None
    project = args.project_dir.resolve()
    try:
        receipt_path = project / args.receipt
        issues = validate(project, receipt_path, source)
        print(json.dumps({"status": "PASSED" if not issues else "BLOCKED_P0", "issues": issues}, ensure_ascii=False, indent=2))
        return 0 if not issues else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_P0", "issues": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
