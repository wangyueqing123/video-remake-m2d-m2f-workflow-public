#!/usr/bin/env python3
"""Zero-provider-call behavioral checks for the D/F-only distribution."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compile_r62_grid_prompt
import r635_source_content_lock
import r637_content_lineage
import validate_distribution
import validate_r619_state_contract
import validate_r619_visual_core
import validate_r62_call_package
import validate_r62_job
import validate_r62_scene_plan
import validate_r62_timeline_evidence


ROOT = Path(__file__).resolve().parents[1]
D = "M2_D_SHARE_FIRST"
F = "M2_F_SOURCE_AUDIO_RESTYLE"
STYLES = {
    "DOG_HIGH_SHARE_MONO_COMIC",
    "DOG_STYLE_C_GHIBLI_PET_NARRATIVE",
    "DOG_STYLE_D_INDOOR_CARE_KEYFRAME",
    "DOG_STYLE_E_REACTION_RESONANCE",
    "CUSTOM_NAMED_STYLE",
}


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main() -> int:
    checks: list[tuple[str, bool]] = []
    checks.append(("job_routes_exactly_df", set(validate_r62_job.ROUTES) == {D, F}))
    checks.append(("timeline_routes_exactly_df", set(validate_r62_timeline_evidence.AUTHORITY_BY_ROUTE) == {D, F}))
    checks.append(("scene_routes_exactly_df", validate_r62_scene_plan.COMPLETE_SCENE_ROUTES == {D, F}))
    checks.append(("call_routes_exactly_df", validate_r62_call_package.CREATIVE_ROUTES == {D, F}))
    checks.append(("visual_core_routes_exactly_df", validate_r619_visual_core.CORE_ROUTES == {D, F}))
    checks.append(("visual_core_loads", validate_r619_visual_core.load_core().get("core_id") == "DOG_DF_VISUAL_CORE_V3"))
    checks.append(("state_routes_exactly_df", validate_r619_state_contract.CORE_ROUTES == {D, F}))
    checks.append(("content_lock_d_only", r635_source_content_lock.ROUTES == {D}))
    checks.append(("content_lineage_d_only", r637_content_lineage.CONTENT_LINEAGE_ROUTES == {D}))
    checks.append(("d_prompt_available", "转发优先" in compile_r62_grid_prompt.route_instruction(D)))
    checks.append(("f_prompt_available", "原声时间轴" in compile_r62_grid_prompt.route_instruction(F)))
    try:
        compile_r62_grid_prompt.route_instruction("UNSUPPORTED_ROUTE")
    except KeyError:
        checks.append(("unsupported_prompt_route_blocked", True))
    else:
        checks.append(("unsupported_prompt_route_blocked", False))

    release = load("RELEASE.json")
    styles = load("assets/style-registry.json")
    profiles = load("assets/creative-profile-registry.json")
    imagegen = load("assets/imagegen-capability-registry.json")
    core_path = ROOT / "assets/shared-visual-production-core.json"
    core_hash = hashlib.sha256(core_path.read_bytes()).hexdigest()

    checks.append(("release_routes_exactly_df", release.get("supported_routes") == [D, F]))
    checks.append(("style_registry_exact", set(styles.get("styles", {})) == STYLES))
    checks.append(("default_style_mono", styles.get("default_style") == "DOG_HIGH_SHARE_MONO_COMIC"))
    checks.append(("profile_distribution_scope_df", profiles.get("distribution_scope") == [D, F]))
    for profile_id in ("DOG_HIGH_SHARE_HEAT_V1", "DOG_SOURCE_AUDIO_RESTYLE_V1", "PROJECT_DEFINED_DF_V1"):
        profile = profiles["profiles"][profile_id]
        checks.append((f"{profile_id}_styles", set(profile.get("allowed_style_profiles", [])) == STYLES))
        checks.append((f"{profile_id}_core_hash", profile["visual_production_core"]["sha256"] == core_hash))

    builtin = imagegen["profiles"]["CODEX_BUILT_IN_IMAGEGEN_PROMPT_ONLY"]
    checks.append(("builtin_flexible", builtin.get("geometry_enforcement") == "FLEXIBLE_REFERENCE"))
    checks.append(("builtin_exact_unavailable_not_blocking", builtin.get("exact_geometry_unavailable_is_blocking") is False))

    failed = [name for name, passed in checks if not passed]
    distribution_status = validate_distribution.main()
    result = {
        "status": "PASSED" if not failed and distribution_status == 0 else "BLOCKED_P0",
        "checks_passed": len(checks) - len(failed),
        "checks_total": len(checks),
        "failed": failed,
        "provider_calls": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
