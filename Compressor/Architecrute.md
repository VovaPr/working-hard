# Compressor GIF Architecture (v2.0.27)

This document describes the current compression architecture inside the Compressor folder.

## Quick Structure

```text
Compressor.py
  -> gif_compress.py
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
        -> webp_loop_steps.py
      -> webp_stats.py
```

### Short Reading Guide

- Entry: `Compressor.py`, `gif_compress.py`, `webp_compress.py`
- GIF orchestration: `gif_balanced_steps.py`
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
|   +-- gif_compress.py
|       +-- starts GIF pipeline
|       +-- owns iteration lifecycle
|
+-- Orchestration Layer
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
|       +-- runs bracketed quality search and timeout/best-effort logic
|
+-- Shared Core
    +-- gif_ops.py (low-level frame and encoding primitives)
    +-- webp_stats.py (animated WEBP stats)
```

### Quick Dependency Map

```text
gif_compress.py
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
      -> webp_loop_steps.py
    -> webp_stats.py
```

## Module Responsibilities

### Entry and Orchestration

- `gif_compress.py`
  - Entrypoint for GIF pipeline.
  - Handles decode/input normalization and iteration lifecycle.
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
- `webp_loop_steps.py`
  - Lower-level encode/fallback/persist/runtime helpers reused by WEBP animated steps.

## Dependency Shape (Simplified)

- FASTOCTREE path:
  - `gif_prepare_medcut.py` -> `gif_prepare_pipeline.py` -> `gif_prepare_steps.py` -> `gif_probe.py` -> `gif_ops.py`
- MEDIANCUT path:
  - `gif_complete_medcut.py` -> `gif_complete_pipeline.py` -> `gif_complete_steps.py` -> `gif_medcut_step.py` -> `gif_ops.py`
- Prediction and decision path:
  - `gif_prepare_pipeline.py` -> `gif_prepare_steps.py` -> `compressor_gif_runtime.py` + `gif_stats.py`
- Animated WEBP path:
  - `webp_compress.py` -> `webp_animated_pipeline.py` -> `webp_animated_steps.py` -> `webp_loop_steps.py`

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
