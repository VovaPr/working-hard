"""Iteration 0 pre-adjustments for scale optimization."""

from compressor_gif_runtime import predict_medcut_size
from gif_ops import _clamp_prediction
from gif_probe import _run_fastoctree_trial


def _apply_iter0_adjustments(
    *,
    iteration,
    source,
    source_is_neighbor,
    fast_size,
    fast_bytes,
    target_mid,
    predicted_medcut,
    state,
    frames_raw,
    width,
    height,
    palette_limit,
    durations,
    gif_cfg,
    stats_mgr,
    total_frames,
    bias_factor,
    executor,
    workers,
    debug_log,
    version,
):
    """Apply pre-correct, soft-preshrink, micro-adjust. Returns (resized_frames_or_None, fast_size, fast_bytes, predicted_medcut)."""
    resized_frames_out = None

    can_pre_correct = (
        iteration == 0
        and source in {"delta_avg (conservative)"}
        and fast_size < target_mid * 0.80
        and predicted_medcut < gif_cfg.target_min_mb * 0.92
    )
    if can_pre_correct:
        debug_log("decision=pre_correction | reason=iter0/formula_or_delta and prediction well below target")
        state.scale *= 0.92
        print(f"{version} | [gif.adjust] | Pre-correction (iter 0) -> scale={state.scale:.3f}")
        resized_frames_out, fast_size, fast_bytes = _run_fastoctree_trial(
            iteration=iteration,
            scale=state.scale,
            frames_raw=frames_raw,
            width=width,
            height=height,
            palette_limit=palette_limit,
            durations=durations,
            fast_cache=state.fast_cache,
            version=version,
            stage_tag="corrected",
        )

    can_soft_preshrink_formula = (
        iteration == 0
        and source == "formula (conservative)"
        and predicted_medcut > gif_cfg.target_max_mb * 0.985
        and predicted_medcut <= gif_cfg.target_max_mb * 1.20
        and fast_size > gif_cfg.target_max_mb * 0.80
    )
    if can_soft_preshrink_formula:
        suggested_scale = state.scale * (target_mid / predicted_medcut) ** 0.5 if predicted_medcut > 0 else state.scale
        suggested_scale *= 0.99

        max_soft_step = state.scale * 0.12
        if abs(suggested_scale - state.scale) > max_soft_step:
            direction = 1 if suggested_scale > state.scale else -1
            suggested_scale = state.scale + direction * max_soft_step

        if state.low_scale < suggested_scale < state.high_scale and abs(suggested_scale - state.scale) > 0.005:
            debug_log("decision=soft_pre_shrink | reason=formula near upper target bound")
            state.scale = suggested_scale
            print(f"{version} | [gif.adjust] | Soft pre-shrink (iter 0) -> scale={state.scale:.3f}")
            resized_frames_out, fast_size, fast_bytes = _run_fastoctree_trial(
                iteration=iteration,
                scale=state.scale,
                frames_raw=frames_raw,
                width=width,
                height=height,
                palette_limit=palette_limit,
                durations=durations,
                fast_cache=state.fast_cache,
                version=version,
                stage_tag="soft-corrected",
            )
            predicted_medcut = predict_medcut_size(
                stats_mgr=stats_mgr,
                palette_limit=palette_limit,
                width=width,
                height=height,
                total_frames=total_frames,
                fast_size=fast_size,
                bias_factor=bias_factor,
                source=source,
                gif_cfg=gif_cfg,
                clamp_prediction_fn=_clamp_prediction,
            )
            print(
                f"{version} | -> Updated predicted MEDIANCUT={predicted_medcut:.2f} MB "
                f"| scale={state.scale:.3f}"
            )

    can_micro_adjust = (
        source_is_neighbor
        and predicted_medcut < gif_cfg.target_min_mb
        and fast_size < target_mid * 0.9
        and not state.micro_adjust_used
        and iteration <= 1
        and total_frames >= 80
        and state.high_scale >= 3.9
        and state.stall_count < 1
    )
    if can_micro_adjust:
        adj_scale = state.scale * (target_mid / (fast_size + 4.0)) ** 0.5
        if abs(adj_scale - state.scale) > 0.01:
            max_micro_step = state.scale * min(0.30, gif_cfg.max_scale_step_ratio * 2.0)
            if abs(adj_scale - state.scale) > max_micro_step:
                direction = 1 if adj_scale > state.scale else -1
                adj_scale = state.scale + direction * max_micro_step

            debug_log("decision=micro_adjust | reason=neighbor_stats and fast below 0.9*target_mid")
            state.scale = adj_scale
            state.micro_adjust_used = True
            print(f"{version} | [gif.adjust] | Micro-adjusting scale -> {state.scale:.3f}")
            resized_frames_out, fast_size, fast_bytes = _run_fastoctree_trial(
                iteration=iteration,
                scale=state.scale,
                frames_raw=frames_raw,
                width=width,
                height=height,
                palette_limit=palette_limit,
                durations=durations,
                fast_cache=state.fast_cache,
                version=version,
                stage_tag="adjusted",
            )

    return resized_frames_out, fast_size, fast_bytes, predicted_medcut
