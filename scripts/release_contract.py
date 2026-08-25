#!/usr/bin/env python3
"""Single machine authority for active-release compatibility branches."""

from __future__ import annotations


CURRENT_SKILL_VERSION = "R6.41"

SUPPORTED_PROJECT_VERSIONS = frozenset({
    "R6.11", "R6.12", "R6.13", "R6.14", "R6.15",
    "R6.16", "R6.17", "R6.18", "R6.19", "R6.20", "R6.21", "R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", CURRENT_SKILL_VERSION,
})

CANONICAL_P5_VERSIONS = SUPPORTED_PROJECT_VERSIONS
R612_CANONICAL_P5_VERSIONS = frozenset(
    version for version in SUPPORTED_PROJECT_VERSIONS if version != "R6.11"
)
R619_SPATIAL_QC_VERSIONS = frozenset({"R6.19", "R6.20", "R6.21", "R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", CURRENT_SKILL_VERSION})
REFERENCE_COMPATIBILITY_VERSIONS = frozenset({"R6.18", "R6.19", "R6.20", "R6.21", "R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", CURRENT_SKILL_VERSION})
SEMANTIC_PROMPT_AUDIT_VERSIONS = frozenset({"R6.21", "R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", CURRENT_SKILL_VERSION})
KEYFRAME_SNAPSHOT_CONTRACT_VERSIONS = frozenset({"R6.39", "R6.40", CURRENT_SKILL_VERSION})
COMPATIBLE_P6_QC_SCHEMAS = frozenset({
    "R6.2-P6-QC-1.0",
    "R6.18-P6-QC-1.0",
    "R6.19-P6-QC-1.0",
})


def canonical_p5_suffix(version: str) -> str:
    if version not in CANONICAL_P5_VERSIONS:
        raise ValueError(f"UNSUPPORTED_CANONICAL_P5_VERSION:{version}")
    if version == "R6.41":
        return "R641"
    if version in {"R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40"}:
        # R6.40 changes only the post-correction controller. P4/P5 semantics and
        # file contracts remain byte-compatible with R6.39, so no P5 rebuild or
        # alias copy is allowed during a zero-call degradation migration.
        return {"R6.34": "R634", "R6.35": "R635", "R6.36": "R636", "R6.37": "R637", "R6.38": "R638", "R6.39": "R639", "R6.40": "R639"}[version]
    return "R612" if version in R612_CANONICAL_P5_VERSIONS else "R611"


def canonical_p5_schema_version(version: str) -> str:
    """Return the schema authority for byte-compatible canonical P5 artifacts."""
    if version not in CANONICAL_P5_VERSIONS:
        raise ValueError(f"UNSUPPORTED_CANONICAL_P5_VERSION:{version}")
    # The pending-grid review schema last changed in R6.37. R6.38-R6.40
    # isolate artifact filenames but intentionally do not relabel this schema.
    return "R6.37" if version in {"R6.38", "R6.39", "R6.40"} else version


def required_core_qc_schema(version: str) -> str:
    if version not in SUPPORTED_PROJECT_VERSIONS:
        raise ValueError(f"UNSUPPORTED_PROJECT_VERSION:{version}")
    return "R6.19-P6-QC-1.0" if version in R619_SPATIAL_QC_VERSIONS else "R6.18-P6-QC-1.0"
