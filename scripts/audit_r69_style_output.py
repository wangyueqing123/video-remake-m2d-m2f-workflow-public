#!/usr/bin/env python3
"""Audit an output image against the closed color style and anchor."""

from __future__ import annotations

import argparse
import colorsys
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from PIL import Image

from accent_color_contract import declared_accent_contract, hue_is_declared, validate_mono_identity
from r62_project import sha256_file, write_json_atomic


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def metrics(path: Path, *, hue_centers_deg: list[float], hue_tolerance_deg: float) -> dict[str, Any]:
    with Image.open(path) as image:
        source_size = [image.width, image.height]
        sample = image.convert("RGB")
        sample.thumbnail((512, 512))
        pixels = list(sample.get_flattened_data())
    chroma = [max(pixel) - min(pixel) for pixel in pixels]
    count = len(chroma)
    chromatic_indexes = [index for index, value in enumerate(chroma) if value > 20]
    declared_count = 0
    for index in chromatic_indexes:
        red, green, blue = pixels[index]
        hue = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)[0] * 360.0
        if hue_is_declared(hue, hue_centers_deg, hue_tolerance_deg):
            declared_count += 1
    undeclared_count = len(chromatic_indexes) - declared_count
    return {
        "source_size_px": source_size,
        "sample_size_px": [sample.width, sample.height],
        "sample_pixel_count": count,
        "pct_chroma_gt_20": round(sum(value > 20 for value in chroma) * 100 / count, 6),
        "pct_chroma_gt_40": round(sum(value > 40 for value in chroma) * 100 / count, 6),
        "pct_declared_accent_chroma_gt_20": round(declared_count * 100 / count, 6),
        "pct_undeclared_chroma_gt_20": round(undeclared_count * 100 / count, 6),
        "mean_chroma": round(sum(chroma) / count, 6),
    }


def project_job(project: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    state_path = project / "R62_PROJECT.json"
    if not state_path.is_file():
        raise ValueError("PROJECT_STATE_MISSING_FOR_ACCENT_AUDIT")
    state = load_json(state_path)
    mode_lock = state.get("mode_lock") if isinstance(state.get("mode_lock"), dict) else {}
    relative = str(mode_lock.get("job_relative_path", "")).replace("\\", "/")
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise ValueError("JOB_PATH_NOT_PROJECT_RELATIVE_FOR_ACCENT_AUDIT")
    path = (project / relative).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError("JOB_MISSING_FOR_ACCENT_AUDIT")
    return state, path, load_json(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--output-image", required=True)
    parser.add_argument("--anchor-image", required=True)
    parser.add_argument("--styles", required=True, type=Path)
    parser.add_argument("--style-id", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        project = args.project_dir.resolve()
        output_relative = args.output_image.replace("\\", "/")
        anchor_relative = args.anchor_image.replace("\\", "/")
        receipt_relative = args.receipt.replace("\\", "/")
        if output_relative.startswith("/") or anchor_relative.startswith("/") or ".." in Path(output_relative).parts or ".." in Path(anchor_relative).parts:
            raise ValueError("PROJECT_RELATIVE_IMAGE_PATH_REQUIRED")
        if (
            not receipt_relative
            or receipt_relative.startswith("/")
            or (len(receipt_relative) >= 2 and receipt_relative[1] == ":")
            or any(part in {"", ".", ".."} for part in Path(receipt_relative).parts)
        ):
            raise ValueError("PROJECT_RELATIVE_RECEIPT_PATH_REQUIRED")
        output_path = (project / output_relative).resolve()
        anchor_path = (project / anchor_relative).resolve()
        receipt_path = (project / receipt_relative).resolve()
        output_path.relative_to(project)
        anchor_path.relative_to(project)
        receipt_path.relative_to(project)
        if not output_path.is_file() or not anchor_path.is_file():
            raise ValueError("STYLE_AUDIT_IMAGE_MISSING")
        registry = load_json(args.styles)
        style = registry.get("styles", {}).get(args.style_id)
        if not isinstance(style, dict):
            raise ValueError("STYLE_NOT_FOUND")
        contract = style.get("color_contract") if isinstance(style.get("color_contract"), dict) else {}
        qc = contract.get("machine_qc") if isinstance(contract.get("machine_qc"), dict) else {}
        if contract.get("mode") != "CLOSED_MONO_WITH_NAMED_ACCENTS" or not qc:
            raise ValueError("STYLE_DOES_NOT_DECLARE_MACHINE_COLOR_QC")
        state, job_path, job = project_job(project)
        if job.get("style_profile") != args.style_id:
            raise ValueError("JOB_STYLE_DIFFERS_FROM_REQUESTED_STYLE_AUDIT")
        identity = job.get("project_identity") if isinstance(job.get("project_identity"), dict) else {}
        named_accent_mode = state.get("skill_version") in {"R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}
        if named_accent_mode:
            identity_issues = validate_mono_identity(identity)
            if identity_issues:
                raise ValueError("INVALID_NAMED_ACCENT_CONTRACT:" + ",".join(identity_issues))
            accent_contract = declared_accent_contract(identity)
            hue_tolerance = float(qc["declared_accent_hue_tolerance_deg"])
        else:
            accent_contract = {"entries": [], "canonical_colors": [], "hue_centers_deg": []}
            hue_tolerance = 0.0
        output_metrics = metrics(
            output_path,
            hue_centers_deg=accent_contract["hue_centers_deg"],
            hue_tolerance_deg=hue_tolerance,
        )
        anchor_metrics = metrics(
            anchor_path,
            hue_centers_deg=accent_contract["hue_centers_deg"],
            hue_tolerance_deg=hue_tolerance,
        )
        if named_accent_mode:
            limits = {
                "pct_undeclared_chroma_gt_20_max": float(qc["absolute_undeclared_pct_chroma_gt_20_max"]),
                "pct_declared_accent_chroma_gt_20_max": float(qc["absolute_declared_accent_pct_chroma_gt_20_max"]),
                "mean_chroma_max": min(
                    float(qc["absolute_mean_chroma_max"]),
                    anchor_metrics["mean_chroma"] * float(qc["anchor_mean_chroma_multiplier_max"]),
                ),
            }
            checks = {
                "undeclared_chroma_within_limit": output_metrics["pct_undeclared_chroma_gt_20"] <= limits["pct_undeclared_chroma_gt_20_max"],
                "declared_accent_area_within_limit": output_metrics["pct_declared_accent_chroma_gt_20"] <= limits["pct_declared_accent_chroma_gt_20_max"],
                "mean_chroma_within_limit": output_metrics["mean_chroma"] <= limits["mean_chroma_max"],
            }
        else:
            limits = {
                "pct_chroma_gt_20_max": min(
                    float(qc["absolute_pct_chroma_gt_20_max"]),
                    anchor_metrics["pct_chroma_gt_20"] * float(qc["anchor_pct_chroma_gt_20_multiplier_max"]),
                ),
                "mean_chroma_max": min(
                    float(qc["absolute_mean_chroma_max"]),
                    anchor_metrics["mean_chroma"] * float(qc["anchor_mean_chroma_multiplier_max"]),
                ),
            }
            checks = {
                "pct_chroma_gt_20_within_limit": output_metrics["pct_chroma_gt_20"] <= limits["pct_chroma_gt_20_max"],
                "mean_chroma_within_limit": output_metrics["mean_chroma"] <= limits["mean_chroma_max"],
            }
        receipt = {
            "schema_version": "R6.26-STYLE-OUTPUT-AUDIT-1.0" if named_accent_mode else "R6.9-STYLE-OUTPUT-AUDIT-1.0",
            "style_id": args.style_id,
            "color_contract_mode": contract.get("mode"),
            "job": {"relative_path": job_path.relative_to(project).as_posix(), "sha256": sha256_file(job_path)},
            "output": {"relative_path": output_relative, "sha256": sha256_file(output_path), "metrics": output_metrics},
            "anchor": {"relative_path": anchor_relative, "sha256": sha256_file(anchor_path), "metrics": anchor_metrics},
            "limits": limits,
            "checks": checks,
            "decision": "PASSED" if all(checks.values()) else "FAILED",
        }
        if named_accent_mode:
            receipt["accent_contract"] = {
                **accent_contract,
                "hue_tolerance_deg": hue_tolerance,
                "scope_policy": "DECLARED_COLOR_PLUS_ENTITY_LABEL",
            }
        write_json_atomic(receipt_path, receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0 if receipt["decision"] == "PASSED" else 1
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_P0", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
