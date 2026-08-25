#!/usr/bin/env python3
"""Validate a portable R6.6 project visual anchor and its passed P6 QC."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_file(project: Path, value: Any) -> Path:
    text = str(value or "").replace("\\", "/")
    if not text or text.startswith("/") or ".." in PurePosixPath(text).parts:
        raise ValueError("ANCHOR_PATH_INVALID")
    path = (project / text).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError("ANCHOR_FILE_MISSING")
    return path


def validate(project: Path, contract_path: Path) -> list[str]:
    issues: list[str] = []
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("schema_version") != "R6.6-PROJECT-VISUAL-ANCHOR-1.0" or contract.get("status") != "VALIDATED":
        issues.append("ANCHOR_SCHEMA_OR_STATUS_INVALID")
    if contract.get("source_type") not in {"FIRST_PASSED_GRID", "APPROVED_DESIGN_SHEET", "SOURCE_LOCKED"}:
        issues.append("ANCHOR_SOURCE_TYPE_INVALID")
    if contract.get("source_grid_order") != 1:
        issues.append("ANCHOR_SOURCE_GRID_ORDER_MUST_BE_ONE")
    asset = contract.get("anchor_asset") if isinstance(contract.get("anchor_asset"), dict) else {}
    qc = contract.get("source_qc") if isinstance(contract.get("source_qc"), dict) else {}
    try:
        asset_path = project_file(project, asset.get("relative_path"))
        if not HEX64.fullmatch(str(asset.get("sha256", ""))) or sha256(asset_path) != asset.get("sha256"):
            issues.append("ANCHOR_ASSET_HASH_MISMATCH")
    except ValueError as exc:
        issues.append(str(exc))
    try:
        qc_path = project_file(project, qc.get("relative_path"))
        if not HEX64.fullmatch(str(qc.get("sha256", ""))) or sha256(qc_path) != qc.get("sha256"):
            issues.append("ANCHOR_QC_HASH_MISMATCH")
        else:
            qc_data = json.loads(qc_path.read_text(encoding="utf-8"))
            if qc_data.get("decision") != "PASSED" or qc_data.get("grid_id") != contract.get("source_grid_id"):
                issues.append("ANCHOR_SOURCE_QC_NOT_PASSED_OR_MISMATCHED")
            generated = qc_data.get("generated_output") if isinstance(qc_data.get("generated_output"), dict) else {}
            if generated.get("relative_path") != asset.get("relative_path") or str(generated.get("sha256", "")).lower() != str(asset.get("sha256", "")).lower():
                issues.append("ANCHOR_SOURCE_QC_OUTPUT_BINDING_MISMATCH")
    except ValueError as exc:
        issues.append(str(exc))
    locks = contract.get("locks") if isinstance(contract.get("locks"), dict) else {}
    if any(locks.get(key) is not True for key in ("person_identity", "animal_identity", "visual_style", "core_environment")):
        issues.append("ANCHOR_REQUIRED_LOCK_MISSING")
    if locks.get("action_structure") is not False:
        issues.append("ANCHOR_MUST_NOT_CONTROL_ACTION_STRUCTURE")
    if contract.get("portable") is not True:
        issues.append("ANCHOR_MUST_BE_PORTABLE")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    contract_path = project_file(project, args.contract)
    issues = validate(project, contract_path)
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
