#!/usr/bin/env python3
"""Validate the frozen M2-D production profile and an optional P9 result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = SKILL_ROOT / "assets" / "m2-d-share-first-production-profile.json"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def expect(issues: list[str], condition: bool, code: str) -> None:
    if not condition:
        issues.append(code)


def validate_profile(profile: dict) -> list[str]:
    issues: list[str] = []
    audio = profile.get("audio", {})
    draft_assembly = profile.get("draft_assembly", {})
    captions = profile.get("captions", {})
    gate = profile.get("release_gate", {})
    capability = profile.get("imagegen_capability_contract", {})
    expect(issues, profile.get("schema_version") == "R6.35-M2D-PRODUCTION-PROFILE-2.0", "PROFILE_SCHEMA")
    expect(issues, profile.get("profile_id") == "M2_D_SHARE_FIRST_PRODUCTION_V1", "PROFILE_ID")
    expect(issues, profile.get("status") == "FROZEN_EXECUTION_CORE_WITH_R635_CONTENT_GATE", "PROFILE_NOT_FROZEN")
    expect(issues, profile.get("route_id") == "M2_D_SHARE_FIRST", "ROUTE")
    expect(issues, profile.get("objective") == "SHARE_FIRST", "OBJECTIVE")
    expect(issues, profile.get("style_profile") == "DOG_HIGH_SHARE_MONO_COMIC", "STYLE_PROFILE")
    expect(issues, profile.get("creative_profile_id") == "DOG_HIGH_SHARE_HEAT_V1", "CREATIVE_PROFILE")
    expect(issues, profile.get("aspect_ratio") == "9:16", "ASPECT_RATIO")
    expected_capability = {
        "tool": "BUILT_IN_IMAGEGEN",
        "profile_id": "CODEX_BUILT_IN_IMAGEGEN_PROMPT_ONLY",
        "geometry_enforcement": "FLEXIBLE_REFERENCE",
        "prompt_only_control_allowed": True,
        "exact_pixels_claimed": False,
        "canvas_and_cell_aspect_are_composition_guidance": True,
        "downstream_start_cell_crop_is_deterministic": True,
        "post_generation_geometry_verification_required": True,
    }
    expect(issues, capability == expected_capability, "IMAGEGEN_CAPABILITY_CONTRACT")
    expect(issues, audio.get("variant") == "POST_DUB_NARRATION", "AUDIO_VARIANT")
    expect(issues, audio.get("timing_authority") == "NARRATION_MASTER", "TIMING_AUTHORITY")
    expect(issues, audio.get("voice_profile_id") == "JIANYING_REAL_PODCAST_FEMALE_1P3", "VOICE_PROFILE")
    expect(issues, audio.get("voice_id") == "真人播客女", "VOICE_ID")
    expect(issues, audio.get("global_speed") == 1.3, "GLOBAL_SPEED")
    expect(issues, audio.get("native_video_audio") == "MUTED", "NATIVE_AUDIO")
    expect(issues, audio.get("video_alignment") == "VIDEO_TO_FINAL_NARRATION", "ALIGNMENT_AUTHORITY")
    expect(issues, draft_assembly.get("primary_method") == "DIRECT_PYJIANYING_EDIT_PLAN", "DRAFT_PRIMARY_METHOD")
    expect(issues, draft_assembly.get("foundation_role") == "READ_ONLY_DEPENDENCY", "FOUNDATION_ROLE")
    expect(issues, draft_assembly.get("computer_use_required_for_assembly") is False, "COMPUTER_USE_ASSEMBLY_POLICY")
    required_captions = {
        "authority": "FINAL_MEASURED_NARRATION",
        "method": "DIRECT_TEXT_TRACK_FROM_FINAL_NARRATION_TIMING",
        "timing_aligner": "LOCAL_FASTER_WHISPER_WITH_APPROVED_SCRIPT_CORRECTION",
        "fallback_method": "NATIVE_SPEECH_RECOGNITION",
        "language_authority": "P1_SOURCE_CONTENT_INHERITANCE",
        "font": "系统",
        "font_size": 12,
        "fill": "#FFFFFF",
        "outline": "black",
        "horizontal_alignment": "center",
        "x": 0,
        "y": -300,
        "maximum_lines": 2,
        "display_punctuation": "OPTIONAL",
        "full_spoken_copy_coverage": True,
        "semantic_word_errors_allowed": 0,
        "temporary_tts_source_text_visible": False,
        "apply_style_to_all_captions": True,
    }
    for key, expected in required_captions.items():
        expect(issues, captions.get(key) == expected, f"CAPTION_PROFILE_{key.upper()}")
    expect(issues, gate.get("captioned_draft_threshold") == 85, "DRAFT_THRESHOLD")
    expect(issues, gate.get("automatic_retry_allowed") is False, "AUTOMATIC_RETRY_POLICY")
    expect(issues, gate.get("caption_overflow_allowed") is False, "CAPTION_OVERFLOW_POLICY")
    return issues


def validate_caption_track(profile: dict, track: dict) -> list[str]:
    issues: list[str] = []
    expected = profile["captions"]
    generation = track.get("generation", {})
    content = track.get("content_check", {})
    style = track.get("style", {})
    stage = track.get("stage_contract", {})
    segments = track.get("segments", [])
    expect(issues, track.get("mode") == profile["route_id"], "TRACK_ROUTE")
    expect(issues, track.get("status") == "PASSED", "TRACK_STATUS")
    expect(issues, track.get("caption_authority") == expected["authority"], "TRACK_AUTHORITY")
    expect(issues, generation.get("method") in {expected["method"], expected["fallback_method"]}, "TRACK_METHOD")
    expect(issues, bool(str(generation.get("language", "")).strip()), "TRACK_LANGUAGE")
    expect(issues, generation.get("automatic_retry_count") == 0, "TRACK_AUTOMATIC_RETRY")
    expect(issues, isinstance(segments, list) and len(segments) > 0, "TRACK_SEGMENTS_EMPTY")
    expect(issues, track.get("segment_count") == len(segments), "TRACK_SEGMENT_COUNT")
    expect(issues, all(isinstance(item, str) and item.strip() for item in segments), "TRACK_EMPTY_CAPTION")
    expect(issues, content.get("full_spoken_copy_coverage") == "PASSED", "TRACK_COPY_COVERAGE")
    expect(issues, content.get("missing_phrase_check") == "PASSED", "TRACK_MISSING_PHRASE")
    expect(issues, content.get("wrong_or_missing_semantic_words") == 0, "TRACK_SEMANTIC_ERRORS")
    style_map = {
        "font": "font",
        "font_size": "font_size",
        "fill": "fill",
        "outline": "outline",
        "horizontal_alignment": "horizontal_alignment",
        "x": "x",
        "y": "y",
        "max_lines_observed": "maximum_lines",
        "applied_to_all_captions": "apply_style_to_all_captions",
    }
    for actual_key, expected_key in style_map.items():
        expect(issues, style.get(actual_key) == expected[expected_key], f"TRACK_STYLE_{actual_key.upper()}")
    expect(issues, style.get("vertical_canvas_overflow") is False, "TRACK_VERTICAL_OVERFLOW")
    expect(issues, stage.get("formal_captions_added_only_in_p9") is True, "TRACK_CAPTION_STAGE")
    expect(issues, stage.get("temporary_tts_source_text_track_visible") is False, "TRACK_TEMP_TEXT_VISIBLE")
    return issues


def validate_draft_qc(profile: dict, qc: dict) -> list[str]:
    issues: list[str] = []
    checks = qc.get("checks", {})
    captions = qc.get("captions", {})
    expect(issues, qc.get("decision") == "PASSED", "QC_DECISION")
    expect(issues, qc.get("draft_score", 0) >= profile["release_gate"]["captioned_draft_threshold"], "QC_SCORE")
    for key in (
        "single_continuous_narration",
        "global_voice_speed_is_1_3x",
        "video_adapts_to_voice",
        "native_video_audio_muted",
        "temporary_tts_text_hidden",
        "formal_caption_track_present",
        "caption_spoken_copy_coverage",
        "caption_timing_uses_speech_recognition",
        "caption_style_applied_to_all",
        "caption_longest_line_within_canvas",
        "canvas_is_vertical_9_16",
    ):
        expect(issues, checks.get(key) == "PASSED", f"QC_CHECK_{key.upper()}")
    expected = profile["captions"]
    expect(issues, captions.get("font") == expected["font"], "QC_FONT")
    expect(issues, captions.get("font_size") == expected["font_size"], "QC_FONT_SIZE")
    expect(issues, captions.get("x") == expected["x"], "QC_X")
    expect(issues, captions.get("y") == expected["y"], "QC_Y")
    expect(issues, captions.get("maximum_lines", 99) <= expected["maximum_lines"], "QC_MAXIMUM_LINES")
    expect(issues, captions.get("content_errors") == 0, "QC_CONTENT_ERRORS")
    expect(issues, captions.get("automatic_retries") == 0, "QC_AUTOMATIC_RETRIES")
    return issues


def validate_direct_draft_receipt(receipt: dict) -> list[str]:
    issues: list[str] = []
    expect(issues, receipt.get("status") == "PASSED", "DIRECT_DRAFT_STATUS")
    expect(issues, receipt.get("method") == "DIRECT_PYJIANYING_EDIT_PLAN", "DIRECT_DRAFT_METHOD")
    expect(issues, receipt.get("computer_use_required_for_assembly") is False, "DIRECT_DRAFT_COMPUTER_USE")
    files = receipt.get("files", [])
    names = {item.get("path") for item in files if isinstance(item, dict)}
    expect(issues, {"draft_content.json", "draft_meta_info.json"}.issubset(names), "DIRECT_DRAFT_FILES")
    expect(issues, receipt.get("file_count") == len(files), "DIRECT_DRAFT_FILE_COUNT")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--caption-track", type=Path)
    parser.add_argument("--draft-qc", type=Path)
    parser.add_argument("--direct-draft-receipt", type=Path)
    args = parser.parse_args()
    try:
        profile = load_json(args.profile)
        issues = validate_profile(profile)
        if args.caption_track:
            issues.extend(validate_caption_track(profile, load_json(args.caption_track)))
        if args.draft_qc:
            issues.extend(validate_draft_qc(profile, load_json(args.draft_qc)))
        if args.direct_draft_receipt:
            issues.extend(validate_direct_draft_receipt(load_json(args.direct_draft_receipt)))
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
        issues = [f"INVALID_INPUT:{exc}"]
    result = {
        "status": "PASSED" if not issues else "BLOCKED_P0",
        "profile_id": "M2_D_SHARE_FIRST_PRODUCTION_V1",
        "issues": sorted(set(issues)),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
