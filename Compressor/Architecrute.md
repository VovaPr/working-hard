# Compressor GIF Architecture (v2.0.46)

This document describes the current compression architecture inside the Compressor folder.

## Quick Structure

```text
Compressor.py
  -> artifact_manager.py
  -> image_compress.py
    -> image_static_pipeline.py
      -> image_static_steps.py
  -> gif_compress.py
    -> gif_main_pipeline.py
      -> gif_main_steps.py
    -> gif_balanced_steps.py
      -> gif_prepare_medcut.py
        -> gif_prepare_pipeline.py
          -> gif_prepare_steps.py
            -> gif_probe.py
            -> gif_skip_logic.py
            -> gif_sample_probe.py
            -> gif_adjustments.py
            -> compressor_gif_runtime.py
            -> gif_stats.py
      -> gif_complete_medcut.py
        -> gif_complete_pipeline.py
          -> gif_complete_steps.py
            -> gif_medcut_step.py
            -> gif_balanced_temporal.py
            -> gif_balanced_result.py
            -> gif_complete_utils.py
            -> gif_scale.py
  -> webp_compress.py
    -> webp_animated_pipeline.py
      -> webp_animated_steps.py
        -> webp_sample_probe.py
        -> webp_loop_steps.py
          -> webp_timeout_steps.py
          -> webp_persist_steps.py
      -> webp_stats.py
```

### Short Reading Guide

- Entry: `Compressor.py`, `image_compress.py`, `gif_compress.py`, `webp_compress.py`
- Static image orchestration: `image_static_pipeline.py`
- GIF orchestration: `gif_balanced_steps.py`
- GIF main orchestration: `gif_main_pipeline.py` -> `gif_main_steps.py`
- GIF prepare path: facade `gif_prepare_medcut.py` -> pipeline `gif_prepare_pipeline.py` -> helpers `gif_prepare_steps.py`
- GIF complete path: facade `gif_complete_medcut.py` -> pipeline `gif_complete_pipeline.py` -> helpers `gif_complete_steps.py`
- WEBP animated path: facade `webp_compress.py` -> pipeline `webp_animated_pipeline.py` -> helpers `webp_animated_steps.py`
- Shared primitives: `gif_ops.py`, `compressor_gif_runtime.py`, `gif_stats.py`, `webp_loop_steps.py`

## High-Level Flow

1. `gif_compress.py` starts GIF processing and controls the main iteration loop.
2. `gif_balanced_steps.py` orchestrates one iteration.
3. `gif_prepare_medcut.py` is a thin facade for prepare stage.
4. `gif_prepare_pipeline.py` executes FASTOCTREE trial and pre-MEDIANCUT decisions.
5. `gif_complete_medcut.py` is a thin facade for complete stage.
6. `gif_complete_pipeline.py` executes MEDIANCUT completion logic and final decisions.

## Module Hierarchy (Visual)

```text
Compressor GIF Pipeline
|
+-- Entry Layer
|   +-- image_compress.py
|       +-- delegates static image flow to image_static_pipeline.py
|   +-- gif_compress.py
|       +-- facade for GIF batch processing
|       +-- delegates single-GIF flow to gif_main_pipeline.py
|
+-- Orchestration Layer
|   +-- image_static_pipeline.py
|       +-- routes PNG/JPG/static WEBP flows
|       +-- delegates operation details to image_static_steps.py
|   +-- gif_main_pipeline.py
|       +-- owns single-GIF orchestration only
|       +-- delegates runtime/decode/loop setup to gif_main_steps.py
|   +-- gif_balanced_steps.py
|       +-- routes: prepare -> complete
|
+-- Prepare Stage
|   +-- gif_prepare_medcut.py (facade)
|       +-- delegates to gif_prepare_pipeline.py
|   +-- gif_prepare_pipeline.py
|       +-- owns stage orchestration only
|       +-- delegates stage details to gif_prepare_steps.py
|   +-- gif_prepare_steps.py
|       +-- uses gif_probe.py (FASTOCTREE trial)
|       +-- uses gif_skip_logic.py (hard/under-target skip)
|       +-- uses gif_sample_probe.py (sample calibration)
|       +-- uses gif_adjustments.py (iter0 pre-adjustments)
|       +-- uses compressor_gif_runtime.py (prediction + decisions)
|       +-- uses gif_stats.py (historical deltas)
|
+-- Complete Stage
|   +-- gif_complete_medcut.py (facade)
|       +-- delegates to gif_complete_pipeline.py
|   +-- gif_complete_pipeline.py
|       +-- owns completion-stage orchestration only
|       +-- delegates branch logic to gif_complete_steps.py
|   +-- gif_complete_steps.py
|       +-- uses gif_medcut_step.py (MEDIANCUT execution + cache)
|       +-- uses gif_balanced_temporal.py (temporal retry)
|       +-- uses gif_balanced_result.py (success finalize/save)
|       +-- uses gif_complete_utils.py (FAST-only fallback scale)
|       +-- uses gif_scale.py (next-scale progression)
|
+-- WEBP Animated Stage
|   +-- webp_compress.py (facade/orchestrator)
|       +-- delegates animated heavy logic to webp_animated_pipeline.py
|   +-- webp_animated_pipeline.py
|       +-- owns the animated loop orchestration only
|       +-- delegates encode/result handling to webp_animated_steps.py
|   +-- webp_animated_steps.py
|       +-- uses webp_loop_steps.py for encode/persist/runtime helpers
|       +-- uses webp_sample_probe.py for frame-subset quality calibration
|       +-- runs bracketed quality search and timeout/best-effort logic
|   +-- webp_sample_probe.py
|       +-- evenly-spaced frame subset encode to predict full size
|       +-- returns calibrated initial quality before main loop
|   +-- webp_loop_steps.py
|       +-- startup/runtime/encode/direct-fast fallback helpers
|       +-- delegates timeout rescue to webp_timeout_steps.py
|       +-- delegates persistence to webp_persist_steps.py
|
+-- Shared Core
    +-- artifact_manager.py (centralized artifact I/O, runtime file management)
    +-- scale_strategy.py (unified scale calculation strategy and constraints)
    +-- gif_ops.py (low-level frame and encoding primitives)
    +-- webp_stats.py (animated WEBP stats)
  +-- image_static_steps.py (JPEG/WEBP static image primitives)
```

### Quick Dependency Map

```text
image_compress.py
  -> image_static_pipeline.py
    -> image_static_steps.py

gif_compress.py
  -> gif_main_pipeline.py
    -> gif_main_steps.py
      -> gif_balanced_steps.py
    -> gif_prepare_medcut.py
      -> gif_prepare_pipeline.py
        -> gif_prepare_steps.py
          -> gif_probe.py -> gif_ops.py
          -> gif_skip_logic.py
          -> gif_sample_probe.py -> gif_ops.py
          -> gif_adjustments.py
          -> compressor_gif_runtime.py
          -> gif_stats.py
    -> gif_complete_medcut.py
      -> gif_complete_pipeline.py
        -> gif_complete_steps.py
          -> gif_medcut_step.py -> gif_ops.py
          -> gif_balanced_temporal.py
          -> gif_balanced_result.py
          -> gif_complete_utils.py
          -> gif_scale.py

webp_compress.py
  -> webp_animated_pipeline.py
    -> webp_animated_steps.py
      -> webp_sample_probe.py
      -> webp_loop_steps.py
        -> webp_timeout_steps.py
        -> webp_persist_steps.py
    -> webp_stats.py
```

## Module Responsibilities

### Entry and Orchestration

- `artifact_manager.py`
  - Centralized artifact manager for runtime files (stats JSON, temp directories).
  - Provides abstract interface for I/O operations (load/save stats).
  - Singleton pattern for global access.
- `image_compress.py`
  - Facade for static image flow.
  - Delegates to `image_static_pipeline.py`.
- `image_static_pipeline.py`
  - Orchestrates PNG/JPG/static WEBP paths.
  - Delegates conversion/compression internals to `image_static_steps.py`.
- `image_static_steps.py`
  - Static JPEG/WEBP primitives and iterative compression helpers.

- `gif_compress.py`
  - Batch entrypoint/facade for GIF and animated WEBP queues.
  - Delegates single-GIF pipeline to `gif_main_pipeline.py`.
- `gif_main_pipeline.py`
  - Single-GIF orchestration layer.
  - Delegates decode/runtime/loop setup to `gif_main_steps.py`.
- `gif_main_steps.py`
  - Decode, runtime-context initialization, and iteration loop execution.
- `gif_balanced_steps.py`
  - Thin orchestrator for iteration stages.
  - Calls prepare stage, then completion stage.

### Prepare Stage

- `gif_prepare_medcut.py`
  - Thin facade module.
  - Re-exports prepare entrypoint used by orchestrator.
- `gif_prepare_pipeline.py`
  - Small scenario/orchestration layer for the prepare stage.
  - Delegates FASTOCTREE trial, prediction, skip, and pre-adjustment details.
- `gif_prepare_steps.py`
  - Contains the actual prepare-stage helpers.
    - Runs FASTOCTREE trial and pre-MEDIANCUT decision flow.
    - Second-level helpers: `_handle_medcut_disabled_path`, `_probe_and_build_skip_decision`,
      `_run_skip_checks`, `_run_probe_skip_flow`, `_continue_predict_result`, `_ready_predict_result`,
      `_apply_skip_decision_if_any`, `_apply_prepare_adjustments`.

Supporting helpers used by prepare stage:

- `gif_probe.py`
  - FASTOCTREE trial wrapper.
- `gif_skip_logic.py`
  - Hard skip and under-target skip rules.
- `gif_sample_probe.py`
  - Sample probe calibration and carry-over ratio.
- `gif_adjustments.py`
  - Iteration-0 pre-correction, soft pre-shrink, and micro-adjust logic.

### Completion Stage

- `gif_complete_medcut.py`
  - Thin facade module.
  - Re-exports completion entrypoint used by orchestrator.
- `gif_complete_pipeline.py`
  - Small scenario/orchestration layer for the completion stage.
  - Delegates guard, retry, finalize, and scale-advance branches.
- `gif_complete_steps.py`
  - Contains the actual completion-stage helpers.
    - Runs guard handling and completion branch routing.
    - Second-level helpers: `_act_on_overhead_guard`, `_finalize_or_advance_scale`.

Supporting helpers used by completion stage:

- `gif_medcut_step.py`
  - MEDIANCUT execution and cache handling.
- `gif_balanced_temporal.py`
  - Temporal preserve/reduction and quality retry logic.
- `gif_balanced_result.py`
  - Final success save/stats handling.
- `gif_complete_utils.py`
  - FAST-only fallback scale advancement helper.

### Shared Runtime and Primitives

- `artifact_manager.py`
  - Centralized artifact manager for runtime files (stats JSON, temp directories).
  - Provides abstract interface for I/O operations (load/save stats).
  - Singleton pattern for global access.
  - Stats file versioning: schema version 1 embedded in JSON metadata.
  - Future: File rotation when stats exceed 5 MB (see /memories/repo/stats-rotation-todo.md).
- `scale_strategy.py`
  - Unified scale calculation: geometric mean formula, step capping, bracket clamping.
  - Used by skip logic, complete stage, and FAST-only search.
  - Encapsulates all scale update logic for consistency and reuse.
- `compressor_gif_runtime.py`
  - Runtime state and decision helpers.
  - Prediction and corridor/target checks.
- `gif_stats.py`
  - Persistent stats manager for model/prediction input.
- `gif_scale.py`
  - Scale update strategy after MEDIANCUT outcome.
- `gif_ops.py`
  - Low-level frame/encoding operations and utility primitives.

### WEBP Animated Pipeline

- `webp_compress.py`
  - Public entrypoint for animated WEBP compression in the launcher flow.
  - Handles file open/decode and delegates animated loop.
- `webp_animated_pipeline.py`
  - Small orchestration layer for animated WEBP iteration.
  - Coordinates startup, per-step execution, and final fallback.
- `webp_animated_steps.py`
  - Contains encode-step execution, bracket updates, timeout handling, and best-effort persistence.
    - Second-level helpers: `_check_early_exits`, `_pick_next_quality`, `_run_sample_probe_if_needed`.
- `webp_sample_probe.py`
  - Frame-subset sample probe for initial quality calibration.
  - Encodes an evenly-spaced subset, extrapolates full file size, returns corrected quality.
  - Only runs when no stats profile exists (`direct_final_from_stats=False`) and `frame_count >= webp_sample_probe_min_frames`.
- `webp_loop_steps.py`
  - Lower-level startup/runtime/encode/fallback helpers reused by WEBP animated steps.
- `webp_timeout_steps.py`
  - Timeout-rescue decision and persistence path.
- `webp_persist_steps.py`
  - Success and best-effort persistence helpers (with stats save).

## Dependency Shape (Simplified)

- FASTOCTREE path:
  - `gif_prepare_medcut.py` -> `gif_prepare_pipeline.py` -> `gif_prepare_steps.py` -> `gif_probe.py` -> `gif_ops.py`
- MEDIANCUT path:
  - `gif_complete_medcut.py` -> `gif_complete_pipeline.py` -> `gif_complete_steps.py` -> `gif_medcut_step.py` -> `gif_ops.py`
- Prediction and decision path:
  - `gif_prepare_pipeline.py` -> `gif_prepare_steps.py` -> `compressor_gif_runtime.py` + `gif_stats.py`
- Animated WEBP path:
  - `webp_compress.py` -> `webp_animated_pipeline.py` -> `webp_animated_steps.py` -> `webp_sample_probe.py` (probe) + `webp_loop_steps.py` -> (`webp_timeout_steps.py`, `webp_persist_steps.py`)

## Runtime Invariants

- GIF target range: 13.5-14.99 MB.
- Max safe iterations: 10.
- MEDIANCUT overhead guard is enabled.
- If repeated MEDIANCUT overhead is too high:
  - Accept FASTOCTREE only when it is in target range.
  - Otherwise switch to FAST-only search mode until target is reached.

## Current Architecture Goals

- Single responsibility per module.
- Explicit stage boundaries: prepare vs complete.
- Predictable dependency graph with focused helpers.
- Preserve output target correctness while reducing runtime.

## Logic Description

### GIF Prediction System

The prediction system estimates the MEDIANCUT output size given the current scale, and selects the
initial scale at startup. Four sources are tried in priority order:

1. **stats** (`average_scale_recent`): Exact match on palette + WxH + frames, time-decay weighted.
  Most reliable - used directly as initial scale.
2. **neighbor stats** (`neighbor_scale_profile`): Nearest neighbor in historical stats.
   Safety factor applied: `neighbor_scale_safety = 0.95`, or `neighbor_scale_safety_confident
   = 0.985` when neighbor count >= 4 and std <= 0.035. A floor based on init-size ratio is enforced.
3. **delta_avg**: Average historical delta between FASTOCTREE and MEDIANCUT output sizes, converted
   to a scale via formula.
4. **formula (conservative)**: Pure size-ratio formula multiplied by 0.95 safety factor. Used when
   no stats are available.

MEDIANCUT size prediction at runtime (`predict_medcut_size()`):
- >= 2 matching stats entries: linear regression `a * fast_size + b`.
- Else if delta available: `fast_size + delta * bias_factor`.
- Else: `fast_size * bias_factor`.

Source-specific extra bias:
- `stats_source_bias_extra = 1.08` for "stats" source.
- `neighbor_source_bias_extra = 1.04` for "neighbor stats" source.

### Sample Probe Calibration

When source is `formula (conservative)` or high-risk neighbor prediction (palette >= 220, frames
>= 100), a sample probe runs before the first full MEDIANCUT. A representative subset of frames is
compressed with both algorithms to measure the real FAST->MED ratio. This ratio is applied to
calibrate the prediction and carried forward to subsequent iterations (`sample_ratio` carry-over).

### Skip Decisions

Before running full MEDIANCUT, the prepare stage checks whether it can skip it and jump to a better
scale. Evaluated in order:

1. **Hard skip** (`_try_hard_skip`): iter=0 only. FASTOCTREE output > 1.30x target_max and source
   is formula/neighbor -> skip and step scale down directly.
2. **Probe overflow skip**: Sample probe measured and predicted MEDIANCUT > target_max x 1.005
   (tight) or x 1.08 (normal) -> skip down, tighten high bracket.
3. **Probe underflow skip**: Predicted MEDIANCUT < target_min - 0.10 MB -> skip up, expand bracket.
4. **Formula extra skip**: iter=1, formula source, not yet used, predicted > target_max x 1.10,
   FASTOCTREE > target_min x 0.90 -> skip down.
5. **Formula under-target skip** (`_try_formula_under_target_skip`): Formula source predicts below
   target_min - 0.35 MB and FASTOCTREE is below target_min -> scale up.

Skip decisions 2-4 are evaluated together via `build_skip_decision()` in `compressor_gif_runtime`;
skip decision 5 is a separate guard evaluated after.

### Iteration-0 Pre-Adjustments (`gif_adjustments.py`)

After all skip decisions pass, three optional adjustments refine the scale before MEDIANCUT runs.
Each re-runs FASTOCTREE at the updated scale and produces fresh `fast_bytes`.

- **Pre-correction**: `delta_avg` source, FASTOCTREE and predicted both well below target -> scale x 0.92.
- **Soft pre-shrink**: Formula source, predicted in (target_max x 0.985, target_max x 1.20) ->
  nudge scale down by up to 12% to avoid first-iteration overshoot.
- **Micro-adjust**: Neighbor source, FASTOCTREE < 0.9x target_mid, not yet used -> nudge scale up
  using `fast_size + 4.0` as conservative denominator.

### MEDIANCUT Overhead Guard

After MEDIANCUT runs and size exceeds FASTOCTREE by >= `medcut_overhead_guard_margin_mb = 6.0 MB`,
`medcut_overhead_hits` increments. When hits reach `medcut_overhead_guard_max_hits = 2`:

- If FASTOCTREE in target range: accept FASTOCTREE result and finish.
- Otherwise: set `medcut_disabled = True` and enter **FAST-only search mode**.

In FAST-only mode, each iteration adjusts scale using a capped correction step to converge
FASTOCTREE into target range, bypassing all prediction and MEDIANCUT logic.

### Result Acceptance Rules

| Label | Condition |
|---|---|
| `fast-direct` | iter=0, FASTOCTREE in target range, frames >= 120 |
| `fast` | iter >= 1, FASTOCTREE in preferred range (13.8-14.6 MB) |
| `fast-guard-target` | Overhead guard or FAST-only path reached target |
| `temporal-preserve` | iter=0 MEDIANCUT too large, large frame count, small resolution -> reduce frames temporally; if in target save, else continue with fewer frames |
| `quality-retry` | MEDIANCUT in corridor/target on small-res high-frame file -> retry with extra frames for quality |
| `medcut success` | MEDIANCUT in preferred corridor (iter >= 1) or target range |
| `advance scale` | None of the above -> update bracket and compute next scale |

### Stall Guard

If the same `(scale_key, round(med_size, 2))` signature repeats, `stall_count` increments.
When >= 2 stalls are detected, debug log emits `stall_guard=active`. Diagnostic-only.

### WEBP Bracketed Quality Search

Animated WEBP compression uses a bracketed binary search over quality values:

1. **Startup**: Stats lookup selects initial quality and whether direct-final mode applies. If no
   stats, ratio-seeded formula initializes quality.
2. **Sample probe** (new in v2.0.34): When no stats profile exists (`direct_final_from_stats=False`)
   and `frame_count >= webp_sample_probe_min_frames` (default 60), a cheap probe encodes
   `webp_sample_probe_sample_count` (default 20) evenly-spaced frames at the initial quality.
   The per-frame size is scaled to the full frame count (with a small `webp_sample_probe_bias = 1.02`
  conservative factor) to predict the full encoded size, then quality is recalculated via
  `sqrt(target_mid / predicted_full)`. The probe prediction is also stored as the first
  `(quality, predicted_size)` observation for later iterations. This typically costs ~3s for
  390-frame files and saves 1-2 full encode iterations (~1-2 minutes).
3. **Direct-fast shortcut**: If direct-final is active and the known result fits within
   `webp_animated_direct_final_fast_max_growth = 1.10` tolerance, method=1 is tried first. If it
   misses target, falls back to method=2.
4. **Per-iteration loop**:
   - Encode at current quality/method.
   - If in target range -> persist success, done.
   - Update `under_target_q` / `over_target_q` bracket and best-effort candidate.
   - Record the `(quality, size)` observation and reuse recent observations to fit a simple
     `size = C * q^alpha` model. When the fit is stable, the next quality is predicted from that
     model instead of repeating the same square-root correction every iteration.
   - **Timeout rescue**: elapsed > `effective_max_seconds` -> persist best-effort via method=2.
   - **Bracket-tight exit**: `over_target_q - under_target_q <= 1` -> persist best-effort.
   - **Near-target nudge**: within 10% of target_mid and bracket unknown -> quality +/-1 or +/-2.
   - **Resize fallback**: once the search reaches the quality floor (`q <= 45`), resize frames and
     estimate a post-resize starting quality from the resized area ratio instead of resetting to 95.
     If the fitted model predicts `q < 45`, the code now skips the wasted extra full encode and
     goes directly into resize fallback.
   - Otherwise: binary search between bracket bounds, or ratio-based correction.
5. **Max iterations**: Persist best-effort (closest to target_mid).

Effective timeout: `max(webp_file_max_seconds, frames x webp_animated_max_seconds_per_frame)`.

## Audit Fixes (v2.0.28)

The following issues were found and fixed during the v2.0.28 logic audit:

- **BUG: Stale `fast_bytes` after iter-0 adjustments** (`gif_adjustments.py` /
  `gif_prepare_steps.py` / `gif_prepare_pipeline.py`): `_apply_iter0_adjustments` returned updated
  `fast_bytes` (re-run FASTOCTREE at adjusted scale) but the caller discarded it with `_`. The
  overhead guard and FAST-only fallback path could write file content from the old scale while
  recording size from the new scale. Fixed: `_apply_prepare_adjustments` now captures and returns
  `fast_bytes`.

- **Dead method `average_scale()`** (`gif_stats.py`): Unweighted average was defined but never
  called anywhere. `average_scale_recent()` (time-decay weighted) is used exclusively. Removed.

- **Inconsistent `is_in_target_range` threshold** (`gif_balanced_result.py`): The fast-direct
  accept path used a strict `<= target_max_mb` check while all other target checks use
  `is_in_target_range()` which adds +0.005 MB tolerance. Fixed: aligned to `is_in_target_range()`.

- **Misleading debug message** (`gif_prepare_steps.py`): `_apply_skip_decision_if_any` printed a
  hardcoded "formula prediction well above target" even when the skip reason was underflow. Fixed:
  message now uses `skip_decision.reason`.

- **Duplicate "Skip decision accepted" message**: Both `_apply_skip_decision_if_any` and
  `_try_formula_under_target_skip` printed the same generic text. Fixed: each path now prints a
  distinct label.

- **Misleading return values in skip functions** (`gif_skip_logic.py`): `_try_hard_skip` and
  `_try_formula_under_target_skip` mutated `state.scale` and returned `suggested_scale`, but callers
  only checked `is not None`. The returned float was silently discarded, creating a false impression
  of side-effect-free functions. Fixed: both now return `True`.
