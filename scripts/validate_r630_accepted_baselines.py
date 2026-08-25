#!/usr/bin/env python3
"""Validate the two frozen production baselines used by R6.30."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_IDS = {
    "M2_D_SHARE_FIRST_PRODUCTION_V1",
    "M2_F_SOURCE_AUDIO_SCENE_RESTYLE_V2",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("REGISTRY_OBJECT_REQUIRED")
    return value


def validate(registry: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if registry.get("schema_version") != "R6.30-ACCEPTED-PRODUCTION-BASELINES-1.0" or registry.get("status") != "FROZEN":
        issues.append("REGISTRY_SCHEMA_OR_STATUS_INVALID")
    shared = registry.get("shared_contract") if isinstance(registry.get("shared_contract"), dict) else {}
    required_true = {
        "human_approval_before_external_call",
        "one_approval_one_submission",
        "direct_jianying_draft_is_primary",
        "computer_use_is_preview_export_or_documented_fallback_only",
    }
    if any(shared.get(key) is not True for key in required_true) or shared.get("automatic_retry") is not False:
        issues.append("SHARED_COST_OR_DRAFT_DISCIPLINE_INVALID")
    if shared.get("segment_visual_floor") != 80 or shared.get("final_aggregate_floor") != 85:
        issues.append("SHARED_RELEASE_THRESHOLDS_INVALID")
    rows = registry.get("baselines") if isinstance(registry.get("baselines"), list) else []
    by_id = {row.get("baseline_id"): row for row in rows if isinstance(row, dict)}
    if set(by_id) != EXPECTED_IDS or len(rows) != 2:
        issues.append("EXACTLY_TWO_ACCEPTED_BASELINES_REQUIRED")
        return sorted(set(issues))
    m2d = by_id["M2_D_SHARE_FIRST_PRODUCTION_V1"]
    if (
        m2d.get("route_id") != "M2_D_SHARE_FIRST"
        or m2d.get("copy_policy") != "REWRITE_FOR_SHARE_WITH_APPROVED_MEANING"
        or m2d.get("audio_variant") != "POST_DUB_NARRATION"
        or m2d.get("timing_authority") != "NARRATION_MASTER"
        or m2d.get("voice_speed") != 1.3
        or m2d.get("video_alignment") != "VIDEO_FOLLOWS_FINAL_MEASURED_NARRATION"
    ):
        issues.append("M2D_ACCEPTED_CONTRACT_INVALID")
    m2f = by_id["M2_F_SOURCE_AUDIO_SCENE_RESTYLE_V2"]
    if (
        m2f.get("route_id") != "M2_F_SOURCE_AUDIO_RESTYLE"
        or m2f.get("copy_policy") != "VERBATIM_NO_REWRITE"
        or m2f.get("audio_variant") != "SOURCE_AUDIO_REUSE"
        or m2f.get("timing_authority") != "SOURCE_AUDIO_MASTER"
        or m2f.get("source_audio_speed") != 1.0
        or m2f.get("video_alignment") != "VIDEO_FOLLOWS_SOURCE_AUDIO"
    ):
        issues.append("M2F_ACCEPTED_CONTRACT_INVALID")
    if m2d.get("source_video_pixels_as_generation_input") is not False or m2f.get("source_video_pixels_as_generation_input") is not False:
        issues.append("SEMANTIC_MODES_MUST_NOT_USE_SOURCE_PIXELS_AS_GENERATION_INPUT")
    test = registry.get("new_mode_test_contract") if isinstance(registry.get("new_mode_test_contract"), dict) else {}
    if any(test.get(key) is not True for key in {
        "derive_from_exactly_one_baseline",
        "declare_changed_fields_before_p2",
        "new_project_revision_required",
        "timing_authority_must_be_single",
        "visual_authority_must_be_single",
        "fresh_p6_and_p8_external_calls_require_new_seals",
        "diagnose_system_before_retry",
        "model_deviation_within_score_policy_does_not_rewrite_workflow",
    }) or test.get("reuse_old_p3_to_p9_artifacts") is not False:
        issues.append("NEW_MODE_TEST_CONTRACT_INVALID")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    args = parser.parse_args()
    try:
        issues = validate(load(args.registry.resolve()))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    result = {"status": "PASSED" if not issues else "FAILED", "issues": issues}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
