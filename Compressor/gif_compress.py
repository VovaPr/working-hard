"""GIF processing pipeline extracted from the main launcher module."""

import os
import time
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageSequence

from compressor_gif_runtime import (
    GifRuntimeState,
    build_skip_decision,
    is_in_preferred_range,
    is_in_target_range,
    predict_medcut_size,
)
from gif_ops import (
    _clamp_prediction,
    _estimate_ratio_sample,
    _scale_key,
    compress_med_cut,
    process_frame_fast_octree,
    resize_frames,
    save_gif,
    temporal_reduce,
)
from gif_stats import CompressorStatsManager


def _run_fastoctree_trial(
    *,
    iteration,
    scale,
    frames_raw,
    width,
    height,
    palette_limit,
    durations,
    fast_cache,
    version,
    stage_tag="base",
):
    """Run FASTOCTREE probe with cache by scale."""
    resize_start = time.time()
    resized_frames = resize_frames(frames_raw, width, height, scale)
    print(f"{version} | [gif.diag] resize scale={scale:.3f} elapsed={time.time() - resize_start:.2f}s")
    key = _scale_key(scale)

    if key in fast_cache:
        fast_size = fast_cache[key]["size"]
        print(f"{version} | [gif.fast] Step {iteration+1}.0 ({stage_tag}, cached) | FASTOCTREE={fast_size:.2f} MB")
        return resized_frames, fast_size, fast_cache[key].get("bytes")

    step_start = time.time()
    frames_fast = [process_frame_fast_octree(frame, palette_limit) for frame in resized_frames]
    buf_fast, fast_size = save_gif(frames_fast, durations, optimize=False)
    fast_cache[key] = {"size": fast_size, "bytes": buf_fast.getvalue()}
    step_elapsed = time.time() - step_start
    print(f"{version} | [gif.fast] Step {iteration+1}.0 ({stage_tag}) | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
    return resized_frames, fast_size, fast_cache[key]["bytes"]


def _choose_initial_scale(stats_mgr, palette_limit, width, height, total_frames, init_size, target_mid, bias_factor, gif_cfg):
    avg_scale = stats_mgr.average_scale_recent(palette_limit, width, height, total_frames)
    delta_avg = stats_mgr.find_delta(palette_limit, width, height, total_frames)
    neighbor_profile = stats_mgr.neighbor_scale_profile(palette_limit, width, height, total_frames)

    if avg_scale:
        return avg_scale, "stats"
    if neighbor_profile:
        neighbor_scale = neighbor_profile["scale"]
        neighbor_std = neighbor_profile["std"]
        neighbor_count = neighbor_profile["count"]

        is_confident_neighbor = (
            neighbor_count >= gif_cfg.neighbor_scale_confident_min_count
            and neighbor_std <= gif_cfg.neighbor_scale_confident_max_std
        )

        safety = (
            gif_cfg.neighbor_scale_safety_confident
            if is_confident_neighbor
            else gif_cfg.neighbor_scale_safety
        )

        safe_neighbor_scale = neighbor_scale * safety
        size_ratio_floor = (target_mid / init_size) ** 0.5 * 0.99
        if size_ratio_floor > safe_neighbor_scale:
            safe_neighbor_scale = size_ratio_floor

        return (
            safe_neighbor_scale,
            f"neighbor stats (safe x{safety:.3f}, n={neighbor_count}, std={neighbor_std:.3f})",
        )
    if delta_avg is not None:
        predicted_medcut = init_size + delta_avg * bias_factor
        scale_from_delta = (target_mid / predicted_medcut) ** 0.5
        return scale_from_delta * 0.97, "delta_avg (conservative)"
    scale_from_formula = (target_mid / (init_size * bias_factor)) ** 0.5
    return scale_from_formula * 0.95, "formula (conservative)"


def _next_scale(scale, low_scale, high_scale, med_cache, target_mid, max_step_ratio):
    new_scale = (low_scale + high_scale) / 2

    if abs(new_scale - scale) > scale * max_step_ratio:
        direction = 1 if new_scale > scale else -1
        new_scale = scale + direction * scale * max_step_ratio

    low_key = _scale_key(low_scale)
    high_key = _scale_key(high_scale)
    if low_key in med_cache and high_key in med_cache and low_scale != high_scale:
        med_low = med_cache[low_key][0]
        med_high = med_cache[high_key][0]
        if med_high != med_low:
            secant_scale = low_scale + (target_mid - med_low) * (high_scale - low_scale) / (med_high - med_low)
            if abs(secant_scale - scale) <= scale * max_step_ratio:
                new_scale = secant_scale

    return new_scale


def _print_gif_result_header(input_path, total_frames, palette_count, width, height, version):
    print(
        f"{version} | [gif.result] file: {os.path.basename(input_path)} "
        f"| Frames={total_frames} | Palette={palette_count} | WxH={width}x{height}"
    )


def _decode_gif_input(input_path, gif_cfg, version):
    frames_raw, durations = [], []
    with Image.open(input_path) as img:
        width, height = img.size
        total_frames = img.n_frames
        colors_first = len(img.getcolors(maxcolors=256 * 256) or [])
        palette_limit = min(colors_first + gif_cfg.extra_palette, 256)

        print(f"{version} | [gif.prepare] Starting file: {input_path}")
        init_size = os.path.getsize(input_path) / (1024 * 1024)
        print(f"{version} | [gif.prepare] Initial Size: {init_size:.2f} MB | Frames={total_frames} | Palette={colors_first} | WxH={width}x{height}")

        decode_start = time.time()
        for frame in ImageSequence.Iterator(img):
            frames_raw.append(frame.convert("RGB"))
            durations.append(frame.info.get("duration", 100))
        print(f"{version} | [gif.diag] decode={time.time() - decode_start:.2f}s ({total_frames} frames)")

    return {
        "frames_raw": frames_raw,
        "durations": durations,
        "width": width,
        "height": height,
        "total_frames": total_frames,
        "colors_first": colors_first,
        "palette_limit": palette_limit,
        "init_size": init_size,
    }


def _save_success_result(
    *,
    input_path,
    output_bytes,
    init_size,
    result_size,
    iteration,
    started_at,
    total_frames,
    colors_first,
    width,
    height,
    version,
    success_label,
):
    with open(input_path, "wb") as f:
        f.write(output_bytes)
    elapsed = time.time() - started_at
    _print_gif_result_header(input_path, total_frames, colors_first, width, height, version)
    print(
        f"{version} | ✅ Success ({success_label}): {init_size:.2f} MB -> {result_size:.2f} MB "
        f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
    )


def _try_fast_accept(
    *,
    iteration,
    fast_size,
    fast_bytes,
    state,
    stats_mgr,
    palette_limit,
    width,
    height,
    total_frames,
    colors_first,
    input_path,
    init_size,
    started_at,
    gif_cfg,
    version,
):
    fast_in_preferred = is_in_preferred_range(fast_size, gif_cfg)
    fast_in_target = gif_cfg.target_min_mb <= fast_size <= gif_cfg.target_max_mb

    can_fast_direct_accept = (
        gif_cfg.fast_direct_accept_enabled
        and iteration == 0
        and fast_in_target
        and total_frames >= gif_cfg.fast_direct_min_frames
    )
    if can_fast_direct_accept:
        fast_saved_size = len(fast_bytes) / (1024 * 1024)
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_saved_size, state.scale)
        _save_success_result(
            input_path=input_path,
            output_bytes=fast_bytes,
            init_size=init_size,
            result_size=fast_saved_size,
            iteration=iteration,
            started_at=started_at,
            total_frames=total_frames,
            colors_first=colors_first,
            width=width,
            height=height,
            version=version,
            success_label="fast-direct",
        )
        return True

    if iteration >= 1 and fast_in_preferred:
        fast_saved_size = len(fast_bytes) / (1024 * 1024)
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_size, state.scale)
        _save_success_result(
            input_path=input_path,
            output_bytes=fast_bytes,
            init_size=init_size,
            result_size=fast_saved_size,
            iteration=iteration,
            started_at=started_at,
            total_frames=total_frames,
            colors_first=colors_first,
            width=width,
            height=height,
            version=version,
            success_label="fast",
        )
        return True

    return False


def _finalize_medcut_success(
    *,
    input_path,
    stats_mgr,
    palette_limit,
    width,
    height,
    total_frames,
    colors_first,
    fast_size,
    med_size,
    med_bytes,
    state,
    init_size,
    iteration,
    started_at,
    version,
):
    print(f"{version} | [gif.finalize] Save final result and stats")
    save_start = time.time()
    stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, med_size, state.scale)
    with open(input_path, "wb") as f:
        f.write(med_bytes)
    print(f"{version} | [gif.diag] save+stats={time.time() - save_start:.2f}s")
    elapsed = time.time() - started_at
    _print_gif_result_header(input_path, total_frames, colors_first, width, height, version)
    print(
        f"{version} | ✅ Success: {init_size:.2f} MB -> {med_size:.2f} MB "
        f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
    )


def _try_temporal_preserve(
    *,
    iteration,
    med_size,
    target_mid,
    frames_raw,
    durations,
    width,
    height,
    palette_limit,
    executor,
    workers,
    gif_cfg,
    state,
    stats_mgr,
    total_frames,
    fast_size,
    input_path,
    init_size,
    started_at,
    colors_first,
    version,
):
    can_try_temporal_preserve = (
        gif_cfg.temporal_preserve_enabled
        and not state.temporal_applied
        and iteration == 0
        and med_size > gif_cfg.target_max_mb
        and total_frames >= gif_cfg.temporal_min_frames
        and (width * height) <= gif_cfg.temporal_max_pixels
        and state.scale < 0.85
    )
    if not can_try_temporal_preserve:
        return {
            "handled": False,
            "succeeded": False,
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    target_ratio = med_size / target_mid if target_mid > 0 else 1.0
    keep_every = max(2, min(gif_cfg.temporal_max_keep_every, int(round(target_ratio))))
    t_frames, t_durations = temporal_reduce(frames_raw, durations, keep_every)

    if len(t_frames) >= len(frames_raw):
        return {
            "handled": False,
            "succeeded": False,
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    t_start = time.time()
    t_resized = resize_frames(t_frames, width, height, 1.0)
    t_buf, t_med_size = compress_med_cut(
        t_resized,
        t_durations,
        palette_limit,
        executor,
        workers,
        gif_cfg,
        final=False,
    )
    t_elapsed = time.time() - t_start
    print(
        f"{version} | [gif.temporal] Temporal preserve probe | keep_every={keep_every} "
        f"| frames {len(frames_raw)}->{len(t_frames)} | MEDIANCUT={t_med_size:.2f} MB "
        f"| finished in {t_elapsed:.2f} sec"
    )

    if is_in_target_range(t_med_size, gif_cfg):
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, t_med_size, 1.0)
        with open(input_path, "wb") as f:
            f.write(t_buf.getvalue())
        elapsed = time.time() - started_at
        _print_gif_result_header(input_path, total_frames, colors_first, width, height, version)
        print(
            f"{version} | ✅ Success (temporal-preserve): {init_size:.2f} MB -> {t_med_size:.2f} MB "
            f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
        )
        return {
            "handled": True,
            "succeeded": True,
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    if t_med_size < med_size:
        new_total_frames = len(t_frames)
        state.fast_cache.clear()
        state.med_cache.clear()
        state.low_scale = 0.01
        state.high_scale = min(state.high_scale, 1.0)
        state.scale = min(1.0, state.scale / 0.92)
        state.temporal_applied = True
        print(
            f"{version} | [gif.temporal] Temporal preserve enabled -> continue with original WxH and "
            f"{new_total_frames} frames"
        )
        return {
            "handled": True,
            "succeeded": False,
            "frames_raw": t_frames,
            "durations": t_durations,
            "total_frames": new_total_frames,
        }

    return {
        "handled": False,
        "succeeded": False,
        "frames_raw": frames_raw,
        "durations": durations,
        "total_frames": total_frames,
    }


def _try_quality_retry(
    *,
    iteration,
    in_target,
    small_res_high_frames,
    med_size,
    target_mid,
    frames_raw,
    durations,
    width,
    height,
    palette_limit,
    executor,
    workers,
    gif_cfg,
    state,
    stats_mgr,
    total_frames,
    fast_size,
    input_path,
    init_size,
    started_at,
    colors_first,
    version,
):
    can_try_quality_retry = (
        gif_cfg.quality_retry_small_res_enabled
        and not state.quality_retry_done
        and not state.temporal_applied
        and iteration == 0
        and in_target
        and small_res_high_frames
        and state.scale < gif_cfg.quality_retry_min_scale
    )
    if not can_try_quality_retry:
        return False

    state.quality_retry_done = True
    target_ratio = med_size / target_mid if target_mid > 0 else 1.0
    keep_every = max(2, min(gif_cfg.temporal_max_keep_every, int(round(target_ratio))))
    q_frames, q_durations = temporal_reduce(frames_raw, durations, keep_every)

    if len(q_frames) >= len(frames_raw):
        return False

    q_start = time.time()
    q_resized = resize_frames(q_frames, width, height, 1.0)
    q_buf, q_med_size = compress_med_cut(
        q_resized,
        q_durations,
        palette_limit,
        executor,
        workers,
        gif_cfg,
        final=False,
    )
    q_elapsed = time.time() - q_start
    print(
        f"{version} | [gif.temporal] Quality retry (temporal) | keep_every={keep_every} "
        f"| frames {len(frames_raw)}->{len(q_frames)} | MEDIANCUT={q_med_size:.2f} MB "
        f"| finished in {q_elapsed:.2f} sec"
    )

    if is_in_target_range(q_med_size, gif_cfg):
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, q_med_size, 1.0)
        with open(input_path, "wb") as f:
            f.write(q_buf.getvalue())
        elapsed = time.time() - started_at
        _print_gif_result_header(input_path, len(q_frames), colors_first, width, height, version)
        print(
            f"{version} | ✅ Success (quality-preserve temporal): {init_size:.2f} MB -> {q_med_size:.2f} MB "
            f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
        )
        return True

    return False


def _advance_scale_after_medcut(*, state, med_size, target_mid, gif_cfg, med_cache, version):
    if med_size > gif_cfg.target_max_mb:
        state.high_scale = state.scale
    else:
        state.low_scale = state.scale

    adaptive_scale = state.scale
    if med_size > 0:
        adaptive_scale = state.scale * (target_mid / med_size) ** 0.5

    max_adaptive_step_ratio = min(0.35, gif_cfg.max_scale_step_ratio * 2.5)
    adaptive_step = state.scale * max_adaptive_step_ratio
    if abs(adaptive_scale - state.scale) > adaptive_step:
        direction = 1 if adaptive_scale > state.scale else -1
        adaptive_scale = state.scale + direction * adaptive_step

    adaptive_in_bracket = state.low_scale < adaptive_scale < state.high_scale
    if adaptive_in_bracket:
        new_scale = adaptive_scale
    else:
        new_scale = _next_scale(
            scale=state.scale,
            low_scale=state.low_scale,
            high_scale=state.high_scale,
            med_cache=med_cache,
            target_mid=target_mid,
            max_step_ratio=gif_cfg.max_scale_step_ratio,
        )
    print(f"{version} | [gif.next-scale] Compute next scale")
    print(f"{version} | [gif.next-scale] Next scale={new_scale:.3f}")
    print(f"{version} | [gif.next-scale] -> bracket: low={state.low_scale:.3f}, high={state.high_scale:.3f}")
    state.scale = new_scale


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


def balanced_compress_gif(
    input_path,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    debug_log_fn=None,
):
    started_at = time.time()
    print(f"{version} | [gif.prepare] Read and decode frames")

    def debug_log(message):
        if debug_log_fn is not None:
            debug_log_fn(message)
        elif log_level == "DEBUG":
            print(f"{version} | Debug | {message}")

    decoded = _decode_gif_input(input_path, gif_cfg, version)
    frames_raw = decoded["frames_raw"]
    durations = decoded["durations"]
    width = decoded["width"]
    height = decoded["height"]
    total_frames = decoded["total_frames"]
    colors_first = decoded["colors_first"]
    palette_limit = decoded["palette_limit"]
    init_size = decoded["init_size"]

    workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"{version} | [gif.prepare] Using {workers} workers for {total_frames} frames")
    debug_log(f"log_level={log_level} | max_safe_iterations={gif_cfg.max_safe_iterations}")

    target_mid = (gif_cfg.target_min_mb + gif_cfg.target_max_mb) / 2
    bias_factor = 1.1 + 0.05 * (palette_limit / 256.0)

    stats_mgr = CompressorStatsManager(stats_file, version)
    scale, source = _choose_initial_scale(
        stats_mgr,
        palette_limit,
        width,
        height,
        total_frames,
        init_size,
        target_mid,
        bias_factor,
        gif_cfg,
    )

    print(f"{version} | [gif.predict] Prediction source: {source}")
    print(f"{version} | [gif.predict] -> initial scale={scale:.3f}")

    state = GifRuntimeState(
        scale=scale,
        low_scale=0.01,
        high_scale=4.0,
        fast_cache={},
        med_cache={},
    )
    small_res_high_frames = (
        (width * height) <= gif_cfg.temporal_max_pixels
        and total_frames >= gif_cfg.temporal_min_frames
    )

    pool_start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        print(f"{version} | [gif.diag] pool_startup={time.time() - pool_start:.2f}s")
        for iteration in range(gif_cfg.max_safe_iterations):
            print(f"{version} | [gif.fast] Iteration {iteration+1}: FASTOCTREE trial")
            resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                iteration=iteration,
                scale=state.scale,
                frames_raw=frames_raw,
                width=width,
                height=height,
                palette_limit=palette_limit,
                durations=durations,
                fast_cache=state.fast_cache,
                version=version,
                stage_tag="base",
            )

            if _try_fast_accept(
                iteration=iteration,
                fast_size=fast_size,
                fast_bytes=fast_bytes,
                state=state,
                stats_mgr=stats_mgr,
                palette_limit=palette_limit,
                width=width,
                height=height,
                total_frames=total_frames,
                colors_first=colors_first,
                input_path=input_path,
                init_size=init_size,
                started_at=started_at,
                gif_cfg=gif_cfg,
                version=version,
            ):
                return

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

            source_is_neighbor = source.startswith("neighbor stats")
            if _try_hard_skip(
                iteration=iteration,
                source=source,
                source_is_neighbor=source_is_neighbor,
                fast_size=fast_size,
                state=state,
                target_mid=target_mid,
                bias_factor=bias_factor,
                stats_mgr=stats_mgr,
                palette_limit=palette_limit,
                width=width,
                height=height,
                total_frames=total_frames,
                gif_cfg=gif_cfg,
                version=version,
            ) is not None:
                continue

            should_probe_formula = source == "formula (conservative)"
            should_probe_neighbor = (
                source_is_neighbor
                and colors_first >= gif_cfg.sample_probe_neighbor_min_palette
                and total_frames >= gif_cfg.sample_probe_neighbor_min_frames
            )
            predicted_medcut, sample_probe_measured_this_iter = _run_sample_probe(
                iteration=iteration,
                should_probe_formula=should_probe_formula,
                should_probe_neighbor=should_probe_neighbor,
                resized_frames=resized_frames,
                durations=durations,
                palette_limit=palette_limit,
                executor=executor,
                workers=workers,
                gif_cfg=gif_cfg,
                state=state,
                predicted_medcut=predicted_medcut,
                fast_size=fast_size,
                total_frames=total_frames,
                version=version,
            )

            print(f"{version} | [gif.predict] -> Predicted MEDIANCUT={predicted_medcut:.2f} MB | scale={state.scale:.3f}")
            print(f"{version} | [gif.predict] -> source: {source}")

            skip_decision = build_skip_decision(
                iteration=iteration,
                source=source,
                source_is_neighbor=source_is_neighbor,
                should_probe_formula=should_probe_formula,
                should_probe_neighbor=should_probe_neighbor,
                sample_ratio=state.sample_ratio,
                sample_probe_measured_this_iter=sample_probe_measured_this_iter,
                predicted_medcut=predicted_medcut,
                fast_size=fast_size,
                current_scale=state.scale,
                low_scale=state.low_scale,
                high_scale=state.high_scale,
                target_mid=target_mid,
                formula_extra_skip_used=state.formula_extra_skip_used,
                gif_cfg=gif_cfg,
            )
            if skip_decision.should_skip:
                print(f"{version} | [gif.skip] Skip decision accepted")
                debug_log("decision=skip_first_med | reason=formula prediction well above target")
                state.low_scale = skip_decision.next_low_scale
                state.high_scale = skip_decision.next_high_scale
                if skip_decision.mark_formula_extra_skip_used:
                    state.formula_extra_skip_used = True
                print(f"{version} | [gif.skip] Skipping MEDIANCUT on iter {iteration+1} ({skip_decision.reason})")
                print(f"{version} | [gif.skip] -> next scale={skip_decision.suggested_scale:.3f}")
                state.scale = skip_decision.suggested_scale
                continue

            if _try_formula_under_target_skip(
                iteration=iteration,
                source=source,
                predicted_medcut=predicted_medcut,
                fast_size=fast_size,
                state=state,
                target_mid=target_mid,
                gif_cfg=gif_cfg,
                version=version,
            ) is not None:
                continue

            resized_adj, fast_size, fast_bytes, predicted_medcut = _apply_iter0_adjustments(
                iteration=iteration,
                source=source,
                source_is_neighbor=source_is_neighbor,
                fast_size=fast_size,
                fast_bytes=fast_bytes,
                target_mid=target_mid,
                predicted_medcut=predicted_medcut,
                state=state,
                frames_raw=frames_raw,
                width=width,
                height=height,
                palette_limit=palette_limit,
                durations=durations,
                gif_cfg=gif_cfg,
                stats_mgr=stats_mgr,
                total_frames=total_frames,
                bias_factor=bias_factor,
                executor=executor,
                workers=workers,
                debug_log=debug_log,
                version=version,
            )
            if resized_adj is not None:
                resized_frames = resized_adj

            med_size, med_bytes = _run_medcut_step(
                iteration=iteration,
                resized_frames=resized_frames,
                durations=durations,
                palette_limit=palette_limit,
                executor=executor,
                workers=workers,
                gif_cfg=gif_cfg,
                state=state,
                debug_log=debug_log,
                version=version,
            )

            pred_error = med_size - predicted_medcut
            pred_error_pct = (pred_error / predicted_medcut * 100.0) if predicted_medcut > 0 else 0.0
            debug_log(f"prediction_error={pred_error:+.2f} MB ({pred_error_pct:+.2f}%)")

            signature = (_scale_key(state.scale), round(med_size, 2))
            if signature == state.last_signature:
                state.stall_count += 1
            else:
                state.stall_count = 0
                state.last_signature = signature
            if state.stall_count >= 2:
                debug_log("stall_guard=active | repeated (scale, med_size) signature")

            print(f"{version} | [gif.compare] Delta vs FASTOCTREE = {med_size - fast_size:+.2f} MB")

            temporal_result = _try_temporal_preserve(
                iteration=iteration,
                med_size=med_size,
                target_mid=target_mid,
                frames_raw=frames_raw,
                durations=durations,
                width=width,
                height=height,
                palette_limit=palette_limit,
                executor=executor,
                workers=workers,
                gif_cfg=gif_cfg,
                state=state,
                stats_mgr=stats_mgr,
                total_frames=total_frames,
                fast_size=fast_size,
                input_path=input_path,
                init_size=init_size,
                started_at=started_at,
                colors_first=colors_first,
                version=version,
            )
            if temporal_result["handled"]:
                if temporal_result["succeeded"]:
                    return
                frames_raw = temporal_result["frames_raw"]
                durations = temporal_result["durations"]
                total_frames = temporal_result["total_frames"]
                continue

            in_preferred_corridor = (
                iteration >= 1
                and is_in_preferred_range(med_size, gif_cfg)
            )
            in_target = is_in_target_range(med_size, gif_cfg)

            if _try_quality_retry(
                iteration=iteration,
                in_target=in_target,
                small_res_high_frames=small_res_high_frames,
                med_size=med_size,
                target_mid=target_mid,
                frames_raw=frames_raw,
                durations=durations,
                width=width,
                height=height,
                palette_limit=palette_limit,
                executor=executor,
                workers=workers,
                gif_cfg=gif_cfg,
                state=state,
                stats_mgr=stats_mgr,
                total_frames=total_frames,
                fast_size=fast_size,
                input_path=input_path,
                init_size=init_size,
                started_at=started_at,
                colors_first=colors_first,
                version=version,
            ):
                return

            if in_preferred_corridor or in_target:
                _finalize_medcut_success(
                    input_path=input_path,
                    stats_mgr=stats_mgr,
                    palette_limit=palette_limit,
                    width=width,
                    height=height,
                    total_frames=total_frames,
                    colors_first=colors_first,
                    fast_size=fast_size,
                    med_size=med_size,
                    med_bytes=med_bytes,
                    state=state,
                    init_size=init_size,
                    iteration=iteration,
                    started_at=started_at,
                    version=version,
                )
                return

            _advance_scale_after_medcut(
                state=state,
                med_size=med_size,
                target_mid=target_mid,
                gif_cfg=gif_cfg,
                med_cache=state.med_cache,
                version=version,
            )

    print(f"{version} | [gif.fail] Failed to converge after {gif_cfg.max_safe_iterations} iterations")


def process_gifs(
    gif_paths,
    animated_webp_paths,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    compress_animated_webp_until_under_target,
    debug_log_fn=None,
):
    worked = False
    for file_path in gif_paths:
        worked = True
        try:
            balanced_compress_gif(
                file_path,
                gif_cfg=gif_cfg,
                version=version,
                stats_file=stats_file,
                log_level=log_level,
                debug_log_fn=debug_log_fn,
            )
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    for file_path in animated_webp_paths:
        worked = True
        try:
            compress_animated_webp_until_under_target(file_path)
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    return worked
