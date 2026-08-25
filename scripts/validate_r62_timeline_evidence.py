#!/usr/bin/env python3
"""Validate R6.2 per-second whole-source evidence and route-specific authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from validate_r62_job import load_json, validate_job


TOLERANCE = 0.06
SOURCE_BOUND: set[str] = set()
SEMANTIC = {"M2_D_SHARE_FIRST"}
SOURCE_AUDIO_SCENE = {"M2_F_SOURCE_AUDIO_RESTYLE"}
AUTHORITY_BY_ROUTE = {
    "M2_D_SHARE_FIRST": "SEMANTIC_FACT_EVIDENCE",
    "M2_F_SOURCE_AUDIO_RESTYLE": "SOURCE_AUDIO_COPY_AND_MACRO_SCENE_EVIDENCE",
}


def canonical_fingerprint(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def validate_timeline_evidence(job: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    issues = [f"JOB:{issue}" for issue in validate_job(job)]
    if evidence.get("schema_version") != "R6.2-TIMELINE-EVIDENCE-1.0":
        issues.append("TIMELINE_EVIDENCE_SCHEMA_INVALID")
    if evidence.get("job_id") != job.get("job_id"):
        issues.append("TIMELINE_EVIDENCE_JOB_MISMATCH")
    route = job.get("route_id")
    if evidence.get("route_id") != route:
        issues.append("TIMELINE_EVIDENCE_ROUTE_MISMATCH")
    if route in SOURCE_AUDIO_SCENE:
        if evidence.get("source_video_sha256") != job.get("source", {}).get("video_sha256"):
            issues.append("M2F_TIMELINE_SOURCE_VIDEO_HASH_MISMATCH")
        if evidence.get("source_audio_sha256") != job.get("source", {}).get("audio_sha256"):
            issues.append("M2F_TIMELINE_SOURCE_AUDIO_HASH_MISMATCH")
    elif evidence.get("source_video_sha256") != job.get("source", {}).get("video_sha256"):
        issues.append("TIMELINE_EVIDENCE_SOURCE_HASH_MISMATCH")
    if evidence.get("authority_role") != AUTHORITY_BY_ROUTE.get(route):
        issues.append("TIMELINE_EVIDENCE_AUTHORITY_ROLE_MISMATCH")
    source_duration = _number(job.get("target", {}).get("duration_s")) if route in SOURCE_AUDIO_SCENE else _number(job.get("source", {}).get("duration_s"))
    evidence_duration = _number(evidence.get("duration_s"))
    if source_duration is None or evidence_duration is None or abs(source_duration - evidence_duration) > TOLERANCE:
        issues.append("TIMELINE_EVIDENCE_DURATION_MISMATCH")

    uncertainties = evidence.get("critical_uncertainties")
    if not isinstance(uncertainties, list):
        issues.append("CRITICAL_UNCERTAINTIES_INVALID")
    elif uncertainties:
        issues.append("CRITICAL_UNCERTAINTY_BLOCKS_P2")

    samples = evidence.get("samples")
    if not isinstance(samples, list) or not samples:
        issues.append("TIMELINE_SAMPLES_MISSING")
        samples = []
    sample_map: dict[str, dict[str, Any]] = {}
    times: list[float] = []
    for index, sample in enumerate(samples, start=1):
        if not isinstance(sample, dict):
            issues.append(f"SAMPLE_{index}_INVALID")
            continue
        sample_id = _text(sample.get("sample_id"))
        if not sample_id or sample_id in sample_map:
            issues.append(f"SAMPLE_{index}_ID_INVALID_OR_DUPLICATE")
            continue
        sample_map[sample_id] = sample
        time_s = _number(sample.get("time_s"))
        if time_s is None:
            issues.append(f"{sample_id}_TIME_INVALID")
        else:
            times.append(time_s)
            if source_duration is not None and not (-TOLERANCE <= time_s <= source_duration + TOLERANCE):
                issues.append(f"{sample_id}_TIME_OUT_OF_RANGE")
        if route in SOURCE_AUDIO_SCENE:
            for field in ("source_audio_segment_id", "exact_copy", "narrative_function"):
                if not _text(sample.get(field)):
                    issues.append(f"{sample_id}_{field.upper()}_MISSING")
            if not isinstance(sample.get("content_facts"), list) or not sample.get("content_facts"):
                issues.append(f"{sample_id}_CONTENT_FACTS_INVALID")
            for forbidden_field in ("camera_space", "object_topology", "action_phase"):
                if _text(sample.get(forbidden_field)):
                    issues.append(f"{sample_id}_M2F_FORBIDS_SOURCE_PIXEL_STRUCTURE_{forbidden_field.upper()}")
        else:
            for field in ("shot_id", "observed_setting", "large_action_or_state", "visible_result"):
                if not _text(sample.get(field)):
                    issues.append(f"{sample_id}_{field.upper()}_MISSING")
            if not isinstance(sample.get("actors"), list) or not sample.get("actors"):
                issues.append(f"{sample_id}_ACTORS_MISSING")
        if sample.get("confidence") not in {"HIGH", "MEDIUM", "LOW"}:
            issues.append(f"{sample_id}_CONFIDENCE_INVALID")
        if not isinstance(sample.get("uncertainty"), str):
            issues.append(f"{sample_id}_UNCERTAINTY_FIELD_INVALID")
        if route in SOURCE_BOUND:
            for field in ("camera_space", "object_topology", "action_phase"):
                if not _text(sample.get(field)):
                    issues.append(f"{sample_id}_SOURCE_STRUCTURE_{field.upper()}_MISSING")
        if route in SEMANTIC:
            if not _text(sample.get("narrative_function")):
                issues.append(f"{sample_id}_NARRATIVE_FUNCTION_MISSING")
            if not isinstance(sample.get("content_facts"), list):
                issues.append(f"{sample_id}_CONTENT_FACTS_INVALID")

    if times:
        if times != sorted(times):
            issues.append("TIMELINE_SAMPLES_NOT_CHRONOLOGICAL")
        ordered = sorted(times)
        if ordered[0] > TOLERANCE:
            issues.append("TIMELINE_DOES_NOT_START_AT_ZERO")
        if source_duration is not None and ordered[-1] < source_duration - TOLERANCE:
            issues.append("TIMELINE_DOES_NOT_REACH_SOURCE_END")
        if any((right - left) > 1.05 for left, right in zip(ordered, ordered[1:])):
            issues.append("TIMELINE_HAS_MORE_THAN_ONE_SECOND_GAP")

    cuts = evidence.get("real_cuts")
    if not isinstance(cuts, list):
        issues.append("REAL_CUTS_INVALID")
        cuts = []
    seen_cut_ids: set[str] = set()
    for index, cut in enumerate(cuts, start=1):
        if not isinstance(cut, dict):
            issues.append(f"CUT_{index}_INVALID")
            continue
        cut_id = _text(cut.get("cut_id"))
        if not cut_id or cut_id in seen_cut_ids:
            issues.append(f"CUT_{index}_ID_INVALID_OR_DUPLICATE")
        seen_cut_ids.add(cut_id)
        cut_time = _number(cut.get("time_s"))
        before = _number(cut.get("before_time_s"))
        after = _number(cut.get("after_time_s"))
        if cut_time is None or before is None or after is None or not before < cut_time < after:
            issues.append(f"{cut_id or index}_CUT_BRACKET_INVALID")
        elif cut_time - before > 0.25 or after - cut_time > 0.25:
            issues.append(f"{cut_id}_CUT_BRACKET_NOT_DENSE_ENOUGH")
        if cut.get("before_sample_id") not in sample_map or cut.get("after_sample_id") not in sample_map:
            issues.append(f"{cut_id}_CUT_SAMPLE_BINDING_INVALID")
        if cut.get("kind") not in {"HARD_CUT", "DISSOLVE", "WIPE", "OTHER_OBSERVED_TRANSITION"}:
            issues.append(f"{cut_id}_CUT_KIND_INVALID")
        if not _text(cut.get("visible_change")):
            issues.append(f"{cut_id}_VISIBLE_CHANGE_MISSING")

    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    try:
        job = load_json(args.job)
        evidence = load_json(args.evidence)
        issues = validate_timeline_evidence(job, evidence)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "issues": [f"READ_ERROR: {exc}"]}, ensure_ascii=False, indent=2))
        return 2
    result = {
        "status": "PASSED" if not issues else "FAILED",
        "timeline_evidence_fingerprint": canonical_fingerprint(evidence),
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
