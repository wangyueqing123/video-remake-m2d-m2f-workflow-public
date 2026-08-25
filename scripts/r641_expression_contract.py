#!/usr/bin/env python3
"""R6.41 practical core-expression policy with an 80-point visual floor."""

from __future__ import annotations

from typing import Any


SCHEMA = "R6.41-EXPRESSION-ACCEPTANCE-1.0"
REVIEW_SCHEMA = "R6.41-P6-EXPRESSION-REVIEW-1.0"
THRESHOLD = 80
SOFT_DEVIATION_CLASSES = {
    "TRANSITION_VISIBILITY_DEGREE",
    "OCCLUDED_COUNT_PRECISION",
    "NON_TEXT_DECORATIVE_DETAIL",
    "MINOR_POSITION_DRIFT",
}
CORE_CHECKS = {
    "main_action_expressed",
    "causal_order_preserved",
    "subject_identity_preserved",
    "scene_continuity_preserved",
    "topology_valid",
    "safety_logic_valid",
}
PARTIAL_MARKERS = ("PARTIAL", "COVERED", "OCCLUDED", "UNDER_HAND", "UNDER HAND")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def uses_r641_contract(plan: dict[str, Any]) -> bool:
    block = plan.get("expression_acceptance_contract")
    return isinstance(block, dict) and block.get("schema_version") == SCHEMA


def visual_proof_mode(entity: dict[str, Any], state_row: dict[str, Any]) -> str:
    """Separate ledger truth from what a single static frame must visibly prove."""
    if state_row.get("visible") is not True:
        return "NOT_VISIBLE"
    material = " ".join(_text(state_row.get(key)).upper() for key in ("state", "location", "prompt_lock"))
    if any(marker in material for marker in PARTIAL_MARKERS):
        return "OCCLUDED_OR_PARTIAL"
    if entity.get("quantity_policy") == "NOT_COUNTED":
        return "GENERAL_VISIBLE"
    return "EXACT_VISIBLE"


def visibility_prompt(state_row: dict[str, Any], *, compact: bool) -> str | None:
    if state_row.get("expression_proof_policy") != "CORE_EXPRESSION_80":
        return None
    material = " ".join(_text(state_row.get(key)).upper() for key in ("state", "location", "prompt_lock"))
    if state_row.get("visible") is True and any(marker in material for marker in PARTIAL_MARKERS):
        return (
            "Clearly show that the objects remain partly occluded; do not require every object to be fully exposed or individually countable"
            if compact
            else "This cell only needs to clearly express that the objects remain partly occluded; it does not need to expose or count every object individually"
        )
    return None


def quantity_prompt(entity: dict[str, Any], state_row: dict[str, Any]) -> str | None:
    if state_row.get("expression_proof_policy") != "CORE_EXPRESSION_80":
        return None
    if visual_proof_mode(entity, state_row) in {"NOT_VISIBLE", "OCCLUDED_OR_PARTIAL", "GENERAL_VISIBLE"}:
        return "The state ledger preserves the total quantity; this cell does not require a complete visual count"
    return None


def validate_plan(plan: dict[str, Any], *, require: bool = False) -> list[str]:
    issues: list[str] = []
    block = plan.get("expression_acceptance_contract")
    if not isinstance(block, dict):
        return ["R641_EXPRESSION_ACCEPTANCE_CONTRACT_MISSING"] if require else []
    if block.get("schema_version") != SCHEMA:
        issues.append("R641_EXPRESSION_ACCEPTANCE_SCHEMA_INVALID")
    if block.get("visual_acceptance_threshold") != THRESHOLD:
        issues.append("R641_VISUAL_ACCEPTANCE_THRESHOLD_MUST_BE_80")
    if block.get("decision_rule") != "CORE_EXPRESSION_FIRST":
        issues.append("R641_DECISION_RULE_INVALID")
    if block.get("transition_precision_policy") != "SOFT_UNLESS_CORE_MEANING_LOST":
        issues.append("R641_TRANSITION_PRECISION_POLICY_INVALID")
    if block.get("decorative_detail_policy") != "SOFT_UNLESS_TEXT_BRAND_OR_OBSTRUCTION":
        issues.append("R641_DECORATIVE_DETAIL_POLICY_INVALID")
    if block.get("automatic_retry_allowed") is not False:
        issues.append("R641_AUTOMATIC_RETRY_MUST_BE_FALSE")
    for grid in plan.get("grids", []) if isinstance(plan.get("grids"), list) else []:
        if not isinstance(grid, dict):
            continue
        grid_id = _text(grid.get("grid_id")) or "GRID"
        ledger = grid.get("state_ledger") if isinstance(grid.get("state_ledger"), dict) else {}
        for cell_state in ledger.get("cell_states", []) if isinstance(ledger.get("cell_states"), list) else []:
            if not isinstance(cell_state, dict):
                continue
            cell = cell_state.get("cell")
            for row in cell_state.get("states", []) if isinstance(cell_state.get("states"), list) else []:
                if isinstance(row, dict) and row.get("expression_proof_policy") != "CORE_EXPRESSION_80":
                    issues.append(f"{grid_id}_CELL_{cell}_R641_EXPRESSION_PROOF_POLICY_MISSING")
    return sorted(set(issues))


def validate_expression_review(review: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if review.get("schema_version") != REVIEW_SCHEMA:
        issues.append("R641_EXPRESSION_REVIEW_SCHEMA_INVALID")
    score = review.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        issues.append("R641_EXPRESSION_SCORE_INVALID")
    elif score < THRESHOLD:
        issues.append("R641_EXPRESSION_SCORE_BELOW_80")
    checks = review.get("core_expression_checks")
    if not isinstance(checks, dict) or set(checks) != CORE_CHECKS or any(checks.get(key) is not True for key in CORE_CHECKS):
        issues.append("R641_CORE_EXPRESSION_CHECK_FAILED")
    hard = review.get("hard_failures")
    if not isinstance(hard, list) or hard:
        issues.append("R641_HARD_FAILURE_PRESENT")
    deviations = review.get("soft_deviations")
    if not isinstance(deviations, list):
        issues.append("R641_SOFT_DEVIATIONS_INVALID")
    else:
        for index, row in enumerate(deviations, start=1):
            if (
                not isinstance(row, dict)
                or row.get("class") not in SOFT_DEVIATION_CLASSES
                or not _text(row.get("code"))
                or not _text(row.get("evidence"))
                or row.get("affects_core_meaning") is not False
            ):
                issues.append(f"R641_SOFT_DEVIATION_{index}_INVALID")
    if review.get("terminal_state_safe") is not True:
        issues.append("R641_TERMINAL_STATE_NOT_SAFE")
    if review.get("decision") != "ACCEPT_AT_80_PLUS" or review.get("automatic_retry_allowed") is not False:
        issues.append("R641_EXPRESSION_DECISION_INVALID")
    return sorted(set(issues))
