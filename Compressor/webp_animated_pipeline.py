import time

from webp_loop_steps import persist_best_effort, try_timeout_rescue
from webp_animated_steps import (
    _save_webp_frames,
    _is_in_target_range,
    _persist_success,
    _resolve_animation_startup,
    _resolve_next_quality,
    _run_encode_step,
    _try_near_target_nudge,
    _try_persist_bracket_tight,
    _try_resize_fallback,
    _update_best_effort,
    _update_quality_bracket,
)


def _compress_animated_webp(
    frames,
    durations,
    path,
    init_size,
    target_min_bytes,
    target_max_bytes,
    target_mid_bytes,
    local_version,
    gif_cfg,
    started_at,
    stats_mgr_webp=None,
    width=None,
    height=None,
    frame_count=None,
):
    """Animated WEBP compression with bracketed quality search and guarded runtime."""
    startup = _resolve_animation_startup(
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        target_mid_bytes=target_mid_bytes,
        gif_cfg=gif_cfg,
        local_version=local_version,
    )
    quality = startup["quality"]
    direct_final_from_stats = startup["direct_final_from_stats"]
    resize_count = 0
    webp_method = startup["webp_method"]
    webp_method_direct_fast = startup["webp_method_direct_fast"]
    effective_max_seconds = startup["effective_max_seconds"]
    can_use_direct_fast = startup["can_use_direct_fast"]

    under_target_q = None
    over_target_q = None
    best_effort = {"buf": None, "size": None, "quality": None, "method": None}

    for step in range(1, gif_cfg.webp_animated_max_iterations + 1):
        step_result = _run_encode_step(
            step=step,
            quality=quality,
            direct_final_from_stats=direct_final_from_stats,
            under_target_q=under_target_q,
            over_target_q=over_target_q,
            frames=frames,
            durations=durations,
            webp_method=webp_method,
            webp_method_direct_fast=webp_method_direct_fast,
            can_use_direct_fast=can_use_direct_fast,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            effective_max_seconds=effective_max_seconds,
            started_at=started_at,
            local_version=local_version,
        )
        if step_result is None:
            return

        quality = step_result["quality"]
        effective_size = step_result["effective_size"]
        effective_buf = step_result["effective_buf"]
        effective_method = step_result["effective_method"]
        step_encode_elapsed = step_result["step_encode_elapsed"]
        bracket_known = step_result["bracket_known"]

        _in_target = _is_in_target_range(
            effective_size=effective_size,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
        )
        if _in_target:
            _persist_success(
                path=path,
                effective_buf=effective_buf,
                effective_size=effective_size,
                init_size=init_size,
                quality=quality,
                effective_method=effective_method,
                resize_count=resize_count,
                local_version=local_version,
                started_at=started_at,
                stats_mgr_webp=stats_mgr_webp,
                width=width,
                height=height,
                frame_count=frame_count,
                step_encode_elapsed=step_encode_elapsed,
                target_min_bytes=target_min_bytes,
                target_max_bytes=target_max_bytes,
            )
            return

        _update_best_effort(
            best_effort=best_effort,
            effective_size=effective_size,
            effective_buf=effective_buf,
            quality=quality,
            effective_method=effective_method,
            target_mid_bytes=target_mid_bytes,
        )
        under_target_q, over_target_q = _update_quality_bracket(
            under_target_q=under_target_q,
            over_target_q=over_target_q,
            effective_size=effective_size,
            quality=quality,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            local_version=local_version,
        )

        elapsed = time.time() - started_at
        timed_out = try_timeout_rescue(
            elapsed=elapsed,
            effective_max_seconds=effective_max_seconds,
            under_target_q=under_target_q,
            over_target_q=over_target_q,
            quality=quality,
            effective_size=effective_size,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            frames=frames,
            durations=durations,
            webp_method=webp_method,
            local_version=local_version,
            save_webp_frames=_save_webp_frames,
            stats_mgr_webp=stats_mgr_webp,
            width=width,
            height=height,
            frame_count=frame_count,
            init_size=init_size,
            path=path,
            started_at=started_at,
        )
        if timed_out:
            return

        if _try_persist_bracket_tight(
            under_target_q=under_target_q,
            over_target_q=over_target_q,
            best_effort=best_effort,
            local_version=local_version,
            target_mid_bytes=target_mid_bytes,
            stats_mgr_webp=stats_mgr_webp,
            width=width,
            height=height,
            frame_count=frame_count,
            init_size=init_size,
            path=path,
            started_at=started_at,
            resize_count=resize_count,
            encode_elapsed=step_encode_elapsed,
        ):
            return

        nudged_quality = _try_near_target_nudge(
            effective_size=effective_size,
            target_mid_bytes=target_mid_bytes,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            gif_cfg=gif_cfg,
            bracket_known=bracket_known,
            quality=quality,
            local_version=local_version,
        )
        if nudged_quality is not None:
            quality = nudged_quality
            continue

        resize_result = _try_resize_fallback(
            quality=quality,
            effective_size=effective_size,
            target_mid_bytes=target_mid_bytes,
            frames=frames,
            resize_count=resize_count,
            local_version=local_version,
        )
        if resize_result is not None:
            frames, resize_count, quality, under_target_q, over_target_q = resize_result
            continue

        quality = _resolve_next_quality(
            under_target_q=under_target_q,
            over_target_q=over_target_q,
            quality=quality,
            effective_size=effective_size,
            target_mid_bytes=target_mid_bytes,
            local_version=local_version,
        )

        print(f"{local_version} | WEBP step {resize_count+1} | Quality={quality}")

    _final_msg = f"could not hit {gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB"
    persisted = persist_best_effort(
        reason="max-iterations",
        local_version=local_version,
        target_mid_bytes=target_mid_bytes,
        best_effort_buf=best_effort["buf"],
        best_effort_size=best_effort["size"],
        best_effort_q=best_effort["quality"],
        best_effort_method=best_effort["method"],
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        path=path,
        started_at=started_at,
        resize_count=resize_count,
        encode_elapsed=0,
    )
    if persisted:
        return
    print(
        f"{local_version} | вљ  WEBP animated max iterations reached; "
        f"file kept unchanged ({_final_msg})"
    )
