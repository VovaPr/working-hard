from gif_prepare_steps import (
    _apply_prepare_adjustments,
    _predict_and_skip_stage,
    _run_fast_trial_stage,
    _ready_prepare_result,
    _terminal_prepare_result,
)


def _prepare_balanced_medcut_context(
    *,
    iteration,
    source,
    state,
    frames_raw,
    durations,
    width,
    height,
    palette_limit,
    total_frames,
    colors_first,
    init_size,
    input_path,
    stats_mgr,
    executor,
    workers,
    target_mid,
    bias_factor,
    gif_cfg,
    started_at,
    version,
    debug_log,
):
    fast_stage = _run_fast_trial_stage(
        iteration=iteration,
        state=state,
        frames_raw=frames_raw,
        durations=durations,
        width=width,
        height=height,
        palette_limit=palette_limit,
        total_frames=total_frames,
        colors_first=colors_first,
        init_size=init_size,
        input_path=input_path,
        stats_mgr=stats_mgr,
        target_mid=target_mid,
        gif_cfg=gif_cfg,
        started_at=started_at,
        version=version,
    )
    if fast_stage["status"] in {"done", "continue"}:
        return _terminal_prepare_result(
            status=fast_stage["status"],
            frames_raw=frames_raw,
            durations=durations,
            total_frames=total_frames,
        )

    resized_frames = fast_stage["resized_frames"]
    fast_size = fast_stage["fast_size"]
    fast_bytes = fast_stage["fast_bytes"]

    pred_stage = _predict_and_skip_stage(
        iteration=iteration,
        source=source,
        state=state,
        colors_first=colors_first,
        total_frames=total_frames,
        durations=durations,
        palette_limit=palette_limit,
        width=width,
        height=height,
        target_mid=target_mid,
        bias_factor=bias_factor,
        stats_mgr=stats_mgr,
        executor=executor,
        workers=workers,
        gif_cfg=gif_cfg,
        resized_frames=resized_frames,
        fast_size=fast_size,
        version=version,
        debug_log=debug_log,
    )
    if pred_stage["status"] == "continue":
        return _terminal_prepare_result(
            status="continue",
            frames_raw=frames_raw,
            durations=durations,
            total_frames=total_frames,
        )

    predicted_medcut = pred_stage["predicted_medcut"]
    source_is_neighbor = pred_stage["source_is_neighbor"]

    resized_frames, fast_size, fast_bytes, predicted_medcut = _apply_prepare_adjustments(
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
        resized_frames=resized_frames,
    )

    return _ready_prepare_result(
        resized_frames=resized_frames,
        fast_size=fast_size,
        fast_bytes=fast_bytes,
        predicted_medcut=predicted_medcut,
        frames_raw=frames_raw,
        durations=durations,
        total_frames=total_frames,
    )
