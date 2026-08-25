#!/usr/bin/env python3
"""Validate the private D/F-only distribution without making provider calls."""

from __future__ import annotations

import hashlib
import ast
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = ["M2_D_SHARE_FIRST", "M2_F_SOURCE_AUDIO_RESTYLE"]
ALLOWED_STYLES = {
    "DOG_HIGH_SHARE_MONO_COMIC",
    "DOG_STYLE_C_GHIBLI_PET_NARRATIVE",
    "DOG_STYLE_D_INDOOR_CARE_KEYFRAME",
    "DOG_STYLE_E_REACTION_RESONANCE",
    "CUSTOM_NAMED_STYLE",
}
PUBLIC_CONTRACTS = [
    ROOT / "SKILL.md",
    ROOT / "README.md",
    ROOT / "RELEASE.json",
    ROOT / "references/architecture-and-routing.md",
    ROOT / "references/phase-and-gates.md",
    ROOT / "assets/creative-profile-registry.json",
]
FORBIDDEN_PUBLIC = {
    "M1_STRICT_1TO1",
    "M2_A_STRUCTURE_RESKIN",
    "M2_B_SEMANTIC_REMAKE",
    "M2_C_CUSTOM",
    "M2_E_SAVE_FIRST",
}
SECRET_PATTERNS = [
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
]
LOCAL_MODULE_PREFIXES = (
    "accept_", "accent_", "audit_", "build_", "check_", "compile_", "extract_",
    "finalize_", "inspect_", "ocr_", "prepare_", "r62_", "r625_", "r628_",
    "r634_", "r635_", "r637_", "r639_", "r641_", "reconcile_", "release_",
    "submit_", "transcribe_", "validate_", "verify_",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    issues: list[str] = []
    required = [
        ROOT / "SKILL.md",
        ROOT / "README.md",
        ROOT / "VERSION",
        ROOT / "RELEASE.json",
        ROOT / "agents/openai.yaml",
        ROOT / "assets/m2-d-share-first-production-profile.json",
        ROOT / "assets/m2-f-source-audio-restyle-profile.json",
        ROOT / "assets/style-registry.json",
        ROOT / "references/m2-d-share-first.md",
        ROOT / "references/m2-f-source-audio-restyle.md",
        ROOT / "references/style-selection.md",
    ]
    for path in required:
        if not path.is_file():
            issues.append(f"MISSING:{path.relative_to(ROOT).as_posix()}")

    release = json.loads((ROOT / "RELEASE.json").read_text(encoding="utf-8"))
    if release.get("supported_routes") != SUPPORTED:
        issues.append("SUPPORTED_ROUTE_SET_MISMATCH")
    styles = (release.get("style_policy") or {}).get("allowed")
    if set(styles or []) != ALLOWED_STYLES:
        issues.append("STYLE_SET_MISMATCH")
    if (release.get("style_policy") or {}).get("default") != "DOG_HIGH_SHARE_MONO_COMIC":
        issues.append("DEFAULT_STYLE_MISMATCH")

    for path in PUBLIC_CONTRACTS:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_PUBLIC:
            if token in text:
                issues.append(f"FORBIDDEN_PUBLIC_ROUTE:{path.relative_to(ROOT).as_posix()}:{token}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.name in {"accounts.local.json", ".env"}:
            issues.append(f"LOCAL_SECRET_FILE_PRESENT:{path.relative_to(ROOT).as_posix()}")
            continue
        if path.suffix.lower() not in {".md", ".json", ".yaml", ".yml", ".py", ".txt", ".example"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix.lower() == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                issues.append(f"JSON_INVALID:{path.relative_to(ROOT).as_posix()}:{exc.lineno}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append(f"POSSIBLE_SECRET:{path.relative_to(ROOT).as_posix()}")

    local_modules = {path.stem for path in (ROOT / "scripts").glob("*.py")}
    for path in (ROOT / "scripts").glob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), path.name)
        except SyntaxError as exc:
            issues.append(f"PYTHON_SYNTAX_INVALID:{path.name}:{exc.lineno}")
            continue
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        for module in imported:
            if module.startswith(LOCAL_MODULE_PREFIXES) and module not in local_modules:
                issues.append(f"LOCAL_IMPORT_MISSING:{path.name}:{module}")

    forbidden_names = [path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*") if path.is_file() and "m2-e" in path.name.lower()]
    issues.extend(f"FORBIDDEN_MODE_FILE:{name}" for name in forbidden_names)

    manifest_path = ROOT / "MANIFEST.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest.get("files", []):
            path = ROOT / item["path"]
            if not path.is_file() or sha256(path) != item["sha256"]:
                issues.append(f"MANIFEST_MISMATCH:{item['path']}")

    result = {
        "status": "PASSED" if not issues else "BLOCKED_P0",
        "release": release.get("release_id"),
        "supported_routes": SUPPORTED,
        "default_style": "DOG_HIGH_SHARE_MONO_COMIC",
        "issues": sorted(set(issues)),
        "provider_calls": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
