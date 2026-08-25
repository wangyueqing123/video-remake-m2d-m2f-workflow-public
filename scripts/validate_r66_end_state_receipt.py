#!/usr/bin/env python3
"""Validate an R6.6 previous-segment end-state crop and its passed source QC."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageChops


HEX64 = re.compile(r"^[0-9a-f]{64}$")
SIDES = {"2x2": 2, "3x3": 3, "4x4": 4, "5x5": 5}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_file(project: Path, value: Any, label: str, issues: list[str]) -> Path | None:
    text = str(value or "").replace("\\", "/")
    if not text or text.startswith("/") or (len(text) >= 2 and text[1] == ":") or ".." in PurePosixPath(text).parts:
        issues.append(f"{label}_PATH_INVALID")
        return None
    path = (project / text).resolve()
    try:
        path.relative_to(project)
    except ValueError:
        issues.append(f"{label}_PATH_ESCAPES_PROJECT")
        return None
    if not path.is_file():
        issues.append(f"{label}_FILE_MISSING")
        return None
    return path


def verify_hash(path: Path | None, expected: Any, label: str, issues: list[str]) -> None:
    value = str(expected or "").lower()
    if not HEX64.fullmatch(value):
        issues.append(f"{label}_HASH_INVALID")
    elif path is not None and sha256(path) != value:
        issues.append(f"{label}_HASH_MISMATCH")


def validate(project: Path, receipt_path: Path) -> list[str]:
    issues: list[str] = []
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("schema_version") != "R6.6-PREVIOUS-END-STATE-CROP-1.0":
        issues.append("END_STATE_RECEIPT_SCHEMA_INVALID")
    if not isinstance(receipt.get("job_id"), str) or not receipt["job_id"].strip():
        issues.append("END_STATE_JOB_ID_MISSING")
    grid_id = receipt.get("source_grid_id")
    grid_order = receipt.get("source_grid_order")
    if not isinstance(grid_id, str) or not grid_id.strip() or isinstance(grid_order, bool) or not isinstance(grid_order, int) or grid_order < 1:
        issues.append("END_STATE_SOURCE_GRID_BINDING_INVALID")
    layout = receipt.get("layout")
    side = SIDES.get(layout)
    if side is None:
        issues.append("END_STATE_LAYOUT_INVALID")
    elif receipt.get("source_cell") != side * side:
        issues.append("END_STATE_SOURCE_CELL_NOT_BOTTOM_RIGHT")
    if receipt.get("method") != "DETERMINISTIC_BOTTOM_RIGHT_CROP_NO_GENERATION" or receipt.get("resized") is not False:
        issues.append("END_STATE_CROP_METHOD_INVALID")

    grid_path = project_file(project, receipt.get("source_grid_relative_path"), "END_STATE_SOURCE_GRID", issues)
    qc_path = project_file(project, receipt.get("source_qc_relative_path"), "END_STATE_SOURCE_QC", issues)
    output_path = project_file(project, receipt.get("output_relative_path"), "END_STATE_OUTPUT", issues)
    verify_hash(grid_path, receipt.get("source_grid_sha256"), "END_STATE_SOURCE_GRID", issues)
    verify_hash(qc_path, receipt.get("source_qc_sha256"), "END_STATE_SOURCE_QC", issues)
    verify_hash(output_path, receipt.get("output_sha256"), "END_STATE_OUTPUT", issues)

    if qc_path is not None:
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        generated = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
        if qc.get("decision") != "PASSED" or qc.get("grid_id") != grid_id:
            issues.append("END_STATE_SOURCE_QC_NOT_PASSED_OR_GRID_MISMATCH")
        if generated.get("relative_path") != receipt.get("source_grid_relative_path") or str(generated.get("sha256", "")).lower() != str(receipt.get("source_grid_sha256", "")).lower():
            issues.append("END_STATE_SOURCE_QC_OUTPUT_BINDING_MISMATCH")

    if grid_path is not None and output_path is not None and side is not None:
        try:
            with Image.open(grid_path) as grid, Image.open(output_path) as output:
                width, height = grid.size
                cell_width, cell_height = width // side, height // side
                box = ((side - 1) * cell_width, (side - 1) * cell_height, side * cell_width, side * cell_height)
                if receipt.get("source_size_px") != [width, height]:
                    issues.append("END_STATE_SOURCE_SIZE_MISMATCH")
                if receipt.get("cell_size_px") != [cell_width, cell_height]:
                    issues.append("END_STATE_CELL_SIZE_MISMATCH")
                if receipt.get("division_remainder_px") != [width % side, height % side]:
                    issues.append("END_STATE_DIVISION_REMAINDER_MISMATCH")
                if receipt.get("crop_box_px") != list(box):
                    issues.append("END_STATE_CROP_BOX_MISMATCH")
                expected = grid.crop(box).convert("RGB")
                actual = output.convert("RGB")
                if expected.size != actual.size or ImageChops.difference(expected, actual).getbbox() is not None:
                    issues.append("END_STATE_OUTPUT_PIXELS_NOT_DETERMINISTIC_CROP")
        except OSError:
            issues.append("END_STATE_IMAGE_DECODE_FAILED")
    return sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    receipt_path = project_file(project, args.receipt, "END_STATE_RECEIPT", [])
    issues = ["END_STATE_RECEIPT_FILE_MISSING"] if receipt_path is None else validate(project, receipt_path)
    print(json.dumps({"status": "PASSED" if not issues else "FAILED", "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
