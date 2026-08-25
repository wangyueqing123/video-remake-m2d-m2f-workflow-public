#!/usr/bin/env python3
"""Shared named-accent parsing and validation for the closed mono style."""

from __future__ import annotations

import re
from typing import Any, Iterable


COLOR_TERMS: dict[str, tuple[str, ...]] = {
    "red": ("红色", "red"),
    "pink": ("粉红色", "粉色", "pink"),
    "blue": ("蓝色", "blue"),
    "green": ("绿色", "green"),
    "yellow": ("黄色", "yellow"),
    "orange": ("橙色", "orange"),
    "purple": ("紫色", "purple", "violet"),
    "cyan": ("蓝绿色", "青色", "cyan", "teal"),
    "magenta": ("洋红色", "品红色", "magenta"),
    "gold": ("金色", "gold"),
}

FORBIDDEN_MONO_COLOR_TERMS: dict[str, tuple[str, ...]] = {
    "brown": ("棕色", "褐色", "brown"),
    "beige": ("米黄色", "米色", "beige", "tan"),
}

COLOR_HUE_CENTERS_DEG: dict[str, tuple[float, ...]] = {
    "red": (0.0,),
    "pink": (335.0,),
    "blue": (220.0,),
    "green": (120.0,),
    "yellow": (55.0,),
    "orange": (30.0,),
    "purple": (275.0,),
    "cyan": (185.0,),
    "magenta": (310.0,),
    "gold": (45.0,),
}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _contains(text: str, term: str) -> bool:
    lowered = text.lower()
    if term.isascii():
        return re.search(rf"(?<![a-z]){re.escape(term.lower())}(?![a-z])", lowered) is not None
    return term in text


def find_colors(value: Any) -> set[str]:
    found: set[str] = set()
    for text in _strings(value):
        for color, terms in COLOR_TERMS.items():
            if any(_contains(text, term) for term in terms):
                found.add(color)
    return found


def find_forbidden_mono_colors(value: Any) -> set[str]:
    found: set[str] = set()
    for text in _strings(value):
        for color, terms in FORBIDDEN_MONO_COLOR_TERMS.items():
            if any(_contains(text, term) for term in terms):
                found.add(color)
    return found


def accent_entries(identity: dict[str, Any]) -> list[str]:
    values = identity.get("accent_colors")
    return [item.strip() for item in values if isinstance(item, str) and item.strip()] if isinstance(values, list) else []


def _entry_has_entity_scope(entry: str, colors: set[str]) -> bool:
    remainder = entry
    for color in colors:
        for term in COLOR_TERMS[color]:
            remainder = re.sub(re.escape(term), "", remainder, flags=re.IGNORECASE if term.isascii() else 0)
    remainder = re.sub(r"[\s\-_/:;,.，。；：、()（）]+", "", remainder)
    return len(remainder) >= 2


def validate_mono_identity(identity: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    entries = accent_entries(identity)
    declared: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        colors = find_colors(entry)
        if not colors:
            issues.append(f"MONO_STYLE_ACCENT_{index}_COLOR_UNRECOGNIZED")
            continue
        declared.update(colors)
        if not _entry_has_entity_scope(entry, colors):
            issues.append(f"MONO_STYLE_ACCENT_{index}_MUST_BIND_COLOR_TO_ENTITY")

    described_source = {
        key: identity.get(key)
        for key in ("person", "animal", "environment", "props")
    }
    described = find_colors(described_source)
    for color in sorted(described - declared):
        issues.append(f"MONO_STYLE_CHROMATIC_IDENTITY_TOKEN_UNDECLARED_{color.upper()}")
    for color in sorted(find_forbidden_mono_colors(described_source)):
        issues.append(f"MONO_STYLE_FORBIDDEN_CHROMATIC_IDENTITY_{color.upper()}")
    return sorted(set(issues))


def declared_accent_contract(identity: dict[str, Any]) -> dict[str, Any]:
    entries = accent_entries(identity)
    colors = sorted(find_colors(entries))
    centers = sorted({center for color in colors for center in COLOR_HUE_CENTERS_DEG[color]})
    return {
        "entries": entries,
        "canonical_colors": colors,
        "hue_centers_deg": centers,
    }


def missing_accent_descriptors(identity: dict[str, Any]) -> list[str]:
    """Return existing identity descriptions that safely scope undeclared colors."""
    declared = find_colors(accent_entries(identity))
    candidates: list[str] = []
    for key in ("person", "animal", "environment", "props"):
        for entry in _strings(identity.get(key)):
            colors = find_colors(entry) - declared
            if colors and _entry_has_entity_scope(entry, colors) and entry not in candidates:
                candidates.append(entry)
                declared.update(colors)
    return candidates


def hue_is_declared(hue_deg: float, centers: list[float], tolerance_deg: float) -> bool:
    return any(min(abs(hue_deg - center), 360.0 - abs(hue_deg - center)) <= tolerance_deg for center in centers)
