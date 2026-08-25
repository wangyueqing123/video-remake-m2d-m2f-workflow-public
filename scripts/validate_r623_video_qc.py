#!/usr/bin/env python3
"""Require R6.23 Prompt, provider-identity and layout proofs before video QC can pass."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, now, sha256_file, write_json_atomic
from validate_r64_video_qc import validate as validate_base_qc
from validate_r623_video_prompt_audit import validate as validate_prompt_audit

sys.dont_write_bytecode = True


GATE_SCHEMA = "R6.23-P8-VIDEO-QC-GATE-1.0"


def project_file(project: Path, relative: str) -> Path:
    normalized = normalize_project_relative(relative)
    path = (project / normalized).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{normalized}")
    return path


def file_ref(project: Path, path: Path) -> dict[str, str]:
    resolved = path.resolve()
    resolved.relative_to(project)
    if not resolved.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{resolved.name}")
    return {
        "relative_path": resolved.relative_to(project).as_posix(),
        "sha256": sha256_file(resolved),
    }


def ref_file(project: Path, value: Any, label: str) -> Path:
    if not isinstance(value, dict):
        raise ValueError(f"{label}_REFERENCE_MISSING")
    path = project_file(project, str(value.get("relative_path", "")))
    if value.get("sha256") != sha256_file(path):
        raise ValueError(f"{label}_REFERENCE_HASH_MISMATCH")
    return path


def validate(project: Path, qc_path: Path, layout_path: Path, prompt_audit_path: Path, identity_path: Path) -> list[str]:
    issues = list(validate_base_qc(project, qc_path))
    qc = load_json(qc_path)
    layout = load_json(layout_path)
    audit = load_json(prompt_audit_path)
    identity = load_json(identity_path)
    segment_id = qc.get("segment_id")
    if layout.get("schema_version") != "R6.23-P8-VIDEO-LAYOUT-FORENSICS-1.0" or layout.get("segment_id") != segment_id:
        issues.append("R623_LAYOUT_RECEIPT_SCHEMA_OR_SEGMENT_INVALID")
    if layout.get("status") != "PASSED" or layout.get("persistent_split_screen_detected") is not False or layout.get("decision") != "PASSED_MACHINE_LAYOUT_SCREEN":
        issues.append("R623_LAYOUT_FORENSICS_NOT_PASSED")
    output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
    if layout.get("video", {}).get("relative_path") != output.get("relative_path") or layout.get("video", {}).get("sha256") != output.get("sha256"):
        issues.append("R623_LAYOUT_VIDEO_BINDING_MISMATCH")
    if audit.get("schema_version") != "R6.23-P7-VIDEO-PROMPT-AUDIT-1.0" or audit.get("status") != "PASSED" or audit.get("segment_id") != segment_id:
        issues.append("R623_PROMPT_AUDIT_NOT_PASSED_OR_SEGMENT_MISMATCH")
    package_path = project / f"artifacts/P7/{segment_id}_SEGMENT_PACKAGE_R623.json"
    if not package_path.is_file():
        issues.append("R623_CANONICAL_SEGMENT_PACKAGE_MISSING")
    else:
        prompt_issues = validate_prompt_audit(project, package_path)
        if prompt_issues:
            issues.extend(f"R623_PROMPT_PROOF:{issue}" for issue in prompt_issues)
        else:
            package = load_json(package_path)
            prompt_block = package.get("video_prompt") if isinstance(package.get("video_prompt"), dict) else {}
            if prompt_block.get("audit_relative_path") != prompt_audit_path.relative_to(project).as_posix() or prompt_block.get("audit_sha256") != sha256_file(prompt_audit_path):
                issues.append("R623_PROMPT_AUDIT_PACKAGE_BINDING_MISMATCH")
    if identity.get("schema_version") != "R6.22-P8-PROVIDER-TASK-IDENTITY-1.0" or identity.get("status") != "PASSED":
        issues.append("R623_PROVIDER_IDENTITY_PROOF_NOT_PASSED")
    identity_checks = identity.get("checks") if isinstance(identity.get("checks"), dict) else {}
    if not identity_checks or any(value is not True for value in identity_checks.values()) or identity.get("failed_checks") != []:
        issues.append("R623_PROVIDER_IDENTITY_INTERNAL_CHECKS_NOT_PASSED")
    if identity.get("segment_id") != segment_id or identity.get("task_id") != qc.get("task_id"):
        issues.append("R623_PROVIDER_IDENTITY_QC_BINDING_MISMATCH")
    if identity.get("expected", {}).get("prompt_sha256") != audit.get("prompt", {}).get("sha256"):
        issues.append("R623_PROVIDER_PROMPT_AUDIT_BINDING_MISMATCH")
    if qc.get("decision") == "PASSED" and issues:
        issues.append("R623_VIDEO_QC_CANNOT_PASS_WITH_MISSING_PREREQUISITE_PROOF")
    return sorted(set(issues))


def build_gate_receipt(project: Path, qc_path: Path, layout_path: Path, prompt_audit_path: Path, identity_path: Path) -> dict[str, Any]:
    issues = validate(project, qc_path, layout_path, prompt_audit_path, identity_path)
    if issues:
        raise ValueError("R623_VIDEO_QC_GATE_PREREQUISITES_FAILED:" + ",".join(issues))
    qc = load_json(qc_path)
    if qc.get("decision") != "PASSED" or qc.get("hard_visual_failures") != []:
        raise ValueError("R623_VIDEO_QC_GATE_REQUIRES_PASSED_QC")
    output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
    output_path = project_file(project, str(output.get("relative_path", "")))
    state = load_json(project / STATE_NAME)
    return {
        "schema_version": GATE_SCHEMA,
        "status": "PASSED",
        "decision": "PASSED_P9_ELIGIBLE",
        "created_at": now(),
        "project_id": state.get("project_id"),
        "segment_id": qc.get("segment_id"),
        "task_id": qc.get("task_id"),
        "external_calls": 0,
        "proofs": {
            "video_qc": file_ref(project, qc_path),
            "layout_forensics": file_ref(project, layout_path),
            "video_prompt_audit": file_ref(project, prompt_audit_path),
            "provider_task_identity": file_ref(project, identity_path),
            "generated_output": file_ref(project, output_path),
        },
    }


def validate_gate_receipt(project: Path, receipt_path: Path, segment_id: str | None = None, qc_path: Path | None = None) -> list[str]:
    issues: list[str] = []
    try:
        receipt = load_json(receipt_path)
        state = load_json(project / STATE_NAME)
        if (
            receipt.get("schema_version") != GATE_SCHEMA
            or receipt.get("status") != "PASSED"
            or receipt.get("decision") != "PASSED_P9_ELIGIBLE"
            or receipt.get("external_calls") != 0
        ):
            issues.append("R623_VIDEO_QC_GATE_SCHEMA_STATUS_OR_COST_INVALID")
        if receipt.get("project_id") != state.get("project_id"):
            issues.append("R623_VIDEO_QC_GATE_PROJECT_MISMATCH")
        if segment_id is not None and receipt.get("segment_id") != segment_id:
            issues.append("R623_VIDEO_QC_GATE_SEGMENT_MISMATCH")
        proofs = receipt.get("proofs") if isinstance(receipt.get("proofs"), dict) else {}
        receipt_qc = ref_file(project, proofs.get("video_qc"), "VIDEO_QC")
        layout = ref_file(project, proofs.get("layout_forensics"), "LAYOUT_FORENSICS")
        prompt_audit = ref_file(project, proofs.get("video_prompt_audit"), "VIDEO_PROMPT_AUDIT")
        identity = ref_file(project, proofs.get("provider_task_identity"), "PROVIDER_TASK_IDENTITY")
        output = ref_file(project, proofs.get("generated_output"), "GENERATED_OUTPUT")
        if qc_path is not None and receipt_qc.resolve() != qc_path.resolve():
            issues.append("R623_VIDEO_QC_GATE_SELECTED_QC_MISMATCH")
        qc = load_json(receipt_qc)
        generated = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
        if (
            receipt.get("segment_id") != qc.get("segment_id")
            or receipt.get("task_id") != qc.get("task_id")
            or generated.get("relative_path") != output.relative_to(project).as_posix()
            or generated.get("sha256") != sha256_file(output)
        ):
            issues.append("R623_VIDEO_QC_GATE_QC_OUTPUT_BINDING_MISMATCH")
        issues.extend(validate(project, receipt_qc, layout, prompt_audit, identity))
        if qc.get("decision") != "PASSED" or qc.get("hard_visual_failures") != []:
            issues.append("R623_VIDEO_QC_GATE_QC_NO_LONGER_PASSED")
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        issues.append(f"R623_VIDEO_QC_GATE_READ_ERROR:{exc}")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--qc", required=True)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--prompt-audit", required=True)
    parser.add_argument("--identity-proof", required=True)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    try:
        project = args.project_dir.resolve()
        qc_path = project_file(project, args.qc)
        layout_path = project_file(project, args.layout)
        prompt_audit_path = project_file(project, args.prompt_audit)
        identity_path = project_file(project, args.identity_proof)
        issues = validate(
            project,
            qc_path,
            layout_path,
            prompt_audit_path,
            identity_path,
        )
        receipt_result = None
        if not issues and args.receipt:
            receipt_path = (project / normalize_project_relative(args.receipt)).resolve()
            receipt_path.relative_to(project)
            receipt_result = build_gate_receipt(project, qc_path, layout_path, prompt_audit_path, identity_path)
            write_json_atomic(receipt_path, receipt_result)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
        receipt_result = None
    result: dict[str, Any] = {"status": "PASSED" if not issues else "FAILED", "issues": issues}
    if receipt_result is not None:
        result["gate_receipt"] = receipt_result
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
