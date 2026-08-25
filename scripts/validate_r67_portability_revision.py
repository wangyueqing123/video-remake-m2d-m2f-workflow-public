#!/usr/bin/env python3
"""Verify immutable R6.7/R6.8 migration proof while allowing legal live progress."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from r62_project import canonical_sha256, sha256_file


HEX64 = re.compile(r"^[0-9a-f]{64}$")
R67_RECEIPT_PATH = "artifacts/P5/R66_TO_R67_PORTABLE_PROOF_RECEIPT.json"
R68_RECEIPT_PATH = "artifacts/P5/R67_TO_R68_LIVE_PROGRESS_RECEIPT.json"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def resolve(project: Path, relative: Any, label: str, issues: list[str]) -> Path | None:
    value = str(relative or "").replace("\\", "/")
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts) or (len(value) >= 2 and value[1] == ":"):
        issues.append(f"{label}_PATH_INVALID")
        return None
    path = (project / Path(*pure.parts)).resolve()
    try:
        path.relative_to(project)
    except ValueError:
        issues.append(f"{label}_PATH_ESCAPES_PROJECT")
        return None
    if not path.is_file():
        issues.append(f"{label}_FILE_MISSING")
        return None
    return path


def validate(project: Path) -> list[str]:
    issues: list[str] = []
    state = load_json(project / "R62_PROJECT.json")
    portable = state.get("portable_revision") if isinstance(state.get("portable_revision"), dict) else {}
    receipt_path = project / R68_RECEIPT_PATH if (project / R68_RECEIPT_PATH).is_file() else project / R67_RECEIPT_PATH
    receipt_relative_path = receipt_path.relative_to(project).as_posix()
    receipt = load_json(receipt_path)
    if state.get("skill_version") not in {"R6.7", "R6.8"}:
        issues.append("PROJECT_VERSION_NOT_R67_OR_R68")
    if receipt.get("schema_version") not in {"R6.7-R66-PORTABLE-PROOF-REVISION-1.0", "R6.8-R67-LIVE-PROGRESS-REVISION-1.0"} or receipt.get("status") != "PASSED":
        issues.append("PORTABLE_REVISION_RECEIPT_INVALID")
    expected_transition = ("R6.6", "R6.7") if receipt.get("schema_version") == "R6.7-R66-PORTABLE-PROOF-REVISION-1.0" else ("R6.7", "R6.8")
    if (receipt.get("source_skill_version"), receipt.get("destination_skill_version")) != expected_transition:
        issues.append("PORTABLE_REVISION_TRANSITION_INVALID")
    if receipt.get("external_calls_during_revision") != 0 or receipt.get("provider_calls_authorized") is not False:
        issues.append("PORTABLE_REVISION_EXTERNAL_CALL_OR_AUTHORITY_INVALID")
    if portable.get("external_source_required_for_validation") is not False or receipt.get("external_source_required_for_validation") is not False:
        issues.append("EXTERNAL_SOURCE_DEPENDENCY_NOT_REMOVED")
    active_session = state.get("active_session")
    if active_session is not None and (not isinstance(active_session, dict) or not active_session.get("session_id")):
        issues.append("LIVE_SESSION_RECORD_INVALID")
    if state.get("resume_contract", {}).get("provider_call_authorized") is not False:
        issues.append("PERSISTED_PROVIDER_AUTHORITY_MUST_REMAIN_FALSE")

    history_state = resolve(project, portable.get("source_state_relative_path"), "HISTORY_STATE", issues)
    inventory_path = resolve(project, portable.get("source_inventory_relative_path"), "HISTORY_INVENTORY", issues)
    history_data: dict[str, Any] = {}
    if history_state is not None:
        digest = sha256_file(history_state)
        if (
            digest != portable.get("source_state_sha256")
            or digest != receipt.get("source_history_state_sha256")
            or digest != receipt.get("source_manifest_sha256")
        ):
            issues.append("HISTORY_STATE_HASH_MISMATCH")
        history_data = load_json(history_state)
        if history_data.get("skill_version") != expected_transition[0]:
            issues.append("HISTORY_STATE_VERSION_MISMATCH")
    stored_files: list[dict[str, Any]] = []
    if inventory_path is not None:
        digest = sha256_file(inventory_path)
        if digest != portable.get("source_inventory_file_sha256") or digest != receipt.get("source_inventory_file_sha256"):
            issues.append("HISTORY_INVENTORY_FILE_HASH_MISMATCH")
        inventory = load_json(inventory_path)
        rows = inventory.get("files") if isinstance(inventory.get("files"), list) else []
        stored_files = [row for row in rows if isinstance(row, dict)]
        expected_count = receipt.get("source_inventory_file_count")
        expected_fingerprint = receipt.get("source_inventory_sha256")
        if (
            inventory.get("file_count") != expected_count
            or len(stored_files) != expected_count
            or inventory.get("inventory_sha256") != expected_fingerprint
            or canonical_sha256(stored_files) != expected_fingerprint
            or portable.get("source_inventory_file_count") != expected_count
            or portable.get("source_inventory_fingerprint") != expected_fingerprint
        ):
            issues.append("HISTORY_INVENTORY_CONTENT_MISMATCH")
        for row in stored_files:
            relative = str(row.get("relative_path", ""))
            actual = history_state if relative == "R62_PROJECT.json" else resolve(project, relative, "HISTORY_SNAPSHOT", issues)
            expected_hash = str(row.get("sha256", "")).lower()
            if not HEX64.fullmatch(expected_hash):
                issues.append(f"HISTORY_SNAPSHOT_HASH_INVALID:{relative}")
            elif actual is not None and (actual.stat().st_size != row.get("bytes") or sha256_file(actual) != expected_hash):
                issues.append(f"HISTORY_SNAPSHOT_FILE_MISMATCH:{relative}")

    if history_data and state.get("generation_budget") != history_data.get("generation_budget"):
        issues.append("MIGRATION_BUDGET_CHANGED")
    if receipt.get("live_budget_preserved") != state.get("generation_budget"):
        issues.append("RECEIPT_LIVE_BUDGET_MISMATCH")
    for count_key, state_key in (
        ("review_seal_count_preserved", "review_seals"),
        ("approval_count_preserved", "approvals"),
        ("submission_count_preserved", "submissions"),
        ("qc_record_count_preserved", "qc_records"),
    ):
        preserved_count = receipt.get(count_key)
        current_count = len(state.get(state_key, []))
        if not isinstance(preserved_count, int) or current_count < preserved_count:
            issues.append(f"{count_key.upper()}_MISMATCH")

    bound_name = "R66_TO_R67_PORTABLE_PROOF_RECEIPT" if receipt.get("schema_version") == "R6.7-R66-PORTABLE-PROOF-REVISION-1.0" else "R67_TO_R68_LIVE_PROGRESS_RECEIPT"
    bound = state.get("artifacts", {}).get("P5", {}).get(bound_name, {})
    if bound.get("relative_path") != receipt_relative_path or bound.get("sha256") != sha256_file(receipt_path) or bound.get("validation_status") != "VALIDATED":
        issues.append("PORTABLE_REVISION_RECEIPT_NOT_BOUND")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        issues = validate(project)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [str(exc)]
    print(json.dumps({"status": "PASSED" if not issues else "BLOCKED_P0", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
