#!/usr/bin/env python3
"""Create a JianYing draft directly from a jyfoundation EditPlan.

The foundation repository is a read-only dependency. This adapter adds the
subtitle mappings that its current pyjianying backend intentionally omits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dependencies(foundation_root: Path) -> tuple[Any, Any, Any]:
    root = foundation_root.resolve()
    for required in (root / "src", root / ".deps", root / "vendor" / "pyJianYingDraft"):
        if not required.exists():
            raise RuntimeError(f"missing foundation dependency: {required}")
        sys.path.insert(0, str(required))

    # pyJianYingDraft imports its optional v6-only export controller at package
    # import time. Draft creation itself does not need uiautomation.
    try:
        import uiautomation  # type: ignore  # noqa: F401
    except ImportError:
        stub = types.ModuleType("uiautomation")
        stub.Control = type("Control", (), {})
        stub.WindowControl = type("WindowControl", (), {})
        sys.modules["uiautomation"] = stub

    import pyJianYingDraft as draft  # type: ignore
    from jyfoundation.io import load_plan
    from jyfoundation.validation import validate_plan

    return draft, load_plan, validate_plan


def rgb(value: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return default
    return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))  # type: ignore[return-value]


def text_segment(draft: Any, plan: Any, segment: Any, timerange: Any) -> Any:
    data = dict(plan.market.get("caption_style", {}))
    data.update(segment.style)
    align_value = data.get("horizontal_alignment", data.get("align", "center"))
    align = {"left": 0, "center": 1, "right": 2}.get(str(align_value).lower(), int(align_value) if isinstance(align_value, int) else 1)
    style = draft.TextStyle(
        size=float(data.get("font_size", data.get("size", 12))),
        bold=bool(data.get("bold", False)),
        italic=bool(data.get("italic", False)),
        underline=bool(data.get("underline", False)),
        color=rgb(data.get("color", "#FFFFFF"), (1.0, 1.0, 1.0)),
        alpha=float(data.get("alpha", 1.0)),
        align=align,
        letter_spacing=int(data.get("letter_spacing", 0)),
        line_spacing=int(data.get("line_spacing", 0)),
        auto_wrapping=bool(data.get("auto_wrapping", True)),
        max_line_width=float(data.get("max_line_width", 0.82)),
    )
    border = None
    if data.get("outline", data.get("border", True)):
        border = draft.TextBorder(
            color=rgb(data.get("outline_color", "#000000"), (0.0, 0.0, 0.0)),
            alpha=float(data.get("outline_alpha", 1.0)),
            width=float(data.get("outline_width", 40.0)),
        )
    position_unit = data.get("position_unit", "normalized_half_canvas")
    x = float(data.get("position_x", data.get("transform_x", 0.0)))
    y = float(data.get("position_y", data.get("transform_y", 0.0)))
    if position_unit == "canvas_pixels":
        x /= plan.canvas.width / 2
        y /= plan.canvas.height / 2
    clip = draft.ClipSettings(transform_x=x, transform_y=y)
    return draft.TextSegment(segment.text or "", timerange, style=style, border=border, clip_settings=clip)


def require_under(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label} must stay inside project-dir: {resolved}") from exc
    return resolved


def export(project_dir: Path, plan_path: Path, foundation_root: Path, drafts_dir: Path, draft_name: str) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    plan_path = require_under(plan_path, project_dir, "plan")
    drafts_dir = require_under(drafts_dir, project_dir, "drafts-dir")
    draft, load_plan, validate_plan = load_dependencies(foundation_root)
    plan = load_plan(plan_path)
    issues = validate_plan(plan, check_files=True)
    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise RuntimeError("edit plan validation failed: " + "; ".join(f"{item.code}@{item.location}" for item in errors))
    for track in plan.tracks:
        for segment in track.segments:
            if segment.kind in {"video", "audio"}:
                require_under(plan.resolve_source(segment.source or ""), project_dir, "media source")
    destination = drafts_dir.resolve() / draft_name
    if destination.exists():
        raise FileExistsError(f"draft already exists; replacement is disabled: {destination}")
    drafts_dir.mkdir(parents=True, exist_ok=True)
    folder = draft.DraftFolder(str(drafts_dir.resolve()))
    script = folder.create_draft(draft_name, plan.canvas.width, plan.canvas.height, plan.canvas.fps, allow_replace=False)
    track_types = {"video": draft.TrackType.video, "audio": draft.TrackType.audio, "text": draft.TrackType.text}
    script.append_tracks([draft.TrackSpec(track_types[track.kind], track.id) for track in plan.tracks])
    for track in plan.tracks:
        for segment in track.segments:
            timerange = draft.trange(segment.start_us, segment.duration_us)
            if segment.kind == "video":
                native = draft.VideoSegment(
                    str(plan.resolve_source(segment.source or "")),
                    timerange,
                    speed=segment.speed,
                    volume=segment.volume,
                )
            elif segment.kind == "audio":
                native = draft.AudioSegment(
                    str(plan.resolve_source(segment.source or "")),
                    timerange,
                    speed=segment.speed,
                    volume=segment.volume,
                )
            else:
                native = text_segment(draft, plan, segment, timerange)
            script.add_segment(native, track.id)
    script.save()
    files = sorted(path for path in destination.rglob("*") if path.is_file())
    return {
        "status": "PASSED",
        "method": "DIRECT_PYJIANYING_EDIT_PLAN",
        "draft_path": destination.relative_to(project_dir).as_posix(),
        "file_count": len(files),
        "files": [
            {
                "path": path.relative_to(destination).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in files
        ],
        "warnings": [issue.message for issue in issues if issue.level == "warning"],
        "computer_use_required_for_assembly": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--foundation-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--drafts-dir", type=Path, required=True)
    parser.add_argument("--draft-name", required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        result = export(args.project_dir.resolve(), args.plan.resolve(), args.foundation_root.resolve(), args.drafts_dir.resolve(), args.draft_name)
        if args.receipt:
            receipt = require_under(args.receipt, args.project_dir.resolve(), "receipt")
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_P0", "issues": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
