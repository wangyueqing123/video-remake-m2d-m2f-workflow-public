#!/usr/bin/env python3
"""Persist portable R6.20 projects with R6.2 artifact-schema compatibility."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from validate_r619_visual_core import validate_qc_cell
from r634_integrity_contract import (
    apply_count_facts,
    build_fact_contract,
    parse_count_deviations,
    resolve_effective_inputs,
    validate_fact_contract,
    validate_segment_state_flow,
    validate_waiver_deviations,
)
from release_contract import (
    COMPATIBLE_P6_QC_SCHEMAS,
    CURRENT_SKILL_VERSION,
    SUPPORTED_PROJECT_VERSIONS,
    required_core_qc_schema,
)

sys.dont_write_bytecode = True

from validate_r62_job import load_json, validate_job


SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets"
STATE_NAME = "R62_PROJECT.json"
PHASES = {f"P{number}" for number in range(10)}
CALL_KINDS = {"GRID_BASELINE", "GRID_CORRECTION", "ASSET_UPLOAD", "VIDEO_API", "VIDEO_API_RECOVERY"}
HUMAN_WAIVABLE_P6_CODES = {"UNWANTED_ARROW_OR_DIAGRAM_MARK"}
UNAUTHORIZED_DECORATIVE_MARK_PATTERN = re.compile(
    r"^UNAUTHORIZED_[A-Z0-9]+(?:_[A-Z0-9]+)*_(?:ICON|MARK)_PRESENT_CELL_\d+$"
)
MINOR_COUNT_DRIFT_PATTERN = re.compile(r"^[A-Z0-9_]+_COUNT_\d+_INSTEAD_OF_\d+$")
MICROSTATE_COMPRESSION_PATTERN = re.compile(
    r"^[A-Z0-9_]+_(?:PREMATURE_FULL_EXPOSURE|PREMATURE_FULL_REVEAL)_CELL_\d+$"
)


def human_qc_waiver_policy(codes: list[str]) -> str | None:
    """Return the narrow waiver policy; causal, topology, and final-cell errors never qualify."""
    code_set = set(codes)
    if code_set and all(
        code in HUMAN_WAIVABLE_P6_CODES or UNAUTHORIZED_DECORATIVE_MARK_PATTERN.fullmatch(code)
        for code in code_set
    ):
        return "UNWANTED_MARK"
    if code_set and all(MINOR_COUNT_DRIFT_PATTERN.fullmatch(code) for code in code_set):
        return "NONCAUSAL_COUNT_DRIFT"
    if code_set and all(MICROSTATE_COMPRESSION_PATTERN.fullmatch(code) for code in code_set):
        return "NONCAUSAL_MICROSTATE_COMPRESSION"
    return None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def exclusive_project_lock(project: Path, timeout_seconds: float = 30.0):
    """Serialize every controller mutation for one project across processes."""
    project = project.resolve()
    project.parent.mkdir(parents=True, exist_ok=True)
    lock_path = project.parent / f".{project.name}.R62_PROJECT.lock"
    token = f"{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > 120.0
            except FileNotFoundError:
                continue
            if stale:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise ValueError("PROJECT_LEDGER_LOCK_TIMEOUT")
            time.sleep(0.05)
            continue
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(token)
            break
        except Exception:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            raise
    try:
        yield
    finally:
        try:
            if lock_path.read_text(encoding="utf-8") == token:
                lock_path.unlink()
        except FileNotFoundError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def skill_tree_fingerprint() -> tuple[str, int]:
    rows = []
    for path in sorted((item for item in SKILL_ROOT.rglob("*") if item.is_file()), key=lambda item: item.relative_to(SKILL_ROOT).as_posix()):
        relative = path.relative_to(SKILL_ROOT)
        if "__pycache__" in relative.parts or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        rows.append({"relative_path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return canonical_sha256(rows), len(rows)


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize_project_relative(value: str) -> str:
    text = value.strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or text.startswith("/") or text.startswith("//") or (len(text) >= 2 and text[1] == ":") or ".." in path.parts:
        raise ValueError(f"PROJECT_PATH_NOT_PORTABLE:{value}")
    return path.as_posix()


def resolve_project_file(project: Path, value: str, *, must_exist: bool = True) -> tuple[str, Path]:
    relative = normalize_project_relative(value)
    candidate = (project / Path(relative)).resolve()
    try:
        candidate.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"PROJECT_PATH_ESCAPES_ROOT:{value}") from exc
    if must_exist and not candidate.is_file():
        raise ValueError(f"PROJECT_FILE_MISSING:{relative}")
    return relative, candidate


def load_state(project: Path) -> tuple[Path, dict[str, Any]]:
    state_path = project.resolve() / STATE_NAME
    if not state_path.is_file():
        raise ValueError("PROJECT_STATE_MISSING")
    return state_path, load_json(state_path)


def append_event(state: dict[str, Any], event_type: str, detail: dict[str, Any]) -> None:
    state.setdefault("events", []).append({"at": now(), "type": event_type, "detail": detail})
    state["updated_at"] = now()


def current_session_id(state: dict[str, Any]) -> str:
    session = state.get("active_session")
    if not isinstance(session, dict) or not isinstance(session.get("session_id"), str):
        raise ValueError("START_NEW_EXECUTION_SESSION_FIRST")
    return session["session_id"]


def require_not_p0_blocked(state: dict[str, Any]) -> None:
    if state.get("status") == "BLOCKED_P0":
        raise ValueError("PROJECT_P0_BLOCK_IS_PERSISTED_USE_NEW_PROJECT_REVISION_OR_EXPLICIT_SKILL_INVALIDATION")


def package_resource(package: dict[str, Any], call_kind: str) -> tuple[str, str | None, int | None]:
    if call_kind in {"GRID_BASELINE", "GRID_CORRECTION"}:
        target = package.get("target") if isinstance(package.get("target"), dict) else {}
        resource_id = str(target.get("grid_id", "")).strip()
        segment_id = str(target.get("segment_id", "")).strip() or None
        grid_order = target.get("grid_order")
        if not resource_id or not segment_id or isinstance(grid_order, bool) or not isinstance(grid_order, int):
            raise ValueError("GRID_CALL_RESOURCE_BINDING_INVALID")
        return resource_id, segment_id, grid_order
    if call_kind == "ASSET_UPLOAD":
        resource_id = str(package.get("batch_id", "")).strip()
        segment_id = str(package.get("segment_id", "")).strip() or None
        if not resource_id or not segment_id:
            raise ValueError("ASSET_UPLOAD_RESOURCE_OR_SEGMENT_ID_MISSING")
        return resource_id, segment_id, None
    if call_kind in {"VIDEO_API", "VIDEO_API_RECOVERY"}:
        resource_id = str(package.get("request_id", "")).strip()
        segment_id = str(package.get("segment_id", "")).strip() or None
        if not resource_id or not segment_id:
            raise ValueError("VIDEO_REQUEST_RESOURCE_BINDING_INVALID")
        return resource_id, segment_id, None
    raise ValueError("CALL_KIND_INVALID_OR_PER_CELL_FORBIDDEN")


def passed_qc_for_grid(state: dict[str, Any], grid_id: str) -> bool:
    submission_ids = {
        row.get("submission_id")
        for row in state.get("submissions", [])
        if isinstance(row, dict) and row.get("resource_id") == grid_id and row.get("call_kind") in {"GRID_BASELINE", "GRID_CORRECTION"}
    }
    return any(
        isinstance(row, dict) and row.get("submission_id") in submission_ids and row.get("decision") == "PASSED"
        for row in state.get("qc_records", [])
    )


def imported_anchor_origin_passed(project: Path, state: dict[str, Any]) -> bool:
    """Accept a migrated G01 pilot only from revalidated, read-only evidence."""
    artifacts = state.get("artifacts", {}).get("P6", {})
    anchor_record = artifacts.get("PROJECT_VISUAL_ANCHOR") if isinstance(artifacts, dict) else None
    historical = state.get("historical_revision") if isinstance(state.get("historical_revision"), dict) else {}
    historical_qc = historical.get("anchor_origin_historical_qc") if isinstance(historical.get("anchor_origin_historical_qc"), dict) else {}
    if not isinstance(anchor_record, dict) or anchor_record.get("validation_status") != "VALIDATED":
        return False
    if historical_qc.get("decision") != "PASSED" or historical_qc.get("grid_id") != "G01" or historical_qc.get("grid_order") != 1:
        return False
    try:
        _, anchor_path = resolve_project_file(project, str(anchor_record.get("relative_path", "")))
        if sha256_file(anchor_path) != anchor_record.get("sha256"):
            return False
        qc_relative, qc_path = resolve_project_file(project, str(historical_qc.get("relative_path", "")))
        if sha256_file(qc_path) != historical_qc.get("sha256"):
            return False
        qc = load_json(qc_path)
        promotion = qc.get("reference_promotion") if isinstance(qc.get("reference_promotion"), dict) else {}
        generated = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
        if (
            qc.get("schema_version") != required_core_qc_schema(str(state.get("skill_version", "")))
            or qc.get("decision") != "PASSED"
            or qc.get("grid_id") != "G01"
            or generated.get("relative_path") != historical_qc.get("generated_output_relative_path")
            or generated.get("sha256") != historical_qc.get("generated_output_sha256")
            or any(promotion.get(key) is not True for key in (
                "eligible", "support_topology_passed", "critical_contacts_verifiable", "end_state_safe_for_next_segment"
            ))
        ):
            return False
        if qc_relative != historical_qc.get("relative_path"):
            return False
        from validate_r66_visual_anchor import validate as validate_visual_anchor
        return validate_visual_anchor(project, anchor_path) == []
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def command_init(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    if project.exists() and any(project.iterdir()):
        raise ValueError("PROJECT_DIRECTORY_MUST_BE_NEW_OR_EMPTY")
    project.mkdir(parents=True, exist_ok=True)
    for directory in ["inputs", "reviews", "outputs", *[f"artifacts/P{number}" for number in range(10)]]:
        (project / directory).mkdir(parents=True, exist_ok=True)

    copies = {
        ASSETS / "r62-job-template.json": project / "artifacts/P1/JOB.json",
        ASSETS / "r62-timeline-evidence-template.json": project / "artifacts/P2/TIMELINE_EVIDENCE.json",
        ASSETS / "r62-scene-plan-template.json": project / "artifacts/P4/SCENE_PLAN.json",
        ASSETS / "r62-call-seal-template.json": project / "reviews/P6_CALL_SEAL_TEMPLATE.json",
        ASSETS / "r62-imagegen-capability-template.json": project / "artifacts/P5/IMAGEGEN_CAPABILITY.json",
    }
    for source, destination in copies.items():
        shutil.copy2(source, destination)

    try:
        entry_relative = Path(os.path.relpath(SKILL_ROOT / "SKILL.md", project)).as_posix()
        version_relative = Path(os.path.relpath(SKILL_ROOT / "VERSION", project)).as_posix()
    except ValueError as exc:
        raise ValueError("PROJECT_AND_SKILL_MUST_SHARE_ONE_PORTABLE_BUNDLE_ROOT") from exc

    template = load_json(ASSETS / "r62-project-manifest-template.json")
    created = now()
    template.update({
        "project_id": args.project_id,
        "created_at": created,
        "updated_at": created,
    })
    tree_sha256, tree_file_count = skill_tree_fingerprint()
    template["skill_version"] = CURRENT_SKILL_VERSION
    template["skill"] = {
        "entry_relative_path": entry_relative,
        "entry_sha256": sha256_file(SKILL_ROOT / "SKILL.md"),
        "version_relative_path": version_relative,
        "version_sha256": sha256_file(SKILL_ROOT / "VERSION"),
        "tree_sha256": tree_sha256,
        "tree_file_count": tree_file_count,
    }
    template["events"] = [{"at": created, "type": "PROJECT_INITIALIZED", "detail": {"project_id": args.project_id}}]
    write_json_atomic(project / STATE_NAME, template)
    return {"status": "PASSED", "project_dir": str(project), "state": str(project / STATE_NAME), "next_action": "session-start"}


def command_session_start(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    session = {"session_id": uuid.uuid4().hex, "started_at": now(), "label": args.label}
    state["active_session"] = session
    state["resume_contract"]["provider_call_authorized"] = False
    append_event(state, "EXECUTION_SESSION_STARTED", session)
    write_json_atomic(state_path, state)
    return {"status": "PASSED", "active_session": session, "provider_call_authorized": False}


def command_refresh_skill(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    current_session_id(state)
    if state.get("review_seals") or state.get("approvals") or state.get("submissions"):
        raise ValueError("SKILL_CHANGE_WITH_APPROVAL_OR_SUBMISSION_REQUIRES_NEW_PROJECT_REVISION")
    downstream = [phase for phase, rows in state.get("artifacts", {}).items() if phase != "P1" and rows]
    removed_artifacts: dict[str, list[str]] = {}
    if downstream and args.invalidate_from is None:
        raise ValueError("SKILL_CHANGE_AFTER_P1_REQUIRES_EXPLICIT_INVALIDATION_OR_NEW_PROJECT_REVISION")
    if args.invalidate_from is not None:
        boundary = int(args.invalidate_from[1:])
        if boundary < 2:
            raise ValueError("SKILL_INVALIDATION_MUST_START_AT_P2_OR_LATER")
        for phase in sorted(list(state.get("artifacts", {}))):
            if int(phase[1:]) >= boundary:
                rows = state["artifacts"].pop(phase)
                removed_artifacts[phase] = sorted(rows) if isinstance(rows, dict) else []
        current_number = int(state.get("current_phase", "P0")[1:])
        if current_number >= boundary:
            state["current_phase"] = f"P{boundary - 1}"
        state["status"] = "ACTIVE"
    previous_skill_version = str(state.get("skill_version", ""))
    try:
        entry_relative = Path(os.path.relpath(SKILL_ROOT / "SKILL.md", project)).as_posix()
        version_relative = Path(os.path.relpath(SKILL_ROOT / "VERSION", project)).as_posix()
    except ValueError as exc:
        raise ValueError("PROJECT_AND_SKILL_MUST_SHARE_ONE_PORTABLE_BUNDLE_ROOT") from exc
    state["skill_version"] = CURRENT_SKILL_VERSION
    state["skill"]["entry_relative_path"] = entry_relative
    state["skill"]["entry_sha256"] = sha256_file(SKILL_ROOT / "SKILL.md")
    state["skill"]["version_relative_path"] = version_relative
    state["skill"]["version_sha256"] = sha256_file(SKILL_ROOT / "VERSION")
    tree_sha256, tree_file_count = skill_tree_fingerprint()
    state["skill"]["tree_sha256"] = tree_sha256
    state["skill"]["tree_file_count"] = tree_file_count
    event_type = "DEVELOPMENT_SKILL_MIGRATED_WITH_INVALIDATION" if args.invalidate_from else "DEVELOPMENT_SKILL_BINDING_REFRESHED_BEFORE_P2"
    append_event(state, event_type, {
        "reason": args.reason,
        "previous_skill_version": previous_skill_version,
        "current_skill_version": CURRENT_SKILL_VERSION,
        "invalidate_from": args.invalidate_from,
        "removed_artifacts": removed_artifacts,
    })
    write_json_atomic(state_path, state)
    return {
        "status": "PASSED",
        "previous_skill_version": previous_skill_version,
        "skill_version": CURRENT_SKILL_VERSION,
        "skill_entry_sha256": state["skill"]["entry_sha256"],
        "skill_entry_relative_path": entry_relative,
        "skill_version_relative_path": version_relative,
        "skill_tree_sha256": tree_sha256,
        "invalidate_from": args.invalidate_from,
        "removed_artifacts": removed_artifacts,
        "current_phase": state["current_phase"],
        "reason": args.reason,
    }


def inspect_state(project: Path) -> tuple[list[str], dict[str, Any]]:
    state_path, state = load_state(project)
    issues: list[str] = []
    if state.get("schema_version") != "R6.2-PROJECT-STATE-1.0" or state.get("skill_version") not in SUPPORTED_PROJECT_VERSIONS:
        issues.append("PROJECT_SCHEMA_OR_VERSION_INVALID")
    if state.get("path_policy") != "PROJECT_RELATIVE_ONLY":
        issues.append("PROJECT_PATH_POLICY_INVALID")
    if state.get("current_phase") not in PHASES:
        issues.append("PROJECT_PHASE_INVALID")
    if state.get("status") not in {"ACTIVE", "WAIT_INPUT", "WAIT_REVIEW", "BLOCKED_P0", "COMPLETE"}:
        issues.append("PROJECT_STATUS_INVALID")

    skill = state.get("skill")
    if not isinstance(skill, dict):
        issues.append("SKILL_BINDING_MISSING")
    else:
        for path_key, hash_key in (("entry_relative_path", "entry_sha256"), ("version_relative_path", "version_sha256")):
            value = str(skill.get(path_key, ""))
            if not value or Path(value).is_absolute() or (len(value) >= 2 and value[1] == ":"):
                issues.append(f"SKILL_{path_key.upper()}_NOT_RELATIVE")
                continue
            resolved = (project / value).resolve()
            if not resolved.is_file():
                issues.append(f"SKILL_{path_key.upper()}_MISSING")
            elif sha256_file(resolved) != skill.get(hash_key):
                issues.append(f"SKILL_{hash_key.upper()}_MISMATCH")
        current_tree_sha256, current_tree_file_count = skill_tree_fingerprint()
        if skill.get("tree_sha256") != current_tree_sha256 or skill.get("tree_file_count") != current_tree_file_count:
            issues.append("SKILL_TREE_FINGERPRINT_MISMATCH")

    source = state.get("source_binding")
    if source is not None:
        if not isinstance(source, dict):
            issues.append("SOURCE_BINDING_INVALID")
        else:
            try:
                _, source_path = resolve_project_file(project, str(source.get("relative_path", "")))
                if sha256_file(source_path) != source.get("sha256"):
                    issues.append("SOURCE_BINDING_HASH_MISMATCH")
            except ValueError as exc:
                issues.append(str(exc))

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        issues.append("ARTIFACT_LEDGER_INVALID")
        artifacts = {}
    for phase, rows in artifacts.items():
        if phase not in PHASES or not isinstance(rows, dict):
            issues.append(f"ARTIFACT_PHASE_INVALID:{phase}")
            continue
        for name, row in rows.items():
            if not isinstance(row, dict):
                issues.append(f"ARTIFACT_RECORD_INVALID:{phase}:{name}")
                continue
            try:
                _, path = resolve_project_file(project, str(row.get("relative_path", "")))
                if path.stat().st_size != row.get("bytes") or sha256_file(path) != row.get("sha256"):
                    issues.append(f"ARTIFACT_HASH_OR_SIZE_MISMATCH:{phase}:{name}")
            except ValueError as exc:
                issues.append(f"ARTIFACT:{phase}:{name}:{exc}")

    fact_rows = state.get("accepted_deviation_fact_contracts", [])
    if not isinstance(fact_rows, list):
        issues.append("FACT_CONTRACT_LEDGER_INVALID")
        fact_rows = []
    for index, row in enumerate(fact_rows, start=1):
        if not isinstance(row, dict):
            issues.append(f"FACT_CONTRACT_LEDGER_ROW_INVALID:{index}")
            continue
        try:
            _, contract_path = resolve_project_file(project, str(row.get("relative_path", "")))
            if sha256_file(contract_path) != row.get("sha256"):
                issues.append(f"FACT_CONTRACT_LEDGER_HASH_MISMATCH:{index}")
                continue
            contract = load_json(contract_path)
            issues.extend(f"FACT_CONTRACT:{index}:{item}" for item in validate_fact_contract(project, contract))
        except ValueError as exc:
            issues.append(f"FACT_CONTRACT:{index}:{exc}")

    seals = state.get("review_seals") if isinstance(state.get("review_seals"), list) else []
    approvals = state.get("approvals") if isinstance(state.get("approvals"), list) else []
    submissions = state.get("submissions") if isinstance(state.get("submissions"), list) else []
    qc_records = state.get("qc_records") if isinstance(state.get("qc_records"), list) else []
    if not isinstance(state.get("review_seals"), list) or not isinstance(state.get("approvals"), list) or not isinstance(state.get("submissions"), list) or not isinstance(state.get("qc_records"), list):
        issues.append("REVIEW_APPROVAL_OR_SUBMISSION_LEDGER_INVALID")
    seal_by_hash = {row.get("seal_sha256"): row for row in seals if isinstance(row, dict)}
    approval_by_id = {row.get("approval_id"): row for row in approvals if isinstance(row, dict)}
    if len(seal_by_hash) != len(seals):
        issues.append("DUPLICATE_OR_INVALID_REVIEW_SEAL")
    if len(approval_by_id) != len(approvals):
        issues.append("DUPLICATE_OR_INVALID_APPROVAL_ID")
    consumed_approvals: set[str] = set()
    submission_ids: set[str] = set()
    for submission in submissions:
        if not isinstance(submission, dict):
            issues.append("SUBMISSION_RECORD_INVALID")
            continue
        approval_id = submission.get("approval_id")
        submission_id = submission.get("submission_id")
        if approval_id not in approval_by_id or submission.get("seal_sha256") not in seal_by_hash:
            issues.append("SUBMISSION_WITHOUT_BOUND_APPROVAL_OR_SEAL")
        if approval_id in consumed_approvals:
            issues.append("APPROVAL_CONSUMED_MORE_THAN_ONCE")
        consumed_approvals.add(approval_id)
        if not submission_id or submission_id in submission_ids:
            issues.append("DUPLICATE_OR_INVALID_SUBMISSION_ID")
        submission_ids.add(submission_id)

    baselines = [row for row in submissions if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE"]
    corrections = [row for row in submissions if isinstance(row, dict) and row.get("call_kind") == "GRID_CORRECTION"]
    baseline_resources = [row.get("resource_id") for row in baselines]
    correction_resources = [row.get("resource_id") for row in corrections]
    if any(not value for value in baseline_resources + correction_resources):
        issues.append("GRID_SUBMISSION_RESOURCE_ID_MISSING")
    if len(baseline_resources) != len(set(baseline_resources)):
        issues.append("PER_GRID_BASELINE_BUDGET_EXCEEDED")
    if len(correction_resources) != len(set(correction_resources)):
        issues.append("PER_GRID_CORRECTION_BUDGET_EXCEEDED")
    state_budget = state.get("generation_budget") if isinstance(state.get("generation_budget"), dict) else {}
    max_baselines = state_budget.get("project_max_grid_baselines")
    max_corrections = state_budget.get("project_max_grid_corrections")
    if not isinstance(max_baselines, int) or len(baselines) > max_baselines:
        issues.append("PROJECT_GRID_BASELINE_BUDGET_EXCEEDED")
    if not isinstance(max_corrections, int) or len(corrections) > max_corrections:
        issues.append("PROJECT_GRID_CORRECTION_BUDGET_EXCEEDED")
    if any(row.get("call_kind") == "PER_CELL" for row in submissions if isinstance(row, dict)):
        issues.append("PER_CELL_CALL_FORBIDDEN")

    qc_by_submission: dict[str, dict[str, Any]] = {}
    for record in qc_records:
        if not isinstance(record, dict):
            issues.append("QC_RECORD_INVALID")
            continue
        submission_id = record.get("submission_id")
        if not submission_id or submission_id in qc_by_submission:
            issues.append("DUPLICATE_OR_INVALID_QC_SUBMISSION_ID")
            continue
        qc_by_submission[submission_id] = record
        submission = next((row for row in submissions if isinstance(row, dict) and row.get("submission_id") == submission_id), None)
        if submission is None:
            issues.append("QC_WITHOUT_SUBMISSION")
        try:
            _, qc_path = resolve_project_file(project, str(record.get("qc_relative_path", "")))
            if sha256_file(qc_path) != record.get("qc_sha256"):
                issues.append("QC_HASH_MISMATCH")
        except ValueError as exc:
            issues.append(f"QC:{exc}")
    pending_qc = state.get("pending_qc_submission_id")
    if pending_qc is not None:
        pending_submission = next((row for row in submissions if isinstance(row, dict) and row.get("submission_id") == pending_qc), None)
        if pending_submission is None or pending_submission.get("call_kind") not in {"GRID_BASELINE", "GRID_CORRECTION"}:
            issues.append("PENDING_QC_SUBMISSION_INVALID")
        if pending_qc in qc_by_submission:
            issues.append("PENDING_QC_ALREADY_RECORDED")

    active_session = state.get("active_session")
    active_id = active_session.get("session_id") if isinstance(active_session, dict) else None
    unconsumed_live_approvals = [
        row for row in approvals
        if isinstance(row, dict) and row.get("session_id") == active_id and row.get("approval_id") not in consumed_approvals
    ]
    recovery_contracts = state.get("recovery_contracts") if isinstance(state.get("recovery_contracts"), list) else []
    if state.get("recovery_contracts") is not None and not isinstance(state.get("recovery_contracts"), list):
        issues.append("RECOVERY_CONTRACT_LEDGER_INVALID")
    for contract in recovery_contracts:
        if not isinstance(contract, dict):
            issues.append("RECOVERY_CONTRACT_RECORD_INVALID")
            continue
        matching = [
            row for row in submissions
            if isinstance(row, dict)
            and row.get("call_kind") == "VIDEO_API_RECOVERY"
            and row.get("segment_id") == contract.get("segment_id")
        ]
        if contract.get("maximum_recovery_calls") != 1 or contract.get("recovery_calls_consumed") not in {0, 1}:
            issues.append("RECOVERY_CONTRACT_BUDGET_INVALID")
        if len(matching) != contract.get("recovery_calls_consumed"):
            issues.append("RECOVERY_CONTRACT_LEDGER_COUNT_MISMATCH")
        expected_status = "CONSUMED" if matching else "ELIGIBLE"
        if contract.get("status") != expected_status:
            issues.append("RECOVERY_CONTRACT_STATUS_INVALID")
    summary = {
        "state_path": str(state_path),
        "project_id": state.get("project_id"),
        "current_phase": state.get("current_phase"),
        "project_status": state.get("status"),
        "active_session_id": active_id,
        "provider_call_authorized": bool(unconsumed_live_approvals) and pending_qc is None and not issues,
        "bound_artifacts": sum(len(rows) for rows in artifacts.values() if isinstance(rows, dict)),
        "prepared_artifacts": sum(1 for rows in artifacts.values() if isinstance(rows, dict) for row in rows.values() if isinstance(row, dict) and row.get("validation_status") == "PREPARED"),
        "validated_artifacts": sum(1 for rows in artifacts.values() if isinstance(rows, dict) for row in rows.values() if isinstance(row, dict) and row.get("validation_status") in {"PASSED", "VALIDATED"}),
        "review_seals": len(seals),
        "approvals": len(approvals),
        "submissions": len(submissions),
        "qc_records": len(qc_records),
        "pending_qc_submission_id": pending_qc,
    }
    return sorted(set(issues)), summary


def command_inspect(args: argparse.Namespace) -> dict[str, Any]:
    issues, summary = inspect_state(args.project_dir.resolve())
    return {"status": "PASSED" if not issues else "BLOCKED_P0", "issues": issues, **summary}


def command_lock(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    session_id = current_session_id(state)
    require_not_p0_blocked(state)
    job_rel, job_path = resolve_project_file(project, args.job)
    job = load_json(job_path)
    issues = validate_job(job)
    if issues:
        raise ValueError("JOB_VALIDATION_FAILED:" + ",".join(issues))
    if job.get("project_manifest_path") != STATE_NAME:
        raise ValueError("JOB_PROJECT_MANIFEST_BINDING_INVALID")
    source_rel, source_path = resolve_project_file(project, str(job["source"]["video_path"]))
    source_hash = sha256_file(source_path)
    if source_hash.lower() != str(job["source"]["video_sha256"]).lower():
        raise ValueError("SOURCE_HASH_MISMATCH")
    job_hash = sha256_file(job_path)
    existing = state.get("mode_lock")
    if existing is not None and existing.get("job_sha256") != job_hash:
        raise ValueError("MODE_LOCK_ALREADY_EXISTS_CREATE_NEW_PROJECT_REVISION")
    state["mode_lock"] = {
        "job_relative_path": job_rel,
        "job_sha256": job_hash,
        "route_id": job["route_id"],
        "execution_profile": job["execution_profile"],
        "objective_profile": job["objective_profile"],
        "style_profile": job["style_profile"],
        "visual_plan_mode": job["visual_plan_mode"],
        "provider_adapter_profile": job["provider_adapter_profile"],
        "provider_intent": job["provider_intent"],
        "grid_strategy": job["grid_strategy"],
        "target": job["target"],
        "locked_at": now(),
        "session_id": session_id,
    }
    state["source_binding"] = {"relative_path": source_rel, "sha256": source_hash, "bytes": source_path.stat().st_size}
    state["generation_budget"] = dict(job["generation_budget"])
    state.setdefault("artifacts", {}).setdefault("P1", {})["JOB"] = {
        "relative_path": job_rel,
        "sha256": job_hash,
        "bytes": job_path.stat().st_size,
        "validator": "validate_r62_job.py",
        "validation_status": "PASSED",
        "bound_at": now(),
    }
    state["current_phase"] = "P1"
    state["status"] = "ACTIVE"
    append_event(state, "MODE_AND_SOURCE_LOCKED", {"route_id": job["route_id"], "job_sha256": job_hash})
    write_json_atomic(state_path, state)
    return {"status": "PASSED", "route_id": job["route_id"], "job_sha256": job_hash, "source_sha256": source_hash}


def command_bind(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    session_id = current_session_id(state)
    require_not_p0_blocked(state)
    submitted_seals = {
        row.get("seal_sha256") for row in state.get("submissions", []) if isinstance(row, dict)
    }
    outstanding_seals = [
        row for row in state.get("review_seals", [])
        if isinstance(row, dict)
        and row.get("session_id") == session_id
        and row.get("seal_sha256") not in submitted_seals
    ]
    if state.get("status") == "WAIT_REVIEW" or outstanding_seals:
        raise ValueError("OUTSTANDING_CALL_REVIEW_SEAL_BLOCKS_ARTIFACT_BIND")
    if state.get("resume_contract", {}).get("provider_call_authorized") is True:
        raise ValueError("LIVE_PROVIDER_AUTHORITY_BLOCKS_ARTIFACT_BIND")
    if state.get("mode_lock") is None:
        raise ValueError("LOCK_P1_JOB_BEFORE_BINDING_DOWNSTREAM_ARTIFACTS")
    if args.phase not in PHASES or args.phase == "P0":
        raise ValueError("ARTIFACT_PHASE_INVALID")
    if state.get("pending_qc_submission_id") is not None and int(args.phase[1:]) > 6:
        raise ValueError("P6_QC_MUST_BE_RECORDED_BEFORE_P7_OR_P8")
    relative, path = resolve_project_file(project, args.path)
    row = {
        "relative_path": relative,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "validator": args.validator,
        "validation_status": args.validation_status,
        "bound_at": now(),
    }
    phase_rows = state.setdefault("artifacts", {}).setdefault(args.phase, {})
    existing = phase_rows.get(args.name)
    if existing is not None and existing.get("sha256") != row["sha256"]:
        raise ValueError("ARTIFACT_NAME_ALREADY_BOUND_TO_DIFFERENT_CONTENT")
    phase_rows[args.name] = row
    if int(args.phase[1:]) > int(state.get("current_phase", "P0")[1:]):
        state["current_phase"] = args.phase
    state["status"] = "ACTIVE"
    append_event(state, "ARTIFACT_BOUND", {"phase": args.phase, "name": args.name, "sha256": row["sha256"]})
    write_json_atomic(state_path, state)
    return {"status": "PASSED", "phase": args.phase, "name": args.name, **row}


def command_seal(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    session_id = current_session_id(state)
    require_not_p0_blocked(state)
    if state.get("mode_lock") is None:
        raise ValueError("LOCK_P1_JOB_BEFORE_SEALING_CALL_PACKAGE")
    if args.phase not in {"P6", "P8"}:
        raise ValueError("CALL_SEAL_PHASE_MUST_BE_P6_OR_P8")
    if args.call_kind not in CALL_KINDS:
        raise ValueError("CALL_KIND_INVALID_OR_PER_CELL_FORBIDDEN")
    if state.get("pending_qc_submission_id") is not None:
        raise ValueError("PENDING_P6_QC_BLOCKS_NEW_CALL_SEAL")
    if args.call_kind == "GRID_BASELINE" and args.call_ordinal != 1:
        raise ValueError("BASELINE_CALL_ORDINAL_MUST_BE_1")
    if args.call_kind == "GRID_CORRECTION" and args.call_ordinal != 2:
        raise ValueError("CORRECTION_CALL_ORDINAL_MUST_BE_2")
    if args.call_kind in {"ASSET_UPLOAD", "VIDEO_API"} and args.call_ordinal != 1:
        raise ValueError("P8_REQUEST_CALL_ORDINAL_MUST_BE_1")
    if args.call_kind == "VIDEO_API_RECOVERY" and args.call_ordinal != 2:
        raise ValueError("P8_RECOVERY_CALL_ORDINAL_MUST_BE_2")
    package_rel, package_path = resolve_project_file(project, args.package)
    package = load_json(package_path)
    if package.get("phase") != args.phase:
        raise ValueError("CALL_PACKAGE_PHASE_DIFFERS_FROM_SEAL_COMMAND")
    if package.get("call_kind") != args.call_kind:
        raise ValueError("CALL_PACKAGE_KIND_DIFFERS_FROM_SEAL_COMMAND")
    if package.get("call_ordinal") != args.call_ordinal:
        raise ValueError("CALL_PACKAGE_ORDINAL_DIFFERS_FROM_SEAL_COMMAND")
    resource_id, segment_id, grid_order = package_resource(package, args.call_kind)
    prior_submissions = state.get("submissions", [])
    same_resource = [row for row in prior_submissions if isinstance(row, dict) and row.get("resource_id") == resource_id]
    budget = state.get("generation_budget") if isinstance(state.get("generation_budget"), dict) else {}
    if args.call_kind == "GRID_BASELINE":
        replacement_scope = budget.get("r66_replacement_scope")
        if isinstance(replacement_scope, list) and resource_id not in replacement_scope:
            raise ValueError("GRID_OUTSIDE_R66_REPLACEMENT_SCOPE")
        if any(row.get("call_kind") == "GRID_BASELINE" for row in same_resource):
            raise ValueError("PER_GRID_BASELINE_BUDGET_ALREADY_CONSUMED")
        baseline_total = sum(1 for row in prior_submissions if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE")
        if baseline_total >= int(budget.get("project_max_grid_baselines", 0)):
            raise ValueError("PROJECT_GRID_BASELINE_BUDGET_ALREADY_CONSUMED")
        if grid_order and grid_order > 1 and budget.get("pilot_gate_after_first_grid") is True:
            pilot_grid = next(
                (row.get("resource_id") for row in prior_submissions if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE" and row.get("grid_order") == 1),
                None,
            )
            live_pilot_passed = bool(pilot_grid) and passed_qc_for_grid(state, str(pilot_grid))
            if not live_pilot_passed and not imported_anchor_origin_passed(project, state):
                raise ValueError("PILOT_GRID_QC_MUST_PASS_BEFORE_SCALE")
    if args.call_kind == "GRID_CORRECTION":
        if not any(row.get("call_kind") == "GRID_BASELINE" for row in same_resource):
            raise ValueError("CORRECTION_REQUIRES_CONSUMED_SAME_GRID_BASELINE")
        if any(row.get("call_kind") == "GRID_CORRECTION" for row in same_resource):
            raise ValueError("PER_GRID_CORRECTION_BUDGET_ALREADY_CONSUMED")
        correction_total = sum(1 for row in prior_submissions if isinstance(row, dict) and row.get("call_kind") == "GRID_CORRECTION")
        if correction_total >= int(budget.get("project_max_grid_corrections", 0)):
            raise ValueError("PROJECT_GRID_CORRECTION_BUDGET_ALREADY_CONSUMED")
    if args.phase == "P6":
        from validate_r62_call_package import validate as validate_call_package

        validation_issues = validate_call_package(project, package_path)
        if validation_issues:
            raise ValueError("P6_CALL_PACKAGE_VALIDATION_FAILED:" + ",".join(validation_issues))
    else:
        from validate_r62_p8_package import validate as validate_p8_package

        validation_issues = validate_p8_package(project, package_path, expected_call_kind=args.call_kind)
        if validation_issues:
            raise ValueError("P8_CALL_PACKAGE_VALIDATION_FAILED:" + ",".join(validation_issues))
    package_sha256 = sha256_file(package_path)
    if any(
        isinstance(row, dict)
        and row.get("session_id") == session_id
        and row.get("package_sha256") == package_sha256
        and row.get("call_kind") == args.call_kind
        for row in state.get("review_seals", [])
    ):
        raise ValueError("EXACT_PACKAGE_ALREADY_SEALED_IN_THIS_SESSION")
    record = {
        "phase": args.phase,
        "package_relative_path": package_rel,
        "package_sha256": package_sha256,
        "package_bytes": package_path.stat().st_size,
        "call_kind": args.call_kind,
        "call_ordinal": args.call_ordinal,
        "resource_id": resource_id,
        "segment_id": segment_id,
        "grid_order": grid_order,
        "session_id": session_id,
        "created_at": now(),
        "status": "WAIT_REVIEW",
    }
    record["seal_sha256"] = canonical_sha256(record)
    seals = state.setdefault("review_seals", [])
    if any(row.get("seal_sha256") == record["seal_sha256"] for row in seals):
        raise ValueError("REVIEW_SEAL_ALREADY_EXISTS")
    seals.append(record)
    state["current_phase"] = args.phase
    state["status"] = "WAIT_REVIEW"
    state["resume_contract"]["provider_call_authorized"] = False
    state["resume_contract"]["first_safe_action"] = "AWAIT_EXACT_HUMAN_APPROVAL_FOR_SEALED_CALL_PACKAGE"
    append_event(state, "CALL_PACKAGE_SEALED", {"seal_sha256": record["seal_sha256"], "call_kind": args.call_kind})
    write_json_atomic(state_path, state)
    return {"status": "WAIT_REVIEW", **record}


def command_approve(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    session_id = current_session_id(state)
    require_not_p0_blocked(state)
    if state.get("pending_qc_submission_id") is not None:
        raise ValueError("PENDING_P6_QC_BLOCKS_NEW_APPROVAL")
    if state.get("status") != "WAIT_REVIEW":
        raise ValueError("PROJECT_NOT_WAITING_FOR_REVIEW")
    seals = state.get("review_seals", [])
    seal = next((row for row in seals if row.get("seal_sha256") == args.seal_sha256), None)
    if seal is None:
        raise ValueError("REVIEW_SEAL_NOT_FOUND")
    if seal.get("session_id") != session_id:
        raise ValueError("REVIEW_SEAL_FROM_OLD_SESSION")
    _, package_path = resolve_project_file(project, seal["package_relative_path"])
    if sha256_file(package_path) != seal["package_sha256"]:
        raise ValueError("SEALED_PACKAGE_CHANGED")
    if not args.authority_text.strip():
        raise ValueError("APPROVAL_AUTHORITY_TEXT_MISSING")
    approvals = state.setdefault("approvals", [])
    if any(row.get("approval_id") == args.approval_id for row in approvals):
        raise ValueError("APPROVAL_ID_ALREADY_EXISTS")
    if any(row.get("seal_sha256") == args.seal_sha256 and row.get("session_id") == session_id for row in approvals):
        raise ValueError("SEAL_ALREADY_APPROVED_IN_THIS_SESSION")
    record = {
        "approval_id": args.approval_id,
        "seal_sha256": args.seal_sha256,
        "package_sha256": seal["package_sha256"],
        "call_kind": seal["call_kind"],
        "call_ordinal": seal["call_ordinal"],
        "resource_id": seal["resource_id"],
        "segment_id": seal.get("segment_id"),
        "session_id": session_id,
        "authority_text": args.authority_text,
        "approved_at": now(),
    }
    approvals.append(record)
    state["resume_contract"]["provider_call_authorized"] = True
    state["resume_contract"]["first_safe_action"] = "CONSUME_APPROVED_CALL_ONCE"
    append_event(state, "EXACT_CALL_APPROVED", {"approval_id": args.approval_id, "seal_sha256": args.seal_sha256})
    write_json_atomic(state_path, state)
    return {"status": "APPROVED_ONCE", **record}


def command_consume(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    session_id = current_session_id(state)
    require_not_p0_blocked(state)
    approval = next((row for row in state.get("approvals", []) if row.get("approval_id") == args.approval_id), None)
    if approval is None or approval.get("seal_sha256") != args.seal_sha256:
        raise ValueError("APPROVAL_NOT_BOUND_TO_SEAL")
    if approval.get("session_id") != session_id:
        raise ValueError("APPROVAL_FROM_OLD_SESSION")
    if any(row.get("approval_id") == args.approval_id for row in state.get("submissions", [])):
        raise ValueError("APPROVAL_ALREADY_CONSUMED")
    if any(row.get("submission_id") == args.submission_id for row in state.get("submissions", [])):
        raise ValueError("SUBMISSION_ID_ALREADY_EXISTS")
    seal = next(row for row in state["review_seals"] if row.get("seal_sha256") == args.seal_sha256)
    _, package_path = resolve_project_file(project, seal["package_relative_path"])
    if sha256_file(package_path) != seal["package_sha256"]:
        raise ValueError("SEALED_PACKAGE_CHANGED")
    provider_task_proof: dict[str, Any] | None = None
    provider_task_proof_rel: str | None = None
    provider_task_proof_hash: str | None = None
    if state.get("skill_version") in {"R6.22", "R6.23", "R6.24", "R6.25", "R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"} and seal["call_kind"] in {"VIDEO_API", "VIDEO_API_RECOVERY"}:
        if not getattr(args, "provider_task_proof", None):
            raise ValueError("R622_VIDEO_CONSUME_REQUIRES_PROVIDER_TASK_IDENTITY_PROOF")
        provider_task_proof_rel, provider_task_proof_path = resolve_project_file(project, args.provider_task_proof)
        provider_task_proof = load_json(provider_task_proof_path)
        provider_task_proof_hash = sha256_file(provider_task_proof_path)
        if (
            provider_task_proof.get("schema_version") != "R6.22-P8-PROVIDER-TASK-IDENTITY-1.0"
            or provider_task_proof.get("status") != "PASSED"
            or provider_task_proof.get("task_id") != args.submission_id
            or provider_task_proof.get("seal_sha256") != args.seal_sha256
            or provider_task_proof.get("approval_id") != args.approval_id
            or provider_task_proof.get("package_sha256") != seal["package_sha256"]
            or provider_task_proof.get("call_kind") != seal["call_kind"]
            or provider_task_proof.get("segment_id") != seal.get("segment_id")
            or provider_task_proof.get("resource_id") != seal.get("resource_id")
            or provider_task_proof.get("failed_checks") != []
            or not isinstance(provider_task_proof.get("checks"), dict)
            or not provider_task_proof["checks"]
            or any(value is not True for value in provider_task_proof["checks"].values())
        ):
            raise ValueError("PROVIDER_TASK_IDENTITY_PROOF_BINDING_OR_STATUS_INVALID")
        provider_record_rel, provider_record_path = resolve_project_file(project, str(provider_task_proof.get("provider_record_relative_path", "")))
        if sha256_file(provider_record_path) != provider_task_proof.get("provider_record_sha256"):
            raise ValueError("PROVIDER_TASK_RECORD_CHANGED_AFTER_IDENTITY_PROOF")
        from verify_r622_provider_task_identity import verify as recompute_provider_identity

        recomputed = recompute_provider_identity(
            project,
            package_path,
            provider_record_path,
            args.seal_sha256,
            args.approval_id,
        )
        stable_fields = (
            "status", "project_id", "segment_id", "resource_id", "call_kind", "call_ordinal",
            "task_id", "seal_sha256", "approval_id", "package_relative_path", "package_sha256",
            "provider_record_relative_path", "provider_record_sha256", "provider_create_time",
            "approval_time", "expected", "actual", "checks", "failed_checks", "identity_rule",
        )
        if any(recomputed.get(key) != provider_task_proof.get(key) for key in stable_fields):
            raise ValueError("PROVIDER_TASK_IDENTITY_PROOF_RECOMPUTE_MISMATCH")
    existing = state.get("submissions", [])
    same_resource = [row for row in existing if isinstance(row, dict) and row.get("resource_id") == seal.get("resource_id")]
    budget = state.get("generation_budget") if isinstance(state.get("generation_budget"), dict) else {}
    if seal["call_kind"] == "GRID_BASELINE" and any(row.get("call_kind") == "GRID_BASELINE" for row in same_resource):
        raise ValueError("PER_GRID_BASELINE_BUDGET_EXCEEDED")
    if seal["call_kind"] == "GRID_BASELINE":
        replacement_scope = budget.get("r66_replacement_scope")
        if isinstance(replacement_scope, list) and seal.get("resource_id") not in replacement_scope:
            raise ValueError("GRID_OUTSIDE_R66_REPLACEMENT_SCOPE_AT_CONSUME")
        baseline_total = sum(1 for row in existing if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE")
        if baseline_total >= int(budget.get("project_max_grid_baselines", 0)):
            raise ValueError("PROJECT_GRID_BASELINE_BUDGET_EXCEEDED_AT_CONSUME")
        if seal.get("grid_order") and seal.get("grid_order") > 1 and budget.get("pilot_gate_after_first_grid") is True:
            pilot_grid = next(
                (row.get("resource_id") for row in existing if isinstance(row, dict) and row.get("call_kind") == "GRID_BASELINE" and row.get("grid_order") == 1),
                None,
            )
            live_pilot_passed = bool(pilot_grid) and passed_qc_for_grid(state, str(pilot_grid))
            if not live_pilot_passed and not imported_anchor_origin_passed(project, state):
                raise ValueError("PILOT_GRID_QC_MUST_PASS_AT_CONSUME")
    if seal["call_kind"] == "GRID_CORRECTION" and any(row.get("call_kind") == "GRID_CORRECTION" for row in same_resource):
        raise ValueError("PER_GRID_CORRECTION_BUDGET_EXCEEDED")
    if seal["call_kind"] == "GRID_CORRECTION":
        correction_total = sum(1 for row in existing if isinstance(row, dict) and row.get("call_kind") == "GRID_CORRECTION")
        if correction_total >= int(budget.get("project_max_grid_corrections", 0)):
            raise ValueError("PROJECT_GRID_CORRECTION_BUDGET_EXCEEDED_AT_CONSUME")
    if seal["call_kind"] in {"ASSET_UPLOAD", "VIDEO_API", "VIDEO_API_RECOVERY"} and any(row.get("call_kind") == seal["call_kind"] for row in same_resource):
        raise ValueError("P8_RESOURCE_ALREADY_SUBMITTED")
    if seal["call_kind"] in {"ASSET_UPLOAD", "VIDEO_API", "VIDEO_API_RECOVERY"} and any(
        isinstance(row, dict)
        and row.get("call_kind") == seal["call_kind"]
        and row.get("segment_id") == seal.get("segment_id")
        for row in existing
    ):
        raise ValueError("P8_SEGMENT_CALL_KIND_ALREADY_SUBMITTED")
    if seal["call_kind"] == "VIDEO_API_RECOVERY":
        prior_video = [
            row for row in existing
            if isinstance(row, dict)
            and row.get("call_kind") == "VIDEO_API"
            and row.get("segment_id") == seal.get("segment_id")
        ]
        if len(prior_video) != 1:
            raise ValueError("P8_RECOVERY_REQUIRES_EXACTLY_ONE_PRIOR_VIDEO_SUBMISSION")
    record = {
        "submission_id": args.submission_id,
        "approval_id": args.approval_id,
        "seal_sha256": args.seal_sha256,
        "package_sha256": seal["package_sha256"],
        "call_kind": seal["call_kind"],
        "call_ordinal": seal["call_ordinal"],
        "resource_id": seal["resource_id"],
        "segment_id": seal.get("segment_id"),
        "grid_order": seal.get("grid_order"),
        "session_id": session_id,
        "submitted_at": now(),
    }
    if provider_task_proof is not None:
        record["provider_task_identity_proof_relative_path"] = provider_task_proof_rel
        record["provider_task_identity_proof_sha256"] = provider_task_proof_hash
        record["provider_task_record_relative_path"] = provider_task_proof.get("provider_record_relative_path")
        record["provider_task_record_sha256"] = provider_task_proof.get("provider_record_sha256")
    state.setdefault("submissions", []).append(record)
    if seal["call_kind"] == "VIDEO_API_RECOVERY":
        package = load_json(package_path)
        recovery = package.get("recovery") if isinstance(package.get("recovery"), dict) else {}
        contracts = state.get("recovery_contracts") if isinstance(state.get("recovery_contracts"), list) else []
        eligible = [
            row for row in contracts
            if isinstance(row, dict)
            and row.get("segment_id") == seal.get("segment_id")
            and row.get("prior_submission_id") == recovery.get("prior_submission_id")
            and row.get("status") == "ELIGIBLE"
            and row.get("recovery_calls_consumed") == 0
        ]
        if len(eligible) != 1:
            raise ValueError("P8_RECOVERY_CONTRACT_NOT_ELIGIBLE_AT_CONSUME")
        eligible[0]["status"] = "CONSUMED"
        eligible[0]["recovery_calls_consumed"] = 1
        eligible[0]["recovery_submission_id"] = args.submission_id
        eligible[0]["consumed_at"] = now()
    state["status"] = "ACTIVE"
    state["resume_contract"]["provider_call_authorized"] = False
    if seal["call_kind"] in {"GRID_BASELINE", "GRID_CORRECTION"}:
        state["pending_qc_submission_id"] = args.submission_id
        state["resume_contract"]["first_safe_action"] = "BIND_GENERATED_OUTPUT_AND_RECORD_P6_QC"
    append_event(state, "APPROVAL_CONSUMED_ONCE", {"approval_id": args.approval_id, "submission_id": args.submission_id})
    write_json_atomic(state_path, state)
    return {"status": "CONSUMED", **record}


def command_qc_record(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    current_session_id(state)
    require_not_p0_blocked(state)
    qc_rel, qc_path = resolve_project_file(project, args.qc)
    qc = load_json(qc_path)
    if qc.get("schema_version") not in COMPATIBLE_P6_QC_SCHEMAS:
        raise ValueError("P6_QC_SCHEMA_INVALID")
    submission_id = qc.get("submission_id")
    if submission_id != state.get("pending_qc_submission_id"):
        raise ValueError("P6_QC_DOES_NOT_MATCH_PENDING_SUBMISSION")
    submission = next((row for row in state.get("submissions", []) if row.get("submission_id") == submission_id), None)
    if submission is None or submission.get("call_kind") not in {"GRID_BASELINE", "GRID_CORRECTION"}:
        raise ValueError("P6_QC_SUBMISSION_NOT_FOUND_OR_NOT_GRID_CALL")
    if qc.get("job_id") != state.get("project_id"):
        raise ValueError("P6_QC_PROJECT_ID_MISMATCH")
    if qc.get("grid_id") != submission.get("resource_id"):
        raise ValueError("P6_QC_GRID_ID_MISMATCH")
    if qc.get("segment_id") != submission.get("segment_id"):
        raise ValueError("P6_QC_SEGMENT_ID_MISMATCH")
    if qc.get("seal_sha256") != submission.get("seal_sha256"):
        raise ValueError("P6_QC_SEAL_MISMATCH")
    if qc.get("automatic_retry_allowed") is not False:
        raise ValueError("P6_QC_AUTO_RETRY_MUST_BE_FALSE")
    decision = qc.get("decision")
    if decision not in {"PASSED", "REJECTED"}:
        raise ValueError("P6_QC_DECISION_INVALID")
    global_checks = qc.get("global_checks")
    cells = qc.get("cells")
    if not isinstance(global_checks, dict) or not isinstance(cells, list) or not cells:
        raise ValueError("P6_QC_GLOBAL_OR_CELL_REVIEWS_MISSING")
    required_global_checks = {
        "layout_correct",
        "geometry_contract_satisfied",
        "chronology_correct",
        "segment_content_isolated",
        "intra_grid_continuity",
        "state_ledger_satisfied",
        "topology_valid",
        "unwanted_text_absent",
        "style_contract_satisfied",
    }
    if set(global_checks) != required_global_checks or any(not isinstance(global_checks[key], bool) for key in required_global_checks):
        raise ValueError("P6_QC_GLOBAL_CHECK_SET_OR_TYPE_INVALID")
    seal_record = next((row for row in state.get("review_seals", []) if row.get("seal_sha256") == submission.get("seal_sha256")), None)
    if seal_record is None:
        raise ValueError("P6_QC_SEAL_RECORD_MISSING")
    _, sealed_package_path = resolve_project_file(project, str(seal_record.get("package_relative_path", "")))
    sealed_package = load_json(sealed_package_path)
    layout = sealed_package.get("target", {}).get("layout")
    grid_order = sealed_package.get("target", {}).get("grid_order")
    cross_grid_checks = qc.get("cross_grid_checks")
    required_cross_grid_checks = {
        "project_anchor_identity_match",
        "project_anchor_style_match",
        "project_anchor_environment_match",
        "previous_segment_state_match",
    }
    if not isinstance(cross_grid_checks, dict) or set(cross_grid_checks) != required_cross_grid_checks:
        raise ValueError("P6_QC_CROSS_GRID_CHECK_SET_INVALID")
    allowed_cross_values = {
        "PASSED",
        "FAILED",
        "NOT_APPLICABLE_ANCHOR_ORIGIN",
        "NOT_APPLICABLE_VIDEO_START",
    }
    if any(cross_grid_checks[key] not in allowed_cross_values for key in required_cross_grid_checks):
        raise ValueError("P6_QC_CROSS_GRID_CHECK_VALUE_INVALID")
    if grid_order == 1:
        expected_origin = {
            "project_anchor_identity_match": "NOT_APPLICABLE_ANCHOR_ORIGIN",
            "project_anchor_style_match": "NOT_APPLICABLE_ANCHOR_ORIGIN",
            "project_anchor_environment_match": "NOT_APPLICABLE_ANCHOR_ORIGIN",
            "previous_segment_state_match": "NOT_APPLICABLE_VIDEO_START",
        }
        if cross_grid_checks != expected_origin:
            raise ValueError("P6_QC_ANCHOR_ORIGIN_CROSS_GRID_VALUES_INVALID")
    elif not isinstance(grid_order, int) or any(value != "PASSED" for value in cross_grid_checks.values()):
        if decision == "PASSED":
            raise ValueError("P6_QC_GRID_TWO_PLUS_REQUIRES_ALL_CROSS_GRID_CHECKS_PASSED")

    references = sealed_package.get("reference_roles") if isinstance(sealed_package.get("reference_roles"), list) else []
    anchor_evidence = qc.get("anchor_evidence") if isinstance(qc.get("anchor_evidence"), dict) else {}
    if grid_order == 1:
        if any(anchor_evidence.get(key) for key in anchor_evidence):
            raise ValueError("P6_QC_ANCHOR_ORIGIN_FORBIDS_PRIOR_ANCHOR_EVIDENCE")
    else:
        role_map = {row.get("role"): row for row in references if isinstance(row, dict)}
        anchor_ref = role_map.get("PROJECT_VISUAL_ANCHOR", {})
        previous_ref = role_map.get("PREVIOUS_SEGMENT_END_STATE", {})
        expected_evidence = {
            "project_anchor_relative_path": anchor_ref.get("relative_path"),
            "project_anchor_sha256": anchor_ref.get("sha256"),
            "previous_segment_end_state_relative_path": previous_ref.get("relative_path"),
            "previous_segment_end_state_sha256": previous_ref.get("sha256"),
        }
        if anchor_evidence != expected_evidence:
            raise ValueError("P6_QC_ANCHOR_EVIDENCE_DIFFERS_FROM_SEALED_REFERENCES")
    expected_capacity = {"2x2": 4, "3x3": 9, "4x4": 16, "5x5": 25}.get(layout)
    if expected_capacity is None or len(cells) != expected_capacity:
        raise ValueError("P6_QC_CELL_COUNT_DIFFERS_FROM_SEALED_LAYOUT")
    expected_cell_numbers = list(range(1, expected_capacity + 1))
    if [row.get("cell") for row in cells if isinstance(row, dict)] != expected_cell_numbers:
        raise ValueError("P6_QC_CELL_NUMBERS_NOT_COMPLETE_ROW_MAJOR")
    for row in cells:
        if not isinstance(row, dict) or row.get("result") not in {"PASSED", "REJECTED"}:
            raise ValueError("P6_QC_CELL_RESULT_INVALID")
        codes = row.get("blocking_failure_codes")
        if not isinstance(codes, list) or any(not isinstance(code, str) or not code.strip() for code in codes):
            raise ValueError("P6_QC_CELL_BLOCKING_CODES_INVALID")
        if row.get("result") == "PASSED" and codes:
            raise ValueError("P6_QC_PASSED_CELL_HAS_BLOCKING_CODES")
        if row.get("result") == "REJECTED" and not codes:
            raise ValueError("P6_QC_REJECTED_CELL_MISSING_BLOCKING_CODE")
    blocking_codes = qc.get("blocking_failure_codes")
    if not isinstance(blocking_codes, list):
        raise ValueError("P6_QC_BLOCKING_CODES_INVALID")
    if decision == "PASSED" and blocking_codes:
        raise ValueError("PASSED_P6_QC_CONTAINS_BLOCKING_FAILURES")
    if decision == "REJECTED" and not blocking_codes:
        raise ValueError("REJECTED_P6_QC_REQUIRES_BLOCKING_FAILURE")
    if not isinstance(qc.get("correction_eligible"), bool):
        raise ValueError("P6_QC_CORRECTION_ELIGIBILITY_INVALID")
    if decision == "PASSED" and (not all(global_checks.values()) or any(row.get("result") != "PASSED" for row in cells)):
        raise ValueError("PASSED_P6_QC_CONTAINS_FAILED_GLOBAL_OR_CELL_CHECK")
    if decision == "PASSED" and qc.get("correction_eligible") is not False:
        raise ValueError("PASSED_P6_QC_CANNOT_BE_CORRECTION_ELIGIBLE")
    cross_grid_failed = any(value == "FAILED" for value in cross_grid_checks.values())
    if decision == "REJECTED" and all(global_checks.values()) and all(row.get("result") == "PASSED" for row in cells) and not cross_grid_failed:
        raise ValueError("REJECTED_P6_QC_HAS_NO_FAILED_GLOBAL_OR_CELL_CHECK")
    failure_class = qc.get("failure_class")
    if failure_class not in {"NONE", "UPSTREAM_PLAN", "PROMPT_COMPILER", "REFERENCE_ROLE", "MODEL_RENDERING"}:
        raise ValueError("P6_QC_FAILURE_CLASS_INVALID")
    if decision == "PASSED" and failure_class != "NONE":
        raise ValueError("PASSED_P6_QC_FAILURE_CLASS_MUST_BE_NONE")
    if decision == "REJECTED" and failure_class == "NONE":
        raise ValueError("REJECTED_P6_QC_FAILURE_CLASS_MISSING")
    if failure_class != "MODEL_RENDERING" and qc.get("correction_eligible") is True:
        raise ValueError("ONLY_MODEL_RENDERING_MAY_USE_CORRECTION_BUDGET")
    if submission.get("call_kind") == "GRID_CORRECTION" and qc.get("correction_eligible") is True:
        raise ValueError("FAILED_GRID_CORRECTION_CANNOT_AUTHORIZE_ANOTHER_CORRECTION")

    output = qc.get("generated_output") if isinstance(qc.get("generated_output"), dict) else {}
    output_rel, output_path = resolve_project_file(project, str(output.get("relative_path", "")))
    if sha256_file(output_path) != str(output.get("sha256", "")).lower():
        raise ValueError("P6_QC_OUTPUT_HASH_MISMATCH")
    artifact_record = None
    for rows in state.get("artifacts", {}).values():
        if not isinstance(rows, dict):
            continue
        artifact_record = next((row for row in rows.values() if isinstance(row, dict) and row.get("relative_path") == output_rel), artifact_record)
    if artifact_record is None or artifact_record.get("sha256") != sha256_file(output_path):
        raise ValueError("P6_QC_OUTPUT_NOT_BOUND_IN_ARTIFACT_LEDGER")

    lineage = sealed_package.get("lineage") if isinstance(sealed_package.get("lineage"), dict) else {}
    _, job_path = resolve_project_file(project, str(lineage.get("job_relative_path", "")))
    job = load_json(job_path)
    core_route = job.get("route_id") in {"M2_D_SHARE_FIRST", "M2_F_SOURCE_AUDIO_RESTYLE"}
    required_schema = required_core_qc_schema(str(state.get("skill_version", "")))
    if core_route and qc.get("schema_version") != required_schema:
        raise ValueError("CORE_ROUTE_REQUIRES_CURRENT_SPATIAL_STATE_QC_SCHEMA")
    if core_route:
        _, plan_path = resolve_project_file(project, str(lineage.get("scene_plan_relative_path", "")))
        plan = load_json(plan_path)
        planned_grids = [row for row in plan.get("grids", []) if isinstance(row, dict) and row.get("grid_id") == qc.get("grid_id")]
        if len(planned_grids) != 1:
            raise ValueError("R618_P6_QC_PLANNED_GRID_NOT_UNIQUE")
        planned_cells = planned_grids[0].get("cells") if isinstance(planned_grids[0].get("cells"), list) else []
        spatial_issues: list[str] = []
        for planned_cell, qc_cell in zip(planned_cells, cells, strict=True):
            spatial_issues.extend(validate_qc_cell(planned_cell, qc_cell))
        if spatial_issues:
            raise ValueError("R618_P6_SPATIAL_EVIDENCE_INVALID:" + ",".join(sorted(set(spatial_issues))))
        promotion = qc.get("reference_promotion")
        if not isinstance(promotion, dict):
            raise ValueError("R618_REFERENCE_PROMOTION_EVIDENCE_MISSING")
        required_promotion_keys = {
            "eligible", "support_topology_passed", "critical_contacts_verifiable", "end_state_safe_for_next_segment"
        }
        if set(promotion) != required_promotion_keys or any(not isinstance(promotion[key], bool) for key in required_promotion_keys):
            raise ValueError("R618_REFERENCE_PROMOTION_EVIDENCE_INVALID")
        if decision == "PASSED" and not all(promotion.values()):
            raise ValueError("R618_PASSED_GRID_NOT_SAFE_FOR_REFERENCE_PROMOTION")
        if decision == "REJECTED" and promotion.get("eligible") is True:
            raise ValueError("R618_REJECTED_GRID_CANNOT_BE_REFERENCE_PROMOTED")
    style_id = job.get("style_profile")
    style_registry = load_json(ASSETS / "style-registry.json")
    style = style_registry.get("styles", {}).get(style_id, {})
    color_contract = style.get("color_contract") if isinstance(style, dict) and isinstance(style.get("color_contract"), dict) else {}
    machine_style_qc_required = isinstance(color_contract.get("machine_qc"), dict)
    style_audit_block = qc.get("style_audit") if isinstance(qc.get("style_audit"), dict) else {}
    if machine_style_qc_required:
        style_audit_rel, style_audit_path = resolve_project_file(project, str(style_audit_block.get("relative_path", "")))
        if sha256_file(style_audit_path) != str(style_audit_block.get("sha256", "")).lower():
            raise ValueError("P6_QC_STYLE_AUDIT_HASH_MISMATCH")
        style_audit = load_json(style_audit_path)
        expected_style_schema = (
            "R6.26-STYLE-OUTPUT-AUDIT-1.0"
            if state.get("skill_version") in {"R6.26", "R6.27", "R6.28", "R6.29", "R6.30", "R6.31", "R6.32", "R6.33", "R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40", "R6.41"}
            else "R6.9-STYLE-OUTPUT-AUDIT-1.0"
        )
        if style_audit.get("schema_version") != expected_style_schema or style_audit.get("style_id") != style_id:
            raise ValueError("P6_QC_STYLE_AUDIT_SCHEMA_OR_STYLE_INVALID")
        audit_output = style_audit.get("output") if isinstance(style_audit.get("output"), dict) else {}
        if audit_output.get("relative_path") != output_rel or audit_output.get("sha256") != sha256_file(output_path):
            raise ValueError("P6_QC_STYLE_AUDIT_OUTPUT_BINDING_MISMATCH")
        audit_passed = style_audit.get("decision") == "PASSED"
        if global_checks.get("style_contract_satisfied") is not audit_passed:
            raise ValueError("P6_QC_STYLE_CONTRACT_RESULT_DIFFERS_FROM_MACHINE_AUDIT")
        if isinstance(grid_order, int) and grid_order > 1:
            expected_anchor = next((row for row in references if isinstance(row, dict) and row.get("role") == "PROJECT_VISUAL_ANCHOR"), {})
            audit_anchor = style_audit.get("anchor") if isinstance(style_audit.get("anchor"), dict) else {}
            if audit_anchor.get("relative_path") != expected_anchor.get("relative_path") or audit_anchor.get("sha256") != expected_anchor.get("sha256"):
                raise ValueError("P6_QC_STYLE_AUDIT_ANCHOR_BINDING_MISMATCH")
            expected_cross_style = "PASSED" if audit_passed else "FAILED"
            if cross_grid_checks.get("project_anchor_style_match") != expected_cross_style:
                raise ValueError("P6_QC_CROSS_GRID_STYLE_DIFFERS_FROM_MACHINE_AUDIT")
    elif style_audit_block:
        raise ValueError("P6_QC_UNDECLARED_STYLE_AUDIT")
    artifact_record["validation_status"] = "VALIDATED" if decision == "PASSED" else "PREPARED"

    qc_hash = sha256_file(qc_path)
    if any(row.get("submission_id") == submission_id for row in state.get("qc_records", [])):
        raise ValueError("P6_QC_ALREADY_RECORDED_FOR_SUBMISSION")
    record = {
        "submission_id": submission_id,
        "seal_sha256": submission["seal_sha256"],
        "qc_relative_path": qc_rel,
        "qc_sha256": qc_hash,
        "decision": decision,
        "grid_id": qc.get("grid_id"),
        "segment_id": qc.get("segment_id"),
        "grid_order": submission.get("grid_order"),
        "failure_class": qc.get("failure_class"),
        "correction_eligible": qc.get("correction_eligible"),
        "recorded_at": now(),
    }
    state.setdefault("qc_records", []).append(record)
    state["pending_qc_submission_id"] = None
    if decision == "PASSED":
        state["status"] = "ACTIVE"
        state["resume_contract"]["first_safe_action"] = "PROCEED_TO_P7_VIDEO_PROMPT_PACKAGE"
    elif qc.get("correction_eligible") is True and submission.get("call_kind") == "GRID_BASELINE":
        state["status"] = "ACTIVE"
        state["resume_contract"]["first_safe_action"] = "PREPARE_ONE_CONSOLIDATED_CORRECTION_PACKAGE"
    else:
        state["status"] = "BLOCKED_P0"
        state["resume_contract"]["first_safe_action"] = "REPORT_DEGRADATION_ROUTE_WITHOUT_NEW_IMAGEGEN_CALL"
    append_event(state, "P6_QC_RECORDED", {"submission_id": submission_id, "decision": decision, "qc_sha256": qc_hash})
    write_json_atomic(state_path, state)
    return {"status": state["status"], **record, "next_action": state["resume_contract"]["first_safe_action"]}


def command_qc_amend(args: argparse.Namespace) -> dict[str, Any]:
    """Persist one explicit human acceptance of a narrowly waivable P6 rendering mark."""
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    current_session_id(state)
    blocked_degradation = state.get("status") == "BLOCKED_P0"
    if blocked_degradation:
        allowed_actions = {
            "REPORT_DEGRADATION_ROUTE_WITHOUT_NEW_IMAGEGEN_CALL",
            "APPLY_APPROVED_ZERO_CALL_DEGRADATION",
        }
        if (
            state.get("skill_version") != "R6.40"
            or state.get("resume_contract", {}).get("first_safe_action") not in allowed_actions
        ):
            raise ValueError("BLOCKED_PROJECT_NOT_ELIGIBLE_FOR_R640_ZERO_CALL_DEGRADATION")
    else:
        require_not_p0_blocked(state)
    if state.get("pending_qc_submission_id") is not None:
        raise ValueError("PENDING_P6_QC_BLOCKS_HUMAN_AMENDMENT")
    if state.get("resume_contract", {}).get("provider_call_authorized") is not False:
        raise ValueError("LIVE_PROVIDER_AUTHORITY_BLOCKS_HUMAN_AMENDMENT")

    rejected_rel, rejected_path = resolve_project_file(project, args.rejected_qc)
    waiver_rel, waiver_path = resolve_project_file(project, args.waiver)
    amended_rel, amended_path = resolve_project_file(project, args.amended_qc, must_exist=False)
    if amended_path.exists():
        raise ValueError("AMENDED_QC_OUTPUT_ALREADY_EXISTS")
    rejected = load_json(rejected_path)
    waiver = load_json(waiver_path)
    rejected_hash = sha256_file(rejected_path)
    waiver_hash = sha256_file(waiver_path)

    records = [
        row for row in state.get("qc_records", [])
        if isinstance(row, dict) and row.get("qc_sha256") == rejected_hash and row.get("decision") == "REJECTED"
    ]
    if len(records) != 1:
        raise ValueError("HUMAN_AMENDMENT_REQUIRES_ONE_RECORDED_REJECTED_QC")
    record = records[0]
    blocking_codes = rejected.get("blocking_failure_codes")
    waiver_policy = human_qc_waiver_policy(blocking_codes) if isinstance(blocking_codes, list) else None
    if (
        rejected.get("failure_class") != "MODEL_RENDERING"
        or not isinstance(blocking_codes, list)
        or not blocking_codes
        or waiver_policy is None
    ):
        raise ValueError("P6_FAILURE_NOT_HUMAN_WAIVABLE")
    degradation_evidence: dict[str, Any] | None = None
    if blocked_degradation:
        if waiver_policy != "UNWANTED_MARK":
            raise ValueError("R640_BLOCKED_DEGRADATION_ONLY_ACCEPTS_DECORATIVE_MARKS")
        baseline_submission = next(
            (
                row for row in state.get("submissions", [])
                if isinstance(row, dict)
                and row.get("submission_id") == record.get("submission_id")
                and row.get("resource_id") == rejected.get("grid_id")
                and row.get("call_kind") == "GRID_BASELINE"
            ),
            None,
        )
        correction_submissions = [
            row for row in state.get("submissions", [])
            if isinstance(row, dict)
            and row.get("resource_id") == rejected.get("grid_id")
            and row.get("call_kind") == "GRID_CORRECTION"
        ]
        if baseline_submission is None or len(correction_submissions) != 1:
            raise ValueError("R640_DEGRADATION_REQUIRES_ONE_BASELINE_AND_ONE_EXHAUSTED_CORRECTION")
        correction_submission = correction_submissions[0]
        correction_records = [
            row for row in state.get("qc_records", [])
            if isinstance(row, dict)
            and row.get("submission_id") == correction_submission.get("submission_id")
            and row.get("grid_id") == rejected.get("grid_id")
            and row.get("decision") == "REJECTED"
            and row.get("correction_eligible") is False
        ]
        passed_records = [
            row for row in state.get("qc_records", [])
            if isinstance(row, dict)
            and row.get("grid_id") == rejected.get("grid_id")
            and row.get("decision") == "PASSED"
        ]
        if len(correction_records) != 1 or passed_records:
            raise ValueError("R640_DEGRADATION_REQUIRES_ONE_FAILED_CORRECTION_AND_NO_EXISTING_PASS")
        degradation_evidence = {
            "mode": "POST_CORRECTION_ZERO_CALL_BASELINE_ACCEPTANCE",
            "baseline_submission_id": baseline_submission.get("submission_id"),
            "correction_submission_id": correction_submission.get("submission_id"),
            "correction_qc_relative_path": correction_records[0].get("qc_relative_path"),
            "correction_qc_sha256": correction_records[0].get("qc_sha256"),
            "provider_calls_added": 0,
        }
    if waiver_policy == "NONCAUSAL_MICROSTATE_COMPRESSION":
        if rejected.get("correction_eligible") is not False:
            raise ValueError("MICROSTATE_COMPRESSION_ACCEPTANCE_REQUIRES_EXHAUSTED_CORRECTION_ROUTE")
    elif rejected.get("correction_eligible") is not True:
        raise ValueError("P6_FAILURE_NOT_HUMAN_WAIVABLE")
    failed_globals = {
        key for key, value in rejected.get("global_checks", {}).items()
        if value is not True
    }
    allowed_failed_globals = {
        "UNWANTED_MARK": {"unwanted_text_absent"},
        "NONCAUSAL_COUNT_DRIFT": {"state_ledger_satisfied"},
        "NONCAUSAL_MICROSTATE_COMPRESSION": {"chronology_correct", "state_ledger_satisfied"},
    }[waiver_policy]
    if waiver_policy == "UNWANTED_MARK":
        if not failed_globals.issubset(allowed_failed_globals):
            raise ValueError("HUMAN_AMENDMENT_HAS_NONWAIVABLE_GLOBAL_FAILURE")
    elif failed_globals != allowed_failed_globals:
        raise ValueError("HUMAN_AMENDMENT_HAS_NONWAIVABLE_GLOBAL_FAILURE")
    if any(value == "FAILED" for value in rejected.get("cross_grid_checks", {}).values()):
        raise ValueError("HUMAN_AMENDMENT_FORBIDS_CROSS_GRID_FAILURE")
    rejected_cells = [row for row in rejected.get("cells", []) if isinstance(row, dict) and row.get("result") == "REJECTED"]
    if not rejected_cells or any(
        not set(row.get("blocking_failure_codes", [])).issubset(set(blocking_codes)) for row in rejected_cells
    ):
        raise ValueError("HUMAN_AMENDMENT_CELL_SCOPE_INVALID")
    promotion = rejected.get("reference_promotion") if isinstance(rejected.get("reference_promotion"), dict) else {}
    if any(promotion.get(key) is not True for key in (
        "support_topology_passed", "critical_contacts_verifiable"
    )):
        raise ValueError("HUMAN_AMENDMENT_REQUIRES_SAFE_REFERENCE_PROMOTION_EVIDENCE")
    final_cell = max((row.get("cell", 0) for row in rejected.get("cells", []) if isinstance(row, dict)), default=0)
    rejected_cell_numbers = {
        row.get("cell") for row in rejected_cells if isinstance(row.get("cell"), int)
    }
    if waiver_policy == "NONCAUSAL_COUNT_DRIFT" and final_cell in rejected_cell_numbers:
        raise ValueError("COUNT_DRIFT_IN_FINAL_CELL_CANNOT_BE_WAIVED_FOR_CONTINUATION")
    if waiver_policy == "NONCAUSAL_MICROSTATE_COMPRESSION":
        if len(rejected_cell_numbers) != 1 or final_cell in rejected_cell_numbers:
            raise ValueError("MICROSTATE_COMPRESSION_MUST_BE_ONE_NONFINAL_CELL")
        evidence = waiver.get("microstate_evidence")
        required_evidence = {
            "full_action_contract_unchanged": True,
            "occlusion_action_visible": True,
            "reveal_action_visible": True,
            "required_hold_or_abstention_visible": True,
            "terminal_outcome_after_condition": True,
            "terminal_state_correct": True,
            "identity_topology_and_counts_unchanged": True,
        }
        if not isinstance(evidence, dict) or any(evidence.get(key) is not value for key, value in required_evidence.items()):
            raise ValueError("MICROSTATE_COMPRESSION_BROAD_SEQUENCE_EVIDENCE_INVALID")
        accepted_cells = evidence.get("accepted_cell_numbers")
        if not isinstance(accepted_cells, list) or set(accepted_cells) != rejected_cell_numbers:
            raise ValueError("MICROSTATE_COMPRESSION_CELL_BINDING_INVALID")
        if not str(evidence.get("visible_deviation", "")).strip():
            raise ValueError("MICROSTATE_COMPRESSION_VISIBLE_DEVIATION_MISSING")
    if waiver_policy == "UNWANTED_MARK" and promotion.get("end_state_safe_for_next_segment") is not True:
        structural_checks = {
            "layout_correct", "geometry_contract_satisfied", "chronology_correct",
            "segment_content_isolated", "intra_grid_continuity", "state_ledger_satisfied",
            "topology_valid", "style_contract_satisfied",
        }
        if not blocked_degradation or any(rejected.get("global_checks", {}).get(key) is not True for key in structural_checks):
            raise ValueError("HUMAN_AMENDMENT_REQUIRES_SAFE_REFERENCE_PROMOTION_EVIDENCE")

    fact_bearing_waiver = state.get("skill_version") in {"R6.34", "R6.35", "R6.36", "R6.37", "R6.38", "R6.39", "R6.40"} and waiver_policy == "NONCAUSAL_COUNT_DRIFT"
    deviation_facts = parse_count_deviations(blocking_codes) if fact_bearing_waiver else []
    deviation_issues = validate_waiver_deviations(waiver, blocking_codes) if fact_bearing_waiver else []
    if deviation_issues:
        raise ValueError("HUMAN_QC_WAIVER_FACT_CONTRACT_INVALID:" + ",".join(deviation_issues))

    required_waiver = {
        "schema_version": "R6.20-P6-HUMAN-QC-WAIVER-1.0",
        "project_id": state.get("project_id"),
        "grid_id": rejected.get("grid_id"),
        "segment_id": rejected.get("segment_id"),
        "rejected_qc_relative_path": rejected_rel,
        "rejected_qc_sha256": rejected_hash,
        "accepted_failure_codes": blocking_codes,
        "decision": "ACCEPT_AS_NON_BLOCKING",
        "scope": "DOWNSTREAM_FACT_PROPAGATION" if fact_bearing_waiver else "CURRENT_GRID_ONLY",
        "automatic_retry_allowed": False,
    }
    if any(waiver.get(key) != value for key, value in required_waiver.items()):
        raise ValueError("HUMAN_QC_WAIVER_BINDING_OR_SCOPE_INVALID")
    authority_text = str(waiver.get("human_authority_text", "")).strip()
    if not authority_text:
        raise ValueError("HUMAN_QC_WAIVER_AUTHORITY_TEXT_MISSING")

    amended = copy.deepcopy(rejected)
    for key in allowed_failed_globals:
        amended["global_checks"][key] = True
    for cell in amended["cells"]:
        if cell.get("result") == "REJECTED":
            cell["result"] = "PASSED"
            cell["blocking_failure_codes"] = []
    amended["blocking_failure_codes"] = []
    amended["failure_class"] = "NONE"
    amended["decision"] = "PASSED"
    amended["correction_eligible"] = False
    amended["automatic_retry_allowed"] = False
    amended["reference_promotion"]["eligible"] = True
    if waiver_policy in {"UNWANTED_MARK", "NONCAUSAL_COUNT_DRIFT"}:
        amended["reference_promotion"]["end_state_safe_for_next_segment"] = True
    amended["human_qc_amendment"] = {
        "decision": "ACCEPTED_NON_BLOCKING",
        "accepted_failure_codes": blocking_codes,
        "original_qc_relative_path": rejected_rel,
        "original_qc_sha256": rejected_hash,
        "waiver_relative_path": waiver_rel,
        "waiver_sha256": waiver_hash,
        "visible_deviation_preserved": True,
        "waiver_policy": waiver_policy,
        "accepted_deviations": deviation_facts,
        "propagation_policy": "PROPAGATE_OBSERVED_FACT_DOWNSTREAM" if fact_bearing_waiver else "CURRENT_GRID_ONLY",
    }
    if waiver_policy == "NONCAUSAL_MICROSTATE_COMPRESSION":
        amended["human_qc_amendment"]["microstate_evidence"] = copy.deepcopy(waiver["microstate_evidence"])
    if not all(amended["global_checks"].values()) or any(row.get("result") != "PASSED" for row in amended["cells"]):
        raise ValueError("AMENDED_QC_STILL_CONTAINS_FAILED_CHECK")
    write_json_atomic(amended_path, amended)
    amended_hash = sha256_file(amended_path)

    fact_contract_record: dict[str, Any] | None = None
    if fact_bearing_waiver:
        job_path, plan_path, prior_fact_lineage = resolve_effective_inputs(project, state)
        job = load_json(job_path)
        plan = load_json(plan_path)
        effective_job, effective_plan = apply_count_facts(
            job,
            plan,
            source_grid_id=str(rejected.get("grid_id", "")),
            facts=deviation_facts,
        )
        state_flow_issues = validate_segment_state_flow(effective_plan)
        if state_flow_issues:
            raise ValueError("R634_ACCEPTED_FACT_CANNOT_BYPASS_STATE_FLOW_GATE:" + ",".join(state_flow_issues))
        effective_job_rel = normalize_project_relative(
            f"artifacts/P6/R634_EFFECTIVE_JOB_AFTER_{rejected.get('grid_id')}.json"
        )
        effective_plan_rel = normalize_project_relative(
            f"artifacts/P6/R634_EFFECTIVE_SCENE_PLAN_AFTER_{rejected.get('grid_id')}.json"
        )
        fact_contract_rel = normalize_project_relative(
            f"artifacts/P6/{rejected.get('grid_id')}_ACCEPTED_DEVIATION_FACT_CONTRACT_R634.json"
        )
        effective_job_path = project / effective_job_rel
        effective_plan_path = project / effective_plan_rel
        fact_contract_path = project / fact_contract_rel
        if any(path.exists() for path in (effective_job_path, effective_plan_path, fact_contract_path)):
            raise ValueError("R634_EFFECTIVE_FACT_OUTPUT_ALREADY_EXISTS")
        write_json_atomic(effective_job_path, effective_job)
        write_json_atomic(effective_plan_path, effective_plan)
        contract = build_fact_contract(
            project_id=str(state.get("project_id", "")),
            grid_id=str(rejected.get("grid_id", "")),
            segment_id=str(rejected.get("segment_id", "")),
            facts=deviation_facts,
            rejected_qc={"relative_path": rejected_rel, "sha256": rejected_hash},
            waiver={"relative_path": waiver_rel, "sha256": waiver_hash},
            amended_qc={"relative_path": amended_rel, "sha256": amended_hash},
            base_job={"relative_path": normalize_project_relative(job_path.relative_to(project).as_posix()), "sha256": sha256_file(job_path)},
            base_plan={"relative_path": normalize_project_relative(plan_path.relative_to(project).as_posix()), "sha256": sha256_file(plan_path)},
            effective_job={"relative_path": effective_job_rel, "sha256": sha256_file(effective_job_path)},
            effective_plan={"relative_path": effective_plan_rel, "sha256": sha256_file(effective_plan_path)},
        )
        contract["prior_fact_contracts"] = prior_fact_lineage
        write_json_atomic(fact_contract_path, contract)
        fact_contract_record = {
            "grid_id": rejected.get("grid_id"),
            "segment_id": rejected.get("segment_id"),
            "relative_path": fact_contract_rel,
            "sha256": sha256_file(fact_contract_path),
            "effective_job_relative_path": effective_job_rel,
            "effective_job_sha256": sha256_file(effective_job_path),
            "effective_scene_plan_relative_path": effective_plan_rel,
            "effective_scene_plan_sha256": sha256_file(effective_plan_path),
        }

    output = rejected.get("generated_output") if isinstance(rejected.get("generated_output"), dict) else {}
    output_relative = str(output.get("relative_path", ""))
    output_hash = str(output.get("sha256", ""))
    matching_artifacts = [
        row for rows in state.get("artifacts", {}).values() if isinstance(rows, dict)
        for row in rows.values() if isinstance(row, dict)
        and row.get("relative_path") == output_relative and row.get("sha256") == output_hash
    ]
    if len(matching_artifacts) != 1:
        raise ValueError("HUMAN_AMENDMENT_OUTPUT_ARTIFACT_NOT_UNIQUE")
    matching_artifacts[0]["validation_status"] = "VALIDATED"
    matching_artifacts[0]["validator"] = "r62_project.py:qc-amend"

    record.update({
        "qc_relative_path": amended_rel,
        "qc_sha256": amended_hash,
        "decision": "PASSED",
        "failure_class": "NONE",
        "correction_eligible": False,
        "original_qc_relative_path": rejected_rel,
        "original_qc_sha256": rejected_hash,
        "human_waiver_relative_path": waiver_rel,
        "human_waiver_sha256": waiver_hash,
        "amended_at": now(),
    })
    waiver_record = {
        "grid_id": rejected.get("grid_id"),
        "segment_id": rejected.get("segment_id"),
        "submission_id": rejected.get("submission_id"),
        "accepted_failure_codes": blocking_codes,
        "original_qc_relative_path": rejected_rel,
        "original_qc_sha256": rejected_hash,
        "amended_qc_relative_path": amended_rel,
        "amended_qc_sha256": amended_hash,
        "waiver_relative_path": waiver_rel,
        "waiver_sha256": waiver_hash,
        "accepted_at": now(),
        "fact_contract": fact_contract_record,
        "zero_call_degradation": degradation_evidence,
    }
    state.setdefault("qc_waivers", []).append(waiver_record)
    if fact_contract_record is not None:
        state.setdefault("accepted_deviation_fact_contracts", []).append({
            "relative_path": fact_contract_record["relative_path"],
            "sha256": fact_contract_record["sha256"],
        })
        p6_artifacts = state.setdefault("artifacts", {}).setdefault("P6", {})
        for name, relative, path in (
            (f"{rejected.get('grid_id')}_ACCEPTED_DEVIATION_FACT_CONTRACT_R634", fact_contract_record["relative_path"], project / fact_contract_record["relative_path"]),
            (f"R634_EFFECTIVE_JOB_AFTER_{rejected.get('grid_id')}", fact_contract_record["effective_job_relative_path"], project / fact_contract_record["effective_job_relative_path"]),
            (f"R634_EFFECTIVE_SCENE_PLAN_AFTER_{rejected.get('grid_id')}", fact_contract_record["effective_scene_plan_relative_path"], project / fact_contract_record["effective_scene_plan_relative_path"]),
        ):
            p6_artifacts[name] = {
                "relative_path": relative,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "validator": "r634_integrity_contract.py",
                "validation_status": "VALIDATED",
            }
    state["status"] = "ACTIVE"
    state["current_phase"] = "P6"
    state["resume_contract"]["provider_call_authorized"] = False
    state["resume_contract"]["first_safe_action"] = (
        "RECOMPILE_DOWNSTREAM_P5_FROM_EFFECTIVE_FACTS"
        if fact_contract_record is not None
        else (
            "PROCEED_TO_P7_VIDEO_PROMPT_PACKAGE"
            if waiver_policy == "NONCAUSAL_MICROSTATE_COMPRESSION"
            else "PROMOTE_G01_ANCHOR_AND_PREPARE_NEXT_GRID"
        )
    )
    append_event(state, "P6_QC_HUMAN_AMENDED", waiver_record)
    write_json_atomic(state_path, state)
    return {
        "status": "PASSED",
        "decision": "ACCEPTED_NON_BLOCKING",
        "grid_id": rejected.get("grid_id"),
        "accepted_failure_codes": blocking_codes,
        "amended_qc_relative_path": amended_rel,
        "amended_qc_sha256": amended_hash,
        "waiver_sha256": waiver_hash,
        "accepted_deviation_fact_contract": fact_contract_record,
        "next_action": state["resume_contract"]["first_safe_action"],
    }


def command_block(args: argparse.Namespace) -> dict[str, Any]:
    """Persist a verified P0 blocker so a resumed Codex cannot continue by accident."""
    project = args.project_dir.resolve()
    state_path, state = load_state(project)
    current_session_id(state)
    if state.get("status") == "BLOCKED_P0":
        raise ValueError("PROJECT_ALREADY_BLOCKED_P0")
    if args.phase not in {f"P{number}" for number in range(1, 9)}:
        raise ValueError("BLOCK_PHASE_INVALID")
    evidence_rel, evidence_path = resolve_project_file(project, args.evidence)
    evidence_sha256 = sha256_file(evidence_path)
    if not args.reason_code.strip():
        raise ValueError("BLOCK_REASON_CODE_MISSING")
    if state.get("pending_qc_submission_id") is not None:
        raise ValueError("PENDING_P6_QC_MUST_BE_RECORDED_BEFORE_BLOCK")
    record = {
        "phase": args.phase,
        "reason_code": args.reason_code.strip(),
        "evidence_relative_path": evidence_rel,
        "evidence_sha256": evidence_sha256,
        "blocked_at": now(),
    }
    state["current_phase"] = args.phase
    state["status"] = "BLOCKED_P0"
    state["resume_contract"]["provider_call_authorized"] = False
    state["resume_contract"]["first_safe_action"] = "RESOLVE_RECORDED_P0_BLOCK_OR_CHANGE_GEOMETRY_CONTRACT"
    append_event(state, "P0_BLOCK_RECORDED", record)
    write_json_atomic(state_path, state)
    return {"status": "BLOCKED_P0", **record}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--project-dir", required=True, type=Path)
    init.add_argument("--project-id", required=True)
    init.set_defaults(handler=command_init)
    session = sub.add_parser("session-start")
    session.add_argument("--project-dir", required=True, type=Path)
    session.add_argument("--label", default="CODEX_SESSION")
    session.set_defaults(handler=command_session_start)
    refresh = sub.add_parser("refresh-skill")
    refresh.add_argument("--project-dir", required=True, type=Path)
    refresh.add_argument("--reason", required=True)
    refresh.add_argument("--invalidate-from", choices=[f"P{number}" for number in range(2, 10)])
    refresh.set_defaults(handler=command_refresh_skill)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--project-dir", required=True, type=Path)
    inspect.set_defaults(handler=command_inspect)
    lock = sub.add_parser("lock")
    lock.add_argument("--project-dir", required=True, type=Path)
    lock.add_argument("--job", required=True)
    lock.set_defaults(handler=command_lock)
    bind = sub.add_parser("bind")
    bind.add_argument("--project-dir", required=True, type=Path)
    bind.add_argument("--phase", required=True)
    bind.add_argument("--name", required=True)
    bind.add_argument("--path", required=True)
    bind.add_argument("--validator", required=True)
    bind.add_argument("--validation-status", choices=["PREPARED", "VALIDATED"], default="VALIDATED")
    bind.set_defaults(handler=command_bind)
    seal = sub.add_parser("seal")
    seal.add_argument("--project-dir", required=True, type=Path)
    seal.add_argument("--phase", required=True)
    seal.add_argument("--package", required=True)
    seal.add_argument("--call-kind", required=True)
    seal.add_argument("--call-ordinal", required=True, type=int)
    seal.set_defaults(handler=command_seal)
    approve = sub.add_parser("approve")
    approve.add_argument("--project-dir", required=True, type=Path)
    approve.add_argument("--seal-sha256", required=True)
    approve.add_argument("--approval-id", required=True)
    approve.add_argument("--authority-text", required=True)
    approve.set_defaults(handler=command_approve)
    consume = sub.add_parser("consume")
    consume.add_argument("--project-dir", required=True, type=Path)
    consume.add_argument("--seal-sha256", required=True)
    consume.add_argument("--approval-id", required=True)
    consume.add_argument("--submission-id", required=True)
    consume.add_argument("--provider-task-proof")
    consume.set_defaults(handler=command_consume)
    qc = sub.add_parser("qc-record")
    qc.add_argument("--project-dir", required=True, type=Path)
    qc.add_argument("--qc", required=True)
    qc.set_defaults(handler=command_qc_record)
    amend = sub.add_parser("qc-amend")
    amend.add_argument("--project-dir", required=True, type=Path)
    amend.add_argument("--rejected-qc", required=True)
    amend.add_argument("--waiver", required=True)
    amend.add_argument("--amended-qc", required=True)
    amend.set_defaults(handler=command_qc_amend)
    block = sub.add_parser("block")
    block.add_argument("--project-dir", required=True, type=Path)
    block.add_argument("--phase", required=True)
    block.add_argument("--reason-code", required=True)
    block.add_argument("--evidence", required=True)
    block.set_defaults(handler=command_block)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "inspect":
            result = args.handler(args)
        else:
            with exclusive_project_lock(args.project_dir):
                result = args.handler(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_P0", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"BLOCKED_P0", "FAILED"} else 1


if __name__ == "__main__":
    sys.exit(main())
