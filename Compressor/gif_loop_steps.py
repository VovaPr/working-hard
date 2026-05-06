"""Per-iteration loop step helpers for GIF compression."""

import time

from compressor_gif_runtime import predict_medcut_size
from gif_ops import _clamp_prediction, _estimate_ratio_sample, _scale_key, compress_med_cut
from gif_probe import _run_fastoctree_trial


def _try_hard_skip(
    *,
    iteration,
    source,
    source_is_neighbor,
    fast_size,
    state,
    target_mid,
    bias_factor,
    stats_mgr,
    palette_limit,
    width,
    height,
    total_frames,
    gif_cfg,
    version,
):
    """Returns suggested_scale if early hard-skip applies, else None."""
    if not (
        iteration == 0
        and (source == "formula (conservative)" or source_is_neighbor)
        and fast_size > gif_cfg.target_max_mb * gif_cfg.fast_probe_hard_skip_ratio
    ):
        return None

    state.high_scale = state.scale
    if source_is_neighbor:
        delta_for_skip = stats_mgr.find_delta(palette_limit, width, height, total_frames)
        if delta_for_skip is not None:
            target_fast = target_mid - delta_for_skip * bias_factor
            if target_fast > 0 and fast_size > 0:
                suggested_scale = state.scale * (target_fast / fast_size) ** 0.5
            else:
                suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 * 0.92
        else:
            suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 * 0.92 if fast_size > 0 else state.scale
    else:
        suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 if fast_size > 0 else state.scale
        suggested_scale *= 0.92

    max_skip_step = state.scale * 0.55
    if abs(suggested_scale - state.scale) > max_skip_step:
        direction = 1 if suggested_scale > state.scale else -1
        suggested_scale = state.scale + direction * max_skip_step
    if not (state.low_scale < suggested_scale < state.high_scale):
        suggested_scale = (state.low_scale + state.high_scale) / 2

    print(
        f"{version} | [gif.skip] Early hard-skip on iter 1: FASTOCTREE={fast_size:.2f} MB "
        f"(>{gif_cfg.fast_probe_hard_skip_ratio:.2f}x target_max)"
    )
    print(f"{version} | [gif.skip] -> next scale={suggested_scale:.3f}")
    state.scale = suggested_scale
    return suggested_scale


def _run_sample_probe(
    *,
    iteration,
    should_probe_formula,
    should_probe_neighbor,
    resized_frames,
    durations,
    palette_limit,
    executor,
    workers,
    gif_cfg,
    state,
    predicted_medcut,
    fast_size,
    total_frames,
    version,
):
    """Run sample probe and apply carry-over ratio. Returns (predicted_medcut, sample_probe_measured_this_iter)."""
    sample_probe_measured_this_iter = False

    if (
        gif_cfg.sample_probe_enabled
        and not state.sample_probe_done
        and iteration <= 1
        and (should_probe_formula or should_probe_neighbor)
        and total_frames >= 120
    ):
        probe_start = time.time()
        state.sample_ratio = _estimate_ratio_sample(
            resized_frames,
            durations,
            palette_limit,
            executor,
            workers,
            gif_cfg,
        )
        sample_probe_measured_this_iter = True
        state.sample_probe_done = True
        probe_elapsed = time.time() - probe_start
        if state.sample_ratio and state.sample_ratio > 1.0:
            calibrated_prediction = fast_size * state.sample_ratio
            if calibrated_prediction > predicted_medcut:
                predicted_medcut = calibrated_prediction
            print(
                f"{version} | [gif.predict] Probe ratio (sample)={state.sample_ratio:.3f} "
                f"-> calibrated MEDIANCUT={predicted_medcut:.2f} MB "
                f"| finished in {probe_elapsed:.2f} sec"
            )

    if state.sample_ratio and state.sample_ratio > 1.0:
        calibrated_prediction = fast_size * state.sample_ratio
        if calibrated_prediction > predicted_medcut:
            predicted_medcut = calibrated_prediction
            print(
                f"{version} | [gif.predict] Probe carry-over ratio={state.sample_ratio:.3f} "
                f"-> adjusted MEDIANCUT={predicted_medcut:.2f} MB"
            )

    return predicted_medcut, sample_probe_measured_this_iter


def _try_formula_under_target_skip(
    *,
    iteration,
    source,
    predicted_medcut,
    fast_size,
    state,
    target_mid,
    gif_cfg,
    version,
):
    """Skip MEDIANCUT when formula predicts below target. Returns suggested_scale or None."""
    if not (
        source == "formula (conservative)"
        and predicted_medcut < (gif_cfg.target_min_mb - 0.35)
        and fast_size < gif_cfg.target_min_mb
        and iteration < (gif_cfg.max_safe_iterations - 1)
    ):
        return None

    state.low_scale = max(state.low_scale, state.scale)
    suggested_scale = state.scale * (target_mid / max(predicted_medcut, 0.1)) ** 0.5

    max_up_step = state.scale * min(0.30, gif_cfg.max_scale_step_ratio * 2.0)
    if abs(suggested_scale - state.scale) > max_up_step:
        direction = 1 if suggested_scale > state.scale else -1
        suggested_scale = state.scale + direction * max_up_step
    if not (state.low_scale < suggested_scale < state.high_scale):
        suggested_scale = (state.low_scale + state.high_scale) / 2

    print(f"{version} | [gif.skip] Skip decision accepted")
    print(
        f"{version} | [gif.skip] Skipping MEDIANCUT on iter {iteration+1} "
        "(formula under-target pre-adjust)"
    )
    print(f"{version} | [gif.skip] -> next scale={suggested_scale:.3f}")
    state.scale = suggested_scale
    return suggested_scale


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
        print(f"{version} | [gif.adjust] Pre-correction (iter 0) -> scale={state.scale:.3f}")
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
            print(f"{version} | [gif.adjust] Soft pre-shrink (iter 0) -> scale={state.scale:.3f}")
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
            print(f"{version} | [gif.adjust] Micro-adjusting scale -> {state.scale:.3f}")
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


def _run_medcut_step(
    *,
    iteration,
    resized_frames,
    durations,
    palette_limit,
    executor,
    workers,
    gif_cfg,
    state,
    debug_log,
    version,
):
    """Run MEDIANCUT with cache. Returns (med_size, med_bytes)."""
    scale_key = _scale_key(state.scale)
    if scale_key in state.med_cache:
        print(f"{version} | [gif.medcut] Use cached MEDIANCUT result")
        med_size, med_bytes = state.med_cache[scale_key]
        print(f"{version} | [gif.medcut] Step {iteration+1}.1 (cached) | MEDIANCUT={med_size:.2f} MB")
        debug_log(f"cache=med | hit | key={scale_key}")
    else:
        print(f"{version} | [gif.medcut] Execute MEDIANCUT")
        step_start = time.time()
        buf_med, med_size = compress_med_cut(
            resized_frames,
            durations,
            palette_limit,
            executor,
            workers,
            gif_cfg,
            final=False,
        )
        med_bytes = buf_med.getvalue()
        state.med_cache[scale_key] = (med_size, med_bytes)
        step_elapsed = time.time() - step_start
        print(f"{version} | [gif.medcut] Step {iteration+1}.1 | MEDIANCUT={med_size:.2f} MB | finished in {step_elapsed:.2f} sec")
        debug_log(f"cache=med | miss | key={scale_key}")
    return med_size, med_bytes
