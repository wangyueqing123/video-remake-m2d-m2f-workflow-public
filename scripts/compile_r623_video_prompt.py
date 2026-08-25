#!/usr/bin/env python3
"""Compile the canonical R6.23 P7 video Prompt without changing the R6.22 flow."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from r62_project import STATE_NAME, load_json, normalize_project_relative, sha256_file, write_json_atomic
from r625_p7_timing import derive_timing_nodes
from r634_integrity_contract import resolve_effective_inputs
from validate_r62_segment_package import validate as validate_segment_package

sys.dont_write_bytecode = True


SCHEMA = "R6.23-P7-VIDEO-PROMPT-AUDIT-1.0"
COMPILER = "compile_r623_video_prompt.py"
INTERNAL_CHARACTER_BUDGET = 4000
ADAPTER = "GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND"


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def relative_seconds(value: object, origin: float) -> float:
    return round(max(0.0, float(value) - origin), 2)


def project_file(project: Path, relative: str) -> Path:
    normalized = normalize_project_relative(relative)
    path = (project / normalized).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{normalized}")
    return path


def find_segment(scene_plan: dict[str, Any], segment_id: str) -> dict[str, Any]:
    rows = [row for row in scene_plan.get("video_segments", []) if isinstance(row, dict) and row.get("segment_id") == segment_id]
    if len(rows) != 1:
        raise ValueError("P4_SEGMENT_MISSING_OR_DUPLICATE")
    return rows[0]


def identity_sentence(job: dict[str, Any]) -> str:
    identity = job.get("project_identity") if isinstance(job.get("project_identity"), dict) else {}
    props = identity.get("props") if isinstance(identity.get("props"), list) else []
    accents = identity.get("accent_colors") if isinstance(identity.get("accent_colors"), list) else []
    fields = [
        f"Person: {clean(identity.get('person'))}",
        f"Animal: {clean(identity.get('animal'))}",
        f"Environment: {clean(identity.get('environment'))}",
        "Props: " + "；".join(clean(item) for item in props if clean(item)),
        "Named accent colors only: " + "；".join(clean(item) for item in accents if clean(item)),
    ]
    return " ".join(item for item in fields if not item.endswith(": "))


def style_sentence(skill_root: Path, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    registry_path = skill_root / "assets/style-registry.json"
    registry = load_json(registry_path)
    style_id = clean(job.get("style_profile"))
    styles = registry.get("styles") if isinstance(registry.get("styles"), dict) else {}
    style = styles.get(style_id) if isinstance(styles.get(style_id), dict) else None
    if not style:
        raise ValueError(f"STYLE_PROFILE_NOT_FOUND:{style_id}")
    negatives = style.get("negatives") if isinstance(style.get("negatives"), list) else []
    payload = {
        "style_id": style_id,
        "prompt_core": clean(style.get("prompt_core")),
        "negatives": [clean(item) for item in negatives if clean(item)],
        "registry_relative_path": "assets/style-registry.json",
        "registry_sha256": sha256_file(registry_path),
    }
    return payload["prompt_core"], payload


def compile_prompt(
    job: dict[str, Any],
    p4_segment: dict[str, Any],
    package: dict[str, Any],
    style_text: str,
    timing_nodes: list[dict[str, Any]],
) -> str:
    segment_id = clean(package.get("segment_id"))
    start = float(package["target_start_s"])
    end = float(package["target_end_s"])
    span = end - start
    request_seconds = max(1, math.ceil(span - 1e-9))
    action = package.get("action_contract") if isinstance(package.get("action_contract"), dict) else {}
    forbidden = action.get("forbidden_alternatives") if isinstance(action.get("forbidden_alternatives"), list) else []

    timing_lines: list[str] = []
    for node in timing_nodes:
        node_start = relative_seconds(node.get("start_s"), start)
        node_end = min(round(span, 2), relative_seconds(node.get("end_s"), start))
        timing_lines.append(
            f"- {node_start:.2f}-{node_end:.2f}s: {clean(node.get('action'))} "
            f"Visible state at the end: {clean(node.get('visible_state_at_end'))}"
        )
    if request_seconds > span + 1e-6:
        timing_lines.append(
            f"- {span:.2f}-{request_seconds:.2f}s: preserve the approved final state with only subtle natural breathing, eye, ear, head or hand settling; do not add a new action or reverse any state."
        )

    forbidden_text = " ".join(f"{index}. {clean(item)}" for index, item in enumerate(forbidden, start=1))
    audio = package.get("audio_contract") if isinstance(package.get("audio_contract"), dict) else {}
    paragraphs = [
        (
            "OUTPUT LAYOUT — ABSOLUTE PRIORITY: Render exactly one ordinary edge-to-edge 9:16 video in one full frame. "
            "@image1 is an off-screen chronological action reference sheet only; it is never a visible object, scene, layout or camera destination. "
            "@image2 is the opening appearance and composition reference. Internally follow @image1 in row-major chronological order, but never reproduce, reveal, zoom out to, pan across or transition between its panels. "
            "No collage, split screen, contact sheet, panels, borders, divider lines, picture-in-picture or tiled layout. If any instruction conflicts, preserve a single full-frame video rather than displaying the reference layout."
        ),
        f"CURRENT SEGMENT: {segment_id}. Generate {request_seconds} seconds for the locked source interval {start:.3f}-{end:.3f}s. Use only this segment; do not introduce another scene or a later result.",
        "LOCKED IDENTITY AND SPACE: " + identity_sentence(job),
        "LOCKED VISUAL STYLE: " + style_text,
        (
            "ACTION CONTRACT: Initial state: " + clean(action.get("initial_state")) + " "
            "Main action path: " + clean(action.get("large_action_path")) + " "
            "Only valid interaction channel: " + clean(action.get("unique_interaction_channel")) + " "
            "Decisive action: " + clean(action.get("decisive_action")) + " "
            "Required visible result: " + clean(action.get("visible_result"))
        ),
        "TIMING AND STATE PROGRESSION:\n" + "\n".join(timing_lines),
        "CAMERA: " + clean(action.get("camera")) + " Keep the required subjects, contact point, hands and changing object state visible in the same normal full-frame composition.",
        "FORBIDDEN ALTERNATIVES: " + forbidden_text,
        (
            "AUDIO POLICY: The locked audio variant is " + clean(audio.get("variant")) + ". "
            "Do not add dialogue, lip-sync acting, captions, subtitles, speech bubbles, logos, watermarks or generated text. "
            "Generated audio is not delivery material and will be muted or replaced according to the locked audio contract."
        ),
    ]
    return "\n\n".join(paragraph.strip() for paragraph in paragraphs if paragraph.strip()) + "\n"


def compile_segment(project: Path, segment_id: str) -> dict[str, Any]:
    skill_root = Path(__file__).resolve().parent.parent
    source_package_rel = f"artifacts/P7/{segment_id}_SEGMENT_PACKAGE.json"
    source_package_path = project_file(project, source_package_rel)
    source_issues = validate_segment_package(project, source_package_path)
    if source_issues:
        raise ValueError("SOURCE_SEGMENT_PACKAGE_INVALID:" + ",".join(source_issues))

    state_path = project / STATE_NAME
    state = load_json(state_path)
    effective_job_path, effective_plan_path, fact_lineage = resolve_effective_inputs(project, state)
    job_relative = effective_job_path.relative_to(project).as_posix()
    scene_plan_relative = effective_plan_path.relative_to(project).as_posix()
    job_path = project_file(project, job_relative)
    scene_plan_path = project_file(project, scene_plan_relative)
    job = load_json(job_path)
    scene_plan = load_json(scene_plan_path)
    package = load_json(source_package_path)
    if package.get("provider_adapter_profile") != ADAPTER or job.get("provider_adapter_profile") != ADAPTER:
        raise ValueError("R623_FLOW_ADAPTER_CHANGED")
    p4_segment = find_segment(scene_plan, segment_id)
    if p4_segment.get("grid_id") != package.get("grid_id"):
        raise ValueError("P4_P7_GRID_BINDING_MISMATCH")
    if p4_segment.get("target_start_s") != package.get("target_start_s") or p4_segment.get("target_end_s") != package.get("target_end_s"):
        raise ValueError("P4_P7_TIMING_CHANGED")

    style_text, style_proof = style_sentence(skill_root, job)
    timing_source, timing_nodes = derive_timing_nodes(scene_plan, p4_segment)
    prompt = compile_prompt(job, p4_segment, package, style_text, timing_nodes)
    prompt_characters = len(prompt)
    issues: list[str] = []
    if prompt_characters > INTERNAL_CHARACTER_BUDGET:
        issues.append(f"INTERNAL_PROMPT_BUDGET_EXCEEDED:{prompt_characters}")
    required_literals = [
        "OUTPUT LAYOUT — ABSOLUTE PRIORITY",
        "@image1 is an off-screen chronological action reference sheet only",
        "@image2 is the opening appearance and composition reference",
        "No collage, split screen, contact sheet, panels, borders, divider lines, picture-in-picture or tiled layout",
        "ACTION CONTRACT",
        "TIMING AND STATE PROGRESSION",
        "FORBIDDEN ALTERNATIVES",
        "AUDIO POLICY",
    ]
    missing = [literal for literal in required_literals if literal not in prompt]
    if missing:
        issues.extend(f"PROMPT_REQUIRED_LITERAL_MISSING:{literal}" for literal in missing)

    prompt_rel = f"artifacts/P7/{segment_id}_VIDEO_PROMPT_R623.txt"
    audit_rel = f"artifacts/P7/{segment_id}_VIDEO_PROMPT_AUDIT_R623.json"
    package_rel = f"artifacts/P7/{segment_id}_SEGMENT_PACKAGE_R623.json"
    prompt_path = project / prompt_rel
    prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
    prompt_sha = sha256_file(prompt_path)

    audit = {
        "schema_version": SCHEMA,
        "status": "PASSED" if not issues else "FAILED",
        "job_id": package.get("job_id"),
        "segment_id": segment_id,
        "grid_id": package.get("grid_id"),
        "compiler": COMPILER,
        "prompt": {
            "relative_path": prompt_rel,
            "sha256": prompt_sha,
            "character_count": prompt_characters,
            "internal_character_budget": INTERNAL_CHARACTER_BUDGET,
            "budget_is_provider_claim": False,
        },
        "unchanged_flow_proof": {
            "provider_adapter_profile": ADAPTER,
            "ordered_image_roles": ["SEGMENT_ACTION_GRID", "DETERMINISTIC_START_PLACEHOLDER"],
            "segment_id": segment_id,
            "grid_id": package.get("grid_id"),
            "target_start_s": package.get("target_start_s"),
            "target_end_s": package.get("target_end_s"),
            "derived_request_duration_s": max(1, math.ceil(float(package["target_end_s"]) - float(package["target_start_s"]) - 1e-9)),
            "additional_images": 0,
            "additional_provider_tasks": 0,
        },
        "lineage": {
            "project_state_relative_path": STATE_NAME,
            "project_skill_version_at_compile": state.get("skill_version"),
            "project_id_at_compile": state.get("project_id"),
            "job_relative_path": job_relative,
            "job_sha256": sha256_file(job_path),
            "scene_plan_relative_path": scene_plan_relative,
            "scene_plan_sha256": sha256_file(scene_plan_path),
            "accepted_deviation_fact_contracts": fact_lineage,
            "source_segment_package_relative_path": source_package_rel,
            "source_segment_package_sha256": sha256_file(source_package_path),
            "grid_asset": copy.deepcopy(package.get("grid_asset")),
            "start_frame_derivative": copy.deepcopy(package.get("start_frame_derivative")),
        },
        "proofs": {
            "layout_contract_first": prompt.startswith("OUTPUT LAYOUT — ABSOLUTE PRIORITY"),
            "input_roles_explicit": "@image1" in prompt and "@image2" in prompt,
            "single_full_frame_hard_clause": "No collage, split screen" in prompt,
            "identity_sha256": canonical_sha256(job.get("project_identity", {})),
            "style": style_proof,
            "p4_action_nodes_sha256": canonical_sha256(p4_segment.get("action_nodes", [])),
            "p4_timing_source": timing_source,
            "p4_timing_nodes_sha256": canonical_sha256(timing_nodes),
            "p4_timing_interval_count": len(timing_nodes),
            "p4_timing_coverage": {
                "start_s": timing_nodes[0]["start_s"],
                "end_s": timing_nodes[-1]["end_s"],
            },
            "p7_action_contract_sha256": canonical_sha256(package.get("action_contract", {})),
            "p7_audio_contract_sha256": canonical_sha256(package.get("audio_contract", {})),
            "required_literals": required_literals,
        },
        "issues": issues,
    }
    write_json_atomic(project / audit_rel, audit)
    if issues:
        raise ValueError("R623_VIDEO_PROMPT_AUDIT_FAILED:" + ",".join(issues))

    upgraded = copy.deepcopy(package)
    upgraded["schema_version"] = "R6.2-P7-SEGMENT-PACKAGE-1.0"
    upgraded["video_prompt"] = {
        "relative_path": prompt_rel,
        "sha256": prompt_sha,
        "audio_variant": package.get("video_prompt", {}).get("audio_variant"),
        "audit_relative_path": audit_rel,
        "audit_sha256": sha256_file(project / audit_rel),
        "compiler": COMPILER,
    }
    upgraded["r623_same_flow_invariants"] = {
        "adapter_unchanged": True,
        "ordered_image_roles_unchanged": True,
        "grid_asset_unchanged": True,
        "start_frame_unchanged": True,
        "target_timing_unchanged": True,
        "audio_contract_unchanged": True,
        "additional_calls": 0,
    }
    write_json_atomic(project / package_rel, upgraded)
    return {
        "status": "PASSED",
        "segment_id": segment_id,
        "prompt_relative_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "prompt_character_count": prompt_characters,
        "audit_relative_path": audit_rel,
        "upgraded_segment_package_relative_path": package_rel,
        "external_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--segment-id", required=True)
    args = parser.parse_args()
    try:
        result = compile_segment(args.project_dir.resolve(), args.segment_id.strip())
        code = 0
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        result = {"status": "FAILED", "issues": [str(exc)], "external_calls": 0}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
