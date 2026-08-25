#!/usr/bin/env python3
"""Validate R6.19 stable identity and mutable visual-state transitions."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from r639_keyframe_contract import uses_r639_contract
from r641_expression_contract import quantity_prompt as r641_quantity_prompt
from r641_expression_contract import visibility_prompt as r641_visibility_prompt


CORE_ROUTES = {"M2_D_SHARE_FIRST", "M2_F_SOURCE_AUDIO_RESTYLE"}
ALLOWED_PHASES = {"HOLD", "TRANSFER", "TERMINAL"}
ALLOWED_INTERACTION_PHASES = {"BEFORE_CONTACT", "CONTACT", "AFTER_CONTACT", "NOT_APPLICABLE"}
IDENTITY_STATE_MARKERS = (
    "始终在", "放在", "盘中", "嘴里", "嘴中", "为空", "空盘", "已吞", "吞下",
    "打开状态", "关闭状态", "已上锁", "未上锁",
    "IN_PLATE", "IN_MOUTH", "EMPTY", "SWALLOWED", "OPEN", "CLOSED", "LOCKED",
)
TERMINAL_MARKERS = (
    "NOT_VISIBLE", "FINISHED", "CONSUMED", "EXHAUSTED", "CLOSED", "LOCKED",
    "EXITED", "COVERED", "REMOVED", "ABSENT", "SWALLOWED", "DEACTIVATED",
)

MODEL_LOCATION_LABELS = {
    "DOG_MOUTH": "狗嘴处",
    "INSIDE_DOG_SWALLOWED": "已吞入狗体内",
    "LOW_TABLE_FRONT_EDGE": "低桌前缘",
    "LOW_TABLE_TOP": "低桌桌面",
    "LOW_PLATFORM": "低矮平台",
    "OPEN_GROUND_PATH": "开放地面路径",
    "TREAT_IN_PLATE": "浅盘内",
    "TREAT_IN_MOUTH": "狗嘴处",
    "SWALLOWED": "已吞入狗体内",
}
COUNT_UNIT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
CAUSAL_FLAG_MARKERS = ("ORDER", "BEFORE", "AFTER", "THEN", "CAUSAL", "CONDITION")
CAUSAL_ZH_PATTERN = re.compile(r"(?:先.{0,60}(?:再|才|后)|只有.{0,60}才|之后|以后|后才)")
CAUSAL_EN_PATTERN = re.compile(r"\b(?:after|before|only\s+when|then)\b", re.IGNORECASE)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_terminal_state(value: Any) -> bool:
    upper = _text(value).upper()
    return any(upper == marker or upper.startswith(marker + "_") or upper.endswith("_" + marker) for marker in TERMINAL_MARKERS)


def _model_location(value: Any) -> str:
    token = _text(value)
    if not token:
        return "未定义位置"
    if token in MODEL_LOCATION_LABELS:
        return MODEL_LOCATION_LABELS[token]
    return token.replace("_", " ")


def _quantity_proof(entity: dict[str, Any], count: Any, state_row: dict[str, Any] | None = None) -> str:
    if isinstance(state_row, dict):
        practical = r641_quantity_prompt(entity, state_row)
        if practical:
            return practical
    unit = _text(entity.get("count_unit")) or "ENTITY"
    if count == 0:
        return f"数量严格为0 {unit}"
    return f"数量严格为{count} {unit}"


def _render_identity(entity: dict[str, Any]) -> str:
    signature = _text(entity.get("visual_signature"))
    label = _text(entity.get("label")) or _text(entity.get("entity_id")) or "跟踪对象"
    # R6.33 declares every independently visible prop once in the global visual
    # identity.  Repeating the complete signature in both the state table and
    # every cell wastes prompt budget without adding a new constraint.  The
    # semantic audit below still proves that every signature occurs globally.
    if signature and entity.get("visibility_unit") == "ATOMIC":
        return label
    return f"{label}（固定可视外观：{signature}）" if signature else label


def _visibility_proof(state_row: dict[str, Any], *, compact: bool) -> str:
    practical = r641_visibility_prompt(state_row, compact=compact)
    if practical:
        return practical
    if state_row.get("visible") is not True:
        return "必须完全不可见" if compact else "本格画面任何位置均不可见"
    mouth_state = _text(state_row.get("location")) in {"DOG_MOUTH", "TREAT_IN_MOUTH"}
    if mouth_state:
        return (
            "必须清楚可见且有可辨识部分露在狗嘴外"
            if compact else
            "本格画面必须清楚可见，并有可辨识部分露在狗嘴外"
        )
    return "必须清楚可见" if compact else "本格画面必须清楚可见"


def state_semantic_clause(entity: dict[str, Any], state_row: dict[str, Any]) -> str:
    """Compile one ledger row into an explicit, model-readable visual proof."""
    label = _render_identity(entity)
    visibility = _visibility_proof(state_row, compact=False)
    return (
        f"{label}状态证据：{_text(state_row.get('prompt_lock'))}；{visibility}；"
        f"{_quantity_proof(entity, state_row.get('count'), state_row)}；位置在{_model_location(state_row.get('location'))}"
    )


def state_cell_visual_clause(entity: dict[str, Any], state_row: dict[str, Any]) -> str:
    """Repeat only the renderable fields inside one cell to keep prompts compact."""
    label = _render_identity(entity)
    visibility = _visibility_proof(state_row, compact=True)
    quantity = _quantity_proof(entity, state_row.get("count"), state_row)
    return f"{label}格内证明：{visibility}；{quantity}；位置在{_model_location(state_row.get('location'))}"


def state_transition_contrast_clauses(job: dict[str, Any], grid: dict[str, Any]) -> list[str]:
    """Emit adjacent-cell contrast locks so a model cannot advance a state early."""
    mutable_ids = mutable_entity_ids(job)
    entities, rows = _state_rows(grid)
    clauses: list[str] = []
    for cell_number in range(2, len(grid.get("cells", [])) + 1):
        previous = rows.get(cell_number - 1, {})
        current = rows.get(cell_number, {})
        for entity_id in sorted(mutable_ids):
            before = previous.get(entity_id, {})
            after = current.get(entity_id, {})
            before_key = (before.get("state"), before.get("visible"), before.get("count"), before.get("location"))
            after_key = (after.get("state"), after.get("visible"), after.get("count"), after.get("location"))
            if before_key == after_key:
                continue
            label = _text(entities.get(entity_id, {}).get("label")) or entity_id
            if before.get("visible") is True and after.get("visible") is False:
                clauses.append(
                    f"第{cell_number - 1}格到第{cell_number}格是{label}的唯一可见性边界："
                    f"第{cell_number - 1}格结束前仍必须清楚可见；只有第{cell_number}格才可首次不可见；禁止提前或回退"
                )
            elif before.get("visible") is False and after.get("visible") is True:
                clauses.append(
                    f"第{cell_number - 1}格到第{cell_number}格是{label}的唯一可见性边界："
                    f"第{cell_number - 1}格必须保持不可见；只有第{cell_number}格才可首次清楚可见；禁止提前或回退"
                )
            else:
                clauses.append(
                    f"第{cell_number - 1}格到第{cell_number}格是{label}的唯一状态边界："
                    f"第{cell_number - 1}格保持“{_text(before.get('prompt_lock'))}”；"
                    f"只有第{cell_number}格才改为“{_text(after.get('prompt_lock'))}”；禁止提前或回退"
                )
    return clauses


def causal_prompt_clauses(grid: dict[str, Any]) -> list[str]:
    clauses: list[str] = []
    for proof in grid.get("causal_proofs", []):
        if not isinstance(proof, dict):
            continue
        condition_cell = proof.get("condition_cell")
        result_cell = proof.get("result_cell")
        condition_visual = _text(proof.get("condition_visual"))
        result_visual = _text(proof.get("result_visual"))
        if not all((isinstance(condition_cell, int), isinstance(result_cell, int), condition_visual, result_visual)):
            continue
        clauses.append(
            f"第{condition_cell}格必须先清楚证明“{condition_visual}”；"
            f"只有之后的第{result_cell}格才可显示“{result_visual}”；禁止把结果提前到条件格"
        )
    return clauses


def semantic_prompt_audit(job: dict[str, Any], grid: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Independently prove that every ledger field reached its exact cell block."""
    mutable_ids = mutable_entity_ids(job)
    entities, rows = _state_rows(grid)
    cells = [row for row in grid.get("cells", []) if isinstance(row, dict) and isinstance(row.get("cell"), int)]
    proofs: list[dict[str, Any]] = []
    issues: list[str] = []
    expected_material: list[dict[str, Any]] = []
    for entity_id in sorted(mutable_ids):
        signature = _text(entities.get(entity_id, {}).get("visual_signature"))
        if signature and prompt.count(signature) < 1:
            issues.append(f"{entity_id}_VISUAL_SIGNATURE_NOT_COMPILED")
    for index, cell in enumerate(cells):
        number = cell["cell"]
        start_token = f"第 {number} 格，"
        start = prompt.find(start_token)
        next_start = prompt.find(f"第 {cells[index + 1]['cell']} 格，") if index + 1 < len(cells) else len(prompt)
        block = prompt[start:next_start] if start >= 0 and next_start >= start else ""
        for entity_id, state_row in sorted(rows.get(number, {}).items()):
            entity = entities.get(entity_id, {})
            table_clause = state_semantic_clause(entity, state_row)
            cell_clause = state_cell_visual_clause(entity, state_row)
            in_cell_block = cell_clause in block
            in_state_table = entity_id not in mutable_ids or table_clause in prompt[:start]
            visibility_phrase = _visibility_proof(state_row, compact=True)
            proof = {
                "cell": number,
                "entity_id": entity_id,
                "visible": state_row.get("visible"),
                "count": state_row.get("count"),
                "count_unit": entity.get("count_unit"),
                "visual_signature": entity.get("visual_signature"),
                "location": state_row.get("location"),
                "state_table_clause_sha256": hashlib.sha256(table_clause.encode("utf-8")).hexdigest(),
                "cell_clause_sha256": hashlib.sha256(cell_clause.encode("utf-8")).hexdigest(),
                "cell_block_compiled": in_cell_block,
                "state_table_compiled": in_state_table,
                "visibility_literal_compiled": visibility_phrase in cell_clause and visibility_phrase in block,
            }
            proofs.append(proof)
            expected_material.append({
                "cell": number,
                "entity_id": entity_id,
                "state_table_clause": table_clause if entity_id in mutable_ids else None,
                "cell_clause": cell_clause,
            })
            if not all((in_cell_block, in_state_table, proof["visibility_literal_compiled"])):
                issues.append(f"CELL_{number}_{entity_id}_SEMANTIC_STATE_PROOF_MISSING")
    contrasts = state_transition_contrast_clauses(job, grid)
    contrast_proofs = []
    for clause in contrasts:
        compiled = prompt.count(clause) == 1
        contrast_proofs.append({
            "clause_sha256": hashlib.sha256(clause.encode("utf-8")).hexdigest(),
            "compiled_once": compiled,
        })
        if not compiled:
            issues.append("STATE_TRANSITION_CONTRAST_MISSING_OR_DUPLICATED")
    causal_clauses = causal_prompt_clauses(grid)
    causal_proofs = []
    for clause in causal_clauses:
        compiled = prompt.count(clause) == 1
        causal_proofs.append({
            "clause_sha256": hashlib.sha256(clause.encode("utf-8")).hexdigest(),
            "compiled_once": compiled,
        })
        if not compiled:
            issues.append("CAUSAL_PROOF_MISSING_OR_DUPLICATED")
    fingerprint_payload = {
        "state_proofs": expected_material,
        "transition_contrasts": contrasts,
        "causal_proofs": causal_clauses,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "R6.21-SEMANTIC-PROMPT-AUDIT-1.0",
        "status": "PASSED" if not issues else "FAILED",
        "expected_state_proof_count": len(proofs),
        "verified_state_proof_count": sum(
            1 for row in proofs
            if row["cell_block_compiled"] and row["state_table_compiled"] and row["visibility_literal_compiled"]
        ),
        "state_proofs": proofs,
        "transition_contrast_count": len(contrasts),
        "transition_contrasts": contrast_proofs,
        "causal_proof_count": len(causal_clauses),
        "causal_proofs": causal_proofs,
        "semantic_fingerprint": fingerprint,
        "issues": sorted(set(issues)),
    }


def mutable_entity_ids(job: dict[str, Any]) -> set[str]:
    contract = job.get("visual_state_contract") if isinstance(job.get("visual_state_contract"), dict) else {}
    values = contract.get("mutable_entity_ids") if isinstance(contract.get("mutable_entity_ids"), list) else []
    return {_text(value) for value in values if _text(value)}


def physical_prop_specs(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contract = job.get("visual_state_contract") if isinstance(job.get("visual_state_contract"), dict) else {}
    rows = contract.get("physical_prop_specs") if isinstance(contract.get("physical_prop_specs"), list) else []
    return {
        _text(row.get("entity_id")): row
        for row in rows
        if isinstance(row, dict) and _text(row.get("entity_id"))
    }


def validate_job(job: dict[str, Any]) -> list[str]:
    if job.get("route_id") not in CORE_ROUTES:
        return []
    issues: list[str] = []
    contract = job.get("visual_state_contract")
    if not isinstance(contract, dict):
        return ["R619_VISUAL_STATE_CONTRACT_MISSING"]
    if contract.get("schema_version") not in {
        "R6.19-VISUAL-STATE-CONTRACT-1.0",
        "R6.32-VISUAL-STATE-CONTRACT-1.0",
        "R6.33-VISUAL-STATE-CONTRACT-1.0",
    }:
        issues.append("R619_VISUAL_STATE_CONTRACT_SCHEMA_INVALID")
    if contract.get("identity_scope") != "STABLE_ATTRIBUTES_ONLY":
        issues.append("R619_IDENTITY_SCOPE_MUST_BE_STABLE_ONLY")
    if contract.get("mutable_state_authority") != "P4_STATE_LEDGER":
        issues.append("R619_MUTABLE_STATE_AUTHORITY_INVALID")

    identity = job.get("project_identity") if isinstance(job.get("project_identity"), dict) else {}
    props = identity.get("props") if isinstance(identity.get("props"), list) else []
    stable = contract.get("stable_prop_descriptions")
    if not isinstance(stable, list) or any(not _text(value) for value in stable):
        issues.append("R619_STABLE_PROP_DESCRIPTIONS_INVALID")
        stable = []
    if props != stable:
        issues.append("R619_PROJECT_PROPS_MUST_EQUAL_STABLE_DESCRIPTIONS")
    for index, description in enumerate(stable, start=1):
        upper = _text(description).upper()
        if any(marker.upper() in upper for marker in IDENTITY_STATE_MARKERS):
            issues.append(f"R619_STABLE_PROP_{index}_CONTAINS_MUTABLE_STATE")

    mutable = contract.get("mutable_entity_ids")
    if not isinstance(mutable, list) or any(not _text(value) for value in mutable):
        issues.append("R619_MUTABLE_ENTITY_IDS_INVALID")
    elif len(set(mutable)) != len(mutable):
        issues.append("R619_MUTABLE_ENTITY_IDS_DUPLICATE")

    if isinstance(mutable, list) and mutable:
        specs = contract.get("physical_prop_specs")
        if contract.get("schema_version") not in {
            "R6.32-VISUAL-STATE-CONTRACT-1.0",
            "R6.33-VISUAL-STATE-CONTRACT-1.0",
        }:
            issues.append("R632_MUTABLE_PROPS_REQUIRE_R632_PHYSICAL_CONTRACT")
        if not isinstance(specs, list) or not specs:
            issues.append("R632_PHYSICAL_PROP_SPECS_MISSING")
            specs = []
        spec_ids: set[str] = set()
        signatures: list[str] = []
        for index, spec in enumerate(specs, start=1):
            if not isinstance(spec, dict):
                issues.append(f"R632_PHYSICAL_PROP_SPEC_{index}_INVALID")
                continue
            entity_id = _text(spec.get("entity_id"))
            count_unit = _text(spec.get("count_unit"))
            signature = _text(spec.get("visual_signature"))
            visibility_unit = _text(spec.get("visibility_unit"))
            if not entity_id or entity_id in spec_ids:
                issues.append(f"R632_PHYSICAL_PROP_SPEC_{index}_ID_INVALID_OR_DUPLICATE")
            else:
                spec_ids.add(entity_id)
            if not COUNT_UNIT_PATTERN.fullmatch(count_unit):
                issues.append(f"R632_PHYSICAL_PROP_SPEC_{index}_COUNT_UNIT_INVALID")
            if len(signature) < 4:
                issues.append(f"R632_PHYSICAL_PROP_SPEC_{index}_VISUAL_SIGNATURE_INVALID")
            if contract.get("schema_version") == "R6.33-VISUAL-STATE-CONTRACT-1.0" and visibility_unit != "ATOMIC":
                issues.append(f"R633_PHYSICAL_PROP_SPEC_{index}_VISIBILITY_UNIT_NOT_ATOMIC")
            signatures.append(signature)
        if len({value.casefold() for value in signatures if value}) != len([value for value in signatures if value]):
            issues.append("R632_PHYSICAL_PROP_VISUAL_SIGNATURES_NOT_UNIQUE")
        if not set(mutable).issubset(spec_ids):
            issues.append("R632_MUTABLE_ENTITY_MISSING_PHYSICAL_PROP_SPEC")
        if stable != signatures or props != signatures:
            issues.append("R632_PROJECT_PROPS_MUST_EQUAL_STRUCTURED_VISUAL_SIGNATURES")
    return sorted(set(issues))


def _state_rows(grid: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, dict[str, Any]]]]:
    ledger = grid.get("state_ledger") if isinstance(grid.get("state_ledger"), dict) else {}
    entities = {
        _text(row.get("entity_id")): row
        for row in ledger.get("tracked_entities", [])
        if isinstance(row, dict) and _text(row.get("entity_id"))
    }
    rows: dict[int, dict[str, dict[str, Any]]] = {}
    for cell_row in ledger.get("cell_states", []):
        if not isinstance(cell_row, dict) or not isinstance(cell_row.get("cell"), int):
            continue
        rows[cell_row["cell"]] = {
            _text(state.get("entity_id")): state
            for state in cell_row.get("states", [])
            if isinstance(state, dict) and _text(state.get("entity_id"))
        }
    return entities, rows


def derive_changes(
    mutable_ids: set[str],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for entity_id in sorted(mutable_ids):
        before = _text(previous.get(entity_id, {}).get("state"))
        after = _text(current.get(entity_id, {}).get("state"))
        if before and after and before != after:
            changes.append({"entity_id": entity_id, "from": before, "to": after})
    return changes


def expected_phase(changes: list[dict[str, str]]) -> str:
    if not changes:
        return "HOLD"
    return "TERMINAL" if any(_is_terminal_state(row["to"]) for row in changes) else "TRANSFER"


def validate_plan(job: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    if job.get("route_id") not in CORE_ROUTES:
        return []
    issues: list[str] = []
    r639 = uses_r639_contract(plan)
    mutable_ids = mutable_entity_ids(job)
    job_specs = physical_prop_specs(job)
    for grid in plan.get("grids", []):
        if not isinstance(grid, dict):
            continue
        grid_id = _text(grid.get("grid_id")) or "GRID"
        entities, state_rows = _state_rows(grid)
        if not mutable_ids.issubset(entities):
            issues.append(f"{grid_id}_R619_MUTABLE_ENTITY_NOT_TRACKED")
        for entity_id in sorted(mutable_ids):
            entity = entities.get(entity_id, {})
            spec = job_specs.get(entity_id, {})
            if entity.get("kind") != "PROP":
                issues.append(f"{grid_id}_{entity_id}_R632_MUTABLE_ENTITY_MUST_BE_PROP")
                continue
            if not _text(entity.get("count_unit")) or not _text(entity.get("visual_signature")):
                issues.append(f"{grid_id}_{entity_id}_R632_RENDER_SPEC_MISSING")
            if entity.get("count_unit") != spec.get("count_unit"):
                issues.append(f"{grid_id}_{entity_id}_R632_COUNT_UNIT_DIFFERS_FROM_P1")
            if entity.get("visual_signature") != spec.get("visual_signature"):
                issues.append(f"{grid_id}_{entity_id}_R632_VISUAL_SIGNATURE_DIFFERS_FROM_P1")
            if job.get("visual_state_contract", {}).get("schema_version") == "R6.33-VISUAL-STATE-CONTRACT-1.0":
                if entity.get("visibility_unit") != "ATOMIC":
                    issues.append(f"{grid_id}_{entity_id}_R633_VISIBILITY_UNIT_NOT_ATOMIC")
                if entity.get("visibility_unit") != spec.get("visibility_unit"):
                    issues.append(f"{grid_id}_{entity_id}_R633_VISIBILITY_UNIT_DIFFERS_FROM_P1")
        changed_prop_ids: set[str] = set()
        for cell_number in range(2, len(state_rows) + 1):
            previous_row = state_rows.get(cell_number - 1, {})
            current_row = state_rows.get(cell_number, {})
            for entity_id, entity in entities.items():
                if entity.get("kind") != "PROP":
                    continue
                if _text(previous_row.get(entity_id, {}).get("state")) != _text(current_row.get(entity_id, {}).get("state")):
                    changed_prop_ids.add(entity_id)
        if not changed_prop_ids.issubset(mutable_ids):
            issues.append(f"{grid_id}_R619_CHANGING_PROP_NOT_DECLARED_MUTABLE")
        cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
        for index, cell in enumerate(cells, start=1):
            if not isinstance(cell, dict):
                continue
            previous = state_rows.get(index - 1, {}) if index > 1 else state_rows.get(index, {})
            current = state_rows.get(index, {})
            derived = derive_changes(mutable_ids, previous, current) if index > 1 else []
            phase = expected_phase(derived)
            contract = cell.get("state_transition_contract")
            if not isinstance(contract, dict):
                issues.append(f"{grid_id}_CELL_{index}_R619_STATE_TRANSITION_CONTRACT_MISSING")
                continue
            if contract.get("phase") not in ALLOWED_PHASES or contract.get("phase") != phase:
                issues.append(f"{grid_id}_CELL_{index}_R619_STATE_TRANSITION_PHASE_MISMATCH")
            changes = contract.get("changes") if isinstance(contract.get("changes"), list) else None
            if changes != derived:
                issues.append(f"{grid_id}_CELL_{index}_R619_STATE_TRANSITION_CHANGES_MISMATCH")
            snapshot_type = (
                cell.get("keyframe_contract", {}).get("snapshot_type")
                if r639 and isinstance(cell.get("keyframe_contract"), dict)
                else None
            )
            expected_visible = snapshot_type == "CONTACT" if r639 else phase != "HOLD"
            if contract.get("transition_visible") is not expected_visible:
                issues.append(f"{grid_id}_CELL_{index}_R619_STATE_TRANSITION_VISIBILITY_MISMATCH")
            interaction_phase = cell.get("interaction_phase")
            if interaction_phase not in ALLOWED_INTERACTION_PHASES:
                issues.append(f"{grid_id}_CELL_{index}_R619_INTERACTION_PHASE_INVALID")
            proof = cell.get("spatial_proof") if isinstance(cell.get("spatial_proof"), dict) else {}
            expected_contact = snapshot_type == "CONTACT" if r639 else interaction_phase == "CONTACT"
            if proof.get("critical_contact_visible") is not expected_contact:
                issues.append(f"{grid_id}_CELL_{index}_R619_CONTACT_FLAG_CONTRADICTS_INTERACTION_PHASE")
            if r639:
                if expected_contact and interaction_phase != "CONTACT":
                    issues.append(f"{grid_id}_CELL_{index}_R639_CONTACT_SNAPSHOT_PHASE_MISMATCH")
                if not expected_contact and interaction_phase == "CONTACT":
                    issues.append(f"{grid_id}_CELL_{index}_R639_STATE_SNAPSHOT_CANNOT_CLAIM_CONTACT")
            else:
                if phase == "TRANSFER" and interaction_phase != "CONTACT":
                    issues.append(f"{grid_id}_CELL_{index}_R619_TRANSFER_MUST_OCCUR_AT_CONTACT")
                if phase == "TERMINAL" and interaction_phase not in {"CONTACT", "AFTER_CONTACT"}:
                    issues.append(f"{grid_id}_CELL_{index}_R619_TERMINAL_CANNOT_PRECEDE_CONTACT")
        issues.extend(validate_causal_proofs(plan, grid))
    return sorted(set(issues))


def _causal_source_texts(plan: dict[str, Any], grid: dict[str, Any]) -> list[str]:
    segment_id = _text(grid.get("segment_id"))
    segment = next(
        (row for row in plan.get("video_segments", []) if isinstance(row, dict) and _text(row.get("segment_id")) == segment_id),
        {},
    )
    texts = [_text(value) for value in segment.get("content_obligations", [])]
    texts.extend(
        _text(node.get("action"))
        for node in segment.get("action_nodes", [])
        if isinstance(node, dict)
    )
    scene_ids = set(segment.get("scene_ids", []))
    for scene in plan.get("scenes", []):
        if not isinstance(scene, dict) or scene.get("scene_id") not in scene_ids:
            continue
        texts.extend(
            _text(contract.get("causal_order"))
            for contract in scene.get("inherited_action_contracts", [])
            if isinstance(contract, dict)
        )
    return [value for value in texts if value]


def _requires_causal_proof(plan: dict[str, Any], grid: dict[str, Any]) -> bool:
    decision = grid.get("layout_decision") if isinstance(grid.get("layout_decision"), dict) else {}
    flags = [_text(value).upper() for value in decision.get("complexity_flags", [])]
    if any(any(marker in flag for marker in CAUSAL_FLAG_MARKERS) for flag in flags):
        return True
    return any(
        CAUSAL_ZH_PATTERN.search(value) or CAUSAL_EN_PATTERN.search(value)
        for value in _causal_source_texts(plan, grid)
    )


def validate_causal_proofs(plan: dict[str, Any], grid: dict[str, Any]) -> list[str]:
    """Require condition and result to be visible in different ordered cells."""
    grid_id = _text(grid.get("grid_id")) or "GRID"
    proofs = grid.get("causal_proofs")
    required = _requires_causal_proof(plan, grid)
    if not required and proofs is None:
        return []
    if not isinstance(proofs, list) or not proofs:
        return [f"{grid_id}_R632_CAUSAL_PROOF_MISSING"]
    cells = {
        row.get("cell"): row
        for row in grid.get("cells", [])
        if isinstance(row, dict) and isinstance(row.get("cell"), int)
    }
    issues: list[str] = []
    proof_ids: set[str] = set()
    for index, proof in enumerate(proofs, start=1):
        if not isinstance(proof, dict):
            issues.append(f"{grid_id}_R632_CAUSAL_PROOF_{index}_INVALID")
            continue
        proof_id = _text(proof.get("proof_id"))
        condition_cell = proof.get("condition_cell")
        result_cell = proof.get("result_cell")
        condition_visual = _text(proof.get("condition_visual"))
        result_visual = _text(proof.get("result_visual"))
        if not proof_id or proof_id in proof_ids:
            issues.append(f"{grid_id}_R632_CAUSAL_PROOF_{index}_ID_INVALID_OR_DUPLICATE")
        proof_ids.add(proof_id)
        if (
            not isinstance(condition_cell, int)
            or isinstance(condition_cell, bool)
            or not isinstance(result_cell, int)
            or isinstance(result_cell, bool)
            or condition_cell not in cells
            or result_cell not in cells
            or condition_cell >= result_cell
        ):
            issues.append(f"{grid_id}_{proof_id or index}_R632_CAUSAL_CELL_ORDER_INVALID")
            continue
        if len(condition_visual) < 2 or condition_visual not in _text(cells[condition_cell].get("visual_statement")):
            issues.append(f"{grid_id}_{proof_id or index}_R632_CONDITION_VISUAL_NOT_PROVEN")
        if len(result_visual) < 2 or result_visual not in _text(cells[result_cell].get("visual_statement")):
            issues.append(f"{grid_id}_{proof_id or index}_R632_RESULT_VISUAL_NOT_PROVEN")
    return sorted(set(issues))


def compile_state_table(job: dict[str, Any], grid: dict[str, Any]) -> str:
    mutable_ids = mutable_entity_ids(job)
    if not mutable_ids:
        return ""
    entities, rows = _state_rows(grid)
    lines = ["可变物体状态表（本表是格间状态的唯一权威，身份描述不得改写本表）："]
    for cell in grid.get("cells", []):
        if not isinstance(cell, dict) or not isinstance(cell.get("cell"), int):
            continue
        number = cell["cell"]
        contract = cell.get("state_transition_contract") if isinstance(cell.get("state_transition_contract"), dict) else {}
        state_parts = []
        for entity_id in sorted(mutable_ids):
            row = rows.get(number, {}).get(entity_id, {})
            state_parts.append(state_semantic_clause(entities.get(entity_id, {}), row))
        phase_label = {"HOLD": "保持，不发生转移", "TRANSFER": "本格发生唯一可见转移", "TERMINAL": "本格完成终态"}.get(contract.get("phase"), "状态未定义")
        lines.append(f"第{number}格—{phase_label}；" + "；".join(state_parts) + "。")
    return "\n".join(lines)
