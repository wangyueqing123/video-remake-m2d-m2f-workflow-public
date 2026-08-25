#!/usr/bin/env python3
"""Validate an R6.2 ImageGen capability artifact against a geometry contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from validate_r62_job import load_json


ASPECT = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
PLACEHOLDERS = ("REPLACE_WITH", "TODO", "<", ">")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _placeholder(value: Any) -> bool:
    text = _text(value)
    return not text or any(marker in text for marker in PLACEHOLDERS)


def validate_capability(
    capability: dict[str, Any],
    *,
    requested_aspect_ratio: str,
    enforcement: str,
    expected_tool: str,
    registry: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    if capability.get("schema_version") != "R6.2-IMAGEGEN-CAPABILITY-1.0":
        issues.append("CAPABILITY_SCHEMA_INVALID")
    if _placeholder(capability.get("capability_id")):
        issues.append("CAPABILITY_ID_MISSING_OR_PLACEHOLDER")
    if capability.get("status") != "VERIFIED":
        issues.append("CAPABILITY_NOT_VERIFIED")
    if capability.get("tool") != expected_tool:
        issues.append("CAPABILITY_TOOL_MISMATCH")
    if capability.get("interface_mode") not in {"PROMPT_ONLY", "EXPLICIT_SIZE_OR_ASPECT_PARAMETER"}:
        issues.append("CAPABILITY_INTERFACE_MODE_INVALID")
    for key in ("size_parameter_exposed", "aspect_ratio_parameter_exposed", "exact_geometry_guarantee"):
        if not isinstance(capability.get(key), bool):
            issues.append(f"CAPABILITY_{key.upper()}_INVALID")
    exact_ratios = capability.get("verified_exact_canvas_aspect_ratios")
    if not isinstance(exact_ratios, list) or any(not ASPECT.fullmatch(_text(value)) for value in exact_ratios):
        issues.append("CAPABILITY_VERIFIED_EXACT_RATIOS_INVALID")
        exact_ratios = []
    observed_ratios = capability.get("observed_default_canvas_aspect_ratios")
    if not isinstance(observed_ratios, list) or any(not ASPECT.fullmatch(_text(value)) for value in observed_ratios):
        issues.append("CAPABILITY_OBSERVED_RATIOS_INVALID")
    if _placeholder(capability.get("evidence_source")) or _placeholder(capability.get("evidence_detail")):
        issues.append("CAPABILITY_EVIDENCE_MISSING_OR_PLACEHOLDER")
    if _placeholder(capability.get("verified_at")):
        issues.append("CAPABILITY_VERIFIED_AT_MISSING")

    if registry is not None:
        profiles = registry.get("profiles") if isinstance(registry, dict) else None
        profile = profiles.get(capability.get("profile_id")) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict):
            issues.append("CAPABILITY_PROFILE_NOT_REGISTERED")
        elif capability.get("profile_id") != "PROJECT_VERIFIED_EXACT_GEOMETRY":
            for key in (
                "tool",
                "interface_mode",
                "size_parameter_exposed",
                "aspect_ratio_parameter_exposed",
                "exact_geometry_guarantee",
            ):
                if capability.get(key) != profile.get(key):
                    issues.append(f"CAPABILITY_PROFILE_{key.upper()}_MISMATCH")
            if sorted(exact_ratios) != sorted(profile.get("verified_exact_canvas_aspect_ratios", [])):
                issues.append("CAPABILITY_PROFILE_EXACT_RATIOS_MISMATCH")

    if not ASPECT.fullmatch(requested_aspect_ratio):
        issues.append("REQUESTED_ASPECT_RATIO_INVALID")
    if enforcement not in {"EXACT_PIXELS", "FLEXIBLE_REFERENCE"}:
        issues.append("GEOMETRY_ENFORCEMENT_INVALID")
    elif enforcement == "EXACT_PIXELS":
        has_control = capability.get("size_parameter_exposed") is True or capability.get("aspect_ratio_parameter_exposed") is True
        if not has_control:
            issues.append("EXACT_GEOMETRY_REQUIRES_EXPOSED_SIZE_OR_ASPECT_PARAMETER")
        if capability.get("exact_geometry_guarantee") is not True:
            issues.append("EXACT_GEOMETRY_NOT_GUARANTEED")
        if requested_aspect_ratio not in exact_ratios:
            issues.append("REQUESTED_EXACT_ASPECT_RATIO_NOT_VERIFIED")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capability", required=True, type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--requested-aspect-ratio", required=True)
    parser.add_argument("--enforcement", required=True, choices=["EXACT_PIXELS", "FLEXIBLE_REFERENCE"])
    parser.add_argument("--tool", required=True)
    args = parser.parse_args()
    try:
        capability = load_json(args.capability)
        registry = load_json(args.registry) if args.registry else None
        issues = validate_capability(
            capability,
            requested_aspect_ratio=args.requested_aspect_ratio,
            enforcement=args.enforcement,
            expected_tool=args.tool,
            registry=registry,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues = [f"READ_ERROR:{exc}"]
    result = {"status": "PASSED" if not issues else "BLOCKED_P0", "issues": issues}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
