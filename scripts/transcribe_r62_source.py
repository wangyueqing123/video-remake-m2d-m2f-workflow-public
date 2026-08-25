#!/usr/bin/env python3
"""Transcribe one project-relative source file into portable timestamped evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel

sys.dont_write_bytecode = True
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from r62_project import resolve_project_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--input", default="inputs/source.mp3")
    parser.add_argument("--output", default="artifacts/P2/TRANSCRIPT.json")
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    input_rel, input_path = resolve_project_file(project, args.input)
    output_rel = args.output.strip().replace("\\", "/")
    output_path = (project / output_rel).resolve()
    try:
        output_path.relative_to(project)
    except ValueError as exc:
        raise SystemExit("OUTPUT_ESCAPES_PROJECT") from exc
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(input_path), language=args.language, beam_size=1, vad_filter=True, word_timestamps=True)
    rows = []
    full_text = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        if not text:
            continue
        words = [
            {"start_s": round(float(word.start), 6), "end_s": round(float(word.end), 6), "text": word.word}
            for word in (segment.words or []) if word.start is not None and word.end is not None
        ]
        rows.append({
            "segment_id": f"ASR{index:03d}",
            "start_s": round(float(segment.start), 6),
            "end_s": round(float(segment.end), 6),
            "text": text,
            "words": words,
            "avg_logprob": float(segment.avg_logprob),
            "no_speech_prob": float(segment.no_speech_prob),
        })
        full_text.append(text)
    payload = {
        "schema_version": "R6.2-TRANSCRIPT-1.0",
        "status": "ASR_DRAFT_REQUIRES_AUDIO_ALIGNMENT_REVIEW",
        "source_relative_path": input_rel,
        "model": args.model,
        "language": info.language,
        "language_probability": float(info.language_probability),
        "duration_s": float(info.duration),
        "full_text": "".join(full_text),
        "segments": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASSED", "output": output_rel, "segments": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
