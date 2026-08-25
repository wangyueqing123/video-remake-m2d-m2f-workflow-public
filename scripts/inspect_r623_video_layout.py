#!/usr/bin/env python3
"""Sample a generated video and detect persistent collage/split-screen divider geometry."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import av
import numpy as np
from PIL import Image

from r62_project import normalize_project_relative, sha256_file, write_json_atomic

sys.dont_write_bytecode = True


SCHEMA = "R6.23-P8-VIDEO-LAYOUT-FORENSICS-1.0"


def project_file(project: Path, relative: str) -> tuple[str, Path]:
    normalized = normalize_project_relative(relative)
    path = (project / normalized).resolve()
    path.relative_to(project)
    if not path.is_file():
        raise ValueError(f"VIDEO_FILE_MISSING:{normalized}")
    return normalized, path


def frame_metrics(rgb: np.ndarray) -> dict[str, Any]:
    gray = np.asarray(Image.fromarray(rgb).convert("L").resize((360, 640), Image.Resampling.BILINEAR), dtype=np.float32)
    vertical = np.abs(gray[:, 1:] - gray[:, :-1])
    horizontal = np.abs(gray[1:, :] - gray[:-1, :])

    def scan(matrix: np.ndarray, axis: int, low: float, high: float, along_slice: slice | None = None) -> dict[str, float]:
        working = matrix if along_slice is None else (matrix[along_slice, :] if axis == 0 else matrix[:, along_slice])
        scores = working.mean(axis=axis)
        spans = (working > 20.0).mean(axis=axis)
        start = max(0, int(len(scores) * low))
        stop = min(len(scores), max(start + 1, int(len(scores) * high)))
        local = scores[start:stop]
        local_index = int(np.argmax(local))
        index = start + local_index
        baseline = float(np.percentile(scores, 65)) + 1e-6
        return {
            "position": round(index / max(1, len(scores) - 1), 6),
            "edge_mean": round(float(scores[index]), 6),
            "edge_ratio": round(float(scores[index]) / baseline, 6),
            "strong_pixel_span": round(float(spans[index]), 6),
        }

    horizontal_center = scan(horizontal, axis=1, low=0.28, high=0.72)
    vertical_full = scan(vertical, axis=0, low=0.28, high=0.72)
    vertical_top = scan(vertical, axis=0, low=0.28, high=0.72, along_slice=slice(0, 320))
    vertical_bottom = scan(vertical, axis=0, low=0.28, high=0.72, along_slice=slice(320, 640))
    strongest_vertical = max((vertical_full, vertical_top, vertical_bottom), key=lambda row: row["edge_ratio"] * row["strong_pixel_span"])

    h = horizontal_center
    v = strongest_vertical
    horizontal_divider = h["edge_ratio"] >= 3.2 and h["strong_pixel_span"] >= 0.45 and h["edge_mean"] >= 18.0
    vertical_divider = v["edge_ratio"] >= 3.0 and v["strong_pixel_span"] >= 0.32 and v["edge_mean"] >= 16.0
    overwhelming_horizontal = h["edge_ratio"] >= 6.5 and h["strong_pixel_span"] >= 0.72 and h["edge_mean"] >= 28.0
    candidate = (horizontal_divider and vertical_divider) or overwhelming_horizontal
    return {
        "horizontal_center": horizontal_center,
        "vertical_full": vertical_full,
        "vertical_top_half": vertical_top,
        "vertical_bottom_half": vertical_bottom,
        "candidate_collage_geometry": bool(candidate),
    }


def sample_video(path: Path, requested_samples: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    container = av.open(str(path))
    stream = next((item for item in container.streams if item.type == "video"), None)
    if stream is None:
        raise ValueError("VIDEO_STREAM_MISSING")
    duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / 1_000_000.0
    if duration <= 0:
        raise ValueError("VIDEO_DURATION_INVALID")
    targets = np.linspace(max(0.08, duration * 0.04), max(0.09, duration * 0.96), max(3, requested_samples))
    rows: list[dict[str, Any]] = []
    target_index = 0
    for frame in container.decode(stream):
        timestamp = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
        while target_index < len(targets) and timestamp >= targets[target_index]:
            rgb = frame.to_ndarray(format="rgb24")
            rows.append({"timestamp_s": round(timestamp, 6), **frame_metrics(rgb)})
            target_index += 1
        if target_index >= len(targets):
            break
    container.close()
    if len(rows) < 3:
        raise ValueError("VIDEO_SAMPLE_COUNT_INSUFFICIENT")
    metadata = {
        "duration_s": round(duration, 6),
        "width": int(stream.codec_context.width),
        "height": int(stream.codec_context.height),
        "sample_targets": [round(float(value), 6) for value in targets],
    }
    return metadata, rows


def inspect(project: Path, segment_id: str, video_relative: str, receipt_relative: str, samples: int) -> dict[str, Any]:
    normalized_video, video_path = project_file(project, video_relative)
    normalized_receipt = normalize_project_relative(receipt_relative)
    receipt_path = (project / normalized_receipt).resolve()
    receipt_path.relative_to(project)
    metadata, rows = sample_video(video_path, samples)
    candidates = [row for row in rows if row["candidate_collage_geometry"]]
    required_persistent = max(3, math.ceil(len(rows) * 0.5))
    persistent = len(candidates) >= required_persistent
    if persistent:
        decision = "HARD_FAIL_PERSISTENT_COLLAGE_OR_SPLIT_SCREEN"
        status = "REJECTED"
    elif len(candidates) >= 2:
        decision = "WAIT_REVIEW_LAYOUT_SUSPECT"
        status = "REVIEW_REQUIRED"
    else:
        decision = "PASSED_MACHINE_LAYOUT_SCREEN"
        status = "PASSED"
    payload = {
        "schema_version": SCHEMA,
        "status": status,
        "segment_id": segment_id,
        "video": {"relative_path": normalized_video, "sha256": sha256_file(video_path), **metadata},
        "sample_count": len(rows),
        "candidate_frame_count": len(candidates),
        "persistent_candidate_threshold": required_persistent,
        "persistent_split_screen_detected": persistent,
        "machine_evidence": {"samples": rows},
        "manual_layout_review_required": True,
        "decision": decision,
        "automatic_retry_allowed": False,
        "external_calls": 0,
    }
    write_json_atomic(receipt_path, payload)
    return {**payload, "receipt_relative_path": normalized_receipt, "receipt_sha256": sha256_file(receipt_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--segment-id", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--samples", type=int, default=9)
    args = parser.parse_args()
    try:
        result = inspect(args.project_dir.resolve(), args.segment_id.strip(), args.video, args.receipt, args.samples)
        code = 1 if result["status"] == "REJECTED" else 0
    except (OSError, ValueError, av.AVError) as exc:
        result = {"status": "FAILED", "issues": [str(exc)], "external_calls": 0}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
