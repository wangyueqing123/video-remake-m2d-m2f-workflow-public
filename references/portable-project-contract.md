# R6.10 portable project contract

R6.10 intentionally retains the `R6.2-*` artifact schemas, `R62_PROJECT.json`, `r62_*.py`, and R6.9 style-audit compatibility entry points. The runtime version and bound Skill fingerprint identify R6.10; approved historical artifacts do not need byte changes merely to migrate. Every new receipt path must be project-relative and resolved under the project root, independent of the current working directory.

R6.5 projects with any approval, submission, or QC must never be edited in place. `migrate_r65_to_r66_visual_anchor.py` creates a new R6.7 project revision, preserves a byte-identical source snapshot and inventory hash, retires every old seal/approval/submission authority, keeps historical costs immutable, establishes the first passed grid as `PROJECT_VISUAL_ANCHOR`, and rebuilds the live P5/P6 chain from the earliest grid that lacked an audited visual anchor. Migration itself performs no provider call. Verify the result independently with `validate_r66_migration_receipt.py`; `--source-project` is optional and only adds comparison with the still-existing old directory.

R6.7 projects enter R6.8 through `migrate_r67_to_r68_live_progress.py`. This revision preserves the self-contained snapshot while allowing the live project to advance through validated controller transitions. The validator freezes migration history and budget, not the current phase or active session.

## Acceptance condition

A project is portable only when a new Codex instance on another computer can determine the source, route, style, current phase, validated artifacts, remaining generation budget, pending review, approvals, and consumed submissions by reading files inside the copied bundle. Chat history and remembered paths are never authority.

## Required bundle structure

Keep the Skill and project as sibling or otherwise relatively addressable directories inside one copied package:

```text
bundle-root/
  video-remake-workflow-r68/
    SKILL.md
    VERSION
    scripts/
    references/
    assets/
  projects/
    <project-id>/
      R62_PROJECT.json
      inputs/
      artifacts/P1/ ... artifacts/P8/
      reviews/
      outputs/
```

`R62_PROJECT.json` is the only live project-state authority. All paths stored in the job, state, artifacts, seals, approvals, submissions, QC records, and receipts are project-relative or relative from the project to the Skill.

## Start and resume

Create a project with:

```powershell
python scripts/r62_project.py init --project-dir <project-dir> --project-id <project-id>
```

On every new session or computer, run:

```powershell
python scripts/r62_project.py inspect --project-dir <project-dir>
python scripts/audit_r62_portability.py --skill-root . --project-dir <project-dir>
python scripts/check_r62_environment.py
```

Stop at `BLOCKED_P0` if the Skill entry/version hash, source binding, artifact hash, path policy, or ledger is invalid. Do not infer current phase from filenames or chat history.

Treat a released Skill hash as immutable. During pre-release development only, `r62_project.py refresh-skill` may refresh the bound Skill hash before P2 when there are no downstream artifacts, approvals, or submissions. Otherwise create a new project revision.

For an R6.18 downstream compiler or validation fix after submissions already exist, use `migrate_r618_development_skill_revision.py`. It creates a new directory, preserves every historical seal, approval, submission, QC record and cost entry, embeds the source state plus full file inventory, updates only the Skill binding/session identity, and authorizes zero provider calls during migration. It must refuse a pending-QC project or any live provider authority.

R6.3 post-submission projects may enter R6.4 only through `migrate_r63_to_r64.py`. The migration copies to a new project directory, audits the R6.3 source first, preserves historical artifacts/seals/approvals/submissions/QC byte-for-byte, records old and new Skill fingerprints plus the resolved blocker, clears live authority, and writes hashed migration and visual-reclassification receipts. Directly editing the old manifest or reusing an old approval is forbidden.

## P1 lock

Place source files under `inputs/` or another project-relative directory. Fill `artifacts/P1/JOB.json`, including exact hashes and `path_policy=PROJECT_RELATIVE_ONLY`. Validate it, then bind the mode/source lock:

```powershell
python scripts/r62_project.py lock --project-dir <project-dir> --job artifacts/P1/JOB.json
```

The command records the exact job hash, route/profile/objective/style, `visual_plan_mode`, `provider_adapter_profile`, provider intent, grid strategy, source relative path/hash, and target configuration. Replacing any of these values, or source, aspect, audio variant, or duration requires a new revision and invalidates downstream state.

## Artifact persistence

Every phase artifact that controls later work must be bound by relative path, byte size, SHA256, validator name, and validation status:

```powershell
python scripts/r62_project.py bind --project-dir <project-dir> --phase P2 --name TIMELINE_EVIDENCE --path artifacts/P2/TIMELINE_EVIDENCE.json --validator validate_r62_timeline_evidence.py
```

During Skill development only, a compiler or downstream contract fix may be adopted without relabeling old artifacts by explicitly invalidating its earliest affected phase, for example `refresh-skill --invalidate-from P5`. This is allowed only before any review seal, approval, or submission exists. The command removes all bound artifacts from that phase onward, moves the live phase back to the preceding gate, records the removed names and new whole-Skill hash, and requires downstream rebuilding. Without an explicit boundary, any Skill change after P1 remains blocked. A source, route, objective, style, duration, adapter, or upstream semantic change still requires a new project revision rather than this development migration.

Compiled Prompt files use exact UTF-8 bytes with LF newlines on every operating system. The compiler audit must hash the bytes after writing the file, and that hash must equal the project artifact binding and the sealed call package. Never approve an in-memory text hash that differs from the submitted file.

Before sealing a P6 call, run `validate_r62_call_package.py`. It reopens lineage, Prompt, capability, registries, correction evidence, the selected `grid_id`, its exclusive `segment_id`, per-grid budget, project budget, and pilot-grid QC. For exact geometry it rejects any capability without exposed size/aspect control and the verified requested ratio. `r62_project.py seal` repeats this validation, so a blocked package cannot be sealed by skipping the documented command.

Use `--validation-status PREPARED` for extractor, ASR, OCR, candidate, or draft artifacts. Use `VALIDATED` only after the named validator or required Codex evidence review has passed. `inspect` reopens every bound file and verifies its size/hash; it never upgrades a draft to validated automatically.

## Human review and exactly-once calls

Before ImageGen or a provider request, create the exact call package inside the project and seal it:

```powershell
python scripts/r62_project.py seal --project-dir <project-dir> --phase P6 --package reviews/P6_CALL_PACKAGE.json --call-kind GRID_BASELINE --call-ordinal 1
```

After the user approves that exact seal, persist approval:

```powershell
python scripts/r62_project.py approve --project-dir <project-dir> --seal-sha256 <seal-sha256> --approval-id <id> --authority-text <exact-user-approval>
```

After the single authorized submission, consume the approval:

```powershell
python scripts/r62_project.py consume --project-dir <project-dir> --seal-sha256 <seal-sha256> --approval-id <id> --submission-id <provider-or-local-call-id>
```

The controller rejects an unapproved seal, mismatched seal, duplicate approval ID, duplicate consumption, wrong call ordinal, auto retry, per-cell call, a second baseline/correction for the same grid, a project-budget overflow, or generation of grid 2+ before grid 1 has passed QC. A grid submission creates `pending_qc_submission_id`; bind the output and persist QC before P7 or another seal:

```powershell
python scripts/r62_project.py qc-record --project-dir <project-dir> --qc artifacts/P6/G01_GRID_QC.json
```

A correction package requires recorded rejected baseline QC, prior output/QC hashes, a non-empty consolidated correction scope, and proof that the failed output is not a visual structure input.

A human may accept only a controller-qualified non-structural rendering deviation with the exact rejected-QC hash, exact failure codes, and exact authority text, then running `r62_project.py qc-amend`. Allowed policies are: an unwanted decorative mark; a non-causal count drift confined to non-final cells when the final cell and downstream entry remain safe; or a non-causal microstate compression in one non-final cell after the single correction route is exhausted. The microstate policy requires explicit evidence that occlusion, reveal, required hold/abstention, and the correct terminal outcome remain visible in order, with identity, count, contact, topology, and terminal state unchanged. The controller preserves the rejected QC, writes a separate amended QC, replaces only the live QC pointer, and records both hashes in `qc_waivers`. The waiver never authorizes a provider call or retry and cannot cover geometry, topology, identity, count changes, missing causal actions, reward-order errors, final-state, subtitle, watermark, or ratio failures.

R6.40 also permits that amendment while the project is `BLOCKED_P0`, but only after `migrate_r639_to_r640_post_correction_degradation.py` has created a new revision and proved one rejected baseline, exactly one exhausted rejected correction, no passed QC, no pending QC, and no live provider authority. In that branch every baseline failure code must be a decorative `ICON/MARK`; the baseline's structural, chronology, state, topology, identity/contact evidence, and downstream end state must otherwise be safe. The migration embeds the exact source state and complete file inventory, preserves every seal, approval, submission, cost entry, QC row, and historical event, and adds zero provider calls. The failed correction remains rejected evidence and is never promoted.

P8 treats upload and video generation as separate resources. Seal `ASSET_UPLOAD` for one segment's local assets, then seal `VIDEO_API` for the corresponding uploaded URLs. Each resource has its own package, approval, consumption receipt, call count, and idempotency key. API credentials never enter the portable bundle.

An archived approval is audit history after relocation, not live authority. `inspect` always sets `provider_call_authorized=false`; a provider call requires a current-session human decision and an unconsumed exact seal.

## Runtime

The R6.8 JSON/state/control layer uses Python 3.10+ standard library. The complete workflow requires Pillow for deterministic PNG cell crops and pixel-level receipt validation. Missing Pillow is a `BLOCKED_P0` environment result, not an optional warning. A portable anchored project must include the project visual anchor, its receipt/QC, every previous-segment end-state crop used by a sealed call, and their hashes. Chat images and generated-image caches are never portable dependencies.

When available, run `prepare_r62_source_evidence.py`, `transcribe_r62_source.py`, and `ocr_r62_cover.py` so P2 preparation is reproducible on a new machine.

## Moving the bundle

Copy the complete bundle, not only `R62_PROJECT.json`. Exclude credentials, `.env`, API keys, Python caches, virtual environments, model caches, and standalone temporary downloads. Preserve provider URLs only when they are immutable fields inside historical receipts or sealed audit packages; mark them non-live and never depend on them for recovery or a new request.
