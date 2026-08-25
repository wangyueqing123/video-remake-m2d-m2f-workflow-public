#!/usr/bin/env python3
"""Validate R6.15 pre-video copy-driven narration and action timing."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file
from r635_source_content_lock import validate_project as validate_r635_source_content_project


TOLERANCE = 0.011
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OBLIGATION_TYPES = {"ENVIRONMENT", "BEHAVIOR", "EMOTION", "RELATION", "CONTRAST", "RESULT", "SAFETY", "CTA"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def text_units(text: str, unit_mode: str = "CJK_CHARACTER") -> tuple[int, int]:
    if unit_mode not in {"CJK_CHARACTER", "WHITESPACE_WORD"}:
        raise ValueError("VOICE_UNIT_MODE_INVALID")
    if unit_mode == "WHITESPACE_WORD":
        spoken = len(re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*", text))
    else:
        spoken = 0
    pause_runs = 0
    in_punctuation = False
    for index, char in enumerate(text):
        if char.isspace():
            in_punctuation = False
            continue
        internal_word_mark = (
            unit_mode == "WHITESPACE_WORD"
            and char in {"'", "’", "-"}
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        )
        is_punctuation = unicodedata.category(char).startswith("P") and not internal_word_mark
        if is_punctuation:
            if not in_punctuation:
                pause_runs += 1
            in_punctuation = True
        else:
            if unit_mode == "CJK_CHARACTER":
                spoken += 1
            in_punctuation = False
    return spoken, pause_runs


def join_text_parts(parts: list[str], unit_mode: str) -> str:
    """Rebuild spoken copy without deleting English word boundaries."""
    cleaned = [_text(part) for part in parts if _text(part)]
    separator = " " if unit_mode == "WHITESPACE_WORD" else ""
    return separator.join(cleaned)


def resolve_language_model(profile: dict[str, Any], language_code: str) -> tuple[str, dict[str, Any]]:
    requested = language_code.strip().lower()
    models = profile.get("language_models") if isinstance(profile.get("language_models"), dict) else {}
    matches: list[tuple[str, dict[str, Any]]] = []
    for model_id, model in models.items():
        if not isinstance(model, dict):
            continue
        aliases = model.get("language_aliases") if isinstance(model.get("language_aliases"), list) else []
        if requested in {str(item).strip().lower() for item in aliases}:
            matches.append((str(model_id), model))
    if len(matches) != 1:
        raise ValueError("VOICE_LANGUAGE_MODEL_NOT_UNIQUE_OR_UNSUPPORTED")
    return matches[0]


def validate(project: Path, plan_path: Path) -> list[str]:
    issues: list[str] = []
    plan = load_json(plan_path)
    manifest = load_json(project / STATE_NAME)
    if plan.get("schema_version") != "R6.15-NARRATION-PLAN-1.0":
        issues.append("NARRATION_PLAN_SCHEMA_INVALID")
    is_language_aware = manifest.get("skill_version") in {"R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}
    expected_status = "LANGUAGE_AWARE_VOICE_ESTIMATE" if is_language_aware else "CALIBRATED_VOICE_ESTIMATE"
    if plan.get("status") != expected_status:
        issues.append("NARRATION_PLAN_STAGE_INVALID")
    if plan.get("job_id") != manifest.get("project_id"):
        issues.append("NARRATION_PLAN_PROJECT_MISMATCH")
    if plan.get("audio_variant") != "POST_DUB_NARRATION" or plan.get("timing_authority") != "NARRATION_MASTER":
        issues.append("NARRATION_PLAN_AUTHORITY_INVALID")
    if plan.get("final_audio_reconciliation_required") is not True:
        issues.append("FINAL_AUDIO_RECONCILIATION_NOT_REQUIRED")

    capability = plan.get("provider_duration_capability") if isinstance(plan.get("provider_duration_capability"), dict) else {}
    if capability != {"minimum_s": 1, "increment_s": 1, "rounding_policy": "CEIL_CREATIVE_TARGET"}:
        issues.append("PROVIDER_DURATION_CAPABILITY_INVALID")

    profile_ref = plan.get("voice_profile") if isinstance(plan.get("voice_profile"), dict) else {}
    try:
        relative = normalize_project_relative(_text(profile_ref.get("relative_path")))
        profile_path = (project / relative).resolve()
    except ValueError:
        profile_path = project / "__invalid__"
        issues.append("VOICE_PROFILE_PATH_INVALID")
    profile_file: dict[str, Any] = {}
    if not profile_path.is_file():
        issues.append("VOICE_PROFILE_MISSING")
    else:
        digest = _text(profile_ref.get("sha256")).lower()
        if not HEX64.fullmatch(digest) or sha256_file(profile_path) != digest:
            issues.append("VOICE_PROFILE_HASH_MISMATCH")
        profile_file = load_json(profile_path)
    profiles = profile_file.get("profiles") if isinstance(profile_file.get("profiles"), list) else []
    matches = [row for row in profiles if isinstance(row, dict) and row.get("profile_id") == profile_ref.get("profile_id")]
    if len(matches) != 1:
        issues.append("VOICE_PROFILE_ID_NOT_UNIQUE")
        profile: dict[str, Any] = {}
    else:
        profile = matches[0]
    speed = _number(profile.get("global_speed"))
    voice_lock = plan.get("voice_lock") if isinstance(plan.get("voice_lock"), dict) else {}
    if (
        voice_lock.get("provider") != profile.get("provider")
        or voice_lock.get("voice_id") != profile.get("voice_id")
        or _number(voice_lock.get("global_speed")) != speed
    ):
        issues.append("VOICE_LOCK_DIFFERS_FROM_PROFILE")
    formula = profile.get("formula") if isinstance(profile.get("formula"), dict) else {}
    unit_mode = "CJK_CHARACTER"
    if is_language_aware:
        delivery_language = _text(plan.get("delivery_language"))
        timing_model = plan.get("timing_model") if isinstance(plan.get("timing_model"), dict) else {}
        try:
            language_model_id, formula = resolve_language_model(profile, delivery_language)
        except ValueError as exc:
            language_model_id, formula = "", {}
            issues.append(str(exc))
        unit_mode = _text(formula.get("unit_mode"))
        if timing_model.get("language_code") != delivery_language:
            issues.append("TIMING_MODEL_LANGUAGE_MISMATCH")
        if timing_model.get("unit_mode") != unit_mode:
            issues.append("TIMING_MODEL_UNIT_MODE_MISMATCH")
        if timing_model.get("formula_status") != formula.get("formula_status"):
            issues.append("TIMING_MODEL_STATUS_MISMATCH")
        if timing_model.get("final_voice_measurement_required") is not True:
            issues.append("TIMING_MODEL_FINAL_MEASUREMENT_NOT_REQUIRED")
        contract_path = project / "artifacts" / "P2" / "SOURCE_CONTENT_CONTRACT.json"
        if contract_path.is_file():
            content_contract = load_json(contract_path)
            locked_language = _text((content_contract.get("language_decision") or {}).get("delivery_language"))
            if locked_language != delivery_language:
                issues.append("NARRATION_LANGUAGE_DIFFERS_FROM_SOURCE_CONTENT_LOCK")
    unit_seconds = _number(formula.get("spoken_unit_seconds") if is_language_aware else formula.get("spoken_character_seconds"))
    pause_seconds = _number(formula.get("punctuation_run_seconds"))
    if speed is None or not 0.5 <= speed <= 2.0:
        issues.append("VOICE_PROFILE_SPEED_INVALID")
    if unit_seconds is None or pause_seconds is None or formula.get("speed_already_embedded") is not True:
        issues.append("VOICE_PROFILE_FORMULA_INVALID")

    segments = plan.get("segments") if isinstance(plan.get("segments"), list) else []
    if not segments:
        issues.append("NARRATION_SEGMENTS_MISSING")
    previous_end = 0.0
    total_request = 0
    reconstructed_segments: list[str] = []
    for index, row in enumerate(segments, start=1):
        if not isinstance(row, dict):
            issues.append(f"SEGMENT_{index}_INVALID")
            continue
        segment_id = _text(row.get("segment_id")) or f"SEGMENT_{index}"
        if row.get("segment_order") != index:
            issues.append(f"{segment_id}_ORDER_INVALID")
        spoken_copy = _text(row.get("spoken_copy"))
        reconstructed_segments.append(spoken_copy)
        units, pauses = text_units(spoken_copy, unit_mode)
        stored_units = row.get("spoken_unit_count") if is_language_aware else row.get("spoken_character_count")
        if stored_units != units or row.get("punctuation_run_count") != pauses:
            issues.append(f"{segment_id}_TEXT_UNIT_COUNT_MISMATCH")
        expected_duration = round(units * float(unit_seconds or 0) + pauses * float(pause_seconds or 0), int(formula.get("round_digits", 2)))
        start = _number(row.get("start_s"))
        end = _number(row.get("end_s"))
        duration = _number(row.get("creative_target_duration_s"))
        if start is None or end is None or duration is None or end <= start:
            issues.append(f"{segment_id}_TIME_INVALID")
            continue
        if abs(start - previous_end) > TOLERANCE:
            issues.append(f"{segment_id}_TIMELINE_NOT_CONTIGUOUS")
        if abs((end - start) - duration) > TOLERANCE or abs(duration - expected_duration) > TOLERANCE:
            issues.append(f"{segment_id}_DURATION_NOT_DERIVED_FROM_COPY")
        if _number(row.get("voice_speed")) != speed:
            issues.append(f"{segment_id}_VOICE_SPEED_DIFFERS_FROM_PROFILE")
        request = row.get("provider_request_duration_s")
        expected_request = max(1, math.ceil(duration - 1e-9))
        if isinstance(request, bool) or not isinstance(request, int) or request != expected_request:
            issues.append(f"{segment_id}_PROVIDER_DURATION_NOT_CEILING")
        else:
            total_request += request

        utterances = row.get("utterances") if isinstance(row.get("utterances"), list) else []
        if not utterances:
            issues.append(f"{segment_id}_UTTERANCES_MISSING")
        utterance_text_parts: list[str] = []
        utterance_previous = start
        obligation_ids: set[str] = set()
        for u_index, utterance in enumerate(utterances, start=1):
            if not isinstance(utterance, dict):
                issues.append(f"{segment_id}_UTTERANCE_{u_index}_INVALID")
                continue
            utterance_id = _text(utterance.get("utterance_id"))
            utterance_text_parts.append(_text(utterance.get("text")))
            u_start, u_end = _number(utterance.get("start_s")), _number(utterance.get("end_s"))
            if not utterance_id or u_start is None or u_end is None or u_end <= u_start or abs(u_start - utterance_previous) > TOLERANCE:
                issues.append(f"{segment_id}_UTTERANCE_{u_index}_TIME_OR_ID_INVALID")
            else:
                utterance_previous = u_end
            obligations = utterance.get("visual_obligations") if isinstance(utterance.get("visual_obligations"), list) else []
            if not obligations:
                issues.append(f"{segment_id}_{utterance_id}_VISUAL_OBLIGATION_MISSING")
            for o_index, obligation in enumerate(obligations, start=1):
                obligation_id = f"{utterance_id}:{o_index}"
                obligation_ids.add(obligation_id)
                if not isinstance(obligation, dict) or obligation.get("type") not in OBLIGATION_TYPES or not _text(obligation.get("meaning")) or not _text(obligation.get("visible_evidence")):
                    issues.append(f"{segment_id}_{obligation_id}_VISUAL_OBLIGATION_INVALID")
        if join_text_parts(utterance_text_parts, unit_mode) != spoken_copy:
            issues.append(f"{segment_id}_UTTERANCES_DO_NOT_RECONSTRUCT_COPY")
        if utterances and abs(utterance_previous - end) > TOLERANCE:
            issues.append(f"{segment_id}_UTTERANCES_DO_NOT_COVER_SEGMENT")

        nodes = row.get("action_nodes") if isinstance(row.get("action_nodes"), list) else []
        if not nodes:
            issues.append(f"{segment_id}_ACTION_NODES_MISSING")
        node_previous = start
        fulfilled: set[str] = set()
        for n_index, node in enumerate(nodes, start=1):
            if not isinstance(node, dict):
                issues.append(f"{segment_id}_ACTION_NODE_{n_index}_INVALID")
                continue
            n_start, n_end = _number(node.get("start_s")), _number(node.get("end_s"))
            if n_start is None or n_end is None or n_end <= n_start or abs(n_start - node_previous) > TOLERANCE or n_end > end + TOLERANCE:
                issues.append(f"{segment_id}_ACTION_NODE_{n_index}_TIME_INVALID")
            else:
                node_previous = n_end
            if not _text(node.get("action")) or not _text(node.get("visible_state_at_end")):
                issues.append(f"{segment_id}_ACTION_NODE_{n_index}_CONTENT_MISSING")
            refs = node.get("fulfills_obligation_ids") if isinstance(node.get("fulfills_obligation_ids"), list) else []
            fulfilled.update(_text(ref) for ref in refs if _text(ref))
        if nodes and abs(node_previous - end) > TOLERANCE:
            issues.append(f"{segment_id}_ACTION_NODES_DO_NOT_REACH_DEADLINE")
        if obligation_ids - fulfilled:
            issues.append(f"{segment_id}_VISUAL_OBLIGATIONS_NOT_FULFILLED")
        if not _text(row.get("terminal_state")):
            issues.append(f"{segment_id}_TERMINAL_STATE_MISSING")
        previous_end = end

    if join_text_parts(reconstructed_segments, unit_mode) != _text(plan.get("full_spoken_copy")):
        issues.append("FULL_COPY_DIFFERS_FROM_SEGMENTS")
    if abs(float(_number(plan.get("total_creative_duration_s")) or -1) - previous_end) > TOLERANCE:
        issues.append("TOTAL_CREATIVE_DURATION_MISMATCH")
    if plan.get("total_provider_request_duration_s") != total_request:
        issues.append("TOTAL_PROVIDER_REQUEST_DURATION_MISMATCH")
    if manifest.get("skill_version") in {"R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
        issues.extend(f"SOURCE_CONTENT:{item}" for item in validate_r635_source_content_project(project, "p3"))
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--plan", default="artifacts/P3/NARRATION_PLAN.json")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    try:
        plan_path = (project / normalize_project_relative(args.plan)).resolve()
        issues = validate(project, plan_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
