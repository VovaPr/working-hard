import time

from compressor_gif_runtime import is_in_target_range
from gif_ops import compress_med_cut, resize_frames, temporal_reduce

from gif_balanced_result import _print_gif_result_header


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
        gif_cfg.temporal.temporal_preserve_enabled
        and not state.temporal_applied
        and iteration == 0
        and med_size > gif_cfg.targets.target_max_mb
        and total_frames >= gif_cfg.temporal.temporal_min_frames
        and (width * height) <= gif_cfg.temporal.temporal_max_pixels
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
    keep_every = max(2, min(gif_cfg.temporal.temporal_max_keep_every, int(round(target_ratio))))
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
        stats_mgr.defer_stats(palette_limit, width, height, total_frames, fast_size, t_med_size, 1.0)
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
        gif_cfg.temporal.quality_retry_small_res_enabled
        and not state.quality_retry_done
        and not state.temporal_applied
        and iteration == 0
        and in_target
        and small_res_high_frames
        and state.scale < gif_cfg.temporal.quality_retry_min_scale
    )
    if not can_try_quality_retry:
        return False

    state.quality_retry_done = True
    target_ratio = med_size / target_mid if target_mid > 0 else 1.0
    keep_every = max(2, min(gif_cfg.temporal.temporal_max_keep_every, int(round(target_ratio))))
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
        stats_mgr.defer_stats(palette_limit, width, height, total_frames, fast_size, q_med_size, 1.0)
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
