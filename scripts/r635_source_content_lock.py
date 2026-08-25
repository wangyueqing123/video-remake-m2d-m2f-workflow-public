#!/usr/bin/env python3
"""Validate R6.35 source language, semantic units, and rewritten-copy lineage."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any


sys.dont_write_bytecode = True

ROUTES = {"M2_D_SHARE_FIRST"}
UNIT_KINDS = {"CLAIM", "ACTION", "CONDITION", "RESULT", "SAFETY", "CONTEXT"}
WRAPPER_TYPES = {
    "M2_D_SHARE_FIRST": {"HOOK", "TONE", "CTA"},
}
WRAPPER_LIMITS = {"M2_D_SHARE_FIRST": 0.25}
RELATIONS_KEEP = {"VERBATIM", "PARAPHRASE", "COMPRESSION"}
RELATIONS_TRANSLATE = {"TRANSLATION", "TRANSLATION_AND_COMPRESSION"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"OBJECT_REQUIRED:{path.name}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def canonical_text(value: Any) -> str:
    raw = unicodedata.normalize("NFKC", text(value)).casefold()
    return "".join(char for char in raw if char.isalnum())


def content_length(value: Any) -> int:
    return sum(1 for char in unicodedata.normalize("NFKC", text(value)) if char.isalnum())


def language_family(value: Any) -> str:
    token = text(value).lower().replace("_", "-")
    if token.startswith("zh"):
        return "zh"
    if token.startswith("en"):
        return "en"
    return token.split("-", 1)[0]


def text_matches_language(value: Any, language: Any) -> bool:
    raw = text(value)
    family = language_family(language)
    cjk = sum(1 for char in raw if "\u4e00" <= char <= "\u9fff")
    latin = sum(1 for char in raw if ("a" <= char.lower() <= "z"))
    if family == "en":
        return latin >= 3 and cjk == 0
    if family == "zh":
        return cjk >= 2 and cjk >= latin
    return bool(raw)


def resolve_project_file(project: Path, relative: Any, issues: list[str], label: str) -> Path | None:
    token = text(relative).replace("\\", "/")
    pure = PurePosixPath(token)
    if not token or pure.is_absolute() or ".." in pure.parts or (len(token) >= 2 and token[1] == ":"):
        issues.append(f"{label}_PATH_INVALID")
        return None
    candidate = (project / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(project.resolve())
    except ValueError:
        issues.append(f"{label}_PATH_ESCAPES_PROJECT")
        return None
    if not candidate.is_file():
        issues.append(f"{label}_MISSING")
        return None
    return candidate


def validate_hash(path: Path | None, digest: Any, issues: list[str], label: str) -> None:
    token = text(digest).lower()
    if path is None or not HEX64.fullmatch(token) or sha256(path) != token:
        issues.append(f"{label}_HASH_INVALID")


def validate_job_policy(job: dict[str, Any], *, require_r635_schema: bool = False) -> list[str]:
    issues: list[str] = []
    route = job.get("route_id")
    if route not in ROUTES:
        return issues
    if require_r635_schema and job.get("schema_version") != "R6.35-JOB-1.0":
        issues.append("R635_JOB_SCHEMA_REQUIRED")
    policy = job.get("source_content_inheritance")
    if not isinstance(policy, dict):
        return ["SOURCE_CONTENT_INHERITANCE_MISSING"]
    expected = {
        "schema_version": "R6.35-SOURCE-CONTENT-POLICY-1.0",
        "source_language_authority": "P2_TRANSCRIPT",
        "audience_may_select_language": False,
        "semantic_fidelity": "STRICT_SOURCE_MEANING",
        "required_semantic_unit_coverage": 1.0,
        "causal_order_policy": "PRESERVE",
        "unsupported_additions_policy": "BLOCK",
        "route_wrapper_max_ratio": WRAPPER_LIMITS[route],
    }
    for key, expected_value in expected.items():
        if policy.get(key) != expected_value:
            issues.append(f"SOURCE_CONTENT_POLICY_{key.upper()}_INVALID")
    language_policy = policy.get("delivery_language_policy")
    requested = text(policy.get("requested_delivery_language"))
    if language_policy == "KEEP_SOURCE_LANGUAGE":
        if requested != "SOURCE":
            issues.append("KEEP_SOURCE_LANGUAGE_REQUIRES_SOURCE_TARGET")
    elif language_policy == "EXPLICIT_TRANSLATION":
        if not requested or requested == "SOURCE":
            issues.append("EXPLICIT_TRANSLATION_TARGET_MISSING")
    else:
        issues.append("DELIVERY_LANGUAGE_POLICY_INVALID")
    return sorted(set(issues))


def transcript_segments(transcript: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    return {
        text(row.get("segment_id")): row
        for row in rows
        if isinstance(row, dict) and text(row.get("segment_id"))
    }


def timeline_samples(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = evidence.get("samples") if isinstance(evidence.get("samples"), list) else []
    return {
        text(row.get("sample_id")): row
        for row in rows
        if isinstance(row, dict) and text(row.get("sample_id"))
    }


def evidence_haystack(row: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("text", "spoken_content", "observed_setting", "large_action_or_state", "visible_result"):
        if text(row.get(key)):
            values.append(text(row.get(key)))
    facts = row.get("content_facts") if isinstance(row.get("content_facts"), list) else []
    values.extend(text(item) for item in facts if text(item))
    return canonical_text(" ".join(values))


def validate_translation_authorization(
    project: Path,
    decision: dict[str, Any],
    source_language: str,
    delivery_language: str,
    job_id: str,
    issues: list[str],
) -> None:
    path = resolve_project_file(project, decision.get("translation_authorization_relative_path"), issues, "TRANSLATION_AUTHORIZATION")
    validate_hash(path, decision.get("translation_authorization_sha256"), issues, "TRANSLATION_AUTHORIZATION")
    if path is None:
        return
    authorization = load(path)
    expected = {
        "schema_version": "R6.35-TRANSLATION-AUTHORIZATION-1.0",
        "status": "APPROVED",
        "job_id": job_id,
        "source_language": source_language,
        "delivery_language": delivery_language,
        "authorized_before_p3": True,
    }
    for key, expected_value in expected.items():
        if authorization.get(key) != expected_value:
            issues.append(f"TRANSLATION_AUTHORIZATION_{key.upper()}_INVALID")
    if not text(authorization.get("authority_text")):
        issues.append("TRANSLATION_AUTHORIZATION_TEXT_MISSING")


def validate_p2(project: Path, job: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    issues = validate_job_policy(job, require_r635_schema=True)
    route = job.get("route_id")
    if route not in ROUTES:
        return sorted(set(issues + ["R635_ROUTE_NOT_SUPPORTED"]))
    if contract.get("schema_version") != "R6.35-SOURCE-CONTENT-CONTRACT-1.0" or contract.get("status") != "VALIDATED":
        issues.append("SOURCE_CONTENT_CONTRACT_SCHEMA_OR_STATUS_INVALID")
    if contract.get("job_id") != job.get("job_id") or contract.get("route_id") != route:
        issues.append("SOURCE_CONTENT_PROJECT_OR_ROUTE_MISMATCH")

    source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
    transcript_path = resolve_project_file(project, source.get("transcript_relative_path"), issues, "SOURCE_TRANSCRIPT")
    timeline_path = resolve_project_file(project, source.get("timeline_evidence_relative_path"), issues, "TIMELINE_EVIDENCE")
    validate_hash(transcript_path, source.get("transcript_sha256"), issues, "SOURCE_TRANSCRIPT")
    validate_hash(timeline_path, source.get("timeline_evidence_sha256"), issues, "TIMELINE_EVIDENCE")
    transcript = load(transcript_path) if transcript_path else {}
    timeline = load(timeline_path) if timeline_path else {}
    source_language = text(source.get("source_language"))
    if not source_language or language_family(source_language) != language_family(transcript.get("language")):
        issues.append("SOURCE_LANGUAGE_DIFFERS_FROM_TRANSCRIPT")
    if not text_matches_language(transcript.get("full_text"), source_language):
        issues.append("TRANSCRIPT_TEXT_LANGUAGE_INVALID")

    policy = job.get("source_content_inheritance") if isinstance(job.get("source_content_inheritance"), dict) else {}
    decision = contract.get("language_decision") if isinstance(contract.get("language_decision"), dict) else {}
    if decision.get("policy") != policy.get("delivery_language_policy"):
        issues.append("P1_P2_LANGUAGE_POLICY_MISMATCH")
    delivery_language = text(decision.get("delivery_language"))
    if decision.get("policy") == "KEEP_SOURCE_LANGUAGE":
        if language_family(delivery_language) != language_family(source_language):
            issues.append("KEEP_SOURCE_LANGUAGE_VIOLATED")
        if text(decision.get("translation_authorization_relative_path")) or text(decision.get("translation_authorization_sha256")):
            issues.append("KEEP_SOURCE_LANGUAGE_FORBIDS_TRANSLATION_AUTHORIZATION")
    elif decision.get("policy") == "EXPLICIT_TRANSLATION":
        if language_family(delivery_language) != language_family(policy.get("requested_delivery_language")):
            issues.append("TRANSLATION_TARGET_DIFFERS_FROM_P1")
        if language_family(delivery_language) == language_family(source_language):
            issues.append("TRANSLATION_TARGET_EQUALS_SOURCE")
        validate_translation_authorization(project, decision, source_language, delivery_language, text(job.get("job_id")), issues)

    segment_by_id = transcript_segments(transcript)
    sample_by_id = timeline_samples(timeline)
    units = contract.get("semantic_units") if isinstance(contract.get("semantic_units"), list) else []
    if not units:
        issues.append("SEMANTIC_UNITS_MISSING")
    seen: set[str] = set()
    order_by_id: dict[str, int] = {}
    for index, unit in enumerate(units, start=1):
        if not isinstance(unit, dict):
            issues.append(f"SEMANTIC_UNIT_{index}_INVALID")
            continue
        unit_id = text(unit.get("unit_id"))
        if not unit_id or unit_id in seen:
            issues.append(f"SEMANTIC_UNIT_{index}_ID_INVALID")
        seen.add(unit_id)
        if unit.get("order") != index:
            issues.append(f"{unit_id or index}_ORDER_INVALID")
        else:
            order_by_id[unit_id] = index
        if unit.get("kind") not in UNIT_KINDS or not text(unit.get("canonical_meaning")):
            issues.append(f"{unit_id or index}_MEANING_OR_KIND_INVALID")
        if unit.get("required") not in {True, False} or unit.get("order_locked") not in {True, False}:
            issues.append(f"{unit_id or index}_REQUIREMENT_FLAGS_INVALID")
        refs = unit.get("source_refs") if isinstance(unit.get("source_refs"), list) else []
        if not refs:
            issues.append(f"{unit_id or index}_SOURCE_REFS_MISSING")
        for ref_index, ref in enumerate(refs, start=1):
            if not isinstance(ref, dict):
                issues.append(f"{unit_id or index}_SOURCE_REF_{ref_index}_INVALID")
                continue
            authority = ref.get("authority")
            evidence_id = text(ref.get("evidence_id"))
            excerpt = canonical_text(ref.get("evidence_excerpt"))
            if not excerpt:
                issues.append(f"{unit_id or index}_SOURCE_REF_{ref_index}_EXCERPT_MISSING")
                continue
            if authority == "TRANSCRIPT":
                row = segment_by_id.get(evidence_id)
            elif authority == "TIMELINE":
                row = sample_by_id.get(evidence_id)
            else:
                row = None
                issues.append(f"{unit_id or index}_SOURCE_REF_{ref_index}_AUTHORITY_INVALID")
            if row is None:
                issues.append(f"{unit_id or index}_SOURCE_REF_{ref_index}_ID_NOT_FOUND")
            elif excerpt not in evidence_haystack(row):
                issues.append(f"{unit_id or index}_SOURCE_REF_{ref_index}_EXCERPT_NOT_IN_EVIDENCE")
    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = text(unit.get("unit_id"))
        dependencies = unit.get("depends_on") if isinstance(unit.get("depends_on"), list) else []
        for dependency in dependencies:
            dependency_id = text(dependency)
            if dependency_id not in order_by_id:
                issues.append(f"{unit_id}_DEPENDENCY_NOT_FOUND")
            elif order_by_id[dependency_id] >= order_by_id.get(unit_id, 0):
                issues.append(f"{unit_id}_DEPENDENCY_NOT_EARLIER")
    return sorted(set(issues))


def narration_utterances(narration: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for segment in narration.get("segments", []) if isinstance(narration.get("segments"), list) else []:
        if not isinstance(segment, dict):
            continue
        for utterance in segment.get("utterances", []) if isinstance(segment.get("utterances"), list) else []:
            if isinstance(utterance, dict):
                result.append(utterance)
    return result


def validate_p3(
    project: Path,
    job: dict[str, Any],
    contract: dict[str, Any],
    narration: dict[str, Any],
    audit: dict[str, Any],
) -> list[str]:
    issues = validate_p2(project, job, contract)
    route = job.get("route_id")
    if audit.get("schema_version") != "R6.35-COPY-FIDELITY-AUDIT-1.0" or audit.get("status") != "PASSED" or audit.get("decision") != "PASSED":
        issues.append("COPY_FIDELITY_AUDIT_SCHEMA_OR_STATUS_INVALID")
    if audit.get("job_id") != job.get("job_id") or audit.get("route_id") != route:
        issues.append("COPY_FIDELITY_PROJECT_OR_ROUTE_MISMATCH")
    for key, expected_path, expected_hash, label in (
        ("source_content_contract", "artifacts/P2/SOURCE_CONTENT_CONTRACT.json", sha256(project / "artifacts/P2/SOURCE_CONTENT_CONTRACT.json"), "SOURCE_CONTENT"),
        ("narration_plan", "artifacts/P3/NARRATION_PLAN.json", sha256(project / "artifacts/P3/NARRATION_PLAN.json"), "NARRATION"),
    ):
        ref = audit.get(key) if isinstance(audit.get(key), dict) else {}
        if ref.get("relative_path") != expected_path or ref.get("sha256") != expected_hash:
            issues.append(f"COPY_FIDELITY_{label}_LINEAGE_INVALID")

    decision = contract.get("language_decision") if isinstance(contract.get("language_decision"), dict) else {}
    delivery_language = text(decision.get("delivery_language"))
    if language_family(audit.get("delivery_language")) != language_family(delivery_language):
        issues.append("COPY_FIDELITY_DELIVERY_LANGUAGE_MISMATCH")
    full_copy = text(narration.get("full_spoken_copy"))
    if not text_matches_language(full_copy, delivery_language):
        issues.append("NARRATION_LANGUAGE_VIOLATES_CONTRACT")

    units = [row for row in contract.get("semantic_units", []) if isinstance(row, dict)]
    unit_by_id = {text(row.get("unit_id")): row for row in units if text(row.get("unit_id"))}
    required = {unit_id for unit_id, row in unit_by_id.items() if row.get("required") is True}
    utterances = narration_utterances(narration)
    utterance_by_id: dict[str, dict[str, Any]] = {}
    utterance_position: dict[str, int] = {}
    for position, row in enumerate(utterances, start=1):
        utterance_id = text(row.get("utterance_id"))
        if not utterance_id or utterance_id in utterance_by_id:
            issues.append("NARRATION_UTTERANCE_ID_MISSING_OR_DUPLICATE")
        else:
            utterance_by_id[utterance_id] = row
            utterance_position[utterance_id] = position

    mappings = audit.get("unit_mappings") if isinstance(audit.get("unit_mappings"), list) else []
    mapped_required: set[str] = set()
    first_position_by_unit: dict[str, int] = {}
    mapping_pairs: set[tuple[str, str]] = set()
    language_policy = decision.get("policy")
    allowed_relations = RELATIONS_KEEP if language_policy == "KEEP_SOURCE_LANGUAGE" else RELATIONS_TRANSLATE
    for index, mapping in enumerate(mappings, start=1):
        if not isinstance(mapping, dict):
            issues.append(f"UNIT_MAPPING_{index}_INVALID")
            continue
        unit_id = text(mapping.get("source_unit_id"))
        if unit_id not in unit_by_id:
            issues.append(f"UNIT_MAPPING_{index}_SOURCE_UNIT_UNKNOWN")
        if mapping.get("relation") not in allowed_relations or mapping.get("meaning_preserved") is not True:
            issues.append(f"{unit_id or index}_RELATION_OR_MEANING_INVALID")
        refs = mapping.get("utterance_ids") if isinstance(mapping.get("utterance_ids"), list) else []
        positions: list[int] = []
        if not refs:
            issues.append(f"{unit_id or index}_MAPPED_UTTERANCES_MISSING")
        for ref in refs:
            utterance_id = text(ref)
            if utterance_id not in utterance_by_id:
                issues.append(f"{unit_id or index}_MAPPED_UTTERANCE_UNKNOWN")
            else:
                positions.append(utterance_position[utterance_id])
                mapping_pairs.add((unit_id, utterance_id))
        if positions:
            first_position_by_unit[unit_id] = min(positions)
        if unit_id in required:
            mapped_required.add(unit_id)

    missing_required = sorted(required - mapped_required)
    if missing_required:
        issues.append("REQUIRED_SEMANTIC_UNITS_NOT_COVERED")
    coverage = len(mapped_required) / len(required) if required else 1.0
    if abs(coverage - 1.0) > 1e-9 or number(audit.get("required_unit_coverage")) != coverage:
        issues.append("REQUIRED_UNIT_COVERAGE_INVALID")

    ordered_units = sorted(
        (row for row in units if row.get("required") is True and row.get("order_locked") is True),
        key=lambda row: row.get("order", 0),
    )
    previous_position = 0
    for unit in ordered_units:
        unit_id = text(unit.get("unit_id"))
        position = first_position_by_unit.get(unit_id)
        if position is not None and position < previous_position:
            issues.append("SOURCE_CAUSAL_ORDER_CHANGED")
        if position is not None:
            previous_position = position
        for dependency in unit.get("depends_on", []) if isinstance(unit.get("depends_on"), list) else []:
            dependency_position = first_position_by_unit.get(text(dependency))
            if position is not None and dependency_position is not None and dependency_position > position:
                issues.append("SOURCE_DEPENDENCY_ORDER_CHANGED")

    claims = audit.get("utterance_claims") if isinstance(audit.get("utterance_claims"), list) else []
    seen_claims: set[str] = set()
    wrapper_length = 0
    total_length = sum(content_length(row.get("text")) for row in utterances)
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            issues.append(f"UTTERANCE_CLAIM_{index}_INVALID")
            continue
        utterance_id = text(claim.get("utterance_id"))
        if not utterance_id or utterance_id in seen_claims or utterance_id not in utterance_by_id:
            issues.append(f"UTTERANCE_CLAIM_{index}_ID_INVALID")
            continue
        seen_claims.add(utterance_id)
        if text(claim.get("text")) != text(utterance_by_id[utterance_id].get("text")):
            issues.append(f"{utterance_id}_CLAIM_TEXT_DIFFERS_FROM_NARRATION")
        scope = claim.get("claim_scope")
        source_ids = claim.get("source_unit_ids") if isinstance(claim.get("source_unit_ids"), list) else []
        if scope == "SOURCE_PAYLOAD":
            if claim.get("non_factual") is not False or not source_ids or text(claim.get("wrapper_type")):
                issues.append(f"{utterance_id}_SOURCE_PAYLOAD_CONTRACT_INVALID")
            for unit_id in source_ids:
                if text(unit_id) not in unit_by_id or (text(unit_id), utterance_id) not in mapping_pairs:
                    issues.append(f"{utterance_id}_SOURCE_PAYLOAD_MAPPING_INVALID")
        elif scope == "ROUTE_WRAPPER":
            if source_ids or claim.get("non_factual") is not True or claim.get("wrapper_type") not in WRAPPER_TYPES.get(route, set()):
                issues.append(f"{utterance_id}_ROUTE_WRAPPER_CONTRACT_INVALID")
            wrapper_length += content_length(claim.get("text"))
        else:
            issues.append(f"{utterance_id}_CLAIM_SCOPE_INVALID")
    if seen_claims != set(utterance_by_id):
        issues.append("NARRATION_UTTERANCES_NOT_EXACTLY_AUDITED")
    ratio = wrapper_length / total_length if total_length else 0.0
    if abs((number(audit.get("route_wrapper_ratio")) or 0.0) - ratio) > 1e-6:
        issues.append("ROUTE_WRAPPER_RATIO_RECEIPT_INVALID")
    if ratio > WRAPPER_LIMITS.get(route, 0.0) + 1e-9:
        issues.append("ROUTE_WRAPPER_RATIO_EXCEEDS_LIMIT")

    for key in ("unsupported_claims", "omitted_required_unit_ids", "causal_order_violations"):
        if audit.get(key) != []:
            issues.append(f"COPY_FIDELITY_{key.upper()}_NOT_EMPTY")
    return sorted(set(issues))


def validate_project(project: Path, stage: str) -> list[str]:
    state = load(project / "R62_PROJECT.json")
    if state.get("skill_version") not in {"R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}:
        return ["R635_PROJECT_VERSION_REQUIRED"]
    job = load(project / "artifacts/P1/JOB.json")
    if job.get("route_id") not in ROUTES:
        return []
    contract = load(project / "artifacts/P2/SOURCE_CONTENT_CONTRACT.json")
    if stage == "p2":
        return validate_p2(project, job, contract)
    narration = load(project / "artifacts/P3/NARRATION_PLAN.json")
    audit = load(project / "artifacts/P3/COPY_FIDELITY_AUDIT.json")
    return validate_p3(project, job, contract, narration, audit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("p2", "p3"))
    args = parser.parse_args()
    try:
        issues = validate_project(args.project_dir.resolve(), args.stage)
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        issues = [f"READ_OR_VALIDATE_ERROR:{exc}"]
    result = {"status": "PASSED" if not issues else "BLOCKED_P0", "stage": args.stage.upper(), "issues": issues}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
