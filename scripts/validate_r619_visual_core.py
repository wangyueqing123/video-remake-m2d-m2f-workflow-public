#!/usr/bin/env python3
"""Validate the shared visual/state-production core for M2-D and M2-F."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from validate_r619_state_contract import validate_plan as validate_state_plan
from r639_keyframe_contract import (
    prompt_lines as r639_prompt_lines,
    uses_r639_contract,
    validate_plan as validate_r639_plan,
    validate_qc_cell as validate_r639_qc_cell,
)

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "shared-visual-production-core.json"
CORE_ROUTES = {"M2_D_SHARE_FIRST", "M2_F_SOURCE_AUDIO_RESTYLE"}
EXPECTED_CORE_ID = "DOG_DF_VISUAL_CORE_V3"
ALLOWED_VISIBILITY = {"VISIBLE", "NOT_APPLICABLE"}


def load_core() -> dict[str, Any]:
    value = json.loads(CORE_PATH.read_text(encoding="utf-8"))
    if value.get("schema_version") != "R6.41.2-DF-SHARED-VISUAL-CORE-1.0":
        raise ValueError("R619_VISUAL_CORE_SCHEMA_INVALID")
    if value.get("core_id") != EXPECTED_CORE_ID or value.get("status") != "FROZEN":
        raise ValueError("R619_VISUAL_CORE_ID_OR_STATUS_INVALID")
    if set(value.get("applies_to_routes", [])) != CORE_ROUTES:
        raise ValueError("R619_VISUAL_CORE_ROUTE_SET_INVALID")
    return value


def validate_plan(job: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    route = job.get("route_id")
    if route not in CORE_ROUTES:
        return []
    issues: list[str] = []
    r639 = uses_r639_contract(plan)
    core = load_core()
    binding = plan.get("visual_production_core")
    if not isinstance(binding, dict):
        return ["R619_VISUAL_CORE_BINDING_MISSING"]
    if binding.get("core_id") != core["core_id"]:
        issues.append("R619_VISUAL_CORE_ID_MISMATCH")
    if binding.get("contract_relative_path") != "assets/shared-visual-production-core.json":
        issues.append("R619_VISUAL_CORE_PATH_INVALID")
    if binding.get("all_scenes_preflighted") is not True:
        issues.append("R619_ALL_SCENES_PREFLIGHT_REQUIRED")
    if binding.get("downstream_feasibility_status") != "PASSED":
        issues.append("R619_DOWNSTREAM_FEASIBILITY_NOT_PASSED")

    scenes = plan.get("scenes") if isinstance(plan.get("scenes"), list) else []
    scene_map: dict[str, dict[str, Any]] = {}
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("scene_id", "")).strip() or f"SCENE_{index}"
        scene_map[scene_id] = scene
        spatial = scene.get("spatial_contract")
        if not isinstance(spatial, dict):
            issues.append(f"{scene_id}_SPATIAL_CONTRACT_MISSING")
            continue
        subjects = spatial.get("subject_supports")
        if not isinstance(subjects, list) or not subjects:
            issues.append(f"{scene_id}_SUBJECT_SUPPORTS_MISSING")
            subjects = []
        subject_ids: set[str] = set()
        for row in subjects:
            if not isinstance(row, dict):
                issues.append(f"{scene_id}_SUBJECT_SUPPORT_INVALID")
                continue
            entity_id = str(row.get("entity_id", "")).strip()
            surface_id = str(row.get("surface_id", "")).strip()
            if not entity_id or entity_id in subject_ids or not surface_id:
                issues.append(f"{scene_id}_SUBJECT_SUPPORT_ID_OR_SURFACE_INVALID")
            subject_ids.add(entity_id)
            if row.get("must_remain_on_surface") is not True:
                issues.append(f"{scene_id}_{entity_id or 'SUBJECT'}_SUPPORT_NOT_HARD_LOCKED")
            if not r639 and row.get("support_must_be_visible") is not True:
                issues.append(f"{scene_id}_{entity_id or 'SUBJECT'}_SUPPORT_VISIBILITY_NOT_REQUIRED")
        if "DOG" not in subject_ids:
            issues.append(f"{scene_id}_DOG_SUPPORT_MISSING")

        props = spatial.get("prop_supports")
        if not isinstance(props, list):
            issues.append(f"{scene_id}_PROP_SUPPORTS_INVALID")
        else:
            for row in props:
                if not isinstance(row, dict) or not str(row.get("entity_id", "")).strip() or not str(row.get("surface_id", "")).strip():
                    issues.append(f"{scene_id}_PROP_SUPPORT_INVALID")

        path = spatial.get("action_path")
        if not isinstance(path, dict) or len(str(path.get("description", "")).strip()) < 8:
            issues.append(f"{scene_id}_ACTION_PATH_MISSING")
        else:
            if path.get("open") is not True:
                issues.append(f"{scene_id}_ACTION_PATH_NOT_OPEN")
            if path.get("requires_climbing") is not False:
                issues.append(f"{scene_id}_ACTION_PATH_REQUIRES_CLIMBING")
            if path.get("requires_penetration") is not False:
                issues.append(f"{scene_id}_ACTION_PATH_REQUIRES_PENETRATION")

        contact = spatial.get("decisive_contact")
        if not isinstance(contact, dict) or len(str(contact.get("description", "")).strip()) < 4:
            issues.append(f"{scene_id}_DECISIVE_CONTACT_MISSING")
        else:
            if contact.get("reachable_without_climbing") is not True:
                issues.append(f"{scene_id}_DECISIVE_CONTACT_NOT_REACHABLE_WITHOUT_CLIMBING")
            if contact.get("reachable_without_penetration") is not True:
                issues.append(f"{scene_id}_DECISIVE_CONTACT_NOT_REACHABLE_WITHOUT_PENETRATION")
            if contact.get("must_be_visible") is not True:
                issues.append(f"{scene_id}_DECISIVE_CONTACT_VISIBILITY_NOT_REQUIRED")
        proofs = spatial.get("camera_proofs")
        if not isinstance(proofs, list) or len(proofs) < 2 or any(len(str(value).strip()) < 4 for value in proofs):
            issues.append(f"{scene_id}_CAMERA_PROOFS_INSUFFICIENT")
        if spatial.get("feasibility_status") != "PASSED":
            issues.append(f"{scene_id}_SPATIAL_FEASIBILITY_NOT_PASSED")

    grids = plan.get("grids") if isinstance(plan.get("grids"), list) else []
    for grid in grids:
        if not isinstance(grid, dict):
            continue
        grid_id = str(grid.get("grid_id", "GRID"))
        for cell in grid.get("cells", []):
            if not isinstance(cell, dict):
                continue
            cell_number = cell.get("cell")
            scene = scene_map.get(str(cell.get("scene_id", "")), {})
            spatial = scene.get("spatial_contract") if isinstance(scene.get("spatial_contract"), dict) else {}
            expected_ids = {
                str(row.get("entity_id")) for row in spatial.get("subject_supports", [])
                if isinstance(row, dict) and str(row.get("entity_id", "")).strip()
            }
            proof = cell.get("spatial_proof")
            if not isinstance(proof, dict):
                issues.append(f"{grid_id}_CELL_{cell_number}_SPATIAL_PROOF_MISSING")
                continue
            supports = proof.get("subject_supports")
            actual_ids = {
                str(row.get("entity_id")) for row in supports
                if isinstance(supports, list) and isinstance(row, dict) and str(row.get("entity_id", "")).strip()
            } if isinstance(supports, list) else set()
            if expected_ids != actual_ids:
                issues.append(f"{grid_id}_CELL_{cell_number}_SUBJECT_SUPPORT_COVERAGE_MISMATCH")
            if not r639 and proof.get("support_surfaces_visible") is not True:
                issues.append(f"{grid_id}_CELL_{cell_number}_SUPPORT_SURFACES_NOT_VISIBLE")
            if proof.get("topology_verifiable") is not True:
                issues.append(f"{grid_id}_CELL_{cell_number}_TOPOLOGY_NOT_VERIFIABLE")
            if not r639 and cell.get("interaction_phase") == "CONTACT" and proof.get("critical_contact_visible") is not True:
                issues.append(f"{grid_id}_CELL_{cell_number}_CRITICAL_CONTACT_NOT_VISIBLE")
    if r639:
        issues.extend(validate_r639_plan(plan, require=True))
    issues.extend(validate_state_plan(job, plan))
    return issues


def spatial_prompt_lines(plan: dict[str, Any], scene: dict[str, Any], cell: dict[str, Any]) -> list[str]:
    spatial = scene.get("spatial_contract") if isinstance(scene.get("spatial_contract"), dict) else {}
    supports = []
    for row in spatial.get("subject_supports", []):
        if isinstance(row, dict):
            if uses_r639_contract(plan):
                supports.append(f"{row.get('entity_id')}保持由{row.get('surface_id')}正常支撑")
            else:
                supports.append(f"{row.get('entity_id')}全程由{row.get('surface_id')}支撑且接触处可见")
    for row in spatial.get("prop_supports", []):
        if isinstance(row, dict):
            supports.append(f"{row.get('entity_id')}稳定放在{row.get('surface_id')}上")
    path = spatial.get("action_path") if isinstance(spatial.get("action_path"), dict) else {}
    contact = spatial.get("decisive_contact") if isinstance(spatial.get("decisive_contact"), dict) else {}
    lines = []
    if supports:
        lines.append("空间支撑硬锁：" + "；".join(supports) + "。")
    lines.append("唯一动作通道：" + str(path.get("description", "")) + "；不得攀爬道具，不得穿过实体。")
    if uses_r639_contract(plan):
        lines.append("决定性接触：" + str(contact.get("description", "")) + "；只有标记为接触瞬间帧的格子才强制显示接触过程。")
    else:
        lines.append("决定性接触：" + str(contact.get("description", "")) + "；接触过程与支撑面必须同时清楚可见。")
    if uses_r639_contract(plan):
        lines.append("验真镜头：整段动作通道和拓扑必须可核验；接触与支撑的逐格可见范围只服从本格关键帧合同。")
    else:
        lines.append("验真镜头必须覆盖：" + "；".join(str(value) for value in spatial.get("camera_proofs", [])) + "。")
    proof = cell.get("spatial_proof") if isinstance(cell.get("spatial_proof"), dict) else {}
    cell_supports = [
        f"{row.get('entity_id')}={row.get('surface_id')}"
        for row in proof.get("subject_supports", []) if isinstance(row, dict)
    ]
    if uses_r639_contract(plan):
        lines.append(" ".join(r639_prompt_lines(cell)))
    else:
        lines.append("本格支撑证明：" + "；".join(cell_supports) + "；任何脚爪、手或关键接触被遮挡都视为不合格。")
    return lines


def validate_qc_cell(planned_cell: dict[str, Any], qc_cell: dict[str, Any]) -> list[str]:
    if isinstance(planned_cell.get("keyframe_contract"), dict):
        return validate_r639_qc_cell(planned_cell, qc_cell)
    cell_number = planned_cell.get("cell")
    issues: list[str] = []
    proof = planned_cell.get("spatial_proof") if isinstance(planned_cell.get("spatial_proof"), dict) else {}
    expected = {
        (str(row.get("entity_id")), str(row.get("surface_id")))
        for row in proof.get("subject_supports", []) if isinstance(row, dict)
    }
    checks = qc_cell.get("support_surface_checks")
    observed = {
        (str(row.get("entity_id")), str(row.get("expected_surface_id")))
        for row in checks if isinstance(checks, list) and isinstance(row, dict)
    } if isinstance(checks, list) else set()
    if expected != observed:
        issues.append(f"P6_QC_CELL_{cell_number}_SUPPORT_EVIDENCE_COVERAGE_MISMATCH")
    if isinstance(checks, list):
        for row in checks:
            if not isinstance(row, dict) or row.get("visibility") not in ALLOWED_VISIBILITY or row.get("result") not in {"PASSED", "REJECTED"}:
                issues.append(f"P6_QC_CELL_{cell_number}_SUPPORT_EVIDENCE_INVALID")
            elif qc_cell.get("result") == "PASSED" and (row.get("visibility") != "VISIBLE" or row.get("result") != "PASSED"):
                issues.append(f"P6_QC_CELL_{cell_number}_PASSED_WITHOUT_VISIBLE_SUPPORT_PROOF")
    contact = qc_cell.get("critical_contact")
    required = planned_cell.get("interaction_phase") == "CONTACT"
    if not isinstance(contact, dict) or contact.get("required") is not required:
        issues.append(f"P6_QC_CELL_{cell_number}_CRITICAL_CONTACT_EVIDENCE_INVALID")
    elif required and qc_cell.get("result") == "PASSED" and (contact.get("visibility") != "VISIBLE" or contact.get("result") != "PASSED"):
        issues.append(f"P6_QC_CELL_{cell_number}_PASSED_WITHOUT_VISIBLE_CONTACT_PROOF")
    if qc_cell.get("topology_result") not in {"PASSED", "REJECTED"}:
        issues.append(f"P6_QC_CELL_{cell_number}_TOPOLOGY_RESULT_INVALID")
    if qc_cell.get("result") == "PASSED" and qc_cell.get("topology_result") != "PASSED":
        issues.append(f"P6_QC_CELL_{cell_number}_PASSED_WITH_FAILED_TOPOLOGY")
    return issues
