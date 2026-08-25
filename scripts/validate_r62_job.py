#!/usr/bin/env python3
"""Validate the R6.2 route lock, geometry contract, and cross-mode isolation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from validate_r619_state_contract import validate_job as validate_r619_job_state_contract
from accent_color_contract import validate_mono_identity
from r635_source_content_lock import validate_job_policy as validate_r635_source_content_policy


sys.dont_write_bytecode = True


ROUTES = {
    "M2_D_SHARE_FIRST": {
        "profile": "SEMANTIC_SCENE_REMAKE",
        "objective": "SHARE_FIRST",
        "authority": "APPROVED_SHARE_SCENE_BLUEPRINT",
        "timing": "NARRATION_MASTER",
    },
    "M2_F_SOURCE_AUDIO_RESTYLE": {
        "profile": "SOURCE_AUDIO_SCENE_RESTYLE",
        "objective": "NONE",
        "authority": "SOURCE_AUDIO_COPY_PLUS_SOURCE_VIDEO_MACRO_SCENES",
        "timing": "SOURCE_AUDIO_MASTER",
    },
}

STYLES = {
    "DOG_HIGH_SHARE_MONO_COMIC",
    "DOG_STYLE_C_GHIBLI_PET_NARRATIVE",
    "DOG_STYLE_D_INDOOR_CARE_KEYFRAME",
    "DOG_STYLE_E_REACTION_RESONANCE",
    "CUSTOM_NAMED_STYLE",
}

VISUAL_PLAN_MODES = {"SCENE_SCOPED_ACTION_GRIDS", "ORDERED_KEYFRAMES"}
IMAGEGEN_CAPABILITY_PROFILES = {
    "CODEX_BUILT_IN_IMAGEGEN_PROMPT_ONLY",
    "PROJECT_VERIFIED_EXACT_GEOMETRY",
}
PROVIDER_ADAPTER_PROFILES = {
    "GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND",
    "GROK_WEB_GRID_PLUS_FIRST_FRAME",
    "SEEDANCE_GRID_ONLY",
    "ORDERED_KEYFRAMES",
    "CUSTOM_VERIFIED",
}
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
ASPECT = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
PLACEHOLDER_MARKERS = ("REPLACE_WITH", "TODO", "<", ">")
PROFILE_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "assets" / "creative-profile-registry.json"
VISUAL_CORE_PATH = Path(__file__).resolve().parents[1] / "assets" / "shared-visual-production-core.json"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _placeholder(value: Any) -> bool:
    text = _text(value)
    return not text or any(marker in text for marker in PLACEHOLDER_MARKERS)


def _list_of_text(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_text(item) for item in value)


def _portable_relative_path(value: Any, *, allow_empty: bool = False) -> bool:
    text = _text(value).replace("\\", "/")
    if not text:
        return allow_empty
    if text.startswith("/") or text.startswith("//") or re.match(r"^[A-Za-z]:", text):
        return False
    parts = PurePosixPath(text).parts
    return bool(parts) and ".." not in parts and "." not in parts


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_creative_profile_registry() -> dict[str, Any]:
    registry = load_json(PROFILE_REGISTRY_PATH)
    if registry.get("schema_version") not in {
        "R6.19-CREATIVE-PROFILE-REGISTRY-1.0",
        "R6.31-CREATIVE-PROFILE-REGISTRY-1.0",
    }:
        raise ValueError("CREATIVE_PROFILE_REGISTRY_SCHEMA_INVALID")
    if not isinstance(registry.get("profiles"), dict):
        raise ValueError("CREATIVE_PROFILE_REGISTRY_PROFILES_MISSING")
    return registry


def resolve_creative_profile(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = load_creative_profile_registry()
    binding = data.get("creative_profile_binding")
    if not isinstance(binding, dict):
        raise ValueError("CREATIVE_PROFILE_BINDING_MISSING")
    profile_id = _text(binding.get("profile_id"))
    profile = registry["profiles"].get(profile_id)
    if not isinstance(profile, dict):
        raise ValueError("CREATIVE_PROFILE_ID_INVALID")
    return profile, registry


def validate_job(data: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    if data.get("schema_version") not in {"R6.2-JOB-1.0", "R6.35-JOB-1.0"}:
        issues.append("SCHEMA_VERSION_INVALID")
    if data.get("schema_version") == "R6.35-JOB-1.0":
        issues.extend(validate_r635_source_content_policy(data, require_r635_schema=True))
    if data.get("status") != "LOCKED":
        issues.append("JOB_NOT_LOCKED")
    if _placeholder(data.get("job_id")):
        issues.append("JOB_ID_MISSING_OR_PLACEHOLDER")
    if data.get("path_policy") != "PROJECT_RELATIVE_ONLY":
        issues.append("PATH_POLICY_MUST_BE_PROJECT_RELATIVE_ONLY")
    if not _portable_relative_path(data.get("project_manifest_path")):
        issues.append("PROJECT_MANIFEST_PATH_NOT_PORTABLE")

    route = data.get("route_id")
    expected = ROUTES.get(route)
    if expected is None:
        issues.append("ROUTE_ID_INVALID_OR_BARE_STYLE_MODE")
    else:
        if data.get("execution_profile") != expected["profile"]:
            issues.append("ROUTE_PROFILE_MISMATCH")
        if data.get("objective_profile") != expected["objective"]:
            issues.append("ROUTE_OBJECTIVE_MISMATCH")
        if data.get("derivation_authority") != expected["authority"]:
            issues.append("ROUTE_DERIVATION_AUTHORITY_MISMATCH")

    source = data.get("source")
    if not isinstance(source, dict):
        issues.append("SOURCE_BLOCK_MISSING")
        source = {}
    if _placeholder(source.get("video_path")):
        issues.append("SOURCE_VIDEO_PATH_MISSING")
    elif not _portable_relative_path(source.get("video_path")):
        issues.append("SOURCE_VIDEO_PATH_NOT_PROJECT_RELATIVE")
    if not HEX64.fullmatch(_text(source.get("video_sha256"))):
        issues.append("SOURCE_VIDEO_SHA256_INVALID")
    try:
        if float(source.get("duration_s", 0)) <= 0:
            issues.append("SOURCE_DURATION_INVALID")
    except (TypeError, ValueError):
        issues.append("SOURCE_DURATION_INVALID")
    if not ASPECT.fullmatch(_text(source.get("aspect_ratio"))):
        issues.append("SOURCE_ASPECT_RATIO_INVALID")

    for path_key, hash_key in (("audio_path", "audio_sha256"), ("cover_path", "cover_sha256")):
        path_value = _text(source.get(path_key))
        hash_value = _text(source.get(hash_key))
        if path_value and not _portable_relative_path(path_value):
            issues.append(f"{path_key.upper()}_NOT_PROJECT_RELATIVE")
        if bool(path_value) != bool(hash_value):
            issues.append(f"{path_key.upper()}_HASH_PAIR_INCOMPLETE")
        if hash_value and not HEX64.fullmatch(hash_value):
            issues.append(f"{hash_key.upper()}_INVALID")

    target = data.get("target")
    if not isinstance(target, dict):
        issues.append("TARGET_BLOCK_MISSING")
        target = {}
    try:
        if float(target.get("duration_s", 0)) <= 0:
            issues.append("TARGET_DURATION_INVALID")
    except (TypeError, ValueError):
        issues.append("TARGET_DURATION_INVALID")
    if not ASPECT.fullmatch(_text(target.get("aspect_ratio"))):
        issues.append("TARGET_ASPECT_RATIO_INVALID")
    if _placeholder(target.get("audience")):
        issues.append("TARGET_AUDIENCE_MISSING")
    if target.get("audio_variant") not in {"POST_DUB_NARRATION", "A_NARRATION", "B_ONSCREEN_SPEECH", "A_AND_B", "SOURCE_AUDIO_REUSE"}:
        issues.append("AUDIO_VARIANT_INVALID")
    timing_authority = target.get("timing_authority")
    if timing_authority not in {"NARRATION_MASTER", "SOURCE_TIMELINE", "SOURCE_AUDIO_MASTER"}:
        issues.append("TIMING_AUTHORITY_INVALID")
    elif expected is not None and expected.get("timing") is not None and timing_authority != expected.get("timing"):
        issues.append("ROUTE_TIMING_AUTHORITY_MISMATCH")
    if target.get("run_purpose") not in {"EXPLORATORY_TEST", "PRODUCTION", "DEBUG_REPAIR"}:
        issues.append("RUN_PURPOSE_INVALID")

    geometry = data.get("grid_geometry_contract")
    if not isinstance(geometry, dict):
        issues.append("GRID_GEOMETRY_CONTRACT_MISSING")
        geometry = {}
    canvas_aspect = _text(geometry.get("canvas_aspect_ratio"))
    cell_aspect = _text(geometry.get("cell_aspect_ratio"))
    if not ASPECT.fullmatch(canvas_aspect):
        issues.append("GRID_CANVAS_ASPECT_RATIO_INVALID")
    if not ASPECT.fullmatch(cell_aspect):
        issues.append("GRID_CELL_ASPECT_RATIO_INVALID")
    if canvas_aspect and canvas_aspect != _text(target.get("aspect_ratio")):
        issues.append("GRID_CANVAS_ASPECT_MUST_MATCH_TARGET")
    if cell_aspect and cell_aspect != _text(target.get("aspect_ratio")):
        issues.append("GRID_CELL_ASPECT_MUST_MATCH_TARGET")
    if geometry.get("enforcement") not in {"EXACT_PIXELS", "FLEXIBLE_REFERENCE"}:
        issues.append("GRID_GEOMETRY_ENFORCEMENT_INVALID")
    if geometry.get("enforcement") == "EXACT_PIXELS" and geometry.get("prompt_only_control_allowed") is not False:
        issues.append("EXACT_GEOMETRY_FORBIDS_PROMPT_ONLY_CONTROL")
    imagegen_profile = _text(data.get("imagegen_capability_profile_id"))
    if imagegen_profile not in IMAGEGEN_CAPABILITY_PROFILES:
        issues.append("IMAGEGEN_CAPABILITY_PROFILE_INVALID")
    elif imagegen_profile == "CODEX_BUILT_IN_IMAGEGEN_PROMPT_ONLY":
        if geometry.get("enforcement") != "FLEXIBLE_REFERENCE":
            issues.append("BUILTIN_IMAGEGEN_REQUIRES_FLEXIBLE_REFERENCE")
        if geometry.get("prompt_only_control_allowed") is not True:
            issues.append("BUILTIN_IMAGEGEN_REQUIRES_PROMPT_ONLY_GEOMETRY_GUIDANCE")
    elif imagegen_profile == "PROJECT_VERIFIED_EXACT_GEOMETRY":
        if geometry.get("enforcement") != "EXACT_PIXELS":
            issues.append("EXACT_IMAGEGEN_PROFILE_REQUIRES_EXACT_PIXELS")
        if geometry.get("prompt_only_control_allowed") is not False:
            issues.append("EXACT_IMAGEGEN_PROFILE_FORBIDS_PROMPT_ONLY_GEOMETRY")

    style = data.get("style_profile")
    if style not in STYLES:
        issues.append("STYLE_PROFILE_INVALID")
    if route in ROUTES and style not in STYLES:
        issues.append("DF_ROUTE_REQUIRES_REGISTERED_VISUAL_STYLE")

    binding = data.get("creative_profile_binding")
    if not isinstance(binding, dict):
        issues.append("CREATIVE_PROFILE_BINDING_MISSING")
        binding = {}
    if binding.get("binding_mode") not in {"FIXED_PROFILE", "PROJECT_DEFINED_PROFILE"}:
        issues.append("CREATIVE_PROFILE_BINDING_MODE_INVALID")
    profile_id = _text(binding.get("profile_id"))
    try:
        profile, profile_registry = resolve_creative_profile(data)
    except (OSError, ValueError, json.JSONDecodeError):
        profile = {}
        profile_registry = {}
        if profile_id:
            issues.append("CREATIVE_PROFILE_ID_OR_REGISTRY_INVALID")
    if profile:
        if _text(binding.get("profile_sha256")) != _canonical_sha256(profile):
            issues.append("CREATIVE_PROFILE_SHA256_MISMATCH")
        if route not in profile.get("allowed_route_ids", []):
            issues.append("CREATIVE_PROFILE_ROUTE_MISMATCH")
        required_style = profile.get("required_style_profile")
        if required_style is not None and style != required_style:
            issues.append("CREATIVE_PROFILE_STYLE_MISMATCH")
        allowed_styles = profile.get("allowed_style_profiles")
        if not isinstance(allowed_styles, list) or style not in allowed_styles:
            issues.append("CREATIVE_PROFILE_STYLE_NOT_ALLOWED")
        expected_mode = "FIXED_PROFILE" if profile.get("identity_policy") == "FIXED_REFERENCE_IDENTITY" else "PROJECT_DEFINED_PROFILE"
        if binding.get("binding_mode") != expected_mode:
            issues.append("CREATIVE_PROFILE_BINDING_MODE_MISMATCH")
        fixed_identity = profile.get("fixed_project_identity") if isinstance(profile.get("fixed_project_identity"), dict) else {}
        actual_identity = data.get("project_identity") if isinstance(data.get("project_identity"), dict) else {}
        for key, expected_value in fixed_identity.items():
            if key == "accent_colors" and isinstance(expected_value, list):
                actual_accents = actual_identity.get(key)
                if not isinstance(actual_accents, list) or any(value not in actual_accents for value in expected_value):
                    issues.append("CREATIVE_PROFILE_FIXED_ACCENT_COLORS_MISMATCH")
            elif actual_identity.get(key) != expected_value:
                issues.append(f"CREATIVE_PROFILE_FIXED_{key.upper()}_MISMATCH")
        if route in {"M2_D_SHARE_FIRST", "M2_F_SOURCE_AUDIO_RESTYLE"}:
            visual_core = profile.get("visual_production_core")
            if not isinstance(visual_core, dict):
                issues.append("R619_VISUAL_PRODUCTION_CORE_BINDING_MISSING")
            else:
                if visual_core.get("core_id") != "DOG_DF_VISUAL_CORE_V3":
                    issues.append("R619_VISUAL_PRODUCTION_CORE_ID_INVALID")
                if visual_core.get("relative_path") != "assets/shared-visual-production-core.json":
                    issues.append("R619_VISUAL_PRODUCTION_CORE_PATH_INVALID")
                actual_core_hash = hashlib.sha256(VISUAL_CORE_PATH.read_bytes()).hexdigest()
                if visual_core.get("sha256") != actual_core_hash:
                    issues.append("R619_VISUAL_PRODUCTION_CORE_HASH_MISMATCH")
    mandatory_bindings = profile_registry.get("mandatory_route_style_bindings", []) if isinstance(profile_registry, dict) else []
    for rule in mandatory_bindings if isinstance(mandatory_bindings, list) else []:
        if not isinstance(rule, dict):
            continue
        if route == rule.get("route_id") and style == rule.get("style_profile") and profile_id != rule.get("required_profile_id"):
            issues.append("MANDATORY_ROUTE_STYLE_CREATIVE_PROFILE_MISMATCH")
    visual_plan_mode = data.get("visual_plan_mode")
    adapter = data.get("provider_adapter_profile")
    if visual_plan_mode not in VISUAL_PLAN_MODES:
        issues.append("VISUAL_PLAN_MODE_INVALID")
    if adapter not in PROVIDER_ADAPTER_PROFILES:
        issues.append("PROVIDER_ADAPTER_PROFILE_INVALID")
    if visual_plan_mode == "ORDERED_KEYFRAMES" and adapter != "ORDERED_KEYFRAMES":
        issues.append("ORDERED_KEYFRAMES_ADAPTER_MISMATCH")
    if visual_plan_mode == "SCENE_SCOPED_ACTION_GRIDS" and adapter == "ORDERED_KEYFRAMES":
        issues.append("SCENE_GRID_ADAPTER_MISMATCH")

    provider_intent = data.get("provider_intent")
    if not isinstance(provider_intent, dict):
        issues.append("PROVIDER_INTENT_MISSING")
        provider_intent = {}
    for key in ("provider", "model_family"):
        if _placeholder(provider_intent.get(key)):
            issues.append(f"PROVIDER_INTENT_{key.upper()}_MISSING")
    if provider_intent.get("exact_model_and_endpoint_lock_phase") != "P8":
        issues.append("PROVIDER_EXACT_LOCK_PHASE_INVALID")
    if adapter == "GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND":
        if provider_intent.get("provider") != "KIE.AI" or provider_intent.get("model_family") != "GROK_IMAGINE_VIDEO_1_5":
            issues.append("GROK_KIE_PROVIDER_INTENT_MISMATCH")

    strategy = data.get("grid_strategy")
    if not isinstance(strategy, dict):
        issues.append("GRID_STRATEGY_MISSING")
        strategy = {}
    expected_strategy = {
        "scope": "ONE_GRID_PER_VIDEO_SEGMENT",
        "adaptive_layout": True,
        "selection_authority": "NECESSARY_VISUAL_BEATS",
        "fixed_time_slicing": False,
        "global_story_grid_provider_input": False,
        "maximum_unrelated_scenes_per_grid": 0,
    }
    for key, value in expected_strategy.items():
        if strategy.get(key) != value:
            issues.append(f"GRID_STRATEGY_{key.upper()}_INVALID")
    if strategy.get("pilot_mode") not in {True, False}:
        issues.append("GRID_STRATEGY_PILOT_MODE_INVALID")
    if strategy.get("pilot_mode") is True:
        if strategy.get("pilot_grid_order") != 1:
            issues.append("PILOT_GRID_ORDER_MUST_BE_ONE")
        if strategy.get("pilot_default_layout") != "2x2":
            issues.append("PILOT_DEFAULT_LAYOUT_MUST_BE_2X2")

    anchor = data.get("visual_anchor_contract")
    if not isinstance(anchor, dict):
        issues.append("VISUAL_ANCHOR_CONTRACT_MISSING")
        anchor = {}
    if anchor.get("required") not in {True, False}:
        issues.append("VISUAL_ANCHOR_REQUIRED_FLAG_INVALID")
    if anchor.get("execution_strategy") not in {
        "TEST_SEQUENTIAL_ANCHORED",
        "PRODUCTION_BATCH_SHARED_ANCHOR",
        "TEST_INCREMENTAL_UNANCHORED",
    }:
        issues.append("VISUAL_ANCHOR_EXECUTION_STRATEGY_INVALID")
    if anchor.get("anchor_origin") not in {"FIRST_PASSED_GRID", "APPROVED_DESIGN_SHEET", "SOURCE_LOCKED"}:
        issues.append("VISUAL_ANCHOR_ORIGIN_INVALID")
    if anchor.get("anchor_grid_order") != 1:
        issues.append("VISUAL_ANCHOR_GRID_ORDER_MUST_BE_ONE")
    if anchor.get("project_anchor_required_from_grid_order") != 2:
        issues.append("VISUAL_ANCHOR_REQUIRED_FROM_GRID_TWO")
    if anchor.get("previous_segment_end_state_required") not in {True, False}:
        issues.append("PREVIOUS_END_STATE_REQUIRED_FLAG_INVALID")
    if anchor.get("unanchored_outputs_may_enter_p7") is not False:
        issues.append("UNANCHORED_OUTPUTS_FORBIDDEN_FROM_P7")
    if anchor.get("same_prompt_n_variants_are_distinct_segments") is not False:
        issues.append("N_VARIANTS_CANNOT_REPRESENT_DISTINCT_SEGMENTS")
    if anchor.get("required") is True and anchor.get("execution_strategy") == "TEST_INCREMENTAL_UNANCHORED":
        issues.append("REQUIRED_VISUAL_ANCHOR_FORBIDS_UNANCHORED_STRATEGY")
    project_identity = data.get("project_identity") if isinstance(data.get("project_identity"), dict) else {}
    cross_segment_identity_requested = all(_text(project_identity.get(key)) for key in ("person", "animal", "environment"))
    if cross_segment_identity_requested and anchor.get("required") is not True:
        issues.append("PROJECT_IDENTITY_REQUIRES_VISUAL_ANCHOR")

    content = data.get("content_contract")
    if not isinstance(content, dict):
        issues.append("CONTENT_CONTRACT_MISSING")
        content = {}
    if _placeholder(content.get("topic")):
        issues.append("TOPIC_MISSING")
    if not _list_of_text(content.get("blocking_obligations")):
        issues.append("BLOCKING_OBLIGATIONS_MISSING")
    if route == "M2_D_SHARE_FIRST" and _placeholder(content.get("objective_brief")):
        issues.append("OBJECTIVE_BRIEF_MISSING")

    source_audio_copy = data.get("source_audio_copy_contract")
    if route == "M2_F_SOURCE_AUDIO_RESTYLE":
        if not isinstance(source_audio_copy, dict):
            issues.append("M2F_SOURCE_AUDIO_COPY_CONTRACT_MISSING")
            source_audio_copy = {}
        expected_contract = {
            "audio_relative_path": source.get("audio_path"),
            "audio_sha256": source.get("audio_sha256"),
            "copy_policy": "VERBATIM_NO_REWRITE",
            "audio_usage": "FULL_TRACK_UNCHANGED",
            "playback_speed": 1.0,
            "source_video_semantic_authority": "MACRO_SCENE_ACTION_CAUSAL_ONLY",
            "source_video_pixel_authority": "NONE",
            "source_video_camera_cut_authority": "NONE",
            "source_video_keyframes_as_generation_input": False,
        }
        for key, expected_value in expected_contract.items():
            if source_audio_copy.get(key) != expected_value:
                issues.append(f"M2F_SOURCE_AUDIO_COPY_{key.upper()}_INVALID")
        if not _portable_relative_path(source_audio_copy.get("copy_relative_path")):
            issues.append("M2F_SOURCE_COPY_PATH_NOT_PROJECT_RELATIVE")
        if not HEX64.fullmatch(_text(source_audio_copy.get("copy_sha256"))):
            issues.append("M2F_SOURCE_COPY_SHA256_INVALID")
        if not _text(source.get("audio_path")) or not HEX64.fullmatch(_text(source.get("audio_sha256"))):
            issues.append("M2F_REQUIRES_LOCKED_SOURCE_AUDIO")
        if not _portable_relative_path(source.get("video_path")) or not HEX64.fullmatch(_text(source.get("video_sha256"))):
            issues.append("M2F_REQUIRES_LOCKED_SOURCE_VIDEO_FOR_MACRO_SCENE_EVIDENCE")
        if target.get("audio_variant") != "SOURCE_AUDIO_REUSE":
            issues.append("M2F_REQUIRES_SOURCE_AUDIO_REUSE")
    elif source_audio_copy:
        issues.append("SOURCE_AUDIO_COPY_CONTRACT_ONLY_ALLOWED_FOR_M2F")

    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        issues.append("PERMISSIONS_MISSING")
        permissions = {}
    for key in ("keep", "redesign", "forbid"):
        if not isinstance(permissions.get(key), list):
            issues.append(f"PERMISSIONS_{key.upper()}_INVALID")
    if route in ROUTES and not permissions.get("redesign"):
        issues.append("DF_REDESIGN_REGISTRY_EMPTY")

    preservation = data.get("preservation_contract")
    if not isinstance(preservation, dict):
        issues.append("PRESERVATION_CONTRACT_MISSING")
        preservation = {}
    required_preservation_keys = {
        "copy_policy",
        "narrative_policy",
        "action_policy",
        "shot_cut_policy",
        "camera_space_policy",
        "timing_policy",
        "visual_policy",
        "source_grid_role",
    }
    if any(not _text(preservation.get(key)) for key in required_preservation_keys):
        issues.append("PRESERVATION_CONTRACT_INCOMPLETE")

    if route == "M2_D_SHARE_FIRST":
        if preservation.get("source_grid_role") != "SEMANTIC_EVIDENCE_ONLY":
            issues.append("SEMANTIC_ROUTE_FORBIDS_SOURCE_GRID_MASTER")
        if preservation.get("action_policy") != "APPROVED_LARGE_ACTIONS":
            issues.append("SEMANTIC_ROUTE_REQUIRES_LARGE_ACTION_POLICY")
    elif route == "M2_F_SOURCE_AUDIO_RESTYLE":
        exact_fields = {
            "copy_policy": "EXACT_SOURCE_COPY",
            "narrative_policy": "EXACT_SOURCE_ORDER",
            "action_policy": "SOURCE_VIDEO_MACRO_ACTIONS_ALIGNED_TO_COPY",
            "shot_cut_policy": "NEW_RESTYLED_SHOTS",
            "camera_space_policy": "NEW_RESTYLED_CAMERA_SPACE",
            "timing_policy": "EXACT_SOURCE_AUDIO_TIMECODES",
            "visual_policy": "FULL_RESTYLE_FROM_SOURCE_SCENE_SEMANTICS",
            "source_grid_role": "SEMANTIC_ACTION_EVIDENCE_ONLY",
        }
        for key, value in exact_fields.items():
            if preservation.get(key) != value:
                issues.append(f"M2F_{key.upper()}_INVALID")

    identity = data.get("project_identity")
    if not isinstance(identity, dict):
        issues.append("PROJECT_IDENTITY_MISSING")
    else:
        for key in ("person", "animal", "environment"):
            if _placeholder(identity.get(key)):
                issues.append(f"IDENTITY_{key.upper()}_MISSING")
        if style == "DOG_HIGH_SHARE_MONO_COMIC":
            issues.extend(validate_mono_identity(identity))

    budget = data.get("generation_budget")
    expected_budget = {
        "per_grid_baseline_calls": 1,
        "per_grid_consolidated_corrections": 1,
        "pilot_gate_after_first_grid": True,
        "per_cell_calls": 0,
        "auto_retry": False,
        "human_approval_before_each_call": True,
        "one_approval_one_submission": True,
    }
    if not isinstance(budget, dict):
        issues.append("GENERATION_BUDGET_MISSING")
    else:
        for key, value in expected_budget.items():
            if budget.get(key) != value:
                issues.append(f"COST_RULE_{key.upper()}_INVALID")
        for key in ("project_max_grid_baselines", "project_max_grid_corrections"):
            value = budget.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                issues.append(f"COST_RULE_{key.upper()}_INVALID")
        baselines = budget.get("project_max_grid_baselines")
        corrections = budget.get("project_max_grid_corrections")
        if isinstance(baselines, int) and isinstance(corrections, int) and corrections > baselines:
            issues.append("PROJECT_CORRECTION_BUDGET_EXCEEDS_BASELINES")

    issues.extend(validate_r619_job_state_contract(data))
    return sorted(set(issues))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    try:
        data = load_json(args.job)
        issues = validate_job(data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "issues": [f"READ_ERROR: {exc}"]}, ensure_ascii=False, indent=2))
        return 2
    result = {
        "status": "PASSED" if not issues else "FAILED",
        "route_id": data.get("route_id"),
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
