#!/usr/bin/env python3
"""Finalize the P5 visual-anchor review after G01 produces a validated anchor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from r62_project import append_event, load_json, resolve_project_file, sha256_file, skill_tree_fingerprint, write_json_atomic
from validate_r66_visual_anchor import validate as validate_visual_anchor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--review", default="artifacts/P5/P5_R66_VISUAL_ANCHOR_REVIEW.json")
    parser.add_argument("--contract", default="artifacts/P6/PROJECT_VISUAL_ANCHOR.json")
    args = parser.parse_args()

    project = args.project_dir.resolve()
    state_path = project / "R62_PROJECT.json"
    state = load_json(state_path)
    tree_sha256, tree_file_count = skill_tree_fingerprint()
    if state.get("skill", {}).get("tree_sha256") != tree_sha256 or state.get("skill", {}).get("tree_file_count") != tree_file_count:
        raise SystemExit("PROJECT_SKILL_TREE_BINDING_STALE")
    if state.get("pending_qc_submission_id") is not None or state.get("resume_contract", {}).get("provider_call_authorized") is not False:
        raise SystemExit("ANCHOR_REVIEW_FINALIZATION_REQUIRES_NO_PENDING_OR_AUTHORIZED_CALL")

    review_relative, review_path = resolve_project_file(project, args.review)
    contract_relative, contract_path = resolve_project_file(project, args.contract)
    review = load_json(review_path)
    contract = load_json(contract_path)
    issues = validate_visual_anchor(project, contract_path)
    if issues:
        raise SystemExit("VISUAL_ANCHOR_CONTRACT_INVALID:" + ",".join(issues))
    if (
        review.get("schema_version") != "R6.6-P5-VISUAL-ANCHOR-REVIEW-1.0"
        or review.get("status") != "PASSED"
        or review.get("job_id") != state.get("project_id")
        or review.get("anchor_contract_relative_path") != contract_relative
        or contract.get("job_id") != state.get("project_id")
        or contract.get("status") != "VALIDATED"
    ):
        raise SystemExit("P5_VISUAL_ANCHOR_REVIEW_OR_CONTRACT_BINDING_INVALID")
    affected = review.get("affected_grids") if isinstance(review.get("affected_grids"), list) else []
    if not affected or [row.get("grid_order") for row in affected if isinstance(row, dict)] != list(range(1, len(affected) + 1)):
        raise SystemExit("P5_VISUAL_ANCHOR_REVIEW_GRID_ORDER_INVALID")

    contract_hash = sha256_file(contract_path)
    current_hash = str(review.get("anchor_contract_sha256", ""))
    if current_hash not in {"", contract_hash}:
        raise SystemExit("P5_VISUAL_ANCHOR_REVIEW_ALREADY_BOUND_TO_DIFFERENT_CONTRACT")
    review["anchor_contract_sha256"] = contract_hash
    review["finalization"] = {
        "method": "DETERMINISTIC_POST_G01_ANCHOR_HASH_BINDING",
        "provider_calls": 0,
        "automatic_retry_allowed": False,
    }
    write_json_atomic(review_path, review)
    review_hash = sha256_file(review_path)

    rows = state.get("artifacts", {}).get("P5", {})
    matches = [
        row for row in rows.values() if isinstance(row, dict) and row.get("relative_path") == review_relative
    ] if isinstance(rows, dict) else []
    if len(matches) != 1:
        raise SystemExit("P5_VISUAL_ANCHOR_REVIEW_LEDGER_RECORD_NOT_UNIQUE")
    matches[0].update({
        "sha256": review_hash,
        "bytes": review_path.stat().st_size,
        "validator": "finalize_r66_visual_anchor_review.py",
        "validation_status": "VALIDATED",
    })
    append_event(state, "P5_VISUAL_ANCHOR_REVIEW_FINALIZED", {
        "review_relative_path": review_relative,
        "review_sha256": review_hash,
        "anchor_contract_relative_path": contract_relative,
        "anchor_contract_sha256": contract_hash,
        "provider_calls": 0,
    })
    write_json_atomic(state_path, state)
    print(json.dumps({
        "status": "PASSED",
        "review_relative_path": review_relative,
        "review_sha256": review_hash,
        "anchor_contract_relative_path": contract_relative,
        "anchor_contract_sha256": contract_hash,
        "provider_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
