#!/usr/bin/env python3
"""Build the deterministic distribution manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    files = []
    for path in sorted(ROOT.rglob("*"), key=lambda item: item.relative_to(ROOT).as_posix()):
        if not path.is_file() or ".git" in path.parts or path.name == "MANIFEST.json" or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        files.append({
            "path": relative,
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    payload = {
        "schema_version": "M2D-M2F-MANIFEST-1.0",
        "release_id": "R6.41.2-DF1-PUBLIC",
        "file_count": len(files),
        "files": files,
    }
    (ROOT / "MANIFEST.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": "BUILT", "file_count": len(files)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
