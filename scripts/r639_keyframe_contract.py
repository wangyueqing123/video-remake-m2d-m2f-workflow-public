#!/usr/bin/env python3
"""Minimal R6.39 contract for state frames, contact frames, and QC scope."""

from __future__ import annotations

from typing import Any


SCHEMA = "R6.39-KEYFRAME-SNAPSHOT-1.0"
POLICY = "ONLY_EXPLICIT_PROOFS_ARE_HARD"
SNAPSHOT_TYPES = {"STATE", "CONTACT"}


def uses_r639_contract(plan: dict[str, Any]) -> bool:
    block = plan.get("keyframe_snapshot_contract")
    return isinstance(block, dict) and block.get("schema_version") == SCHEMA


def validate_plan(plan: dict[str, Any], *, require: bool = False) -> list[str]:
    issues: list[str] = []
    block = plan.get("keyframe_snapshot_contract")
    if not isinstance(block, dict):
        return ["R639_KEYFRAME_SNAPSHOT_CONTRACT_MISSING"] if require else []
    if block.get("schema_version") != SCHEMA:
        issues.append("R639_KEYFRAME_SNAPSHOT_SCHEMA_INVALID")
    if block.get("qc_policy") != POLICY:
        issues.append("R639_KEYFRAME_QC_POLICY_INVALID")

    grids = plan.get("grids") if isinstance(plan.get("grids"), list) else []
    contact_scenes: set[str] = set()
    for grid in grids:
        if not isinstance(grid, dict):
            continue
        grid_id = str(grid.get("grid_id", "GRID"))
        cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            number = cell.get("cell")
            label = f"{grid_id}_CELL_{number}"
            contract = cell.get("keyframe_contract")
            if not isinstance(contract, dict):
                issues.append(f"{label}_R639_KEYFRAME_CONTRACT_MISSING")
                continue
            snapshot_type = contract.get("snapshot_type")
            if snapshot_type not in SNAPSHOT_TYPES:
                issues.append(f"{label}_R639_SNAPSHOT_TYPE_INVALID")
            pair = contract.get("contact_pair")
            if not isinstance(pair, list):
                issues.append(f"{label}_R639_CONTACT_PAIR_INVALID")
                pair = []
            clean_pair = [str(value).strip() for value in pair if str(value).strip()]
            if snapshot_type == "CONTACT":
                contact_scenes.add(str(cell.get("scene_id", "")).strip())
                if len(clean_pair) != 2 or len(set(clean_pair)) != 2:
                    issues.append(f"{label}_R639_CONTACT_FRAME_REQUIRES_ONE_PAIR")
            elif clean_pair:
                issues.append(f"{label}_R639_STATE_FRAME_FORBIDS_CONTACT_PAIR")
            support_required = contract.get("support_proof_required")
            if not isinstance(support_required, bool):
                issues.append(f"{label}_R639_SUPPORT_PROOF_FLAG_INVALID")

            transition = cell.get("state_transition_contract") if isinstance(cell.get("state_transition_contract"), dict) else {}
            proof = cell.get("spatial_proof") if isinstance(cell.get("spatial_proof"), dict) else {}
            contact_expected = snapshot_type == "CONTACT"
            interaction_phase = cell.get("interaction_phase")
            if contact_expected and interaction_phase != "CONTACT":
                issues.append(f"{label}_R639_CONTACT_SNAPSHOT_PHASE_MISMATCH")
            if not contact_expected and interaction_phase == "CONTACT":
                issues.append(f"{label}_R639_STATE_SNAPSHOT_CANNOT_CLAIM_CONTACT")
            if transition.get("transition_visible") is not contact_expected:
                issues.append(f"{label}_R639_TRANSITION_VISIBILITY_DIFFERS_FROM_SNAPSHOT")
            if proof.get("critical_contact_visible") is not contact_expected:
                issues.append(f"{label}_R639_CONTACT_VISIBILITY_DIFFERS_FROM_SNAPSHOT")
            if proof.get("support_surfaces_visible") is not support_required:
                issues.append(f"{label}_R639_SUPPORT_VISIBILITY_DIFFERS_FROM_SCOPE")
    for scene in plan.get("scenes", []) if isinstance(plan.get("scenes"), list) else []:
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("scene_id", "")).strip()
        spatial = scene.get("spatial_contract") if isinstance(scene.get("spatial_contract"), dict) else {}
        decisive = spatial.get("decisive_contact") if isinstance(spatial.get("decisive_contact"), dict) else {}
        if decisive.get("must_be_visible") is True and scene_id not in contact_scenes:
            issues.append(f"{scene_id or 'SCENE'}_R639_REQUIRED_CONTACT_HAS_NO_CONTACT_SNAPSHOT")
    return sorted(set(issues))


def prompt_lines(cell: dict[str, Any]) -> list[str]:
    contract = cell.get("keyframe_contract") if isinstance(cell.get("keyframe_contract"), dict) else {}
    snapshot_type = contract.get("snapshot_type")
    support_required = contract.get("support_proof_required") is True
    if snapshot_type == "CONTACT":
        pair = [str(value).strip() for value in contract.get("contact_pair", []) if str(value).strip()]
        pair_text = f"{pair[0]}与{pair[1]}" if len(pair) == 2 else "合同指定对象之间"
        moment = f"本格是接触瞬间帧：{pair_text}的真实接触必须直接可见。"
    else:
        moment = "本格是状态关键帧：只画本格明确状态，不强制重演前一步接触过程。"
    if support_required:
        support = "本格明确要求支撑证明：合同列出的主体与支撑面接触必须完整可核验。"
    else:
        support = "本格不要求完整支撑面证明：允许为教学重点合理裁切，但仍禁止穿模、悬浮和不可能拓扑。"
    return [moment, support]


def prompt_proof(grid: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Prove that each cell received its own snapshot/QC instruction block."""
    rows: list[dict[str, Any]] = []
    cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        number = cell.get("cell")
        marker = f"第 {number} 格，"
        start = prompt.find(marker)
        next_marker = (
            f"第 {cells[index + 1].get('cell')} 格，"
            if index + 1 < len(cells) and isinstance(cells[index + 1], dict)
            else ""
        )
        end = prompt.find(next_marker, start + len(marker)) if next_marker and start >= 0 else -1
        block = prompt[start:(end if end >= 0 else len(prompt))] if start >= 0 else ""
        expected = prompt_lines(cell)
        missing = [line for line in expected if line not in block]
        rows.append({
            "cell": number,
            "snapshot_type": cell.get("keyframe_contract", {}).get("snapshot_type"),
            "expected_lines": expected,
            "missing_lines": missing,
            "status": "PASSED" if start >= 0 and not missing else "FAILED",
        })
    failed = [row for row in rows if row["status"] != "PASSED"]
    return {
        "schema_version": "R6.39-KEYFRAME-PROMPT-PROOF-1.0",
        "grid_id": grid.get("grid_id"),
        "status": "PASSED" if rows and not failed else "FAILED",
        "cells": rows,
    }


def validate_qc_cell(planned_cell: dict[str, Any], qc_cell: dict[str, Any]) -> list[str]:
    number = planned_cell.get("cell")
    issues: list[str] = []
    contract = planned_cell.get("keyframe_contract") if isinstance(planned_cell.get("keyframe_contract"), dict) else {}
    proof = planned_cell.get("spatial_proof") if isinstance(planned_cell.get("spatial_proof"), dict) else {}
    support_required = contract.get("support_proof_required") is True
    expected_supports = {
        (str(row.get("entity_id")), str(row.get("surface_id")))
        for row in proof.get("subject_supports", []) if support_required and isinstance(row, dict)
    }
    checks = qc_cell.get("support_surface_checks")
    observed_supports = {
        (str(row.get("entity_id")), str(row.get("expected_surface_id")))
        for row in checks if isinstance(checks, list) and isinstance(row, dict)
    } if isinstance(checks, list) else set()
    if expected_supports != observed_supports:
        issues.append(f"P6_QC_CELL_{number}_R639_SUPPORT_SCOPE_MISMATCH")
    if support_required and isinstance(checks, list):
        for row in checks:
            if not isinstance(row, dict) or row.get("visibility") not in {"VISIBLE", "NOT_APPLICABLE"} or row.get("result") not in {"PASSED", "REJECTED"}:
                issues.append(f"P6_QC_CELL_{number}_R639_SUPPORT_EVIDENCE_INVALID")
            elif qc_cell.get("result") == "PASSED" and (row.get("visibility") != "VISIBLE" or row.get("result") != "PASSED"):
                issues.append(f"P6_QC_CELL_{number}_R639_PASSED_WITHOUT_REQUIRED_SUPPORT")

    contact = qc_cell.get("critical_contact")
    contact_required = contract.get("snapshot_type") == "CONTACT"
    expected_pair = [str(value).strip() for value in contract.get("contact_pair", [])]
    if not isinstance(contact, dict) or contact.get("required") is not contact_required:
        issues.append(f"P6_QC_CELL_{number}_R639_CONTACT_REQUIREMENT_MISMATCH")
    elif contact_required:
        observed_pair = [str(value).strip() for value in contact.get("pair", [])] if isinstance(contact.get("pair"), list) else []
        if observed_pair != expected_pair:
            issues.append(f"P6_QC_CELL_{number}_R639_CONTACT_PAIR_MISMATCH")
        if qc_cell.get("result") == "PASSED" and (contact.get("visibility") != "VISIBLE" or contact.get("result") != "PASSED"):
            issues.append(f"P6_QC_CELL_{number}_R639_PASSED_WITHOUT_REQUIRED_CONTACT")
    codes = qc_cell.get("blocking_failure_codes")
    if not isinstance(codes, list) or any(not isinstance(code, str) or not code.strip() for code in codes):
        issues.append(f"P6_QC_CELL_{number}_R639_BLOCKING_CODES_INVALID")
        codes = []
    normalized_codes = [code.upper() for code in codes]
    if qc_cell.get("result") == "PASSED" and normalized_codes:
        issues.append(f"P6_QC_CELL_{number}_R639_PASSED_WITH_BLOCKING_CODES")
    if qc_cell.get("result") == "REJECTED" and not normalized_codes:
        issues.append(f"P6_QC_CELL_{number}_R639_REJECTED_WITHOUT_BLOCKING_CODE")
    if not support_required and any(
        "SUPPORT" in code and ("NOT_VISIBLE" in code or "MISSING" in code)
        for code in normalized_codes
    ):
        issues.append(f"P6_QC_CELL_{number}_R639_UNSCOPED_SUPPORT_VISIBILITY_FAILURE")
    if not contact_required and any(
        "CONTACT" in code and ("NOT_VISIBLE" in code or "MISSING" in code)
        for code in normalized_codes
    ):
        issues.append(f"P6_QC_CELL_{number}_R639_UNSCOPED_CONTACT_VISIBILITY_FAILURE")
    if qc_cell.get("topology_result") not in {"PASSED", "REJECTED"}:
        issues.append(f"P6_QC_CELL_{number}_TOPOLOGY_RESULT_INVALID")
    if qc_cell.get("result") == "PASSED" and qc_cell.get("topology_result") != "PASSED":
        issues.append(f"P6_QC_CELL_{number}_PASSED_WITH_FAILED_TOPOLOGY")
    return sorted(set(issues))
