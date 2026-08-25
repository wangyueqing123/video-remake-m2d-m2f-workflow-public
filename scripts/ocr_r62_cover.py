#!/usr/bin/env python3
"""OCR a project-relative cover image without storing machine-specific executable paths."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

sys.dont_write_bytecode = True
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from r62_project import resolve_project_file


def find_tesseract() -> str | None:
    discovered = shutil.which("tesseract")
    if discovered:
        return discovered
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base:
                candidate = Path(base) / "Tesseract-OCR" / "tesseract.exe"
                if candidate.is_file():
                    return str(candidate)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--image", default="inputs/cover.jpg")
    parser.add_argument("--output", default="artifacts/P2/COVER_OCR.json")
    parser.add_argument("--languages", default="chi_sim+eng")
    parser.add_argument("--tessdata-dir", type=Path)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    image_rel, image_path = resolve_project_file(project, args.image)
    output_rel = args.output.strip().replace("\\", "/")
    output_path = (project / output_rel).resolve()
    try:
        output_path.relative_to(project)
    except ValueError as exc:
        raise SystemExit("OUTPUT_ESCAPES_PROJECT") from exc
    executable = find_tesseract()
    if executable is None:
        raise SystemExit("TESSERACT_EXECUTABLE_NOT_FOUND")
    pytesseract.pytesseract.tesseract_cmd = executable
    config = "--oem 1 --psm 6"
    previous_tessdata = os.environ.get("TESSDATA_PREFIX")
    if args.tessdata_dir:
        os.environ["TESSDATA_PREFIX"] = str(args.tessdata_dir.resolve())
    try:
        with Image.open(image_path) as image:
            processed = ImageOps.grayscale(image).resize((image.width * 2, image.height * 2))
            text = pytesseract.image_to_string(processed, lang=args.languages, config=config).strip()
    finally:
        if args.tessdata_dir:
            if previous_tessdata is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = previous_tessdata
    payload = {
        "schema_version": "R6.2-COVER-OCR-1.0",
        "status": "OCR_DRAFT_REQUIRES_VISUAL_REVIEW",
        "source_relative_path": image_rel,
        "languages": args.languages,
        "psm": 6,
        "text": text,
        "machine_specific_executable_path_persisted": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASSED", "output": output_rel, "text": text}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
