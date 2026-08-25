#!/usr/bin/env python3
"""R6.34 accepted-deviation facts and segment state-flow gates.

This module is intentionally small.  It does not redesign scene/grid/provider
topology.  It only prevents two previously silent contradictions:

1. a human-accepted visible fact is lost before the next grid or P7; and
2. a provider segment starts and ends in the same decisive contact while a
   reset is expected in the middle (A -> B -> A).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any


COUNT_DRIFT = re.compile(r"^(?P<entity>[A-Z0-9_]+)_COUNT_(?P<observed>\d+)_INSTEAD_OF_(?P<expected>\d+)$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CONTACT_MARKERS = ("CONTACT", "DELIVERY", "MOUTH", "HANDOFF", "GRASP", "BITE", "TOUCH")
CHINESE_DIGITS = {0: "零", 1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
ENGLISH_DIGITS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
COUNT_MEASURES = ("块", "个", "只", "条", "份", "颗", "枚", "件", "张", "根")


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_count_deviations(codes: list[str]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for code in codes:
        match = COUNT_DRIFT.fullmatch(code)
        if match is None:
            continue
        facts.append({
            "failure_code": code,
            "category": "COUNT",
            "entity_id": match.group("entity"),
            "property": "count",
            "expected_value": int(match.group("expected")),
            "observed_value": int(match.group("observed")),
            "propagation_policy": "SOURCE_GRID_AND_DOWNSTREAM",
        })
    return facts


def validate_waiver_deviations(waiver: dict[str, Any], blocking_codes: list[str]) -> list[str]:
    """Validate the human input before a fact-bearing waiver is accepted."""
    issues: list[str] = []
    expected = parse_count_deviations(blocking_codes)
    if not expected:
        return issues
    if waiver.get("scope") != "DOWNSTREAM_FACT_PROPAGATION":
        issues.append("COUNT_DEVIATION_SCOPE_MUST_PROPAGATE_DOWNSTREAM")
    if waiver.get("propagation_policy") != "PROPAGATE_OBSERVED_FACT_DOWNSTREAM":
        issues.append("COUNT_DEVIATION_PROPAGATION_POLICY_MISSING")
    supplied = waiver.get("accepted_deviations")
    if not isinstance(supplied, list) or supplied != expected:
        issues.append("ACCEPTED_DEVIATIONS_DO_NOT_MATCH_FAILURE_CODES")
    return issues


def _replace_count_text(
    value: Any,
    expected: int,
    observed: int,
    *,
    chinese_measures: tuple[str, ...] = COUNT_MEASURES,
    english_nouns: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, str):
        result = value
        result = re.sub(rf"(?<!\d){expected}\s*PIECE(?![A-Z])", f"{observed} PIECE", result)
        if chinese_measures:
            result = re.sub(rf"(?<!\d){expected}\s*(?={'|'.join(map(re.escape, chinese_measures))})", str(observed), result)
        old_cn = CHINESE_DIGITS.get(expected)
        new_cn = CHINESE_DIGITS.get(observed)
        if old_cn is not None and new_cn is not None:
            for measure in chinese_measures:
                result = result.replace(old_cn + measure, new_cn + measure)
        old_en = ENGLISH_DIGITS.get(expected)
        new_en = ENGLISH_DIGITS.get(observed)
        if old_en is not None and new_en is not None:
            for noun in english_nouns:
                result = re.sub(
                    rf"\b{re.escape(old_en)}\s+{re.escape(noun)}\b",
                    f"{new_en} {noun}",
                    result,
                    flags=re.IGNORECASE,
                )
                result = re.sub(rf"(?<!\d){expected}\s+{re.escape(noun)}\b", f"{observed} {noun}", result, flags=re.IGNORECASE)
        return result
    if isinstance(value, list):
        return [
            _replace_count_text(
                item,
                expected,
                observed,
                chinese_measures=chinese_measures,
                english_nouns=english_nouns,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_count_text(
                item,
                expected,
                observed,
                chinese_measures=chinese_measures,
                english_nouns=english_nouns,
            )
            for key, item in value.items()
        }
    return value


def _entity_text_scope(signature: str, expected: int) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    old_cn = CHINESE_DIGITS.get(expected, "")
    measures = tuple(sorted({
        match.group(1)
        for match in re.finditer(re.escape(old_cn) + rf"({'|'.join(map(re.escape, COUNT_MEASURES))})", signature)
    })) if old_cn else ()
    chinese_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", signature)
    keywords = tuple(sorted({chunk[-2:] for chunk in chinese_chunks if len(chunk) >= 2}))
    old_en = ENGLISH_DIGITS.get(expected, "")
    english_nouns = tuple(sorted({
        match.group(1).lower()
        for match in re.finditer(rf"\b(?:{re.escape(old_en)}|{expected})\s+([A-Za-z][A-Za-z-]*)\b", signature, flags=re.IGNORECASE)
    })) if old_en else ()
    return measures, english_nouns, keywords


def _replace_entity_scoped_text(
    value: Any,
    expected: int,
    observed: int,
    *,
    chinese_measures: tuple[str, ...],
    english_nouns: tuple[str, ...],
    keywords: tuple[str, ...],
) -> Any:
    if isinstance(value, str):
        if not any(keyword in value for keyword in (*keywords, *english_nouns)):
            return value
        result = value
        old_cn = CHINESE_DIGITS.get(expected)
        new_cn = CHINESE_DIGITS.get(observed)
        clause_chars = r"[^，。；,;!?！？\r\n]"
        if old_cn is not None and new_cn is not None:
            for keyword in keywords:
                for measure in chinese_measures:
                    # Only rewrite the quantity occurrence locally joined to
                    # this entity keyword. A second same-count prop elsewhere
                    # in the sentence is outside the match and stays intact.
                    result = re.sub(
                        rf"{re.escape(old_cn)}{re.escape(measure)}(?={clause_chars}{{0,40}}{re.escape(keyword)})",
                        new_cn + measure,
                        result,
                    )
                    result = re.sub(
                        rf"({re.escape(keyword)}{clause_chars}{{0,40}}){re.escape(old_cn)}{re.escape(measure)}",
                        lambda match: match.group(1) + new_cn + measure,
                        result,
                    )
                    result = re.sub(
                        rf"(?<!\d){expected}\s*(?={re.escape(measure)}{clause_chars}{{0,40}}{re.escape(keyword)})",
                        str(observed),
                        result,
                    )
        old_en = ENGLISH_DIGITS.get(expected)
        new_en = ENGLISH_DIGITS.get(observed)
        if old_en is not None and new_en is not None:
            for noun in english_nouns:
                result = re.sub(
                    rf"\b{re.escape(old_en)}\s+{re.escape(noun)}\b",
                    f"{new_en} {noun}",
                    result,
                    flags=re.IGNORECASE,
                )
                result = re.sub(rf"(?<!\d){expected}\s+{re.escape(noun)}\b", f"{observed} {noun}", result, flags=re.IGNORECASE)
        return result
    if isinstance(value, list):
        return [
            _replace_entity_scoped_text(
                item,
                expected,
                observed,
                chinese_measures=chinese_measures,
                english_nouns=english_nouns,
                keywords=keywords,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_entity_scoped_text(
                item,
                expected,
                observed,
                chinese_measures=chinese_measures,
                english_nouns=english_nouns,
                keywords=keywords,
            )
            for key, item in value.items()
        }
    return value


def _grid_order(plan: dict[str, Any], grid_id: str) -> int:
    rows = [row for row in plan.get("grids", []) if isinstance(row, dict) and row.get("grid_id") == grid_id]
    if len(rows) != 1 or not isinstance(rows[0].get("grid_order"), int):
        raise ValueError("DEVIATION_SOURCE_GRID_NOT_UNIQUE")
    return int(rows[0]["grid_order"])


def apply_count_facts(
    job: dict[str, Any],
    plan: dict[str, Any],
    *,
    source_grid_id: str,
    facts: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return derived effective inputs; never mutate approved P1/P4 artifacts."""
    effective_job = copy.deepcopy(job)
    effective_plan = copy.deepcopy(plan)
    source_order = _grid_order(effective_plan, source_grid_id)

    for fact in facts:
        if fact.get("category") != "COUNT" or fact.get("property") != "count":
            raise ValueError("UNSUPPORTED_ACCEPTED_DEVIATION_CATEGORY")
        entity_id = str(fact.get("entity_id", ""))
        expected = fact.get("expected_value")
        observed = fact.get("observed_value")
        if not entity_id or not isinstance(expected, int) or not isinstance(observed, int) or expected == observed:
            raise ValueError("ACCEPTED_COUNT_FACT_INVALID")

        state_contract = effective_job.get("visual_state_contract") if isinstance(effective_job.get("visual_state_contract"), dict) else {}
        specs = state_contract.get("physical_prop_specs") if isinstance(state_contract.get("physical_prop_specs"), list) else []
        matching_specs = [row for row in specs if isinstance(row, dict) and row.get("entity_id") == entity_id]
        if len(matching_specs) != 1 or not isinstance(matching_specs[0].get("visual_signature"), str):
            raise ValueError(f"ACCEPTED_COUNT_FACT_ENTITY_SPEC_NOT_UNIQUE:{entity_id}")
        signature = matching_specs[0]["visual_signature"]
        measures, english_nouns, keywords = _entity_text_scope(signature, expected)
        if not measures and not english_nouns and not keywords:
            raise ValueError(f"ACCEPTED_COUNT_FACT_TEXT_SCOPE_UNRESOLVED:{entity_id}")

        # P1 holds stable descriptions used by the P5 compiler. Replace only
        # text bearing this entity's visual signature/noun scope; unrelated
        # props with the same number remain untouched.
        effective_job = _replace_entity_scoped_text(
            effective_job,
            expected,
            observed,
            chinese_measures=measures,
            english_nouns=english_nouns,
            keywords=keywords,
        )

        grids = effective_plan.get("grids") if isinstance(effective_plan.get("grids"), list) else []
        touched = 0
        for grid in grids:
            if not isinstance(grid, dict) or not isinstance(grid.get("grid_order"), int) or grid["grid_order"] < source_order:
                continue
            ledger = grid.get("state_ledger") if isinstance(grid.get("state_ledger"), dict) else {}
            entity_ids = {
                row.get("entity_id") for row in ledger.get("tracked_entities", []) if isinstance(row, dict)
            }
            if entity_id not in entity_ids:
                continue
            patched = _replace_entity_scoped_text(
                grid,
                expected,
                observed,
                chinese_measures=measures,
                english_nouns=english_nouns,
                keywords=keywords,
            )
            grid.clear()
            grid.update(patched)
            ledger = grid.get("state_ledger") if isinstance(grid.get("state_ledger"), dict) else {}
            for tracked in ledger.get("tracked_entities", []):
                if isinstance(tracked, dict) and tracked.get("entity_id") == entity_id:
                    patched_tracked = _replace_count_text(
                        tracked,
                        expected,
                        observed,
                        chinese_measures=measures,
                        english_nouns=english_nouns,
                    )
                    tracked.clear()
                    tracked.update(patched_tracked)
            for state_row in ledger.get("cell_states", []):
                if not isinstance(state_row, dict):
                    continue
                for state in state_row.get("states", []):
                    if isinstance(state, dict) and state.get("entity_id") == entity_id:
                        patched_state = _replace_count_text(
                            state,
                            expected,
                            observed,
                            chinese_measures=measures,
                            english_nouns=english_nouns,
                        )
                        state.clear()
                        state.update(patched_state)
                        if state.get("count") == expected:
                            state["count"] = observed
                            touched += 1
        if touched == 0:
            raise ValueError(f"ACCEPTED_COUNT_FACT_ENTITY_NOT_FOUND:{entity_id}")

        # Segment/scene prose is also consumed by P7.  Exact measure-token
        # replacement avoids changing unrelated ordinal or stage counts.
        effective_plan["scenes"] = _replace_entity_scoped_text(
            effective_plan.get("scenes", []),
            expected,
            observed,
            chinese_measures=measures,
            english_nouns=english_nouns,
            keywords=keywords,
        )
        effective_plan["video_segments"] = _replace_entity_scoped_text(
            effective_plan.get("video_segments", []),
            expected,
            observed,
            chinese_measures=measures,
            english_nouns=english_nouns,
            keywords=keywords,
        )

    return effective_job, effective_plan


def build_fact_contract(
    *,
    project_id: str,
    grid_id: str,
    segment_id: str,
    facts: list[dict[str, Any]],
    rejected_qc: dict[str, str],
    waiver: dict[str, str],
    amended_qc: dict[str, str],
    base_job: dict[str, str],
    base_plan: dict[str, str],
    effective_job: dict[str, str],
    effective_plan: dict[str, str],
) -> dict[str, Any]:
    contract = {
        "schema_version": "R6.34-ACCEPTED-DEVIATION-FACT-CONTRACT-1.0",
        "project_id": project_id,
        "source_grid_id": grid_id,
        "source_segment_id": segment_id,
        "facts": facts,
        "propagation_policy": "PROPAGATE_OBSERVED_FACT_DOWNSTREAM",
        "rejected_qc": rejected_qc,
        "human_waiver": waiver,
        "amended_qc": amended_qc,
        "base_job": base_job,
        "base_scene_plan": base_plan,
        "effective_job": effective_job,
        "effective_scene_plan": effective_plan,
        "provider_calls": 0,
    }
    contract["fact_fingerprint"] = canonical_sha256({
        "project_id": project_id,
        "source_grid_id": grid_id,
        "source_segment_id": segment_id,
        "facts": facts,
    })
    return contract


def validate_fact_contract(project: Path, contract: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if contract.get("schema_version") != "R6.34-ACCEPTED-DEVIATION-FACT-CONTRACT-1.0":
        issues.append("FACT_CONTRACT_SCHEMA_INVALID")
    if contract.get("propagation_policy") != "PROPAGATE_OBSERVED_FACT_DOWNSTREAM":
        issues.append("FACT_CONTRACT_PROPAGATION_INVALID")
    facts = contract.get("facts")
    if not isinstance(facts, list) or not facts:
        issues.append("FACT_CONTRACT_FACTS_MISSING")
    for label in ("rejected_qc", "human_waiver", "amended_qc", "base_job", "base_scene_plan", "effective_job", "effective_scene_plan"):
        row = contract.get(label)
        if not isinstance(row, dict):
            issues.append(f"FACT_CONTRACT_{label.upper()}_MISSING")
            continue
        relative = row.get("relative_path")
        digest = row.get("sha256")
        if not isinstance(relative, str) or not relative or not isinstance(digest, str) or HEX64.fullmatch(digest) is None:
            issues.append(f"FACT_CONTRACT_{label.upper()}_BINDING_INVALID")
            continue
        path = (project / relative).resolve()
        try:
            path.relative_to(project.resolve())
        except ValueError:
            issues.append(f"FACT_CONTRACT_{label.upper()}_PATH_ESCAPES_PROJECT")
            continue
        if not path.is_file() or sha256_file(path) != digest:
            issues.append(f"FACT_CONTRACT_{label.upper()}_HASH_MISMATCH")
    return sorted(set(issues))


def resolve_effective_inputs(project: Path, state: dict[str, Any]) -> tuple[Path, Path, list[dict[str, str]]]:
    """Resolve current JOB/P4 authority and verify every active fact contract."""
    job_path = project / "artifacts/P1/JOB.json"
    plan_path = project / "artifacts/P4/SCENE_PLAN.json"
    lineage: list[dict[str, str]] = []
    rows = state.get("accepted_deviation_fact_contracts", [])
    if not isinstance(rows, list):
        raise ValueError("FACT_CONTRACT_LEDGER_INVALID")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("FACT_CONTRACT_LEDGER_ROW_INVALID")
        relative = str(row.get("relative_path", ""))
        path = (project / relative).resolve()
        path.relative_to(project)
        if not path.is_file() or sha256_file(path) != row.get("sha256"):
            raise ValueError("FACT_CONTRACT_LEDGER_BINDING_STALE")
        contract = json.loads(path.read_text(encoding="utf-8"))
        issues = validate_fact_contract(project, contract)
        if issues:
            raise ValueError("FACT_CONTRACT_INVALID:" + ",".join(issues))
        job_path = project / contract["effective_job"]["relative_path"]
        plan_path = project / contract["effective_scene_plan"]["relative_path"]
        lineage.append({"relative_path": relative, "sha256": row["sha256"]})
    expression_revision = state.get("r641_expression_contract")
    if expression_revision is not None:
        if not isinstance(expression_revision, dict):
            raise ValueError("R641_EXPRESSION_CONTRACT_BINDING_INVALID")
        relative = str(expression_revision.get("effective_scene_plan_relative_path", ""))
        path = (project / relative).resolve()
        path.relative_to(project)
        if not path.is_file() or sha256_file(path) != expression_revision.get("effective_scene_plan_sha256"):
            raise ValueError("R641_EXPRESSION_CONTRACT_BINDING_STALE")
        plan_path = path
    return job_path, plan_path, lineage


def _is_decisive_contact(state: Any, location: Any) -> bool:
    text = f"{state or ''} {location or ''}".upper()
    return any(marker in text for marker in CONTACT_MARKERS)


def audit_segment_state_flow(plan: dict[str, Any]) -> dict[str, Any]:
    """Detect boundary-contact cycles that a grid-conditioned video model can collapse."""
    findings: list[dict[str, Any]] = []
    grids = plan.get("grids") if isinstance(plan.get("grids"), list) else []
    for grid in grids:
        if not isinstance(grid, dict):
            continue
        grid_id = str(grid.get("grid_id", "UNKNOWN"))
        segment_id = str(grid.get("segment_id", "UNKNOWN"))
        ledger = grid.get("state_ledger") if isinstance(grid.get("state_ledger"), dict) else {}
        rows = ledger.get("cell_states") if isinstance(ledger.get("cell_states"), list) else []
        cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
        if len(rows) < 3:
            continue
        by_cell = {row.get("cell"): row for row in rows if isinstance(row, dict) and isinstance(row.get("cell"), int)}
        first_row = by_cell.get(1, {})
        last_row = by_cell.get(len(cells), {})
        first_states = {row.get("entity_id"): row for row in first_row.get("states", []) if isinstance(row, dict)}
        last_states = {row.get("entity_id"): row for row in last_row.get("states", []) if isinstance(row, dict)}
        for entity_id in sorted(set(first_states) & set(last_states)):
            first = first_states[entity_id]
            last = last_states[entity_id]
            first_location = first.get("location")
            last_location = last.get("location")
            if first_location != last_location or not _is_decisive_contact(first.get("state"), first_location):
                continue
            middle = []
            for cell_number in range(2, len(cells)):
                state = next((row for row in by_cell.get(cell_number, {}).get("states", []) if isinstance(row, dict) and row.get("entity_id") == entity_id), None)
                if isinstance(state, dict):
                    middle.append({"cell": cell_number, "state": state.get("state"), "location": state.get("location")})
            if any(row.get("location") != first_location for row in middle):
                findings.append({
                    "code": "CYCLIC_DECISIVE_CONTACT_AT_SEGMENT_BOUNDARIES",
                    "grid_id": grid_id,
                    "segment_id": segment_id,
                    "entity_id": entity_id,
                    "entry": {"state": first.get("state"), "location": first_location},
                    "middle": middle,
                    "exit": {"state": last.get("state"), "location": last_location},
                    "required_resolution": "MOVE_SHARED_BOUNDARY_TO_POST_RESET_MONOTONIC_STATE",
                })
        if cells and isinstance(cells[0], dict):
            first_phase = cells[0].get("interaction_phase")
            for entity_id, state in first_states.items():
                if first_phase == "BEFORE_CONTACT" and _is_decisive_contact(state.get("state"), state.get("location")):
                    findings.append({
                        "code": "FIRST_CELL_INTERACTION_PHASE_CONTRADICTS_CONTACT_STATE",
                        "grid_id": grid_id,
                        "segment_id": segment_id,
                        "entity_id": entity_id,
                    })
    return {
        "schema_version": "R6.34-SEGMENT-STATE-FLOW-AUDIT-1.0",
        "status": "PASSED" if not findings else "BLOCKED_P0",
        "findings": findings,
        "flow_fingerprint": canonical_sha256(findings),
        "provider_calls": 0,
    }


def validate_segment_state_flow(plan: dict[str, Any]) -> list[str]:
    audit = audit_segment_state_flow(plan)
    return [
        f"{row.get('grid_id')}_{row.get('code')}"
        for row in audit["findings"]
        if isinstance(row, dict)
    ]


def require_state_flow_receipt(project: Path, state: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    """Reverify the persisted P4 gate before any P6/P7/P8 preparation."""
    relative = "artifacts/P4/SEGMENT_STATE_FLOW_AUDIT_R634.json"
    path = project / relative
    if not path.is_file():
        raise ValueError("R634_SEGMENT_STATE_FLOW_AUDIT_MISSING")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if (
        receipt.get("schema_version") != "R6.34-SEGMENT-STATE-FLOW-AUDIT-1.0"
        or receipt.get("status") != "PASSED"
        or receipt.get("scene_plan_relative_path") != plan_path.relative_to(project).as_posix()
        or receipt.get("scene_plan_sha256") != sha256_file(plan_path)
    ):
        raise ValueError("R634_SEGMENT_STATE_FLOW_AUDIT_STALE_OR_FAILED")
    artifact = state.get("artifacts", {}).get("P4", {}).get("SEGMENT_STATE_FLOW_AUDIT_R634", {})
    if (
        not isinstance(artifact, dict)
        or artifact.get("relative_path") != relative
        or artifact.get("sha256") != sha256_file(path)
        or artifact.get("validation_status") != "VALIDATED"
    ):
        raise ValueError("R634_SEGMENT_STATE_FLOW_AUDIT_NOT_LEDGER_BOUND")
    return {"relative_path": relative, "sha256": sha256_file(path)}
