#!/usr/bin/env python3
"""Deterministically crop cell 1 from an approved square R6.2 action grid."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from PIL import Image

from r62_project import normalize_project_relative


LAYOUT_SIDE = {"2x2": 2, "3x3": 3, "4x4": 4, "5x5": 5}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--grid", required=True)
    parser.add_argument("--layout", required=True, choices=sorted(LAYOUT_SIDE))
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()

    project = args.project_dir.resolve()
    try:
        grid_rel = normalize_project_relative(args.grid)
        output_rel = normalize_project_relative(args.output)
        receipt_rel = normalize_project_relative(args.receipt)
        grid_path = (project / grid_rel).resolve()
        output_path = (project / output_rel).resolve()
        receipt_path = (project / receipt_rel).resolve()
        if not grid_path.is_file():
            raise ValueError("GRID_FILE_MISSING")
        side = LAYOUT_SIDE[args.layout]
        with Image.open(grid_path) as image:
            width, height = image.size
            if width < side or height < side:
                raise ValueError("GRID_TOO_SMALL_FOR_LAYOUT")
            cell_width = width // side
            cell_height = height // side
            if cell_width <= 0 or cell_height <= 0:
                raise ValueError("GRID_CELL_DIMENSIONS_INVALID")
            crop_box = (0, 0, cell_width, cell_height)
            cell = image.crop(crop_box)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            cell.save(output_path, format="PNG")
        receipt = {
            "schema_version": "R6.2-START-CELL-CROP-RECEIPT-1.0",
            "method": "DETERMINISTIC_CROP_NO_GENERATION",
            "source_cell": 1,
            "layout": args.layout,
            "source_grid_relative_path": grid_rel,
            "source_grid_sha256": sha256(grid_path),
            "source_dimensions_px": [width, height],
            "division_policy": "FLOOR_FROM_TOP_LEFT_ORIGIN",
            "division_remainder_px": [width % side, height % side],
            "remainder_placement": "RIGHT_AND_BOTTOM_OUTSIDE_CELL1",
            "crop_box_px": [0, 0, cell_width, cell_height],
            "output_relative_path": output_rel,
            "output_sha256": sha256(output_path),
            "output_dimensions_px": [cell_width, cell_height],
            "resized": False,
            "status": "PASSED",
        }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "PASSED", "receipt": receipt_rel, "output": output_rel}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "issues": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
