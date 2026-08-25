#!/usr/bin/env python3
"""Resolve the one authoritative PASSED P6 QC for downstream P7/P8 use."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from r62_project import load_json, normalize_project_relative, sha256_file


def _project_file(project: Path, relative: object, label: str) -> tuple[str, Path]:
    try:
        normalized = normalize_project_relative(str(relative or ""))
    except ValueError as exc:
        raise ValueError(f"{label}_PATH_INVALID") from exc
    path = (project / normalized).resolve()
    try:
        path.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}_PATH_ESCAPES_PROJECT") from exc
    if not path.is_file():
        raise ValueError(f"{label}_FILE_MISSING")
    return normalized, path


def _validate_qc(
    qc: dict[str, Any], *, grid_id: str, segment_id: str,
    grid_relative_path: str, grid_sha256: str,
) -> None:
    output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
    if qc.get("decision") != "PASSED" or qc.get("grid_id") != grid_id or qc.get("segment_id") != segment_id:
        raise ValueError("AUTHORITATIVE_P6_QC_IDENTITY_OR_DECISION_INVALID")
    if output.get("relative_path") != grid_relative_path or output.get("sha256") != grid_sha256:
        raise ValueError("AUTHORITATIVE_P6_QC_OUTPUT_BINDING_INVALID")


def resolve_authoritative_passed_qc(
    project: Path, state: dict[str, Any], *, grid_id: str, segment_id: str,
    grid_relative_path: str, grid_sha256: str,
) -> tuple[str, Path, dict[str, Any], str]:
    """Return relative path, path, QC object, and authority source.

    Live PASSED QC records are authoritative for normal grids. The sole exception is
    the R6.27 zero-call G01 re-audit, whose immutable proof intentionally lives in
    historical_revision.anchor_origin_historical_qc rather than rewriting qc_records.
    """
    live_rows = [
        row for row in state.get("qc_records", [])
        if isinstance(row, dict)
        and row.get("grid_id") == grid_id
        and row.get("segment_id") == segment_id
        and row.get("decision") == "PASSED"
    ]
    if len(live_rows) > 1:
        raise ValueError("AUTHORITATIVE_P6_QC_DUPLICATE_LIVE_PASSES")
    if live_rows:
        row = live_rows[0]
        relative, path = _project_file(project, row.get("qc_relative_path"), "LIVE_P6_QC")
        if row.get("qc_sha256") != sha256_file(path):
            raise ValueError("LIVE_P6_QC_LEDGER_HASH_MISMATCH")
        qc = load_json(path)
        _validate_qc(
            qc, grid_id=grid_id, segment_id=segment_id,
            grid_relative_path=grid_relative_path, grid_sha256=grid_sha256,
        )
        return relative, path, qc, "LIVE_QC_RECORD"

    historical_revision = state.get("historical_revision") if isinstance(state.get("historical_revision"), dict) else {}
    historical = historical_revision.get("anchor_origin_historical_qc") if isinstance(historical_revision.get("anchor_origin_historical_qc"), dict) else {}
    if grid_id != "G01" or historical.get("grid_id") != grid_id or historical.get("grid_order") != 1:
        raise ValueError("AUTHORITATIVE_P6_PASSED_QC_MISSING")
    if historical.get("decision") != "PASSED" or historical.get("origin") != "R627_ZERO_CALL_REAUDIT_OF_PRESERVED_SUBMISSION":
        raise ValueError("HISTORICAL_P6_QC_AUTHORITY_INVALID")
    if (
        historical.get("generated_output_relative_path") != grid_relative_path
        or historical.get("generated_output_sha256") != grid_sha256
    ):
        raise ValueError("HISTORICAL_P6_QC_GRID_BINDING_INVALID")
    relative, path = _project_file(project, historical.get("relative_path"), "HISTORICAL_P6_QC")
    if historical.get("sha256") != sha256_file(path):
        raise ValueError("HISTORICAL_P6_QC_HASH_MISMATCH")
    qc = load_json(path)
    _validate_qc(
        qc, grid_id=grid_id, segment_id=segment_id,
        grid_relative_path=grid_relative_path, grid_sha256=grid_sha256,
    )
    reaudit = qc.get("r627_zero_call_reaudit") if isinstance(qc.get("r627_zero_call_reaudit"), dict) else {}
    if reaudit.get("provider_calls") != 0 or reaudit.get("physical_qc_reused") is not True or reaudit.get("style_qc_recomputed") is not True:
        raise ValueError("HISTORICAL_P6_QC_ZERO_CALL_PROOF_INVALID")
    return relative, path, qc, "R627_HISTORICAL_ANCHOR_QC"
