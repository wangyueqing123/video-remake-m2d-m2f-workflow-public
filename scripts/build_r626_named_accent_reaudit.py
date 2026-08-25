#!/usr/bin/env python3
"""Build a non-executable, zero-provider-call R6.26 re-audit bundle."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

ROOT = Path(__file__).resolve().parent.parent

from accent_color_contract import missing_accent_descriptors
from validate_r62_job import validate_job


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_json(command: list[str]) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    payload = json.loads(result.stdout)
    if result.returncode != 0:
        raise ValueError(f"ZERO_CALL_REAUDIT_COMMAND_FAILED:{payload}")
    return payload


def copy_evidence(source: Path, destination: Path, relative: str) -> Path:
    src = source / relative
    if not src.is_file():
        raise ValueError(f"SOURCE_EVIDENCE_MISSING:{relative}")
    dst = destination / relative
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", required=True, type=Path)
    parser.add_argument("--destination-bundle", required=True, type=Path)
    parser.add_argument("--grid-id", required=True)
    parser.add_argument("--output-image", required=True)
    args = parser.parse_args()
    source = args.source_project.resolve()
    destination = args.destination_bundle.resolve()
    try:
        if not source.is_dir() or destination.exists():
            raise ValueError("SOURCE_MISSING_OR_DESTINATION_ALREADY_EXISTS")
        source_state_path = source / "R62_PROJECT.json"
        source_job_path = source / "artifacts/P1/JOB.json"
        source_state = json.loads(source_state_path.read_text(encoding="utf-8"))
        source_job = json.loads(source_job_path.read_text(encoding="utf-8"))
        if source_state.get("skill_version") not in {"R6.23", "R6.24", "R6.25"}:
            raise ValueError("SOURCE_SKILL_VERSION_NOT_ELIGIBLE")
        output_relative = Path(args.output_image).as_posix()
        if Path(output_relative).is_absolute() or ".." in Path(output_relative).parts:
            raise ValueError("OUTPUT_IMAGE_MUST_BE_PROJECT_RELATIVE")

        fixed_job = copy.deepcopy(source_job)
        identity = fixed_job.get("project_identity")
        if not isinstance(identity, dict):
            raise ValueError("PROJECT_IDENTITY_MISSING")
        added_accents = missing_accent_descriptors(identity)
        accents = identity.get("accent_colors")
        if not isinstance(accents, list):
            raise ValueError("ACCENT_COLORS_MISSING")
        accents.extend(item for item in added_accents if item not in accents)
        issues = validate_job(fixed_job)
        if issues:
            raise ValueError(f"FIXED_JOB_VALIDATION_FAILED:{issues}")

        destination.mkdir(parents=True, exist_ok=False)
        history = destination / "source-evidence"
        history.mkdir()
        shutil.copy2(source_state_path, history / "SOURCE_R62_PROJECT.json")
        shutil.copy2(source_job_path, history / "SOURCE_JOB.json")
        for old_receipt in sorted((source / "artifacts/P6").glob(f"{args.grid_id}*.json")):
            shutil.copy2(old_receipt, history / old_receipt.name)

        write_json(destination / "artifacts/P1/JOB.json", fixed_job)
        copy_evidence(source, destination, "artifacts/P2/TIMELINE_EVIDENCE.json")
        copy_evidence(source, destination, "artifacts/P4/SCENE_PLAN.json")
        output_path = copy_evidence(source, destination, output_relative)
        write_json(destination / "R62_PROJECT.json", {
            "schema_version": "R6.26-NONEXECUTABLE-AUDIT-CONTEXT-1.0",
            "skill_version": "R6.26",
            "bundle_type": "ZERO_CALL_REAUDIT_ONLY",
            "provider_call_authorized": False,
            "mode_lock": {"job_relative_path": "artifacts/P1/JOB.json"},
        })

        prompt_relative = f"artifacts/P5/{args.grid_id}_GRID_PROMPT_R626.txt"
        prompt_audit_relative = f"artifacts/P5/{args.grid_id}_GRID_PROMPT_R626_AUDIT.json"
        run_json([
            sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/compile_r62_grid_prompt.py"),
            "--job", str(destination / "artifacts/P1/JOB.json"),
            "--evidence", str(destination / "artifacts/P2/TIMELINE_EVIDENCE.json"),
            "--plan", str(destination / "artifacts/P4/SCENE_PLAN.json"),
            "--styles", str(ROOT / "assets/style-registry.json"),
            "--grid-id", args.grid_id,
            "--output", str(destination / prompt_relative),
            "--audit-output", str(destination / prompt_audit_relative),
        ])
        style_audit_relative = f"artifacts/P6/{args.grid_id}_STYLE_AUDIT_R626.json"
        style_audit = run_json([
            sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/audit_r69_style_output.py"),
            "--project-dir", str(destination),
            "--output-image", output_relative,
            "--anchor-image", output_relative,
            "--styles", str(ROOT / "assets/style-registry.json"),
            "--style-id", str(fixed_job.get("style_profile")),
            "--receipt", style_audit_relative,
        ])

        report = {
            "schema_version": "R6.26-NAMED-ACCENT-ZERO-CALL-REAUDIT-1.0",
            "status": "PASSED",
            "bundle_type": "NONEXECUTABLE_AUDIT_EVIDENCE",
            "source_project_id": source_state.get("project_id"),
            "source_skill_version": source_state.get("skill_version"),
            "source_state_sha256": sha256_file(source_state_path),
            "source_job_sha256": sha256_file(source_job_path),
            "fixed_job_sha256": sha256_file(destination / "artifacts/P1/JOB.json"),
            "added_entity_scoped_accents": added_accents,
            "grid_id": args.grid_id,
            "reused_output": {"relative_path": output_relative, "sha256": sha256_file(output_path)},
            "compiled_prompt": {"relative_path": prompt_relative, "sha256": sha256_file(destination / prompt_relative)},
            "compiled_prompt_audit": {"relative_path": prompt_audit_relative, "sha256": sha256_file(destination / prompt_audit_relative)},
            "style_audit": {
                "relative_path": style_audit_relative,
                "sha256": sha256_file(destination / style_audit_relative),
                "metrics": style_audit.get("output", {}).get("metrics"),
            },
            "provider_calls": 0,
            "provider_call_authorized": False,
            "next_action": "HUMAN_REVIEW_REQUIRED_BEFORE_CREATING_A_NEW_EXECUTABLE_PROJECT",
        }
        write_json(destination / "R626_ZERO_CALL_REAUDIT.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_P0", "error": str(exc), "provider_calls": 0}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
