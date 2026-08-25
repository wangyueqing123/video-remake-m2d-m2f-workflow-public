#!/usr/bin/env python3
"""Deterministically crop the bottom-right end-state cell from a passed grid."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from PIL import Image


SIDES = {"2x2": 2, "3x3": 3, "4x4": 4, "5x5": 5}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(project: Path, value: str) -> tuple[str, Path]:
    normalized = str(PurePosixPath(value.replace("\\", "/")))
    if not normalized or normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        raise ValueError("PATH_MUST_BE_PROJECT_RELATIVE")
    path = (project / normalized).resolve()
    path.relative_to(project)
    return normalized, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--grid-id", required=True)
    parser.add_argument("--grid-order", required=True, type=int)
    parser.add_argument("--grid", required=True)
    parser.add_argument("--grid-qc", required=True)
    parser.add_argument("--layout", required=True, choices=sorted(SIDES))
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    grid_rel, grid_path = resolve(project, args.grid)
    qc_rel, qc_path = resolve(project, args.grid_qc)
    output_rel, output_path = resolve(project, args.output)
    receipt_rel, receipt_path = resolve(project, args.receipt)
    if output_path.exists() or receipt_path.exists():
        raise SystemExit("OUTPUT_ALREADY_EXISTS_NO_OVERWRITE")
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    if qc.get("decision") != "PASSED" or qc.get("grid_id") != args.grid_id:
        raise SystemExit("SOURCE_GRID_QC_NOT_PASSED_OR_MISMATCHED")
    side = SIDES[args.layout]
    with Image.open(grid_path) as image:
        width, height = image.size
        cell_width, cell_height = width // side, height // side
        left, top = (side - 1) * cell_width, (side - 1) * cell_height
        box = (left, top, left + cell_width, top + cell_height)
        cell = image.crop(box)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cell.save(output_path, format="PNG")
    receipt = {
        "schema_version": "R6.6-PREVIOUS-END-STATE-CROP-1.0",
        "job_id": args.job_id,
        "source_grid_id": args.grid_id,
        "source_grid_order": args.grid_order,
        "source_grid_relative_path": grid_rel,
        "source_grid_sha256": sha256(grid_path),
        "source_qc_relative_path": qc_rel,
        "source_qc_sha256": sha256(qc_path),
        "source_cell": side * side,
        "layout": args.layout,
        "method": "DETERMINISTIC_BOTTOM_RIGHT_CROP_NO_GENERATION",
        "source_size_px": [width, height],
        "cell_size_px": [cell_width, cell_height],
        "division_remainder_px": [width % side, height % side],
        "crop_box_px": list(box),
        "output_relative_path": output_rel,
        "output_sha256": sha256(output_path),
        "resized": False,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASSED", "receipt": receipt_rel, **receipt}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
