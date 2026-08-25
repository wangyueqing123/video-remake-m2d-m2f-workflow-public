#!/usr/bin/env python3
"""Compile one approved R6.6 grid into model-readable Chinese instructions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from validate_r62_job import load_json, resolve_creative_profile, validate_job
from validate_r62_scene_plan import validate_scene_plan
from validate_r619_state_contract import (
    causal_prompt_clauses,
    compile_state_table,
    semantic_prompt_audit,
    state_cell_visual_clause,
    state_transition_contrast_clauses,
)
from validate_r619_visual_core import spatial_prompt_lines
from r639_keyframe_contract import prompt_proof as r639_prompt_proof, uses_r639_contract
from r641_expression_contract import uses_r641_contract
from validate_r62_timeline_evidence import canonical_fingerprint


# Adaptive grids need proportional space. Truncation is forbidden because it
# could silently remove a final state, topology lock, or reference role.
#
# R6.21 compiles stable identity, interaction phase, mutable state and
# support/path/contact proofs into every prompt. A baseline
# must therefore leave deterministic room for the single permitted
# consolidated correction instead of consuming the entire provider budget.
# A real, previously accepted 2x2 production prompt is over 5,000
# characters. Keep the proven 6,600-character baseline capacity and reserve
# correction space on top of it; this is an internal safety budget, not a
# claimed provider limit.
MAX_CHARS = {"2x2": 8200, "3x3": 9500, "4x4": 13000, "5x5": 17500}
CORRECTION_HEADROOM_CHARS = 1600
MACHINE_TOKENS = ("sha256", "schema_version", "state=", "status=", "package_hash", "|", "{", "}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    text = text.replace("|", "，").replace("{", "（").replace("}", "）")
    return re.sub(r"\s+", " ", text)


def requires_previous_end_reference(
    anchor_required: bool,
    grid_order: int,
    anchor_contract: dict[str, Any],
    segment: dict[str, Any],
) -> bool:
    """Require mutable end-state inheritance only across a continuous boundary."""
    entry_transition = segment.get("entry_transition") if isinstance(segment.get("entry_transition"), dict) else {}
    return (
        anchor_required
        and grid_order > 1
        and anchor_contract.get("previous_segment_end_state_required") is True
        and entry_transition.get("kind") == "CONTINUOUS"
    )


def join_text(values: Any, default: str = "无") -> str:
    if not isinstance(values, list):
        return default
    cleaned = [clean(value) for value in values if clean(value)]
    return "；".join(cleaned) if cleaned else default


def sentence(value: Any) -> str:
    """Return text without duplicate terminal punctuation for fixed clauses."""
    return clean(value).rstrip("。！？；，,.!?; ")


def route_instruction(route: str) -> str:
    return {
        "M2_D_SHARE_FIRST": "路线权限：转发优先二创。依据已批准的高转发叙事与新场景蓝图，让观众快速看懂处境、行动、反转结果和转发价值；不照搬源宫格。",
        "M2_F_SOURCE_AUDIO_RESTYLE": "路线权限：原声时间轴锁定的大场景视觉二创。原 MP3、逐字文案与时间码不可改变；源视频只锁定大场景、动作主体、动作对象、动作路径、因果顺序和可见结果。人物、动物、环境、构图和镜头按批准风格重构；禁止把源视频像素、构图、镜头、切点或关键帧作为生图参考。",
    }[route]


def phase_label(value: str) -> str:
    return {
        "BEFORE_ACTION": "动作前",
        "ACTION_SETUP": "动作准备",
        "ACTION": "动作发生",
        "AFTER_ACTION": "动作后",
        "STATIC_RESULT": "静态结果",
        "CUT_PRE": "硬切前",
        "CUT_POST": "硬切后",
    }[value]


def interaction_label(value: str) -> str:
    return {
        "BEFORE_CONTACT": "接触前，禁止提前发生接触或物体转移",
        "CONTACT": "本格发生明确接触",
        "AFTER_CONTACT": "接触后，不得回退到接触前状态",
        "NOT_APPLICABLE": "本格无接触事件",
    }.get(value, "交互阶段未定义")


def compile_prompt(
    job: dict[str, Any],
    evidence: dict[str, Any],
    plan: dict[str, Any],
    registry: dict[str, Any],
    grid_id: str | None,
) -> tuple[str, dict[str, Any]]:
    issues = sorted(set(validate_job(job) + validate_scene_plan(job, evidence, plan)))
    if issues:
        raise ValueError("validation failed: " + ", ".join(issues))

    grids = plan["grids"]
    if grid_id is None:
        if len(grids) != 1:
            raise ValueError("multiple grids require --grid-id")
        grid = grids[0]
    else:
        matches = [item for item in grids if item.get("grid_id") == grid_id]
        if len(matches) != 1:
            raise ValueError(f"unknown or duplicate grid id: {grid_id}")
        grid = matches[0]

    styles = registry.get("styles") if isinstance(registry, dict) else None
    if not isinstance(styles, dict):
        raise ValueError("style registry is invalid")
    style_id = job["style_profile"]
    style = styles.get(style_id)
    if not isinstance(style, dict):
        raise ValueError(f"style not found in registry: {style_id}")
    color_contract = style.get("color_contract") if isinstance(style.get("color_contract"), dict) else {}

    segment_matches = [row for row in plan.get("video_segments", []) if row.get("segment_id") == grid.get("segment_id")]
    if len(segment_matches) != 1:
        raise ValueError("grid must bind exactly one video segment")
    segment = segment_matches[0]
    layout = grid["layout"]
    geometry = job["grid_geometry_contract"]
    canvas_aspect = clean(geometry["canvas_aspect_ratio"])
    cell_aspect = clean(geometry["cell_aspect_ratio"])
    geometry_enforcement = geometry["enforcement"]
    identity = job["project_identity"]
    state_contract = job.get("visual_state_contract") if isinstance(job.get("visual_state_contract"), dict) else {}
    stable_props = state_contract.get("stable_prop_descriptions") if isinstance(state_contract.get("stable_prop_descriptions"), list) else identity.get("props")
    creative_profile, _ = resolve_creative_profile(job)
    creative_binding = job["creative_profile_binding"]
    content = job["content_contract"]
    anchor_contract = job["visual_anchor_contract"]
    grid_order = grid["grid_order"]

    geometry_sentence = (
        "比例为硬性像素合同，不得改成近似比例。"
        if geometry_enforcement == "EXACT_PIXELS"
        else "比例用于参考构图，视频输出比例由后续视频段合同锁定。"
    )
    lines = []
    if color_contract.get("mode") == "CLOSED_MONO_WITH_NAMED_ACCENTS":
        lines.append(
            "最高优先级色彩合同：这是封闭配色，不是建议。整张图除项目身份明确列出的局部强调色外，"
            "人物皮肤、头发、服装、墙面、地面、桌面、植物、画框、狗垫、盘子和光影只能使用黑、白、中性灰。"
            "“温暖”只表示情绪友善，绝不允许暖色调、肤色铺色、棕色、米黄色或普通全彩日系动漫。"
            "若动作描述、参考图解释或其它修饰词与本合同冲突，以本合同为准。"
        )
    lines.extend([
        f"生成一张只服务于视频段 {clean(segment['segment_id'])} 的完整 {layout} 动作宫格图。整张画布比例为 {canvas_aspect}，每格比例为 {cell_aspect}。所有格子等大、边界整齐，按从左到右、从上到下表示当前完整动作场景的连续时间；不要裁掉动作主体。{geometry_sentence}最终只输出一张干净宫格图，不输出说明文字、标题、字幕、水印、编号或额外版式。",
        route_instruction(job["route_id"]),
        "画面风格：" + clean(style.get("prompt_core")),
        "项目视觉身份：人物为" + clean(identity.get("person"))
        + "；动物为" + clean(identity.get("animal"))
        + "；核心环境为" + clean(identity.get("environment"))
        + "；关键道具稳定身份为" + join_text(stable_props)
        + "；允许强调色为" + join_text(identity.get("accent_colors"), "沿用风格默认配色") + "。",
    ])
    hierarchy = creative_profile.get("visual_hierarchy") if isinstance(creative_profile.get("visual_hierarchy"), dict) else {}
    if hierarchy.get("primary_subject") == "DOG" and hierarchy.get("supporting_subject") == "OWNER":
        lines.append("创作身份档案：狗狗始终是画面第一主体；主人只辅助叙事，不得抢狗狗主体。")

    anchor_origin = grid_order == anchor_contract.get("anchor_grid_order")
    anchor_required = anchor_contract.get("required") is True
    project_anchor_expected = anchor_required and grid_order >= anchor_contract.get("project_anchor_required_from_grid_order", 2)
    entry_transition = segment.get("entry_transition") if isinstance(segment.get("entry_transition"), dict) else {}
    previous_end_expected = requires_previous_end_reference(anchor_required, grid_order, anchor_contract, segment)
    if anchor_required:
        if anchor_origin:
            lines.append(
                "参考角色：本宫格是 PROJECT_VISUAL_ANCHOR 的候选来源。人物、动物、画风和核心环境必须在本宫格内部稳定；只有整张图通过 P6 审核后，才能成为后续宫格的项目视觉锚点。"
            )
        else:
            lines.append(
                "参考图1是 PROJECT_VISUAL_ANCHOR：只用于锁定同一个人物、同一只动物、同一画风和同一核心环境，不得借它改写当前段动作。"
            )
            if previous_end_expected:
                lines.append(
                    "参考图2是 PREVIOUS_SEGMENT_END_STATE：只用于锁定本段第1格的连续入口状态。本段第1格必须延续其中的主体身份、数量、可见性、相对位置和物体状态，再按当前段蓝图推进。"
                )
            elif entry_transition.get("kind") == "HARD_CUT":
                lines.append(
                    "本段从明确硬切后的新回合开始：只继承参考图1中的人物、动物、画风和核心环境；可变物体状态必须完全服从本段第1格状态表，不得从上一段末态复制。"
                )

    lines.append(
        "内容边界：全片主题为“" + clean(content.get("topic")) + "”，目标观众为" + clean(job["target"]["audience"])
        + "。本宫格只能表达当前视频段，不得提前或回放其他视频段。当前段必须覆盖：" + join_text(segment.get("content_obligations")) + "。"
    )
    lines.append(
        "逐格隔离规则：每格只画该格指定的唯一时刻；不得把前态、动作过程和后态叠进同一格。硬状态账本高于概括描述，任何后续结果不得提前出现。"
    )
    state_table = compile_state_table(job, grid)
    if state_table:
        lines.append(
            "逐格可视状态证据是最高优先级画面合同：每格的可见性、唯一数量与位置必须全部直接画出；"
            "场景概括、终态描述或动作修饰词不得覆盖当前格证据。"
        )
        lines.append(state_table)
        if uses_r641_contract(plan):
            lines.append(
                "R6.41 override: prioritize clear expression of the main action and causal order. For covered, partly visible, "
                "or transitional objects, preserve the state-ledger truth without forcing full exposure or an exact visual count. "
                "This rule overrides any earlier generic instruction to expose every object. Exact visual counting is required "
                "only in an unobstructed result frame whose contract explicitly requires it."
            )
        transition_contrasts = state_transition_contrast_clauses(job, grid)
        if transition_contrasts:
            lines.append("相邻格状态边界：" + "。".join(transition_contrasts) + "。")
    causal_clauses = causal_prompt_clauses(grid)
    if causal_clauses:
        lines.append("因果顺序证明：" + "。".join(causal_clauses) + "。")

    action_nodes = segment.get("action_nodes") if isinstance(segment.get("action_nodes"), list) else []
    if action_nodes:
        segment_start = float(segment["target_start_s"])
        node_lines = []
        for node in action_nodes:
            relative_start = round(float(node["start_s"]) - segment_start, 2)
            relative_end = round(float(node["end_s"]) - segment_start, 2)
            node_lines.append(
                f"{relative_start:.2f}至{relative_end:.2f}秒，{sentence(node.get('action'))}；到该节点末尾必须看见{sentence(node.get('visible_state_at_end'))}"
            )
        lines.append(
            "动作时间路径：" + "。".join(node_lines) + "。所有动作必须在本段创意截止时间内完成，不得延迟终态，也不得借用下一段时间。"
        )

    scene_map = {scene["scene_id"]: scene for scene in plan["scenes"]}
    ledger = grid["state_ledger"]
    ledger_rows = {row["cell"]: row for row in ledger["cell_states"]}
    completion_pair_count = 0
    emitted_cells: list[int] = []
    for scene_index, scene_id in enumerate(segment["scene_ids"], start=1):
        scene = scene_map[scene_id]
        scene_cells = [cell for cell in grid["cells"] if cell["scene_id"] == scene_id]
        if not scene_cells:
            continue
        cell_numbers = "、".join(str(cell["cell"]) for cell in scene_cells)
        lines.append(
            f"大动作场景 {scene_index}，仅对应第 {cell_numbers} 格。场景位置：{sentence(scene.get('setting'))}。"
            f"完整动作路径：{sentence(scene.get('large_action'))}。完成后可见结果：{sentence(scene.get('visible_result'))}。"
            "这段说明只定义时间推进，不授权任何格提前显示后续状态。"
        )
        if job["route_id"] == "M2_F_SOURCE_AUDIO_RESTYLE":
            inherited = scene.get("inherited_action_contracts") if isinstance(scene.get("inherited_action_contracts"), list) else []
            contract_lines = []
            for contract in inherited:
                if not isinstance(contract, dict):
                    continue
                contract_lines.append(
                    "动作主体是" + sentence(contract.get("action_subject"))
                    + "；动作对象是" + sentence(contract.get("action_object"))
                    + "；动作路径是" + sentence(contract.get("action_path"))
                    + "；因果顺序是" + sentence(contract.get("causal_order"))
                    + "；最终必须看见" + sentence(contract.get("visible_result"))
                    + "；不得替换为" + join_text(contract.get("forbidden_substitutions"))
                )
            if not contract_lines:
                raise ValueError("M2-F source macro-scene action contract missing")
            lines.append("源视频大场景语义锁：" + "。".join(contract_lines) + "。只继承这些动作语义，不复刻源像素或镜头。")
        scene_forbidden = join_text(scene.get("forbidden_alternatives"))
        if scene_forbidden != "无":
            lines.append("本场景统一禁止：" + scene_forbidden + "。")
        spatial_lines = spatial_prompt_lines(plan, scene, scene_cells[0])
        lines.append("\n".join(spatial_lines[:-1]))
        for cell in scene_cells:
            emitted_cells.append(cell["cell"])
            ledger_row = ledger_rows[cell["cell"]]
            hard_locks = join_text([
                state_cell_visual_clause(
                    next(
                        (
                            entity for entity in ledger.get("tracked_entities", [])
                            if isinstance(entity, dict) and entity.get("entity_id") == state.get("entity_id")
                        ),
                        {},
                    ),
                    state,
                )
                for state in ledger_row["states"] if isinstance(state, dict)
            ])
            terminal_proof_text = ""
            if clean(cell.get("completion_action")) and clean(cell.get("visible_end_state")):
                completion_pair_count += 1
                terminal_proof_text = (
                    "完成动作：" + sentence(cell.get("completion_action"))
                    + "；终态画面：" + sentence(cell.get("visible_end_state")) + "。"
                )
            actors = join_text(scene.get("actors"))
            result_clause = (
                "本格必须清楚显示本场完成结果：" + clean(scene.get("visible_result")) + "。"
                if cell.get("result_visible") is True
                else "本格不是结果格，不得提前显示本场后续结果。"
            )
            interaction_text = (
                "本格为指定接触瞬间"
                if uses_r639_contract(plan) and cell.get("keyframe_contract", {}).get("snapshot_type") == "CONTACT"
                else "本格为状态快照，不重演此前接触"
                if uses_r639_contract(plan)
                else interaction_label(cell.get("interaction_phase"))
            )
            lines.append(
                f"第 {cell['cell']} 格，{phase_label(cell['temporal_phase'])}。交互阶段：{interaction_text}。主体：{actors}。"
                f"画面时刻：{sentence(cell.get('visual_statement'))}。镜头：{sentence(cell.get('camera'))}。"
                f"硬状态锁：{hard_locks}。{terminal_proof_text}{result_clause}"
            )
            lines.append(spatial_prompt_lines(plan, scene, cell)[-1])

    if emitted_cells != [cell["cell"] for cell in grid["cells"]]:
        raise ValueError("scene/cell order is not contiguous row-major")

    common_negative = [
        "任何文字、字幕、标题、水印、编号、图标或界面元素",
        "人物、动物、服装、饰品、毛色、环境或道具跨格漂移",
        "主体复制、额外人物、额外动物、额外道具或道具无因消失",
        "身体穿过实体、错误遮挡、悬浮、断肢、多余肢体或接触关系错误",
        "把后续结果提前画进前一格，或把多个时刻拼进同一格",
    ]
    all_negative = list(style.get("negatives", [])) + common_negative + list(content.get("forbidden_claims", []))
    lines.extend([
        "跨格连续性：" + join_text(style.get("continuity")) + "。除明确动作变化外，人物脸型、发型、体型、服装，动物体型、毛色、饰物，环境几何和核心道具必须保持一致。",
        "全图禁止：" + join_text(all_negative) + "。",
    ])

    prompt = "\n\n".join(line for line in lines if clean(line)) + "\n"
    required_profile_literals = creative_profile.get("prompt_required_literals") if isinstance(creative_profile.get("prompt_required_literals"), list) else []
    missing_profile_literals = [literal for literal in required_profile_literals if isinstance(literal, str) and literal not in prompt]
    if missing_profile_literals:
        raise ValueError("creative profile literals missing from prompt: " + ", ".join(missing_profile_literals))
    lowered = prompt.lower()
    leaked = [token for token in MACHINE_TOKENS if token in lowered]
    if leaked:
        raise ValueError("machine syntax leaked into prompt: " + ", ".join(leaked))
    if project_anchor_expected and "PROJECT_VISUAL_ANCHOR" not in prompt:
        raise ValueError("project visual anchor semantics missing from compiled prompt")
    if previous_end_expected and "PREVIOUS_SEGMENT_END_STATE" not in prompt:
        raise ValueError("previous segment end-state semantics missing from compiled prompt")
    if not all(token in prompt for token in ("逐格隔离规则", "硬状态锁", "全图禁止")):
        raise ValueError("model-readable semantic sections missing from compiled prompt")
    state_semantic_audit = semantic_prompt_audit(job, grid, prompt)
    if state_semantic_audit.get("status") != "PASSED":
        raise ValueError(
            "semantic state compilation failed: " + ", ".join(state_semantic_audit.get("issues", []))
        )
    keyframe_prompt_proof = r639_prompt_proof(grid, prompt) if uses_r639_contract(plan) else None
    if keyframe_prompt_proof is not None and keyframe_prompt_proof.get("status") != "PASSED":
        raise ValueError("R639 keyframe snapshot instructions missing from compiled prompt")
    max_chars = MAX_CHARS[layout]
    baseline_chars = max_chars - CORRECTION_HEADROOM_CHARS
    if len(prompt) > baseline_chars:
        raise ValueError(
            f"compiled baseline prompt has {len(prompt)} characters; {layout} baseline budget is "
            f"{baseline_chars} after reserving {CORRECTION_HEADROOM_CHARS} characters for one correction"
        )

    audit = {
        "schema_version": "R6.6-COMPILED-PROMPT-AUDIT-1.0",
        "job_id": job["job_id"],
        "route_id": job["route_id"],
        "style_profile": style_id,
        "creative_profile_binding": creative_binding,
        "creative_profile_required_literals": required_profile_literals,
        "creative_profile_literal_check": "PASSED",
        "grid_id": grid["grid_id"],
        "grid_order": grid_order,
        "segment_id": segment["segment_id"],
        "segment_scene_ids": segment["scene_ids"],
        "segment_grid_isolation": "PASSED",
        "layout": layout,
        "timeline_evidence_fingerprint": canonical_fingerprint(evidence),
        "character_count": len(prompt),
        "maximum_character_budget": max_chars,
        "baseline_character_budget": baseline_chars,
        "reserved_correction_headroom": CORRECTION_HEADROOM_CHARS,
        "cell_count": len(grid["cells"]),
        "cell_state_isolation": state_semantic_audit["status"],
        "scene_cell_interleaving": "PASSED",
        "hard_state_ledger_compiled": state_semantic_audit["status"],
        "state_semantic_audit": state_semantic_audit,
        "r641_core_expression_contract_compiled": uses_r641_contract(plan),
        "keyframe_snapshot_prompt_proof": keyframe_prompt_proof,
        "terminal_proof_contract": {
            "completion_pair_count": completion_pair_count,
            "status": "PASSED",
        },
        "tracked_entity_count": len(ledger["tracked_entities"]),
        "geometry_enforcement": geometry_enforcement,
        "canvas_aspect_ratio": canvas_aspect,
        "cell_aspect_ratio": cell_aspect,
        "model_readable_language": "zh-CN",
        "semantic_sections": ["output_spec", "route_authority", "stable_visual_identity", "reference_roles", "segment_scope", "copy_driven_action_deadline", "scene_action_path", "spatial_feasibility", "single_mutable_state_table", "adjacent_state_contrasts", "ordered_causal_proofs", "per_cell_state_proof", "per_cell_support_proof", "global_negatives"],
        "m2f_source_macro_scene_contract_compiled": job["route_id"] != "M2_F_SOURCE_AUDIO_RESTYLE" or "源视频大场景语义锁" in prompt,
        "copy_driven_action_node_count": len(action_nodes),
        "machine_syntax_leaks": [],
        "visual_anchor_contract": {
            "required": anchor_required,
            "anchor_origin": anchor_origin,
            "project_anchor_role_expected": project_anchor_expected,
            "project_anchor_role_compiled": "PROJECT_VISUAL_ANCHOR" in prompt,
            "previous_end_state_role_expected": previous_end_expected,
            "previous_end_state_role_compiled": "PREVIOUS_SEGMENT_END_STATE" in prompt,
        },
        "style_color_contract": {
            "mode": color_contract.get("mode", "OPEN_STYLE"),
            "compiled_as_highest_priority": color_contract.get("mode") == "CLOSED_MONO_WITH_NAMED_ACCENTS",
            "machine_qc_required": isinstance(color_contract.get("machine_qc"), dict),
            "declared_accents": identity.get("accent_colors", []),
            "declared_accents_compiled": all(
                isinstance(value, str) and value in prompt
                for value in identity.get("accent_colors", [])
            ) if isinstance(identity.get("accent_colors"), list) else False,
        },
        "status": "PASSED",
    }
    return prompt, audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--styles", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--grid-id")
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args()
    try:
        job = load_json(args.job)
        evidence = load_json(args.evidence)
        plan = load_json(args.plan)
        registry = load_json(args.styles)
        prompt, audit = compile_prompt(job, evidence, plan, registry, args.grid_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(prompt.encode("utf-8"))
        audit.update({
            "job_sha256": sha256_file(args.job),
            "timeline_evidence_file_sha256": sha256_file(args.evidence),
            "scene_plan_sha256": sha256_file(args.plan),
            "style_registry_sha256": sha256_file(args.styles),
            "prompt_sha256": sha256_file(args.output),
        })
        audit_path = args.audit_output or args.output.with_suffix(args.output.suffix + ".audit.json")
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "PASSED", "output": str(args.output), "audit": str(audit_path), "characters": audit["character_count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
