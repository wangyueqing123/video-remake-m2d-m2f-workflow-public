#!/usr/bin/env python3
"""Write the zero-call R6.34 state-flow preflight receipt for one project."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from r62_project import STATE_NAME, load_json, sha256_file, write_json_atomic
from r634_integrity_contract import audit_segment_state_flow, resolve_effective_inputs


OUTPUT = "artifacts/P4/SEGMENT_STATE_FLOW_AUDIT_R634.json"


def run(project: Path) -> dict[str, object]:
    project = project.resolve()
    state_path = project / STATE_NAME
    state = load_json(state_path)
    _, plan_path, fact_lineage = resolve_effective_inputs(project, state)
    plan = load_json(plan_path)
    receipt = audit_segment_state_flow(plan)
    receipt.update({
        "project_id": state.get("project_id"),
        "skill_version": state.get("skill_version"),
        "scene_plan_relative_path": plan_path.relative_to(project).as_posix(),
        "scene_plan_sha256": sha256_file(plan_path),
        "accepted_deviation_fact_contracts": fact_lineage,
    })
    output_path = project / OUTPUT
    write_json_atomic(output_path, receipt)
    state.setdefault("artifacts", {}).setdefault("P4", {})["SEGMENT_STATE_FLOW_AUDIT_R634"] = {
        "relative_path": OUTPUT,
        "sha256": sha256_file(output_path),
        "bytes": output_path.stat().st_size,
        "validator": "audit_r634_project_integrity.py",
        "validation_status": "VALIDATED" if receipt["status"] == "PASSED" else "REJECTED",
    }
    state.setdefault("events", []).append({
        "type": "R634_SEGMENT_STATE_FLOW_AUDITED",
        "detail": {
            "status": receipt["status"],
            "receipt_relative_path": OUTPUT,
            "receipt_sha256": sha256_file(output_path),
            "provider_calls": 0,
        },
    })
    write_json_atomic(state_path, state)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(args.project_dir)
        code = 0 if result.get("status") == "PASSED" else 1
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result = {"status": "BLOCKED_P0", "error": str(exc), "provider_calls": 0}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
