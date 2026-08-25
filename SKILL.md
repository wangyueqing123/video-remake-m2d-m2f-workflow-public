---
name: video-remake-m2d-m2f
description: "Run the portable M2-D share-first or M2-F source-audio-restyle video workflow from source evidence through ImageGen grids, Grok/KIE video segments, and JianYing/CapCut drafts. Use when Codex must create a share-oriented narrated remake or preserve the original MP3 and verbatim copy while replacing only the visuals. Supports a default monochrome dog-comic style plus explicitly selected alternative or custom styles."
---

# M2-D / M2-F Video Production

Use repository artifacts and validators as authority. Do not reconstruct rules from chat memory.

## Entry gate

1. Run `python -X utf8 -B scripts/validate_distribution.py` from the repository root.
2. Stop at `BLOCKED_P0` if validation fails.
3. Select exactly one supported route:
   - `M2_D_SHARE_FIRST`
   - `M2_F_SOURCE_AUDIO_RESTYLE`
4. Reject every other route. Do not relabel an unsupported request as D or F.
5. Create a new project revision. Never reuse P3-P9 packages, approvals, uploads or provider tasks from another route, version, style or timing authority.

## Route selection

Use `M2_D_SHARE_FIRST` when the user permits a faithful share-oriented rewrite and new visuals. Preserve source language, every required semantic unit, facts, conditions, causal order and conclusion. Use post-dub narration; final measured narration is the timing authority.

Use `M2_F_SOURCE_AUDIO_RESTYLE` when the original MP3 and verbatim transcript must remain unchanged. Preserve the full source audio at 1.0 speed with no trimming, translation, rewriting or TTS. Use the source video's macro-action semantics only; source pixels and keyframes are not ImageGen authority.

Read `references/m2-d-share-first.md` for D or `references/m2-f-source-audio-restyle.md` for F. Then read `references/phase-and-gates.md`, `references/style-selection.md`, `references/scene-slice-and-grid.md`, `references/provider-adapters.md`, and `references/portable-project-contract.md`.

## Style axis

Style is independent of route, copy, audio and timing.

- Default: `DOG_HIGH_SHARE_MONO_COMIC`.
- Alternatives: `DOG_STYLE_C_GHIBLI_PET_NARRATIVE`, `DOG_STYLE_D_INDOOR_CARE_KEYFRAME`, `DOG_STYLE_E_REACTION_RESONANCE`.
- Custom: `CUSTOM_NAMED_STYLE`, only with a complete P1 style contract.

Changing style requires a new project revision and invalidates P3-P9. Style selection alone cannot change identity, meaning, copy, language, audio, action causality or time authority.

## Fixed production core

- One complete macro-action scene equals one segment and one dedicated grid.
- Grid layout is the minimum sufficient layout, beginning at 2x2; never split by fixed seconds or equal cell duration.
- Generate a whole grid in one ImageGen call. Never generate or repair individual cells.
- G01 creates the project visual anchor. G02+ use the G01 anchor and the previous approved end-state cell.
- Built-in ImageGen uses `CODEX_BUILT_IN_IMAGEGEN_PROMPT_ONLY` and `FLEXIBLE_REFERENCE`. Request 9:16 composition but do not claim exact pixels. Verify and deterministically crop after generation.
- Grok/KIE input order is the segment action grid first and deterministic start cell second.
- Each segment must score at least 80, the final master average at least 85, and hard failures must be zero.
- One baseline plus at most one consolidated whole-grid correction is allowed. No automatic retry.

## P1-P9

1. P1 locks route, source language, copy policy, timing authority, style, identities, provider, aspect guidance and budgets.
2. P2 creates transcript, per-second evidence, cuts, semantic units and macro-action evidence. Never infer missing evidence from common sense.
3. P3 locks copy and sound. D measures the selected narration voice; F binds the full original MP3 and verbatim copy.
4. P4 creates complete macro-action scenes, causal paths, contacts, support surfaces, apertures and terminal states.
5. P5 compiles one grid Prompt and one video Prompt per segment, then audits content lineage, style, physical logic and cost before any call.
6. P6 generates and reviews whole grids. Only model-rendering errors may consume the one correction budget.
7. P7 seals segment-specific grid, start cell, action path, duration and video Prompt.
8. P8 separately seals asset upload and video-task creation. Human approval is mandatory before each external state change. Submit each sealed package exactly once.
9. P9 mutes model audio, aligns video to the route's sole timing authority, creates captions, writes the JianYing/CapCut draft and performs final QC.

## Hard stops

Stop only at `WAIT_INPUT`, `WAIT_REVIEW` or `BLOCKED_P0`, or immediately before an unapproved ImageGen/upload/video task. Block missing semantic units, unsupported facts, implicit translation, wrong audio authority, action/causal inversion, unsafe topology, identity/style drift, text/logo/watermark, missing terminal state, stale lineage, repeated submission or cost overflow.

Do not block built-in ImageGen merely because exact 9:16 pixels are unavailable. Record the capability as flexible and verify the returned image at P6.

## Cost and approval

Never call ImageGen, upload assets, create a video task or retry without an exact immutable package and matching human approval. Approval for upload is not approval for video creation. Never store API keys in the project or repository.

