import time

from webp_animated_steps import (
    _build_animation_state,
    _handle_iteration_outcome,
    _persist_max_iterations,
    _resolve_animation_startup,
    _run_encode_step,
    _run_sample_probe_if_needed,
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
    state = _build_animation_state(startup=startup, frames=frames)

    _run_sample_probe_if_needed(
        state=state,
        frames=frames,
        durations=durations,
        target_mid_bytes=target_mid_bytes,
        frame_count=frame_count,
        local_version=local_version,
        gif_cfg=gif_cfg,
    )

    for step in range(1, gif_cfg.webp_animated_max_iterations + 1):
        step_result = _run_encode_step(
            step=step,
            quality=state["quality"],
            direct_final_from_stats=state["direct_final_from_stats"],
            under_target_q=state["under_target_q"],
            over_target_q=state["over_target_q"],
            frames=state["frames"],
            durations=durations,
            webp_method=state["webp_method"],
            webp_method_direct_fast=state["webp_method_direct_fast"],
            can_use_direct_fast=state["can_use_direct_fast"],
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            effective_max_seconds=state["effective_max_seconds"],
            started_at=started_at,
            local_version=local_version,
        )
        if step_result is None:
            return

        action = _handle_iteration_outcome(
            state=state,
            step_result=step_result,
            durations=durations,
            path=path,
            init_size=init_size,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            target_mid_bytes=target_mid_bytes,
            local_version=local_version,
            gif_cfg=gif_cfg,
            started_at=started_at,
            stats_mgr_webp=stats_mgr_webp,
            width=width,
            height=height,
            frame_count=frame_count,
        )
        if action == "done":
            return

    _persist_max_iterations(
        state=state,
        target_mid_bytes=target_mid_bytes,
        gif_cfg=gif_cfg,
        local_version=local_version,
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        path=path,
        started_at=started_at,
    )
