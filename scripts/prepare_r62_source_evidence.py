#!/usr/bin/env python3
"""Probe project-relative source video, export per-second frames, and flag cut candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import av
import numpy as np
from PIL import Image, ImageDraw

sys.dont_write_bytecode = True
from r62_project import resolve_project_file


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def relative_to_project(project: Path, path: Path) -> str:
    return path.resolve().relative_to(project.resolve()).as_posix()


def make_contact_sheet(project: Path, rows: list[dict[str, Any]], output: Path, columns: int = 5) -> None:
    tile_width, tile_height, label_height = 180, 320, 24
    sheet_rows = max(1, math.ceil(len(rows) / columns))
    canvas = Image.new("RGB", (columns * tile_width, sheet_rows * (tile_height + label_height)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, row in enumerate(rows):
        source = project / row["relative_path"]
        with Image.open(source) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((tile_width, tile_height))
            x = (index % columns) * tile_width + (tile_width - thumb.width) // 2
            y = (index // columns) * (tile_height + label_height) + (tile_height - thumb.height) // 2
            canvas.paste(thumb, (x, y))
        label_y = (index // columns) * (tile_height + label_height) + tile_height
        draw.text(((index % columns) * tile_width + 4, label_y + 4), f"{row['sample_id']}  {row['decoded_time_s']:.3f}s", fill="black")
    canvas.save(output, format="JPEG", quality=92)


def make_cut_sheet(project: Path, candidates: list[dict[str, Any]], output: Path) -> None:
    tile_width, tile_height, label_height = 160, 300, 24
    pairs_per_row = 4
    sheet_rows = max(1, math.ceil(len(candidates) / pairs_per_row))
    canvas = Image.new("RGB", (pairs_per_row * tile_width * 2, sheet_rows * (tile_height + label_height)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, candidate in enumerate(candidates):
        base_x = (index % pairs_per_row) * tile_width * 2
        base_y = (index // pairs_per_row) * (tile_height + label_height)
        for offset, key in enumerate(("pre_relative_path", "post_relative_path")):
            with Image.open(project / candidate[key]) as image:
                thumb = image.convert("RGB")
                thumb.thumbnail((tile_width, tile_height))
                x = base_x + offset * tile_width + (tile_width - thumb.width) // 2
                y = base_y + (tile_height - thumb.height) // 2
                canvas.paste(thumb, (x, y))
        draw.text((base_x + 4, base_y + tile_height + 4), f"{candidate['candidate_id']}  {candidate['time_s']:.3f}s  pre/post", fill="black")
    canvas.save(output, format="JPEG", quality=92)


def frame_time(frame: av.VideoFrame, fallback_index: int, average_rate: float) -> float:
    if frame.time is not None:
        return float(frame.time)
    if frame.pts is not None and frame.time_base is not None:
        return float(frame.pts * frame.time_base)
    return fallback_index / average_rate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--video", default="inputs/source.mp4")
    parser.add_argument("--output-dir", default="artifacts/P2/source_evidence")
    parser.add_argument("--sample-step", type=float, default=1.0)
    parser.add_argument("--cut-threshold", type=float, default=22.0)
    args = parser.parse_args()
    if args.sample_step <= 0:
        raise SystemExit("SAMPLE_STEP_MUST_BE_POSITIVE")
    project = args.project_dir.resolve()
    video_rel, video_path = resolve_project_file(project, args.video)
    output_rel = args.output_dir.strip().replace("\\", "/")
    output = (project / output_rel).resolve()
    try:
        output.relative_to(project)
    except ValueError as exc:
        raise SystemExit("OUTPUT_ESCAPES_PROJECT") from exc
    frames_dir = output / "frames"
    cuts_dir = output / "cut_candidates"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cuts_dir.mkdir(parents=True, exist_ok=True)

    container = av.open(str(video_path))
    stream = next((item for item in container.streams if item.type == "video"), None)
    if stream is None:
        raise SystemExit("VIDEO_STREAM_MISSING")
    average_rate = float(stream.average_rate) if stream.average_rate else 30.0
    metadata_duration = float(stream.duration * stream.time_base) if stream.duration is not None and stream.time_base is not None else None
    if metadata_duration is None and container.duration is not None:
        metadata_duration = float(container.duration / av.time_base)

    sample_rows: list[dict[str, Any]] = []
    raw_candidates: list[dict[str, Any]] = []
    next_sample = 0.0
    previous_gray: np.ndarray | None = None
    previous_image = None
    previous_time = 0.0
    last_time = 0.0
    frame_count = 0

    for frame in container.decode(stream):
        time_s = frame_time(frame, frame_count, average_rate)
        last_time = max(last_time, time_s)
        image = frame.to_image().convert("RGB")
        while time_s + 1e-6 >= next_sample:
            frame_name = f"T{next_sample:08.3f}.jpg"
            frame_path = frames_dir / frame_name
            image.save(frame_path, format="JPEG", quality=92)
            sample_rows.append({
                "sample_id": f"T{len(sample_rows):04d}",
                "requested_time_s": round(next_sample, 6),
                "decoded_time_s": round(time_s, 6),
                "relative_path": relative_to_project(project, frame_path),
                "sha256": sha256_file(frame_path),
            })
            next_sample += args.sample_step

        gray = frame.to_ndarray(format="gray")
        row_step = max(1, gray.shape[0] // 96)
        column_step = max(1, gray.shape[1] // 96)
        small = gray[::row_step, ::column_step].astype(np.int16)
        if previous_gray is not None and previous_gray.shape == small.shape:
            difference = float(np.mean(np.abs(small - previous_gray)))
            if difference >= args.cut_threshold and previous_image is not None:
                candidate_number = len(raw_candidates) + 1
                pre_path = cuts_dir / f"C{candidate_number:03d}_PRE.jpg"
                post_path = cuts_dir / f"C{candidate_number:03d}_POST.jpg"
                previous_image.save(pre_path, format="JPEG", quality=92)
                image.save(post_path, format="JPEG", quality=92)
                raw_candidates.append({
                    "candidate_id": f"C{candidate_number:03d}",
                    "time_s": round(time_s, 6),
                    "previous_time_s": round(previous_time, 6),
                    "mean_absolute_gray_delta": round(difference, 4),
                    "pre_relative_path": relative_to_project(project, pre_path),
                    "post_relative_path": relative_to_project(project, post_path),
                    "status": "CANDIDATE_REQUIRES_VISUAL_REVIEW",
                })
        previous_gray = small
        previous_image = image.copy()
        previous_time = time_s
        frame_count += 1

    container.close()
    decoded_duration = metadata_duration if metadata_duration is not None else last_time
    if sample_rows and decoded_duration > sample_rows[-1]["requested_time_s"] + 0.05:
        final_name = f"T{decoded_duration:08.3f}_END.jpg"
        final_path = frames_dir / final_name
        if previous_image is not None:
            previous_image.save(final_path, format="JPEG", quality=92)
            sample_rows.append({
                "sample_id": f"T{len(sample_rows):04d}",
                "requested_time_s": round(decoded_duration, 6),
                "decoded_time_s": round(last_time, 6),
                "relative_path": relative_to_project(project, final_path),
                "sha256": sha256_file(final_path),
            })

    grouped: list[dict[str, Any]] = []
    for candidate in raw_candidates:
        if grouped and candidate["time_s"] - grouped[-1]["time_s"] <= 0.25:
            if candidate["mean_absolute_gray_delta"] > grouped[-1]["mean_absolute_gray_delta"]:
                grouped[-1] = candidate
        else:
            grouped.append(candidate)

    timeline_sheet = output / "TIMELINE_SHEET.jpg"
    cut_sheet = output / "CUT_CANDIDATE_SHEET.jpg"
    make_contact_sheet(project, sample_rows, timeline_sheet)
    make_cut_sheet(project, grouped, cut_sheet)

    probe = {
        "schema_version": "R6.2-SOURCE-PROBE-1.0",
        "source_relative_path": video_rel,
        "source_sha256": sha256_file(video_path),
        "bytes": video_path.stat().st_size,
        "duration_s": round(decoded_duration, 6),
        "width": int(stream.codec_context.width),
        "height": int(stream.codec_context.height),
        "average_frame_rate": average_rate,
        "decoded_frame_count": frame_count,
        "sample_step_s": args.sample_step,
        "cut_threshold": args.cut_threshold,
        "cut_candidates_are_real_cuts": False,
        "timeline_sheet_relative_path": relative_to_project(project, timeline_sheet),
        "cut_candidate_sheet_relative_path": relative_to_project(project, cut_sheet),
    }
    write_json(output / "SOURCE_PROBE.json", probe)
    write_json(output / "FRAME_INDEX.json", {"schema_version": "R6.2-FRAME-INDEX-1.0", "source_sha256": probe["source_sha256"], "frames": sample_rows})
    write_json(output / "CUT_CANDIDATES.json", {
        "schema_version": "R6.2-CUT-CANDIDATES-1.0",
        "source_sha256": probe["source_sha256"],
        "raw_candidate_count": len(raw_candidates),
        "grouped_candidate_count": len(grouped),
        "candidates": grouped,
        "decision": "REQUIRES_CODEX_VISUAL_REVIEW_BEFORE_REAL_CUT_LOCK",
    })
    result = {
        "status": "PASSED",
        "output_dir": output_rel,
        "duration_s": probe["duration_s"],
        "frames": len(sample_rows),
        "grouped_cut_candidates": len(grouped),
        "timeline_sheet": relative_to_project(project, timeline_sheet),
        "cut_candidate_sheet": relative_to_project(project, cut_sheet),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
