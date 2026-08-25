#!/usr/bin/env python3
"""Reconcile derived runtime audio policy to immutable P1 without changing route or media."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--receipt", default="artifacts/P7/R619_RUNTIME_AUDIO_POLICY_RECONCILIATION.json")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    state_path = project / "R62_PROJECT.json"
    job_path = project / "artifacts" / "P1" / "JOB.json"
    receipt_path = project / args.receipt
    state = load(state_path)
    job = load(job_path)
    locked = state.get("mode_lock") if isinstance(state.get("mode_lock"), dict) else {}
    target = job.get("target") if isinstance(job.get("target"), dict) else {}
    if locked.get("job_sha256") != sha256(job_path):
        raise SystemExit("P1_JOB_HASH_DIFFERS_FROM_MODE_LOCK")
    if locked.get("route_id") != job.get("route_id"):
        raise SystemExit("P1_ROUTE_DIFFERS_FROM_MODE_LOCK")
    audio_variant = target.get("audio_variant")
    if audio_variant == "SOURCE_AUDIO_REUSE":
        expected = {
            "audio_pipeline": "SOURCE_AUDIO_REUSE",
            "video_model_speech": "FORBIDDEN",
            "generated_audio": "MUTE",
            "audio_excluded_from_visual_score": True,
            "visual_acceptance_threshold": 80,
            "final_master_acceptance_threshold": 85,
        }
    elif audio_variant == "POST_DUB_NARRATION":
        expected = {
            "audio_pipeline": "POST_DUB_NARRATION",
            "video_model_speech": "FORBIDDEN",
            "generated_audio": "IGNORE_OR_REPLACE",
            "audio_excluded_from_visual_score": True,
            "visual_acceptance_threshold": 80,
            "final_master_acceptance_threshold": 85,
        }
    else:
        raise SystemExit(f"UNSUPPORTED_AUDIO_VARIANT:{audio_variant}")
    before = state.get("runtime_policy") if isinstance(state.get("runtime_policy"), dict) else {}
    if before == expected:
        result = "ALREADY_CONSISTENT"
    else:
        result = "RECONCILED"
    receipt = {
        "schema_version": "R6.19-RUNTIME-AUDIO-POLICY-RECONCILIATION-1.0",
        "status": "PASSED",
        "result": result,
        "project_id": state.get("project_id"),
        "route_id": job.get("route_id"),
        "job_relative_path": "artifacts/P1/JOB.json",
        "job_sha256": sha256(job_path),
        "locked_audio_variant": audio_variant,
        "locked_timing_authority": target.get("timing_authority"),
        "before_runtime_policy": before,
        "after_runtime_policy": expected,
        "external_provider_calls_added": 0,
        "p1_through_p6_artifacts_changed": False,
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(receipt_path, receipt)
    state["runtime_policy"] = expected
    state["runtime_policy_reconciliation"] = {
        "relative_path": args.receipt.replace("\\", "/"),
        "sha256": sha256(receipt_path),
        "job_sha256": sha256(job_path),
    }
    atomic_json(state_path, state)
    print(json.dumps({"status": "PASSED", "result": result, "receipt": args.receipt}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
