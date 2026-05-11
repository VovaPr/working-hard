from compressor_gif_runtime import is_in_preferred_range, is_in_target_range
from gif_medcut_step import _run_medcut_step
from gif_ops import _scale_key
from gif_scale import _advance_scale_after_medcut

from gif_balanced_result import _finalize_medcut_success, _save_success_result
from gif_balanced_temporal import _try_quality_retry, _try_temporal_preserve


def _record_prediction_and_guard_signature(*, state, med_size, predicted_medcut, debug_log):
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


def _handle_overhead_guard(
    *,
    iteration,
    state,
    med_size,
    med_input,
    width,
    height,
    palette_limit,
    total_frames,
    colors_first,
    init_size,
    input_path,
    stats_mgr,
    gif_cfg,
    started_at,
    version,
    frames_raw,
    durations,
):
    medcut_overhead_mb = med_size - med_input["fast_size"]
    print(f"{version} | [gif.compare] Delta vs FASTOCTREE = {medcut_overhead_mb:+.2f} MB")

    if medcut_overhead_mb >= gif_cfg.medcut_overhead_guard_margin_mb:
        state.medcut_overhead_hits += 1
        print(
            f"{version} | [gif.guard] MEDIANCUT overhead hit "
            f"{state.medcut_overhead_hits}/{gif_cfg.medcut_overhead_guard_max_hits} "
            f"(delta={medcut_overhead_mb:+.2f} MB)"
        )
    else:
        state.medcut_overhead_hits = 0

    if not (
        gif_cfg.medcut_overhead_guard_enabled
        and state.medcut_overhead_hits >= gif_cfg.medcut_overhead_guard_max_hits
    ):
        return {"status": "pass"}

    if is_in_target_range(med_input["fast_size"], gif_cfg):
        fast_saved_size = len(med_input["fast_bytes"]) / (1024 * 1024)
        stats_mgr.save_stats(
            palette_limit,
            width,
            height,
            total_frames,
            med_input["fast_size"],
            fast_saved_size,
            state.scale,
        )
        print(
            f"{version} | [gif.guard] Repeated MEDIANCUT overhead is too high; "
            f"using FASTOCTREE because it is already in target"
        )
        _save_success_result(
            input_path=input_path,
            output_bytes=med_input["fast_bytes"],
            init_size=init_size,
            result_size=fast_saved_size,
            iteration=iteration,
            started_at=started_at,
            total_frames=total_frames,
            colors_first=colors_first,
            width=width,
            height=height,
            version=version,
            success_label="fast-guard-target",
        )
        return {
            "status": "done",
            "done": True,
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    print(
        f"{version} | [gif.guard] Repeated MEDIANCUT overhead is too high; "
        f"FASTOCTREE is outside target, switching to FAST-only search"
    )
    state.medcut_disabled = True
    state.medcut_overhead_hits = 0
    return {
        "status": "done",
        "done": False,
        "frames_raw": frames_raw,
        "durations": durations,
        "total_frames": total_frames,
    }


def _resolve_temporal_quality_or_finalize(
    *,
    iteration,
    state,
    med_size,
    med_bytes,
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
):
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
        fast_size=med_input["fast_size"],
        input_path=input_path,
        init_size=init_size,
        started_at=started_at,
        colors_first=colors_first,
        version=version,
    )
    if temporal_result["handled"]:
        if temporal_result["succeeded"]:
            return {
                "done": True,
                "frames_raw": frames_raw,
                "durations": durations,
                "total_frames": total_frames,
            }
        return {
            "done": False,
            "frames_raw": temporal_result["frames_raw"],
            "durations": temporal_result["durations"],
            "total_frames": temporal_result["total_frames"],
        }

    in_preferred_corridor = iteration >= 1 and is_in_preferred_range(med_size, gif_cfg)
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
        fast_size=med_input["fast_size"],
        input_path=input_path,
        init_size=init_size,
        started_at=started_at,
        colors_first=colors_first,
        version=version,
    ):
        return {
            "done": True,
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    if in_preferred_corridor or in_target:
        _finalize_medcut_success(
            input_path=input_path,
            stats_mgr=stats_mgr,
            palette_limit=palette_limit,
            width=width,
            height=height,
            total_frames=total_frames,
            colors_first=colors_first,
            fast_size=med_input["fast_size"],
            med_size=med_size,
            med_bytes=med_bytes,
            state=state,
            init_size=init_size,
            iteration=iteration,
            started_at=started_at,
            version=version,
        )
        return {
            "done": True,
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    _advance_scale_after_medcut(
        state=state,
        med_size=med_size,
        target_mid=target_mid,
        gif_cfg=gif_cfg,
        med_cache=state.med_cache,
        version=version,
    )
    return {
        "done": False,
        "frames_raw": frames_raw,
        "durations": durations,
        "total_frames": total_frames,
    }


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
