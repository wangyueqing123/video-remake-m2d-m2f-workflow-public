#!/usr/bin/env python3
"""Derive complete P7 action intervals from validated P4 artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


TOLERANCE = 1e-6


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"P7_TIMING_{label}_INVALID")
    return float(value)


def _validate_intervals(
    nodes: list[dict[str, Any]],
    segment_start: float,
    segment_end: float,
) -> None:
    if not nodes:
        raise ValueError("P7_TIMING_NODES_MISSING")
    expected_start = segment_start
    for index, node in enumerate(nodes, start=1):
        start = number(node.get("start_s"), f"NODE_{index}_START")
        end = number(node.get("end_s"), f"NODE_{index}_END")
        if end <= start + TOLERANCE:
            raise ValueError(f"P7_TIMING_NODE_{index}_NON_POSITIVE")
        if abs(start - expected_start) > TOLERANCE:
            raise ValueError(f"P7_TIMING_NODE_{index}_GAP_OR_OVERLAP")
        if not clean(node.get("action")):
            raise ValueError(f"P7_TIMING_NODE_{index}_ACTION_MISSING")
        if not clean(node.get("visible_state_at_end")):
            raise ValueError(f"P7_TIMING_NODE_{index}_VISIBLE_END_MISSING")
        expected_start = end
    if abs(nodes[0]["start_s"] - segment_start) > TOLERANCE:
        raise ValueError("P7_TIMING_FIRST_NODE_START_MISMATCH")
    if abs(expected_start - segment_end) > TOLERANCE:
        raise ValueError("P7_TIMING_LAST_NODE_END_MISMATCH")


def derive_timing_nodes(
    scene_plan: dict[str, Any],
    p4_segment: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Return (authority, normalized nodes), never guessing fixed durations."""
    segment_id = clean(p4_segment.get("segment_id"))
    grid_id = clean(p4_segment.get("grid_id"))
    segment_start = number(p4_segment.get("target_start_s"), "SEGMENT_START")
    segment_end = number(p4_segment.get("target_end_s"), "SEGMENT_END")
    if segment_end <= segment_start + TOLERANCE:
        raise ValueError("P7_TIMING_SEGMENT_RANGE_INVALID")

    explicit = p4_segment.get("action_nodes")
    explicit_present = explicit is not None
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise ValueError("P7_TIMING_EXPLICIT_ACTION_NODES_INVALID")
        nodes: list[dict[str, Any]] = []
        for index, row in enumerate(explicit, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"P7_TIMING_EXPLICIT_NODE_{index}_INVALID")
            nodes.append({
                "start_s": number(row.get("start_s"), f"EXPLICIT_NODE_{index}_START"),
                "end_s": number(row.get("end_s"), f"EXPLICIT_NODE_{index}_END"),
                "action": clean(row.get("action")),
                "visible_state_at_end": clean(row.get("visible_state_at_end")),
            })
        _validate_intervals(nodes, segment_start, segment_end)

    grids = [
        row for row in scene_plan.get("grids", [])
        if isinstance(row, dict)
        and row.get("segment_id") == segment_id
        and row.get("grid_id") == grid_id
    ]
    if len(grids) != 1:
        raise ValueError("P7_TIMING_SEGMENT_GRID_MISSING_OR_DUPLICATE")
    cells = grids[0].get("cells")
    if not isinstance(cells, list) or len(cells) < 2:
        raise ValueError("P7_TIMING_GRID_CELLS_INSUFFICIENT")

    times: list[float] = []
    for index, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            raise ValueError(f"P7_TIMING_CELL_{index}_INVALID")
        times.append(number(cell.get("target_time_s"), f"CELL_{index}_TIME"))
        if not clean(cell.get("visual_statement")):
            raise ValueError(f"P7_TIMING_CELL_{index}_VISUAL_STATEMENT_MISSING")
    if abs(times[0] - segment_start) > TOLERANCE:
        raise ValueError("P7_TIMING_FIRST_CELL_START_MISMATCH")
    if abs(times[-1] - segment_end) > TOLERANCE:
        raise ValueError("P7_TIMING_LAST_CELL_END_MISMATCH")
    if any(current <= previous + TOLERANCE for previous, current in zip(times, times[1:])):
        raise ValueError("P7_TIMING_CELL_TIMES_NOT_STRICTLY_INCREASING")

    nodes = []
    for index in range(1, len(cells)):
        current = cells[index]
        visible = clean(current.get("visual_statement"))
        nodes.append({
            "start_s": times[index - 1],
            "end_s": times[index],
            "action": visible,
            "visible_state_at_end": visible,
        })
    _validate_intervals(nodes, segment_start, segment_end)
    return ("GRID_CELL_TIMELINE_WITH_ACTION_NODE_COVERAGE" if explicit_present else "GRID_CELL_TIMELINE"), nodes
