"""WEBP-specific tuning adapter built on top of shared tuner helpers."""

from compression_tuner import frame_adjusted_timeout, seed_ratio_quality


def _seed_default_quality(*, init_size, target_mid_bytes, target_max_mb):
    # For files already near the upper bound, avoid starting too close to q=95.
    # This reduces first-step overshoot and converges faster.
    target_max_bytes = target_max_mb * 1024 * 1024
    near_target = init_size > 0 and target_max_bytes > 0 and (init_size / target_max_bytes) <= 1.25
    capped_max_quality = 88 if near_target else 95
    return seed_ratio_quality(
        init_size_bytes=init_size,
        target_mid_bytes=target_mid_bytes,
        min_quality=60,
        max_quality=capped_max_quality,
        bias=1.02,
    )


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
            gif_cfg.targets.target_min_mb,
            gif_cfg.targets.target_max_mb,
            gif_cfg,
        )

    known_result_size_mb = None
    startup_pre_resize = None
    if startup_plan is not None:
        quality = startup_plan["quality"]
        source = startup_plan["source"]
        direct_final_from_stats = startup_plan["direct_final"]
        known_result_size_mb = startup_plan.get("result_size_mb")
        startup_pre_resize = startup_plan.get("pre_resize")
    elif stats_mgr_webp and width and height and frame_count:
        quality = _seed_default_quality(
            init_size=init_size,
            target_mid_bytes=target_mid_bytes,
            target_max_mb=gif_cfg.targets.target_max_mb,
        )
        source = (
            f"default (no webp match, records={stats_mgr_webp.stats_count()}, "
            f"ratio-seeded q={quality})"
        )
        direct_final_from_stats = False
    else:
        quality = _seed_default_quality(
            init_size=init_size,
            target_mid_bytes=target_mid_bytes,
            target_max_mb=gif_cfg.targets.target_max_mb,
        )
        source = f"default (stats unavailable, ratio-seeded q={quality})"
        direct_final_from_stats = False

    return quality, source, direct_final_from_stats, known_result_size_mb, startup_pre_resize


def resolve_runtime_settings(gif_cfg, frame_count, local_version, direct_final_from_stats, known_result_size_mb):
    webp_method = max(0, min(6, gif_cfg.webp.webp_animated_method_default))
    webp_method_direct_fast = max(0, min(6, gif_cfg.webp.webp_animated_direct_final_fast_method))
    webp_method_exploratory_fast = max(0, min(6, gif_cfg.webp.webp_animated_exploratory_fast_method))
    direct_fast_growth = max(1.0, float(gif_cfg.webp.webp_animated_direct_final_fast_max_growth))
    direct_fast_safety_ratio = max(0.50, min(1.0, float(gif_cfg.webp.webp_animated_direct_final_fast_safety_ratio)))
    effective_max_seconds = frame_adjusted_timeout(
        frame_count=frame_count,
        base_seconds=gif_cfg.webp.webp_file_max_seconds,
        min_seconds=gif_cfg.webp.webp_file_min_seconds,
        per_frame_seconds=gif_cfg.webp.webp_animated_max_seconds_per_frame,
    )
    if effective_max_seconds < gif_cfg.webp.webp_file_max_seconds:
        print(
            f"{local_version} | [webp.startup] | timeout={effective_max_seconds:.0f}s "
            f"(frame-adjusted, frames={frame_count}, base={gif_cfg.webp.webp_file_max_seconds:.0f}s)"
        )

    can_use_direct_fast = False
    if (
        direct_final_from_stats
        and gif_cfg.webp.webp_animated_direct_final_fast_enabled
        and known_result_size_mb is not None
    ):
        projected_fast_mb = known_result_size_mb * direct_fast_growth
        safe_target_max_mb = gif_cfg.targets.target_max_mb * direct_fast_safety_ratio
        can_use_direct_fast = projected_fast_mb <= safe_target_max_mb

    if direct_final_from_stats:
        direct_mode = webp_method_direct_fast if can_use_direct_fast else webp_method
        print(
            f"{local_version} | [webp.startup] | direct-final enabled | method={direct_mode}"
        )
        if gif_cfg.webp.webp_animated_direct_final_fast_enabled and not can_use_direct_fast:
            print(
                f"{local_version} | [webp.startup] | direct-fast skipped | "
                f"known={known_result_size_mb:.2f} MB growth_limit={direct_fast_growth:.2f}x "
                f"safety={direct_fast_safety_ratio:.2f}"
            )

    can_use_exploratory_fast = (
        gif_cfg.webp.webp_animated_exploratory_fast_enabled
        and not direct_final_from_stats
        and (frame_count or 0) >= gif_cfg.webp.webp_animated_exploratory_fast_min_frames
        and webp_method_exploratory_fast != webp_method
    )
    if can_use_exploratory_fast:
        print(
            f"{local_version} | [webp.startup] | exploratory-fast enabled "
            f"method={webp_method_exploratory_fast} steps<="
            f"{gif_cfg.webp.webp_animated_exploratory_fast_max_steps}"
        )

    return {
        "webp_method": webp_method,
        "webp_method_direct_fast": webp_method_direct_fast,
        "webp_method_exploratory_fast": webp_method_exploratory_fast,
        "effective_max_seconds": effective_max_seconds,
        "can_use_direct_fast": can_use_direct_fast,
        "can_use_exploratory_fast": can_use_exploratory_fast,
        "exploratory_fast_max_steps": gif_cfg.webp.webp_animated_exploratory_fast_max_steps,
    }
