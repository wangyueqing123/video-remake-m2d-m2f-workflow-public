#!/usr/bin/env python3
"""Build route-locked M2-F timing, alignment, captions, and JianYing EditPlan."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import av

from validate_r623_video_qc import validate_gate_receipt


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def duration(path: Path) -> float:
    container = av.open(str(path))
    try:
        if container.duration is not None:
            return float(container.duration) / float(av.time_base)
        stream = container.streams.video[0] if container.streams.video else container.streams.audio[0]
        if stream.duration is None or stream.time_base is None:
            raise RuntimeError(f"duration unavailable: {path}")
        return float(stream.duration * stream.time_base)
    finally:
        container.close()


def rel_from(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()


def resolve_transcript_path(project: Path) -> Path:
    """Resolve the current P2 transcript name while retaining legacy projects."""
    current = project / "artifacts/P2/TRANSCRIPT.json"
    legacy = project / "artifacts/P2/ASR_TRANSCRIPT.json"
    if current.is_file():
        return current
    if legacy.is_file():
        return legacy
    raise RuntimeError("P2_TRANSCRIPT_ARTIFACT_MISSING")


def choose_video(project: Path, segment_id: str, require_r623_gate: bool = False) -> tuple[Path, dict[str, Any]]:
    candidates = [
        project / f"artifacts/P8/{segment_id}_VIDEO_QC_REPAIRED.json",
        project / f"artifacts/P8/{segment_id}_VIDEO_QC.json",
    ]
    for qc_path in candidates:
        if not qc_path.is_file():
            continue
        qc = load_json(qc_path)
        if qc.get("decision") != "PASSED" or qc.get("hard_visual_failures") != []:
            continue
        output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
        video = project / str(output.get("relative_path", ""))
        if not video.is_file() or sha256(video) != str(output.get("sha256", "")):
            continue
        if require_r623_gate:
            gate_path = project / f"artifacts/P8/{segment_id}_VIDEO_QC_GATE_R623.json"
            if not gate_path.is_file():
                continue
            if validate_gate_receipt(project, gate_path, segment_id=segment_id, qc_path=qc_path):
                continue
        return video, qc
    raise RuntimeError(f"no PASSED P8 video QC output for {segment_id}")


def speech_window(asr: dict[str, Any], start: float, end: float) -> tuple[float, float]:
    words: list[tuple[float, float]] = []
    for segment in asr.get("segments", []):
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words", []):
            if not isinstance(word, dict):
                continue
            word_start = float(word.get("start_s", -1))
            word_end = float(word.get("end_s", -1))
            if word_end > start + 1e-6 and word_start < end - 1e-6:
                words.append((max(start, word_start), min(end, word_end)))
    if not words:
        return start, end
    return min(item[0] for item in words), max(item[1] for item in words)


def normalize_caption_token(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum() or character in "'’")


def asr_words_in_window(asr: dict[str, Any], start: float, end: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in asr.get("segments", []):
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words", []):
            if not isinstance(word, dict):
                continue
            word_start = float(word.get("start_s", -1))
            word_end = float(word.get("end_s", -1))
            if word_end > start + 1e-6 and word_start < end - 1e-6:
                text = str(word.get("text", "")).strip()
                normalized = normalize_caption_token(text)
                if normalized:
                    rows.append({
                        "text": text,
                        "normalized": normalized,
                        "start_s": max(start, word_start),
                        "end_s": min(end, word_end),
                    })
    return rows


def caption_chunks(
    asr: dict[str, Any],
    segment_id: str,
    exact_copy: str,
    start: float,
    end: float,
    max_words: int = 7,
    max_characters: int = 38,
) -> list[dict[str, Any]]:
    """Split locked copy into readable captions and bind chunks to ASR word times."""
    copy_text = exact_copy.strip()
    compact_cjk = not re.search(r"\s", copy_text) and bool(re.search(r"[\u3400-\u9fff]", copy_text))
    copy_tokens = list(copy_text) if compact_cjk else re.findall(r"\S+", copy_text)
    separator = "" if compact_cjk else " "
    if not copy_tokens:
        return []
    asr_words = asr_words_in_window(asr, start, end)
    copy_norm = [normalize_caption_token(token) for token in copy_tokens]
    asr_norm = [str(word["normalized"]) for word in asr_words]
    mapped: dict[int, int] = {}
    matcher = SequenceMatcher(a=copy_norm, b=asr_norm, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapped[block.a + offset] = block.b + offset

    groups: list[tuple[int, int]] = []
    group_start = 0
    character_count = 0
    for index, token in enumerate(copy_tokens):
        proposed = character_count + (1 if character_count else 0) + len(token)
        group_size = index - group_start + 1
        terminal = bool(re.search(r"[.!?][\"')\]]*$", token))
        soft_break = bool(re.search(r"[,;:][\"')\]]*$", token)) and group_size >= 4
        if group_size > max_words or proposed > max_characters:
            groups.append((group_start, index))
            group_start = index
            character_count = len(token)
        else:
            character_count = proposed
        if terminal or soft_break:
            groups.append((group_start, index + 1))
            group_start = index + 1
            character_count = 0
    if group_start < len(copy_tokens):
        groups.append((group_start, len(copy_tokens)))

    speech_start, speech_end = speech_window(asr, start, end)
    total_tokens = len(copy_tokens)
    rows: list[dict[str, Any]] = []
    previous_end = speech_start
    for group_index, (first, last) in enumerate(groups, start=1):
        matched = [mapped[index] for index in range(first, last) if index in mapped]
        if matched:
            chunk_start = float(asr_words[min(matched)]["start_s"])
            chunk_end = float(asr_words[max(matched)]["end_s"])
        else:
            chunk_start = speech_start + (speech_end - speech_start) * first / total_tokens
            chunk_end = speech_start + (speech_end - speech_start) * last / total_tokens
        chunk_start = max(start, previous_end, chunk_start)
        next_boundary = end if group_index == len(groups) else speech_end
        chunk_end = min(end, next_boundary, max(chunk_start + 0.12, chunk_end))
        if chunk_end <= chunk_start:
            chunk_end = min(end, chunk_start + 0.12)
        rows.append({
            "segment_id": segment_id,
            "caption_id": f"{segment_id}-C{group_index:02d}",
            "text": separator.join(copy_tokens[first:last]),
            "start_s": round(chunk_start, 6),
            "end_s": round(chunk_end, 6),
            "duration_s": round(chunk_end - chunk_start, 6),
            "timing_method": "LOCKED_COPY_ALIGNED_TO_ASR_WORD_TIMES",
        })
        previous_end = chunk_end
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--title", default="R619_M2F_SOURCE_AUDIO_RESTYLE")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    p9 = project / "artifacts/P9"
    state = load_json(project / "R62_PROJECT.json")
    require_r623_gate = state.get("skill_version") in {"R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}
    target = ((state.get("mode_lock") or {}).get("target") or {})
    if target.get("audio_variant") != "SOURCE_AUDIO_REUSE" or target.get("timing_authority") != "SOURCE_AUDIO_MASTER":
        raise SystemExit("BLOCKED_P0:PROJECT_IS_NOT_M2F_SOURCE_AUDIO_REUSE")
    plan = load_json(project / "artifacts/P3/SOURCE_AUDIO_PLAN.json")
    transcript_path = resolve_transcript_path(project)
    asr = load_json(transcript_path)
    source_audio_rel = str(plan["source_audio"]["relative_path"])
    source_copy_rel = str(plan["source_copy"]["relative_path"])
    source_audio = project / source_audio_rel
    source_copy = project / source_copy_rel
    measured_audio_duration = duration(source_audio)
    editor_audio_duration = math.floor(measured_audio_duration * 1000.0 + 1e-9) / 1000.0
    locked_audio_duration = float(plan["source_audio"]["duration_s"])
    if abs(measured_audio_duration - locked_audio_duration) > 0.002:
        raise SystemExit("BLOCKED_P0:SOURCE_AUDIO_DURATION_DIFFERS_FROM_P3_LOCK")

    timing_rows: list[dict[str, Any]] = []
    alignment_rows: list[dict[str, Any]] = []
    video_rows: list[dict[str, Any]] = []
    caption_rows: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    source_segments = plan["segments"]
    for index, row in enumerate(source_segments, start=1):
        sid = str(row["segment_id"])
        start = float(row["start_s"])
        end = float(row["end_s"])
        if index == len(source_segments):
            end = editor_audio_duration
        if end <= start:
            raise SystemExit(f"BLOCKED_P0:EDITOR_TIME_GRID_COLLAPSES_SEGMENT:{sid}")
        span = end - start
        video, qc = choose_video(project, sid, require_r623_gate=require_r623_gate)
        video_duration = duration(video)
        video_speed = video_duration / span
        timing_rows.append({"segment_id": sid, "segment_order": index, "exact_copy": row["exact_copy"], "start_s": start, "end_s": end, "duration_s": round(span, 6), "audio_speed": 1.0})
        alignment_rows.append({"segment_id": sid, "segment_order": index, "requested_generation_s": max(1, math.ceil(span - 1e-9)), "source_video_duration_s": round(video_duration, 6), "edited_video_duration_s": round(span, 6), "audio_duration_s": round(span, 6), "source_audio_speed": 1.0, "video_speed": round(video_speed, 9), "alignment_policy": "VIDEO_FOLLOWS_SOURCE_AUDIO"})
        video_rows.append({"kind": "video", "source": rel_from(video, p9), "start": f"{start:.6f}s", "duration": f"{span:.6f}s", "speed": round(video_speed, 9), "volume": 0})
        caption_rows.extend(caption_chunks(asr, sid, str(row["exact_copy"]), start, end))
        selection = {"segment_id": sid, "video_relative_path": video.relative_to(project).as_posix(), "video_sha256": sha256(video), "qc_score": qc["visual_score"], "qc_task_id": qc["task_id"]}
        if require_r623_gate:
            gate_path = project / f"artifacts/P8/{sid}_VIDEO_QC_GATE_R623.json"
            selection["r623_video_qc_gate_relative_path"] = gate_path.relative_to(project).as_posix()
            selection["r623_video_qc_gate_sha256"] = sha256(gate_path)
        selections.append(selection)

    timing = {
        "schema_version": "R6.19-SOURCE-AUDIO-TIMING-1.0",
        "status": "MEASURED_SOURCE_AUDIO",
        "job_id": state["project_id"],
        "audio_variant": "SOURCE_AUDIO_REUSE",
        "timing_authority": "SOURCE_AUDIO_MASTER",
        "source_audio": {"relative_path": source_audio_rel, "sha256": sha256(source_audio), "duration_s": round(editor_audio_duration, 6), "measured_media_duration_s": round(measured_audio_duration, 6), "editor_timeline_quantization_s": 0.001, "playback_speed": 1.0, "measurement_method": "MEDIA_PROBE_PYAV_EDITOR_SAFE_FLOOR_MS"},
        "source_copy": {"relative_path": source_copy_rel, "sha256": sha256(source_copy), "policy": "VERBATIM_NO_REWRITE"},
        "segments": timing_rows,
        "total_duration_s": round(editor_audio_duration, 6),
    }
    timing_path = p9 / "SOURCE_AUDIO_TIMING.json"
    write_json(timing_path, timing)
    alignment = {
        "schema_version": "R6.14-EDITING-ALIGNMENT-1.0",
        "status": "REVIEWED",
        "job_id": state["project_id"],
        "timing_manifest": {"relative_path": "artifacts/P9/SOURCE_AUDIO_TIMING.json", "sha256": sha256(timing_path)},
        "timeline_fps": 24.0,
        "segments": alignment_rows,
        "total_duration_s": round(editor_audio_duration, 6),
        "per_segment_voice_speed_changes": False,
        "decision": "PASSED",
    }
    alignment_path = p9 / "EDITING_ALIGNMENT.json"
    write_json(alignment_path, alignment)
    caption_track = {
        "schema_version": "R6.19-P9-SOURCE-AUDIO-CAPTION-TRACK-1.0",
        "job_id": state["project_id"],
        "mode": "M2_F_SOURCE_AUDIO_RESTYLE",
        "status": "PASSED",
        "caption_authority": "LOCKED_SOURCE_COPY_PLUS_SOURCE_AUDIO_ASR_TIMES",
        "segmentation_policy": "READABLE_PHRASE_CHUNKS_MAX_7_WORDS_MAX_38_CHARACTERS",
        "audio_speed": 1.0,
        "segments": caption_rows,
        "style": {"font": "系统", "font_size": 12, "fill": "#FFFFFF", "outline": "#000000", "outline_width": 40, "horizontal_alignment": "center", "x": 0, "y": -300, "max_lines": 2},
        "full_copy_coverage": "PASSED",
        "blocking_failures": [],
    }
    caption_path = p9 / "FINAL_CAPTION_TRACK.json"
    write_json(caption_path, caption_track)
    edit_plan = {
        "version": "1.0",
        "title": args.title,
        "canvas": {"width": 1080, "height": 1920, "fps": 24},
        "tracks": [
            {"id": "main_video", "kind": "video", "segments": video_rows},
            {"id": "source_audio", "kind": "audio", "segments": [{"kind": "audio", "source": rel_from(source_audio, p9), "start": "0s", "duration": f"{editor_audio_duration:.6f}s", "speed": 1.0, "volume": 1}]},
            {"id": "captions", "kind": "text", "segments": [{"kind": "text", "text": item["text"], "start": f"{item['start_s']:.6f}s", "duration": f"{item['duration_s']:.6f}s"} for item in caption_rows]},
        ],
        "market": {"caption_style": {"font": "系统", "font_size": 12, "color": "#FFFFFF", "outline": True, "outline_color": "#000000", "outline_width": 40, "horizontal_alignment": "center", "auto_wrapping": True, "max_line_width": 0.82, "position_x": 0, "position_y": -300, "position_unit": "canvas_pixels"}},
    }
    edit_plan_path = p9 / "DIRECT_JIANYING_EDIT_PLAN.json"
    write_json(edit_plan_path, edit_plan)
    receipt = {
        "schema_version": "R6.19-P9-SOURCE-AUDIO-BUILD-1.0",
        "status": "PASSED",
        "provider_calls": 0,
        "source_audio_playback_speed": 1.0,
        "measured_media_duration_s": round(measured_audio_duration, 6),
        "editor_safe_timeline_duration_s": round(editor_audio_duration, 6),
        "terminal_time_grid_adjustment_s": round(measured_audio_duration - editor_audio_duration, 6),
        "model_video_volume": 0,
        "transcript_authority": {
            "relative_path": transcript_path.relative_to(project).as_posix(),
            "sha256": sha256(transcript_path),
            "resolution_policy": "CURRENT_TRANSCRIPT_NAME_FIRST_LEGACY_FALLBACK",
        },
        "caption_policy": {
            "timing": "LOCKED_COPY_ALIGNED_TO_ASR_WORD_TIMES",
            "max_words": 7,
            "max_characters": 38,
            "single_macro_scene_caption_forbidden": True,
        },
        "selected_outputs": selections,
        "outputs": [
            {"relative_path": path.relative_to(project).as_posix(), "sha256": sha256(path)}
            for path in (timing_path, alignment_path, caption_path, edit_plan_path)
        ],
    }
    receipt_path = p9 / "P9_BUILD_RECEIPT.json"
    write_json(receipt_path, receipt)
    print(json.dumps({"status": "PASSED", "receipt": receipt_path.relative_to(project).as_posix(), "selected_outputs": selections}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
