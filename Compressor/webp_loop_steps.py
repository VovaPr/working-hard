import time

from webp_persist_steps import persist_best_effort, persist_success_result
from webp_timeout_steps import try_timeout_rescue


def resolve_startup_quality(
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    target_mid_bytes,
    gif_cfg,
):
    startup_plan = None
    if stats_mgr_webp and width and height and frame_count:
        startup_plan = stats_mgr_webp.select_startup_plan(
            width,
            height,
            frame_count,
            init_size / (1024 * 1024),
            gif_cfg.target_min_mb,
            gif_cfg.target_max_mb,
            gif_cfg,
        )

    known_result_size_mb = None
    if startup_plan is not None:
        quality = startup_plan["quality"]
        source = startup_plan["source"]
        direct_final_from_stats = startup_plan["direct_final"]
        known_result_size_mb = startup_plan.get("result_size_mb")
    elif stats_mgr_webp and width and height and frame_count:
        ratio = (target_mid_bytes / init_size) ** 0.5 if init_size > 0 else 1.0
        quality = max(60, min(95, int(95 * ratio * 1.02)))
        source = (
            f"default (no webp match, records={stats_mgr_webp.stats_count()}, "
            f"ratio-seeded q={quality})"
        )
        direct_final_from_stats = False
    else:
        ratio = (target_mid_bytes / init_size) ** 0.5 if init_size > 0 else 1.0
        quality = max(60, min(95, int(95 * ratio * 1.02)))
        source = f"default (stats unavailable, ratio-seeded q={quality})"
        direct_final_from_stats = False

    return quality, source, direct_final_from_stats, known_result_size_mb


def resolve_runtime_settings(gif_cfg, frame_count, local_version, direct_final_from_stats, known_result_size_mb):
    webp_method = max(0, min(6, gif_cfg.webp_animated_method_default))
    webp_method_direct_fast = max(0, min(6, gif_cfg.webp_animated_direct_final_fast_method))
    direct_fast_growth = max(1.0, float(gif_cfg.webp_animated_direct_final_fast_max_growth))
    effective_max_seconds = max(
        gif_cfg.webp_file_max_seconds,
        (frame_count or 0) * gif_cfg.webp_animated_max_seconds_per_frame,
    )
    if effective_max_seconds > gif_cfg.webp_file_max_seconds:
        print(
            f"{local_version} | [webp.startup] timeout={effective_max_seconds:.0f}s "
            f"(frame-adjusted, frames={frame_count}, base={gif_cfg.webp_file_max_seconds:.0f}s)"
        )

    can_use_direct_fast = False
    if (
        direct_final_from_stats
        and gif_cfg.webp_animated_direct_final_fast_enabled
        and known_result_size_mb is not None
    ):
        can_use_direct_fast = (known_result_size_mb * direct_fast_growth) <= gif_cfg.target_max_mb

    if direct_final_from_stats:
        direct_mode = webp_method_direct_fast if can_use_direct_fast else webp_method
        print(
            f"{local_version} | [webp.startup] direct-final enabled | method={direct_mode}"
        )
        if gif_cfg.webp_animated_direct_final_fast_enabled and not can_use_direct_fast:
            print(
                f"{local_version} | [webp.startup] direct-fast skipped | "
                f"known={known_result_size_mb:.2f} MB growth_limit={direct_fast_growth:.2f}x"
            )

    return {
        "webp_method": webp_method,
        "webp_method_direct_fast": webp_method_direct_fast,
        "effective_max_seconds": effective_max_seconds,
        "can_use_direct_fast": can_use_direct_fast,
    }


def encode_with_fallback(frames, durations, quality, method_in_use, local_version, save_webp_frames):
    try:
        encoded_buf = save_webp_frames(frames, durations, quality, method=method_in_use)
    except ValueError as e:
        fallback_method = 0
        fallback_quality = max(1, min(100, quality))
        print(
            f"{local_version} | [webp.encode] config error: {e} "
            f"| retry q={fallback_quality} method={fallback_method}"
        )
        try:
            encoded_buf = save_webp_frames(frames, durations, fallback_quality, method=fallback_method)
            quality = fallback_quality
            method_in_use = fallback_method
        except ValueError as e2:
            print(f"{local_version} | [webp.encode] failed: {e2} | file unchanged")
            return None, quality, method_in_use

    return encoded_buf, quality, method_in_use


def maybe_fallback_from_direct_fast(
    *,
    direct_final_this_step,
    method_in_use,
    webp_method,
    target_min_bytes,
    target_max_bytes,
    encoded_size,
    encoded_buf,
    frames,
    durations,
    quality,
    local_version,
    save_webp_frames,
):
    effective_size = encoded_size
    effective_buf = encoded_buf
    effective_method = method_in_use
    fallback_elapsed = 0.0

    if direct_final_this_step and method_in_use != webp_method:
        if target_min_bytes <= encoded_size <= target_max_bytes:
            print(
                f"{local_version} | [webp.direct] accepted | size={encoded_size/1024:.2f} KB | method={method_in_use}"
            )
            return effective_size, effective_buf, effective_method, fallback_elapsed

        print(
            f"{local_version} | [webp.direct] miss | size={encoded_size/1024:.2f} KB -> fallback method={webp_method}"
        )
        fallback_start = time.time()
        try:
            final_buf = save_webp_frames(frames, durations, quality, method=webp_method)
            final_method = webp_method
        except ValueError as e:
            fallback_method = 0
            print(
                f"{local_version} | [webp.direct] fallback error: {e} | retry method={fallback_method}"
            )
            final_buf = save_webp_frames(frames, durations, quality, method=fallback_method)
            final_method = fallback_method

        fallback_elapsed = time.time() - fallback_start
        final_size = len(final_buf.getvalue())
        effective_size = final_size
        effective_buf = final_buf
        effective_method = final_method
        print(
            f"{local_version} | [webp.direct] fallback result | size={final_size/1024:.2f} KB method={final_method} | elapsed={fallback_elapsed:.2f}s"
        )

    return effective_size, effective_buf, effective_method, fallback_elapsed
