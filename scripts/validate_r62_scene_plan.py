#!/usr/bin/env python3
"""Validate R6.2 scene slices, square-grid beats, and per-cell state ledgers."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from validate_r62_job import load_json, validate_job
from validate_r62_timeline_evidence import canonical_fingerprint, validate_timeline_evidence
from validate_r619_visual_core import validate_plan as validate_r619_visual_core
from r634_integrity_contract import validate_segment_state_flow
from r641_expression_contract import validate_plan as validate_r641_expression_contract


LAYOUT_CAPACITY = {"2x2": 4, "3x3": 9, "4x4": 16, "5x5": 25}
ALLOWED_BEATS = {"VIDEO_START", "SCENE_SETUP", "ANCHOR", "DECISIVE_ACTION", "SCENE_RESULT", "NARRATIVE_BRIDGE"}
ALLOWED_TEMPORAL_PHASES = {"BEFORE_ACTION", "ACTION_SETUP", "ACTION", "AFTER_ACTION", "STATIC_RESULT", "CUT_PRE", "CUT_POST"}
ALLOWED_SEGMENT_BOUNDARIES = {"START", "NONE", "END"}
ALLOWED_GRID_ROLES = {"SEGMENT_ACTION_AUTHORITY"}
ALLOWED_ENTITY_KINDS = {"PROP", "ACTOR", "ANIMAL", "PORTAL", "SURFACE", "ENVIRONMENT_ANCHOR"}
ALLOWED_QUANTITY_POLICIES = {"EXACT_ONE", "AT_MOST_ONE", "NOT_COUNTED"}
ALLOWED_CONTINUITY_SCOPES = {"PROJECT", "SEGMENT"}
ALLOWED_BOUNDARY_TRANSITIONS = {"VIDEO_START", "CONTINUOUS", "HARD_CUT", "VIDEO_END"}
FORBIDDEN_BEATS = {"MICRO_ACTION", "FILLER", "STYLE_ONLY"}
SOURCE_BOUND_ROUTES: set[str] = set()
SEMANTIC_ROUTES = {"M2_D_SHARE_FIRST"}
COPY_DERIVED_ROUTES = {"M2_F_SOURCE_AUDIO_RESTYLE"}
COMPLETE_SCENE_ROUTES = SEMANTIC_ROUTES | COPY_DERIVED_ROUTES
TOLERANCE = 0.06
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
TERMINAL_STATE_MARKERS = (
    "NOT_VISIBLE", "FINISHED", "CONSUMED", "EXHAUSTED", "CLOSED", "LOCKED",
    "EXITED", "COVERED", "REMOVED", "ABSENT", "SWALLOWED", "DEACTIVATED",
)
TERMINAL_STATE_PHRASES = ("已吃完", "已耗尽", "已关闭", "已上锁", "已遮盖", "已移除", "已消失", "已吞咽")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _terminal_state(value: Any) -> bool:
    state = _text(value)
    upper = state.upper()
    english = any(
        upper == marker or upper.startswith(marker + "_") or upper.endswith("_" + marker)
        for marker in TERMINAL_STATE_MARKERS
    )
    return english or any(phrase in state for phrase in TERMINAL_STATE_PHRASES)


def _micro_action_only(text: str) -> bool:
    compact = "".join(text.split())
    exact = {
        "伸手", "收手", "抬手", "手靠近", "手接触", "转头", "看向", "眨眼",
        "抬爪", "落爪", "抬脚", "落脚", "开锁扣", "碰锁扣", "拉一下", "推一下",
    }
    return compact in exact or len(compact) < 5


def _layout_for_beat_count(count: int) -> str | None:
    if 2 <= count <= 4:
        return "2x2"
    if 5 <= count <= 9:
        return "3x3"
    if 10 <= count <= 16:
        return "4x4"
    if 17 <= count <= 25:
        return "5x5"
    return None


def _has_fixed_duration_split_marker(split_reason: str) -> bool:
    """Reject explicit time-slice markers without confusing ordinal words with units."""
    upper = split_reason.upper()
    if re.search(r"(?:^|[_:\-\s])FIXED(?:$|[_:\-\s])", upper):
        return True
    return bool(
        re.search(
            r"(?:^|[_:\-\s])\d+(?:\.\d+)?[_\-\s]*(?:SEC|SECS|SECOND|SECONDS)(?:$|[_:\-\s])",
            upper,
        )
    )


def _validate_state_ledger(grid: dict[str, Any], cells: list[Any], grid_id: str, issues: list[str]) -> None:
    ledger = grid.get("state_ledger")
    if not isinstance(ledger, dict):
        issues.append(f"{grid_id}_STATE_LEDGER_MISSING")
        return

    entities = ledger.get("tracked_entities")
    if not isinstance(entities, list) or not entities:
        issues.append(f"{grid_id}_TRACKED_ENTITIES_MISSING")
        entities = []
    entity_map: dict[str, dict[str, Any]] = {}
    for index, entity in enumerate(entities, start=1):
        if not isinstance(entity, dict):
            issues.append(f"{grid_id}_TRACKED_ENTITY_{index}_INVALID")
            continue
        entity_id = _text(entity.get("entity_id"))
        if not entity_id or entity_id in entity_map:
            issues.append(f"{grid_id}_TRACKED_ENTITY_{index}_ID_INVALID_OR_DUPLICATE")
            continue
        entity_map[entity_id] = entity
        if entity.get("kind") not in ALLOWED_ENTITY_KINDS:
            issues.append(f"{grid_id}_{entity_id}_KIND_INVALID")
        if not _text(entity.get("label")):
            issues.append(f"{grid_id}_{entity_id}_LABEL_MISSING")
        if entity.get("quantity_policy") not in ALLOWED_QUANTITY_POLICIES:
            issues.append(f"{grid_id}_{entity_id}_QUANTITY_POLICY_INVALID")
        if entity.get("continuity_scope") not in ALLOWED_CONTINUITY_SCOPES:
            issues.append(f"{grid_id}_{entity_id}_CONTINUITY_SCOPE_INVALID")
        allowed_states = entity.get("allowed_states")
        if not isinstance(allowed_states, list) or not allowed_states or any(not _text(state) for state in allowed_states):
            issues.append(f"{grid_id}_{entity_id}_ALLOWED_STATES_INVALID")
        elif len(set(allowed_states)) != len(allowed_states):
            issues.append(f"{grid_id}_{entity_id}_ALLOWED_STATES_DUPLICATE")

    transitions = ledger.get("allowed_transitions")
    if not isinstance(transitions, list):
        issues.append(f"{grid_id}_ALLOWED_TRANSITIONS_INVALID")
        transitions = []
    allowed_transition_set: set[tuple[str, str, str]] = set()
    for index, transition in enumerate(transitions, start=1):
        if not isinstance(transition, dict):
            issues.append(f"{grid_id}_TRANSITION_{index}_INVALID")
            continue
        entity_id = _text(transition.get("entity_id"))
        from_state = _text(transition.get("from"))
        to_state = _text(transition.get("to"))
        entity = entity_map.get(entity_id)
        if entity is None:
            issues.append(f"{grid_id}_TRANSITION_{index}_UNKNOWN_ENTITY")
            continue
        allowed_states = set(entity.get("allowed_states", []))
        if from_state not in allowed_states or to_state not in allowed_states or from_state == to_state:
            issues.append(f"{grid_id}_TRANSITION_{index}_STATE_INVALID")
            continue
        allowed_transition_set.add((entity_id, from_state, to_state))

    cell_states = ledger.get("cell_states")
    if not isinstance(cell_states, list):
        issues.append(f"{grid_id}_CELL_STATES_INVALID")
        cell_states = []
    state_by_cell: dict[int, dict[str, dict[str, Any]]] = {}
    phase_by_cell: dict[int, str] = {}
    for index, row in enumerate(cell_states, start=1):
        if not isinstance(row, dict) or not isinstance(row.get("cell"), int):
            issues.append(f"{grid_id}_CELL_STATE_{index}_INVALID")
            continue
        cell_number = row["cell"]
        if cell_number in state_by_cell:
            issues.append(f"{grid_id}_CELL_{cell_number}_STATE_ROW_DUPLICATE")
            continue
        phase = row.get("phase")
        if phase not in ALLOWED_TEMPORAL_PHASES:
            issues.append(f"{grid_id}_CELL_{cell_number}_LEDGER_PHASE_INVALID")
        phase_by_cell[cell_number] = phase
        states = row.get("states")
        if not isinstance(states, list):
            issues.append(f"{grid_id}_CELL_{cell_number}_ENTITY_STATES_INVALID")
            states = []
        current: dict[str, dict[str, Any]] = {}
        for state_index, state_row in enumerate(states, start=1):
            if not isinstance(state_row, dict):
                issues.append(f"{grid_id}_CELL_{cell_number}_STATE_{state_index}_INVALID")
                continue
            entity_id = _text(state_row.get("entity_id"))
            if entity_id not in entity_map:
                issues.append(f"{grid_id}_CELL_{cell_number}_STATE_{state_index}_UNKNOWN_ENTITY")
                continue
            if entity_id in current:
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_DUPLICATE_LOCATION")
                continue
            current[entity_id] = state_row
            entity = entity_map[entity_id]
            if state_row.get("state") not in entity.get("allowed_states", []):
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_STATE_NOT_ALLOWED")
            if not isinstance(state_row.get("visible"), bool):
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_VISIBLE_INVALID")
            count = state_row.get("count")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_COUNT_INVALID")
            elif entity.get("quantity_policy") == "EXACT_ONE" and count != 1:
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_EXACT_ONE_VIOLATED")
            elif entity.get("quantity_policy") == "AT_MOST_ONE" and count not in {0, 1}:
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_AT_MOST_ONE_VIOLATED")
            if not _text(state_row.get("location")):
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_LOCATION_MISSING")
            if not _text(state_row.get("prompt_lock")):
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_PROMPT_LOCK_MISSING")
        if set(current) != set(entity_map):
            issues.append(f"{grid_id}_CELL_{cell_number}_TRACKED_ENTITY_COVERAGE_INCOMPLETE")
        state_by_cell[cell_number] = current

    expected_cell_numbers = set(range(1, len(cells) + 1))
    if set(state_by_cell) != expected_cell_numbers:
        issues.append(f"{grid_id}_STATE_LEDGER_CELL_COVERAGE_INCOMPLETE")
    for index, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            continue
        if cell.get("temporal_phase") not in ALLOWED_TEMPORAL_PHASES:
            issues.append(f"{grid_id}_CELL_{index}_TEMPORAL_PHASE_INVALID")
        if phase_by_cell.get(index) != cell.get("temporal_phase"):
            issues.append(f"{grid_id}_CELL_{index}_PHASE_LEDGER_MISMATCH")
        completion_action = _text(cell.get("completion_action"))
        visible_end_state = _text(cell.get("visible_end_state"))
        if bool(completion_action) != bool(visible_end_state):
            issues.append(f"{grid_id}_CELL_{index}_COMPLETION_PAIR_INCOMPLETE")

    for cell_number in range(2, len(cells) + 1):
        previous = state_by_cell.get(cell_number - 1, {})
        current = state_by_cell.get(cell_number, {})
        for entity_id in entity_map:
            before = previous.get(entity_id, {}).get("state")
            after = current.get(entity_id, {}).get("state")
            if before and after and before != after and (entity_id, before, after) not in allowed_transition_set:
                issues.append(f"{grid_id}_CELL_{cell_number}_{entity_id}_UNDECLARED_STATE_TRANSITION")
            prior_row = previous.get(entity_id, {})
            current_row = current.get(entity_id, {})
            terminal_risk = (
                prior_row.get("visible") is True and current_row.get("visible") is False
            ) or (
                isinstance(prior_row.get("count"), int)
                and isinstance(current_row.get("count"), int)
                and current_row.get("count") < prior_row.get("count")
            ) or (
                before != after and _terminal_state(after)
            )
            if terminal_risk:
                target_cell = cells[cell_number - 1] if cell_number <= len(cells) and isinstance(cells[cell_number - 1], dict) else {}
                if len(_text(target_cell.get("completion_action"))) < 8:
                    issues.append(f"{grid_id}_CELL_{cell_number}_COMPLETION_ACTION_REQUIRED")
                if len(_text(target_cell.get("visible_end_state"))) < 8:
                    issues.append(f"{grid_id}_CELL_{cell_number}_VISIBLE_END_STATE_REQUIRED")


def validate_scene_plan(job: dict[str, Any], evidence: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    issues = [f"JOB:{item}" for item in validate_job(job)]
    issues.extend(f"P2:{item}" for item in validate_timeline_evidence(job, evidence))
    if plan.get("schema_version") != "R6.2-SCENE-PLAN-1.0":
        issues.append("SCENE_PLAN_SCHEMA_INVALID")
    if plan.get("job_id") != job.get("job_id"):
        issues.append("SCENE_PLAN_JOB_ID_MISMATCH")
    if plan.get("route_id") != job.get("route_id"):
        issues.append("SCENE_PLAN_ROUTE_MISMATCH")
    if plan.get("creative_profile_binding") != job.get("creative_profile_binding"):
        issues.append("SCENE_PLAN_CREATIVE_PROFILE_BINDING_MISMATCH")
    route = job.get("route_id")
    if route in COPY_DERIVED_ROUTES:
        if plan.get("source_video_sha256") != job.get("source", {}).get("video_sha256"):
            issues.append("M2F_SCENE_PLAN_SOURCE_VIDEO_HASH_MISMATCH")
        if plan.get("source_audio_sha256") != job.get("source", {}).get("audio_sha256"):
            issues.append("M2F_SCENE_PLAN_SOURCE_AUDIO_HASH_MISMATCH")
    elif plan.get("source_video_sha256") != job.get("source", {}).get("video_sha256"):
        issues.append("SCENE_PLAN_SOURCE_HASH_MISMATCH")
    if plan.get("timeline_evidence_fingerprint") != canonical_fingerprint(evidence):
        issues.append("SCENE_PLAN_TIMELINE_EVIDENCE_FINGERPRINT_MISMATCH")
    if not HEX64.fullmatch(_text(plan.get("p3_blueprint_sha256"))):
        issues.append("P3_BLUEPRINT_SHA256_INVALID")
    timing_authority = job.get("target", {}).get("timing_authority")
    if plan.get("timing_authority") != timing_authority:
        issues.append("SCENE_PLAN_TIMING_AUTHORITY_MISMATCH")
    if timing_authority == "NARRATION_MASTER":
        timing_block = plan.get("narration_timing") if isinstance(plan.get("narration_timing"), dict) else {}
        timing_path = _text(timing_block.get("relative_path"))
        if timing_path not in {"artifacts/P3/NARRATION_TIMING.json", "artifacts/P3/NARRATION_PLAN.json"} or not HEX64.fullmatch(_text(timing_block.get("sha256"))):
            issues.append("SCENE_PLAN_NARRATION_TIMING_LINEAGE_MISSING")
    if timing_authority == "SOURCE_AUDIO_MASTER":
        timing_block = plan.get("source_audio_timing") if isinstance(plan.get("source_audio_timing"), dict) else {}
        if timing_block.get("relative_path") != "artifacts/P3/SOURCE_AUDIO_PLAN.json" or not HEX64.fullmatch(_text(timing_block.get("sha256"))):
            issues.append("SCENE_PLAN_SOURCE_AUDIO_TIMING_LINEAGE_MISSING")

    target_duration = _number(job.get("target", {}).get("duration_s"))
    plan_duration = _number(plan.get("target_duration_s"))
    if target_duration is None or plan_duration is None or abs(target_duration - plan_duration) > TOLERANCE:
        issues.append("TARGET_DURATION_MISMATCH")

    timeline = plan.get("timeline_analysis")
    if not isinstance(timeline, dict):
        issues.append("TIMELINE_ANALYSIS_MISSING")
        timeline = {}
    expected_sampling = "SOURCE_AUDIO_PLUS_SOURCE_MACRO_SCENES" if route in COPY_DERIVED_ROUTES else "WHOLE_VIDEO_PER_SECOND_PLUS_REAL_CUTS"
    if timeline.get("sampling_policy") != expected_sampling:
        issues.append("TIMELINE_SAMPLING_POLICY_INVALID")
    source_duration = target_duration if route in COPY_DERIVED_ROUTES else _number(job.get("source", {}).get("duration_s"))
    coverage_start = _number(timeline.get("coverage_start_s"))
    coverage_end = _number(timeline.get("coverage_end_s"))
    if coverage_start is None or abs(coverage_start) > TOLERANCE:
        issues.append("SOURCE_COVERAGE_MUST_START_AT_ZERO")
    if source_duration is None or coverage_end is None or abs(source_duration - coverage_end) > TOLERANCE:
        issues.append("SOURCE_COVERAGE_INCOMPLETE")
    uncertainties = timeline.get("critical_uncertainties")
    if not isinstance(uncertainties, list):
        issues.append("CRITICAL_UNCERTAINTIES_INVALID")
    elif uncertainties:
        issues.append("CRITICAL_UNCERTAINTY_BLOCKS_PLAN")
    evidence_cut_times = [] if route in COPY_DERIVED_ROUTES else [round(float(cut["time_s"]), 4) for cut in evidence.get("real_cuts", []) if isinstance(cut, dict) and _number(cut.get("time_s")) is not None]
    plan_cut_times = [round(float(value), 4) for value in timeline.get("real_cut_times_s", []) if _number(value) is not None]
    if evidence_cut_times != plan_cut_times:
        issues.append("SCENE_PLAN_REAL_CUT_SUMMARY_MISMATCH")

    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        issues.append("SCENES_MISSING")
        scenes = []
    scene_map: dict[str, dict[str, Any]] = {}
    previous_end = 0.0
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            issues.append(f"SCENE_{index}_INVALID")
            continue
        scene_id = _text(scene.get("scene_id"))
        if not scene_id or scene_id in scene_map:
            issues.append(f"SCENE_{index}_ID_INVALID_OR_DUPLICATE")
            continue
        scene_map[scene_id] = scene
        start = _number(scene.get("target_start_s"))
        end = _number(scene.get("target_end_s"))
        if start is None or end is None or end <= start:
            issues.append(f"{scene_id}_TARGET_INTERVAL_INVALID")
        else:
            if abs(start - previous_end) > TOLERANCE:
                issues.append(f"{scene_id}_TARGET_TIMELINE_GAP_OR_OVERLAP")
            previous_end = end
        for field in ("setting", "narrative_function", "large_action", "visible_result", "continuity_in", "continuity_out", "copy_or_audio", "authority"):
            if not _text(scene.get(field)):
                issues.append(f"{scene_id}_{field.upper()}_MISSING")
        if not isinstance(scene.get("actors"), list) or not scene.get("actors"):
            issues.append(f"{scene_id}_ACTORS_MISSING")
        large_action = _text(scene.get("large_action"))
        if _micro_action_only(large_action):
            issues.append(f"{scene_id}_MICRO_ACTION_IS_NOT_A_SCENE")
        if not isinstance(scene.get("forbidden_alternatives"), list) or not scene.get("forbidden_alternatives"):
            issues.append(f"{scene_id}_FORBIDDEN_ALTERNATIVES_MISSING")
        source_intervals = scene.get("source_evidence_intervals")
        if route in COPY_DERIVED_ROUTES:
            if not isinstance(source_intervals, list) or not source_intervals:
                issues.append(f"{scene_id}_M2F_SOURCE_MACRO_SCENE_EVIDENCE_MISSING")
            copy_spans = scene.get("source_copy_spans")
            if not isinstance(copy_spans, list) or not copy_spans:
                issues.append(f"{scene_id}_SOURCE_COPY_SPANS_MISSING")
            source_scene_ids = scene.get("source_scene_ids")
            if not isinstance(source_scene_ids, list) or not source_scene_ids or not all(_text(value) for value in source_scene_ids):
                issues.append(f"{scene_id}_SOURCE_SCENE_IDS_MISSING")
            inherited = scene.get("inherited_action_contracts")
            if not isinstance(inherited, list) or not inherited:
                issues.append(f"{scene_id}_INHERITED_ACTION_CONTRACTS_MISSING")
        elif not isinstance(source_intervals, list) or not source_intervals:
            issues.append(f"{scene_id}_SOURCE_EVIDENCE_MISSING")
        authority = scene.get("authority")
        if route in SOURCE_BOUND_ROUTES and authority != "SOURCE_OBSERVED_INTERVAL":
            issues.append(f"{scene_id}_SOURCE_BOUND_AUTHORITY_INVALID")
        if route in SEMANTIC_ROUTES and authority != "APPROVED_CREATIVE_BLUEPRINT":
            issues.append(f"{scene_id}_SEMANTIC_AUTHORITY_INVALID")
        if route in COPY_DERIVED_ROUTES and authority != "SOURCE_AUDIO_SCENE_SEMANTIC_BLUEPRINT":
            issues.append(f"{scene_id}_M2F_AUDIO_SCENE_AUTHORITY_INVALID")

    if target_duration is not None and scenes and abs(previous_end - target_duration) > TOLERANCE:
        issues.append("SCENES_DO_NOT_COVER_TARGET_DURATION")
    if scenes and _number(scenes[0].get("target_start_s")) not in (0, 0.0):
        issues.append("FIRST_SCENE_MUST_START_AT_ZERO")

    segments = plan.get("video_segments")
    if not isinstance(segments, list) or not segments:
        issues.append("VIDEO_SEGMENTS_MISSING")
        segments = []
    segment_map: dict[str, dict[str, Any]] = {}
    segment_grid_ids: set[str] = set()
    assigned_scene_ids: list[str] = []
    previous_segment_end = 0.0
    for segment_index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            issues.append(f"SEGMENT_{segment_index}_INVALID")
            continue
        segment_id = _text(segment.get("segment_id"))
        if not segment_id or segment_id in segment_map:
            issues.append(f"SEGMENT_{segment_index}_ID_INVALID_OR_DUPLICATE")
            continue
        segment_map[segment_id] = segment
        if segment.get("segment_order") != segment_index:
            issues.append(f"{segment_id}_ORDER_INVALID")
        grid_id = _text(segment.get("grid_id"))
        if not grid_id or grid_id in segment_grid_ids:
            issues.append(f"{segment_id}_GRID_ID_MISSING_OR_REUSED")
        segment_grid_ids.add(grid_id)
        start = _number(segment.get("target_start_s"))
        end = _number(segment.get("target_end_s"))
        if start is None or end is None or end <= start:
            issues.append(f"{segment_id}_TIME_RANGE_INVALID")
        else:
            if abs(start - previous_segment_end) > TOLERANCE:
                issues.append(f"{segment_id}_SEGMENT_TIMELINE_GAP_OR_OVERLAP")
            previous_segment_end = end
        scene_ids = segment.get("scene_ids")
        if not isinstance(scene_ids, list) or not scene_ids or not all(_text(value) for value in scene_ids):
            issues.append(f"{segment_id}_SCENE_IDS_MISSING")
            scene_ids = []
        if len(set(scene_ids)) != len(scene_ids):
            issues.append(f"{segment_id}_SCENE_IDS_DUPLICATE")
        if any(scene_id not in scene_map for scene_id in scene_ids):
            issues.append(f"{segment_id}_UNKNOWN_SCENE")
        assigned_scene_ids.extend(scene_ids)
        if job.get("route_id") in COMPLETE_SCENE_ROUTES and len(scene_ids) != 1:
            issues.append(f"{segment_id}_SEMANTIC_SEGMENT_REQUIRES_ONE_COMPLETE_SCENE")
        if segment.get("duration_authority") != "COMPLETE_ACTION_AND_SPEECH_SPAN":
            issues.append(f"{segment_id}_DURATION_AUTHORITY_INVALID")
        if segment.get("fixed_time_slice") is not False:
            issues.append(f"{segment_id}_FIXED_TIME_SLICE_FORBIDDEN")
        if timing_authority == "NARRATION_MASTER":
            timing_relative = _text((plan.get("narration_timing") or {}).get("relative_path")) if isinstance(plan.get("narration_timing"), dict) else ""
            duration_key = "planned_narration_duration_s" if timing_relative.endswith("NARRATION_PLAN.json") else "measured_narration_duration_s"
            narration_duration = _number(segment.get(duration_key))
            if start is None or end is None or narration_duration is None or abs((end - start) - narration_duration) > TOLERANCE:
                label = "PLANNED" if duration_key.startswith("planned") else "MEASURED"
                issues.append(f"{segment_id}_{label}_NARRATION_DURATION_MISMATCH")
        if timing_authority == "SOURCE_AUDIO_MASTER":
            source_audio_duration = _number(segment.get("source_audio_duration_s"))
            if start is None or end is None or source_audio_duration is None or abs((end - start) - source_audio_duration) > TOLERANCE:
                issues.append(f"{segment_id}_SOURCE_AUDIO_DURATION_MISMATCH")
            if _text(segment.get("source_audio_segment_id")) != segment_id:
                issues.append(f"{segment_id}_SOURCE_AUDIO_SEGMENT_BINDING_INVALID")
        if not isinstance(segment.get("content_obligations"), list) or not segment.get("content_obligations") or not all(_text(value) for value in segment.get("content_obligations", [])):
            issues.append(f"{segment_id}_CONTENT_OBLIGATIONS_MISSING")
        for key in ("continuity_in", "continuity_out"):
            if not _text(segment.get(key)):
                issues.append(f"{segment_id}_{key.upper()}_MISSING")
        for transition_key in ("entry_transition", "exit_transition"):
            transition = segment.get(transition_key)
            if not isinstance(transition, dict):
                issues.append(f"{segment_id}_{transition_key.upper()}_MISSING")
                continue
            kind = transition.get("kind")
            if kind not in ALLOWED_BOUNDARY_TRANSITIONS:
                issues.append(f"{segment_id}_{transition_key.upper()}_KIND_INVALID")
            if not isinstance(transition.get("state_match_required"), bool):
                issues.append(f"{segment_id}_{transition_key.upper()}_STATE_MATCH_INVALID")
            cut_id = transition.get("cut_id")
            if not isinstance(cut_id, str):
                issues.append(f"{segment_id}_{transition_key.upper()}_CUT_ID_INVALID")
            elif kind == "HARD_CUT" and not cut_id.strip():
                issues.append(f"{segment_id}_{transition_key.upper()}_HARD_CUT_ID_MISSING")
            elif kind != "HARD_CUT" and cut_id:
                issues.append(f"{segment_id}_{transition_key.upper()}_UNEXPECTED_CUT_ID")
            if transition.get("state_match_required") is not (kind == "CONTINUOUS"):
                issues.append(f"{segment_id}_{transition_key.upper()}_STATE_MATCH_POLICY_INVALID")
        entry_kind = segment.get("entry_transition", {}).get("kind") if isinstance(segment.get("entry_transition"), dict) else None
        exit_kind = segment.get("exit_transition", {}).get("kind") if isinstance(segment.get("exit_transition"), dict) else None
        if segment_index == 1 and entry_kind != "VIDEO_START":
            issues.append(f"{segment_id}_FIRST_SEGMENT_ENTRY_MUST_BE_VIDEO_START")
        if segment_index > 1 and entry_kind not in {"CONTINUOUS", "HARD_CUT"}:
            issues.append(f"{segment_id}_INTERIOR_ENTRY_TRANSITION_INVALID")
        if segment_index == len(segments) and exit_kind != "VIDEO_END":
            issues.append(f"{segment_id}_LAST_SEGMENT_EXIT_MUST_BE_VIDEO_END")
        if segment_index < len(segments) and exit_kind not in {"CONTINUOUS", "HARD_CUT"}:
            issues.append(f"{segment_id}_INTERIOR_EXIT_TRANSITION_INVALID")
    if target_duration is not None and segments and abs(previous_segment_end - target_duration) > TOLERANCE:
        issues.append("SEGMENTS_DO_NOT_COVER_TARGET_DURATION")
    if set(assigned_scene_ids) != set(scene_map) or len(assigned_scene_ids) != len(set(assigned_scene_ids)):
        issues.append("EVERY_SCENE_MUST_BELONG_TO_EXACTLY_ONE_SEGMENT")

    grids = plan.get("grids")
    if not isinstance(grids, list) or not grids:
        issues.append("GRIDS_MISSING")
        grids = []
    all_cells: list[dict[str, Any]] = []
    seen_grid_ids: set[str] = set()
    seen_scene_ids: set[str] = set()
    seen_segment_ids: set[str] = set()
    grid_by_segment: dict[str, dict[str, Any]] = {}
    result_scene_ids: set[str] = set()
    previous_target_time = -TOLERANCE
    for grid_index, grid in enumerate(grids, start=1):
        if not isinstance(grid, dict):
            issues.append(f"GRID_{grid_index}_INVALID")
            continue
        grid_id = _text(grid.get("grid_id"))
        if not grid_id or grid_id in seen_grid_ids:
            issues.append(f"GRID_{grid_index}_ID_INVALID_OR_DUPLICATE")
        seen_grid_ids.add(grid_id)
        if grid.get("grid_order") != grid_index:
            issues.append(f"{grid_id or grid_index}_ORDER_INVALID")
        segment_id = _text(grid.get("segment_id"))
        if segment_id not in segment_map:
            issues.append(f"{grid_id or grid_index}_UNKNOWN_SEGMENT")
            segment = {}
        else:
            segment = segment_map[segment_id]
            seen_segment_ids.add(segment_id)
            grid_by_segment[segment_id] = grid
            if segment.get("grid_id") != grid_id:
                issues.append(f"{grid_id}_SEGMENT_GRID_MAPPING_MISMATCH")
        if grid.get("grid_role") not in ALLOWED_GRID_ROLES:
            issues.append(f"{grid_id or grid_index}_GRID_ROLE_INVALID")
        layout = grid.get("layout")
        capacity = LAYOUT_CAPACITY.get(layout)
        if capacity is None:
            issues.append(f"{grid_id or grid_index}_LAYOUT_INVALID")
        cells = grid.get("cells")
        if not isinstance(cells, list):
            issues.append(f"{grid_id or grid_index}_CELLS_MISSING")
            continue
        geometry = job.get("grid_geometry_contract", {})
        if grid.get("target_canvas_aspect_ratio") != geometry.get("canvas_aspect_ratio"):
            issues.append(f"{grid_id}_CANVAS_ASPECT_CONTRACT_MISMATCH")
        if grid.get("target_cell_aspect_ratio") != geometry.get("cell_aspect_ratio"):
            issues.append(f"{grid_id}_CELL_ASPECT_CONTRACT_MISMATCH")
        if capacity is not None and len(cells) != capacity:
            issues.append(f"{grid_id or grid_index}_CELL_COUNT_MUST_EQUAL_LAYOUT_CAPACITY")
        split_reason = _text(grid.get("split_reason"))
        if not split_reason.startswith("SCENE_SCOPED_SEGMENT:"):
            issues.append(f"{grid_id}_GRID_NOT_SCENE_SCOPED")
        if _has_fixed_duration_split_marker(split_reason):
            issues.append(f"{grid_id}_FIXED_DURATION_SPLIT_FORBIDDEN")
        decision = grid.get("layout_decision")
        if not isinstance(decision, dict):
            issues.append(f"{grid_id}_LAYOUT_DECISION_MISSING")
            decision = {}
        beat_count = decision.get("necessary_beat_count")
        visual_beats = decision.get("necessary_visual_beats")
        if isinstance(beat_count, bool) or not isinstance(beat_count, int):
            issues.append(f"{grid_id}_NECESSARY_BEAT_COUNT_INVALID")
        if not isinstance(visual_beats, list) or not visual_beats:
            issues.append(f"{grid_id}_NECESSARY_VISUAL_BEATS_MISSING")
            visual_beats = []
        if isinstance(beat_count, int) and beat_count != len(visual_beats):
            issues.append(f"{grid_id}_NECESSARY_BEAT_COUNT_MISMATCH")
        expected_layout = _layout_for_beat_count(beat_count) if isinstance(beat_count, int) else None
        if expected_layout is None:
            issues.append(f"{grid_id}_NECESSARY_BEAT_COUNT_OUT_OF_RANGE")
        elif layout != expected_layout:
            issues.append(f"{grid_id}_LAYOUT_NOT_SMALLEST_SUFFICIENT")
        if decision.get("selected_layout") != layout:
            issues.append(f"{grid_id}_LAYOUT_DECISION_MISMATCH")
        if decision.get("selection_authority") != "NECESSARY_VISUAL_BEATS":
            issues.append(f"{grid_id}_LAYOUT_AUTHORITY_INVALID")
        if decision.get("time_slicing_used") is not False:
            issues.append(f"{grid_id}_TIME_BASED_LAYOUT_FORBIDDEN")
        if not isinstance(decision.get("complexity_flags"), list):
            issues.append(f"{grid_id}_COMPLEXITY_FLAGS_INVALID")
        for beat_index, beat_row in enumerate(visual_beats, start=1):
            if not isinstance(beat_row, dict) or not _text(beat_row.get("beat_id")) or not _text(beat_row.get("role")) or not _text(beat_row.get("reason")):
                issues.append(f"{grid_id}_VISUAL_BEAT_{beat_index}_INVALID")
        if capacity is not None:
            if grid.get("start_cell") != 1 or grid.get("end_cell") != capacity:
                issues.append(f"{grid_id}_SEGMENT_CELL_BOUNDARY_INVALID")
        for local_index, cell in enumerate(cells, start=1):
            if not isinstance(cell, dict):
                issues.append(f"{grid_id}_CELL_{local_index}_INVALID")
                continue
            if cell.get("cell") != local_index:
                issues.append(f"{grid_id}_CELL_NUMBER_NOT_ROW_MAJOR")
            beat = cell.get("beat_role")
            if beat in FORBIDDEN_BEATS or beat not in ALLOWED_BEATS:
                issues.append(f"{grid_id}_CELL_{local_index}_BEAT_ROLE_INVALID")
            scene_id = cell.get("scene_id")
            if scene_id not in scene_map:
                issues.append(f"{grid_id}_CELL_{local_index}_UNKNOWN_SCENE")
            else:
                seen_scene_ids.add(scene_id)
                if cell.get("result_visible") is True:
                    result_scene_ids.add(scene_id)
                if segment and scene_id not in segment.get("scene_ids", []):
                    issues.append(f"{grid_id}_CELL_{local_index}_UNRELATED_SCENE_LEAK")
            boundary = cell.get("segment_boundary")
            if boundary not in ALLOWED_SEGMENT_BOUNDARIES:
                issues.append(f"{grid_id}_CELL_{local_index}_SEGMENT_BOUNDARY_INVALID")
            elif local_index == 1 and boundary != "START":
                issues.append(f"{grid_id}_FIRST_CELL_MUST_START_SEGMENT")
            elif capacity is not None and local_index == capacity and boundary != "END":
                issues.append(f"{grid_id}_LAST_CELL_MUST_END_SEGMENT")
            elif local_index not in {1, capacity} and boundary != "NONE":
                issues.append(f"{grid_id}_INTERIOR_CELL_BOUNDARY_INVALID")
            target_time = _number(cell.get("target_time_s"))
            if target_time is None:
                issues.append(f"{grid_id}_CELL_{local_index}_TARGET_TIME_MISSING")
            else:
                if target_time + TOLERANCE < previous_target_time:
                    issues.append(f"{grid_id}_CELL_{local_index}_CHRONOLOGY_REVERSED")
                previous_target_time = target_time
                if target_duration is not None and not (-TOLERANCE <= target_time <= target_duration + TOLERANCE):
                    issues.append(f"{grid_id}_CELL_{local_index}_TARGET_TIME_OUT_OF_RANGE")
                segment_start = _number(segment.get("target_start_s")) if segment else None
                segment_end = _number(segment.get("target_end_s")) if segment else None
                if segment_start is not None and segment_end is not None and not (segment_start - TOLERANCE <= target_time <= segment_end + TOLERANCE):
                    issues.append(f"{grid_id}_CELL_{local_index}_TIME_OUTSIDE_SEGMENT")
            if not _text(cell.get("visual_statement")):
                issues.append(f"{grid_id}_CELL_{local_index}_VISUAL_STATEMENT_MISSING")
            if not _text(cell.get("camera")):
                issues.append(f"{grid_id}_CELL_{local_index}_CAMERA_MISSING")
            if job.get("route_id") in SOURCE_BOUND_ROUTES and _number(cell.get("source_time_s")) is None:
                issues.append(f"{grid_id}_CELL_{local_index}_SOURCE_TIME_REQUIRED")
            if job.get("route_id") in COMPLETE_SCENE_ROUTES and cell.get("source_time_s") is not None:
                issues.append(f"{grid_id}_CELL_{local_index}_SEMANTIC_CELL_FORBIDS_SOURCE_TIME_LOCK")
            all_cells.append(cell)
        _validate_state_ledger(grid, cells, grid_id or f"GRID_{grid_index}", issues)
        if cells and segment:
            first_time = _number(cells[0].get("target_time_s"))
            last_time = _number(cells[-1].get("target_time_s"))
            if first_time is None or abs(first_time - float(segment["target_start_s"])) > TOLERANCE:
                issues.append(f"{grid_id}_FIRST_CELL_TIME_MUST_MATCH_SEGMENT_START")
            if last_time is None or abs(last_time - float(segment["target_end_s"])) > TOLERANCE:
                issues.append(f"{grid_id}_LAST_CELL_TIME_MUST_MATCH_SEGMENT_END")

    if all_cells:
        if all_cells[0].get("beat_role") != "VIDEO_START" or _number(all_cells[0].get("target_time_s")) not in (0, 0.0):
            issues.append("GLOBAL_CELL_1_MUST_BE_VIDEO_START_AT_ZERO")
        if any(cell.get("beat_role") == "VIDEO_START" for cell in all_cells[1:]):
            issues.append("VIDEO_START_BEAT_MAY_APPEAR_ONLY_ONCE")
    if scene_map and set(scene_map) != seen_scene_ids:
        issues.append("EVERY_SCENE_REQUIRES_AT_LEAST_ONE_GRID_CELL")
    if scene_map and set(scene_map) != result_scene_ids:
        issues.append("EVERY_SCENE_REQUIRES_VISIBLE_RESULT_EVIDENCE")
    if set(segment_map) != seen_segment_ids:
        issues.append("EVERY_SEGMENT_REQUIRES_EXACTLY_ONE_GRID")
    if len(grids) != len(segment_map):
        issues.append("GRID_AND_SEGMENT_COUNT_MISMATCH")
    if job.get("route_id") in SOURCE_BOUND_ROUTES:
        for cut in evidence.get("real_cuts", []):
            if not isinstance(cut, dict) or _number(cut.get("time_s")) is None:
                continue
            cut_id = _text(cut.get("cut_id")) or "UNKNOWN_CUT"
            cut_time = float(cut["time_s"])
            pre = [
                cell for cell in all_cells
                if cell.get("temporal_phase") == "CUT_PRE"
                and _number(cell.get("source_time_s")) is not None
                and 0 <= cut_time - float(cell["source_time_s"]) <= 0.11
            ]
            post = [
                cell for cell in all_cells
                if cell.get("temporal_phase") == "CUT_POST"
                and _number(cell.get("source_time_s")) is not None
                and 0 <= float(cell["source_time_s"]) - cut_time <= 0.11
            ]
            if not pre:
                issues.append(f"{cut_id}_SOURCE_CUT_PRE_CELL_MISSING")
            if not post:
                issues.append(f"{cut_id}_SOURCE_CUT_POST_CELL_MISSING")

    def project_boundary_states(grid: dict[str, Any], *, first: bool) -> dict[str, tuple[Any, Any, Any, Any]]:
        ledger = grid.get("state_ledger") if isinstance(grid.get("state_ledger"), dict) else {}
        entities = ledger.get("tracked_entities") if isinstance(ledger.get("tracked_entities"), list) else []
        project_ids = {
            row.get("entity_id") for row in entities
            if isinstance(row, dict) and row.get("continuity_scope") == "PROJECT"
        }
        rows = ledger.get("cell_states") if isinstance(ledger.get("cell_states"), list) else []
        if not rows:
            return {}
        boundary_row = rows[0] if first else rows[-1]
        states = boundary_row.get("states") if isinstance(boundary_row, dict) and isinstance(boundary_row.get("states"), list) else []
        return {
            row.get("entity_id"): (row.get("state"), row.get("visible"), row.get("count"), row.get("location"))
            for row in states
            if isinstance(row, dict) and row.get("entity_id") in project_ids
        }

    for index in range(len(segments) - 1):
        current = segments[index]
        following = segments[index + 1]
        if not isinstance(current, dict) or not isinstance(following, dict):
            continue
        current_exit = current.get("exit_transition") if isinstance(current.get("exit_transition"), dict) else {}
        following_entry = following.get("entry_transition") if isinstance(following.get("entry_transition"), dict) else {}
        kind = current_exit.get("kind")
        if kind != following_entry.get("kind"):
            issues.append(f"{current.get('segment_id')}_TO_{following.get('segment_id')}_BOUNDARY_KIND_MISMATCH")
        if current_exit.get("cut_id") != following_entry.get("cut_id"):
            issues.append(f"{current.get('segment_id')}_TO_{following.get('segment_id')}_CUT_ID_MISMATCH")
        current_grid = grid_by_segment.get(str(current.get("segment_id")))
        following_grid = grid_by_segment.get(str(following.get("segment_id")))
        if current_grid is None or following_grid is None:
            continue
        if kind == "CONTINUOUS":
            outgoing = project_boundary_states(current_grid, first=False)
            incoming = project_boundary_states(following_grid, first=True)
            if not outgoing or outgoing != incoming:
                issues.append(f"{current.get('segment_id')}_TO_{following.get('segment_id')}_CONTINUITY_STATE_MISMATCH")
        if kind == "HARD_CUT":
            current_cells = current_grid.get("cells") if isinstance(current_grid.get("cells"), list) else []
            following_cells = following_grid.get("cells") if isinstance(following_grid.get("cells"), list) else []
            if not current_cells or current_cells[-1].get("temporal_phase") != "CUT_PRE":
                issues.append(f"{current.get('segment_id')}_HARD_CUT_REQUIRES_CUT_PRE")
            if not following_cells or following_cells[0].get("temporal_phase") != "CUT_POST":
                issues.append(f"{following.get('segment_id')}_HARD_CUT_REQUIRES_CUT_POST")

    issues.extend(validate_r619_visual_core(job, plan))
    issues.extend(validate_segment_state_flow(plan))
    issues.extend(validate_r641_expression_contract(plan, require=False))
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args()
    try:
        job = load_json(args.job)
        evidence = load_json(args.evidence)
        plan = load_json(args.plan)
        issues = validate_scene_plan(job, evidence, plan)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "issues": [f"READ_ERROR: {exc}"]}, ensure_ascii=False, indent=2))
        return 2
    result = {"status": "PASSED" if not issues else "FAILED", "issues": issues}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
