from gif_medcut_step import _run_medcut_step
from gif_complete_steps import (
    _handle_overhead_guard,
    _record_prediction_and_guard_signature,
    _resolve_temporal_quality_or_finalize,
)


def _complete_balanced_iteration(
    *,
    iteration,
    state,
    med_input,
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
    small_res_high_frames,
    gif_cfg,
    started_at,
    version,
    debug_log,
):
    med_size, med_bytes = _run_medcut_step(
        iteration=iteration,
        resized_frames=med_input["resized_frames"],
        durations=durations,
        palette_limit=palette_limit,
        executor=executor,
        workers=workers,
        gif_cfg=gif_cfg,
        state=state,
        debug_log=debug_log,
        version=version,
    )

    predicted_medcut = med_input["predicted_medcut"]
    _record_prediction_and_guard_signature(
        state=state,
        med_size=med_size,
        predicted_medcut=predicted_medcut,
        debug_log=debug_log,
    )

    guard_result = _handle_overhead_guard(
        iteration=iteration,
        state=state,
        med_size=med_size,
        med_input=med_input,
        width=width,
        height=height,
        palette_limit=palette_limit,
        total_frames=total_frames,
        colors_first=colors_first,
        init_size=init_size,
        input_path=input_path,
        stats_mgr=stats_mgr,
        gif_cfg=gif_cfg,
        started_at=started_at,
        version=version,
        frames_raw=frames_raw,
        durations=durations,
    )
    if guard_result["status"] == "done":
        return {
            "done": guard_result["done"],
            "frames_raw": guard_result["frames_raw"],
            "durations": guard_result["durations"],
            "total_frames": guard_result["total_frames"],
        }

    return _resolve_temporal_quality_or_finalize(
        iteration=iteration,
        state=state,
        med_size=med_size,
        med_bytes=med_bytes,
        med_input=med_input,
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
        executor=executor,
        workers=workers,
        target_mid=target_mid,
        small_res_high_frames=small_res_high_frames,
        gif_cfg=gif_cfg,
        started_at=started_at,
        version=version,
    )
