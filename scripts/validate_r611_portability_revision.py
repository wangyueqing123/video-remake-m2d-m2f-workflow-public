#!/usr/bin/env python3
"""Verify an R6.11 immutable R6.10 snapshot plus append-only live ledgers."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from r62_project import canonical_sha256, sha256_file


HEX64 = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_PATH = "artifacts/P6/R610_TO_R611_P5_LINEAGE_LOCK_RECEIPT.json"


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
    revision = state.get("r611_revision") if isinstance(state.get("r611_revision"), dict) else {}
    receipt_path = project / RECEIPT_PATH
    receipt = load_json(receipt_path)
    if state.get("skill_version") != "R6.11":
        issues.append("PROJECT_VERSION_NOT_R611")
    if receipt.get("schema_version") != "R6.11-R610-P5-LINEAGE-LOCK-REVISION-1.0" or receipt.get("status") != "PASSED":
        issues.append("R611_REVISION_RECEIPT_INVALID")
    if (receipt.get("source_skill_version"), receipt.get("destination_skill_version")) != ("R6.10", "R6.11"):
        issues.append("R611_REVISION_TRANSITION_INVALID")
    if receipt.get("external_calls_during_revision") != 0 or receipt.get("provider_calls_authorized") is not False:
        issues.append("R611_REVISION_EXTERNAL_CALL_OR_AUTHORITY_INVALID")
    if state.get("resume_contract", {}).get("provider_call_authorized") is not False:
        issues.append("PERSISTED_PROVIDER_AUTHORITY_MUST_REMAIN_FALSE")

    history_state = resolve(project, revision.get("source_state_relative_path"), "HISTORY_STATE", issues)
    inventory_path = resolve(project, revision.get("source_inventory_relative_path"), "HISTORY_INVENTORY", issues)
    history: dict[str, Any] = {}
    if history_state is not None:
        digest = sha256_file(history_state)
        if digest != revision.get("source_state_sha256") or digest != receipt.get("source_manifest_sha256"):
            issues.append("HISTORY_STATE_HASH_MISMATCH")
        history = load_json(history_state)
        if history.get("skill_version") != "R6.10":
            issues.append("HISTORY_STATE_VERSION_MISMATCH")
    if inventory_path is not None:
        digest = sha256_file(inventory_path)
        if digest != revision.get("source_inventory_file_sha256") or digest != receipt.get("source_inventory_file_sha256"):
            issues.append("HISTORY_INVENTORY_FILE_HASH_MISMATCH")
        inventory = load_json(inventory_path)
        rows = inventory.get("files") if isinstance(inventory.get("files"), list) else []
        expected_count = receipt.get("source_inventory_file_count")
        expected_fingerprint = receipt.get("source_inventory_sha256")
        if inventory.get("file_count") != expected_count or len(rows) != expected_count or canonical_sha256(rows) != expected_fingerprint:
            issues.append("HISTORY_INVENTORY_CONTENT_MISMATCH")
        for row in rows:
            if not isinstance(row, dict):
                issues.append("HISTORY_INVENTORY_ROW_INVALID")
                continue
            relative = str(row.get("relative_path", ""))
            actual = history_state if relative == "R62_PROJECT.json" else resolve(project, relative, "HISTORY_SNAPSHOT", issues)
            expected_hash = str(row.get("sha256", "")).lower()
            if not HEX64.fullmatch(expected_hash):
                issues.append(f"HISTORY_SNAPSHOT_HASH_INVALID:{relative}")
            elif actual is not None and (actual.stat().st_size != row.get("bytes") or sha256_file(actual) != expected_hash):
                issues.append(f"HISTORY_SNAPSHOT_FILE_MISMATCH:{relative}")

    if history and state.get("generation_budget") != history.get("generation_budget"):
        issues.append("MIGRATION_BUDGET_CHANGED")
    for state_key, receipt_key in {
        "review_seals": "review_seal_hashes",
        "approvals": "approval_hashes",
        "submissions": "submission_hashes",
        "qc_records": "qc_record_hashes",
    }.items():
        rows = state.get(state_key) if isinstance(state.get(state_key), list) else []
        preserved = receipt.get(receipt_key) if isinstance(receipt.get(receipt_key), list) else []
        if len(rows) < len(preserved) or [canonical_sha256(row) for row in rows[:len(preserved)]] != preserved:
            issues.append(f"{state_key.upper()}_PRESERVED_PREFIX_CHANGED")

    bound = state.get("artifacts", {}).get("P6", {}).get("R610_TO_R611_P5_LINEAGE_LOCK_RECEIPT", {})
    if bound.get("relative_path") != RECEIPT_PATH or bound.get("sha256") != sha256_file(receipt_path) or bound.get("validation_status") != "VALIDATED":
        issues.append("R611_REVISION_RECEIPT_NOT_BOUND")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        issues = validate(args.project_dir.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [str(exc)]
    print(json.dumps({"status": "PASSED" if not issues else "BLOCKED_P0", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
