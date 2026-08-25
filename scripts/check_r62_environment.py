#!/usr/bin/env python3
"""Report portable R6.2 core readiness and optional local media capabilities."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def executable_available(name: str) -> bool:
    if shutil.which(name) is not None:
        return True
    if name.lower() == "tesseract" and os.name == "nt":
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base and (Path(base) / "Tesseract-OCR" / "tesseract.exe").is_file():
                return True
    return False


def main() -> int:
    requirements = json.loads((ROOT / "assets" / "r62-environment.json").read_text(encoding="utf-8"))
    python_ready = sys.version_info >= (3, 10)
    pillow_ready = module_available("PIL")
    core_ready = python_ready and pillow_ready
    tesseract_ready = executable_available("tesseract")
    optional = {
        "video_audio_decode": {"available": module_available("av"), "modules": {"av": module_available("av")}},
        "transcription": {
            "available": module_available("faster_whisper") and module_available("numpy"),
            "modules": {"faster_whisper": module_available("faster_whisper"), "numpy": module_available("numpy")},
        },
        "cover_ocr": {
            "available": module_available("PIL") and module_available("pytesseract") and tesseract_ready,
            "modules": {"PIL": module_available("PIL"), "pytesseract": module_available("pytesseract")},
            "executable_detected": {"tesseract": tesseract_ready},
        },
        "spreadsheet_read": {"available": module_available("openpyxl"), "modules": {"openpyxl": module_available("openpyxl")}},
        "deterministic_png_crop_and_pixel_validation": {
            "available": pillow_ready,
            "required": True,
            "modules": {"PIL": pillow_ready},
            "required_for_adapters": ["GROK_KIE_GRID_FIRST_PLACEHOLDER_SECOND", "GROK_WEB_GRID_PLUS_FIRST_FRAME"],
        },
        "image_generation": {
            "available": None,
            "status": "SEALED_CAPABILITY_ARTIFACT_REQUIRED",
            "exact_geometry_rule": "PROMPT_ONLY_INTERFACE_BLOCKS_EXACT_PIXELS",
        },
        "provider_api": {"available": None, "status": "CHECK_NETWORK_AND_SEPARATE_SECRET_STORE"},
    }
    result = {
        "schema_version": "R6.2-ENVIRONMENT-CHECK-1.0",
        "status": "PASSED" if core_ready else "BLOCKED_P0",
        "core_ready": core_ready,
        "python_ready": python_ready,
        "pillow_ready": pillow_ready,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "core_dependencies": requirements["core_runtime"]["python_dependencies"],
        "optional_capabilities": optional,
        "credentials_bundled": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if core_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
