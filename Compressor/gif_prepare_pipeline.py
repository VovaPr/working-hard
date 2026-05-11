from compressor_gif_runtime import (
    build_skip_decision,
    predict_medcut_size,
)
from gif_adjustments import _apply_iter0_adjustments
from gif_sample_probe import _run_sample_probe
from gif_skip_logic import _try_formula_under_target_skip, _try_hard_skip
from gif_ops import _clamp_prediction
from gif_probe import _run_fastoctree_trial

from gif_balanced_result import _try_fast_accept


def _run_fast_trial_stage(
    *,
    iteration,
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
    target_mid,
    gif_cfg,
    started_at,
    version,
):
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
        return {
            "status": "done",
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    if state.medcut_disabled:
        from compressor_gif_runtime import is_in_target_range
        from gif_balanced_result import _save_success_result
        from gif_complete_utils import _advance_fast_only_scale

        if is_in_target_range(fast_size, gif_cfg):
            fast_saved_size = len(fast_bytes) / (1024 * 1024)
            stats_mgr.save_stats(
                palette_limit,
                width,
                height,
                total_frames,
                fast_size,
                fast_saved_size,
                state.scale,
            )
            print(f"{version} | [gif.guard] FAST-only path reached target range")
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
                success_label="fast-guard-target",
            )
            return {
                "status": "done",
                "frames_raw": frames_raw,
                "durations": durations,
                "total_frames": total_frames,
            }

        print(f"{version} | [gif.guard] MEDIANCUT disabled; FAST-only search continues")
        _advance_fast_only_scale(
            state=state,
            fast_size=fast_size,
            target_mid=target_mid,
            gif_cfg=gif_cfg,
            version=version,
        )
        return {
            "status": "continue",
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    return {
        "status": "ready",
        "resized_frames": resized_frames,
        "fast_size": fast_size,
        "fast_bytes": fast_bytes,
    }


def _predict_and_skip_stage(
    *,
    iteration,
    source,
    state,
    colors_first,
    total_frames,
    durations,
    palette_limit,
    width,
    height,
    target_mid,
    bias_factor,
    stats_mgr,
    executor,
    workers,
    gif_cfg,
    resized_frames,
    fast_size,
    version,
    debug_log,
):
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
        return {
            "status": "continue",
            "source_is_neighbor": source_is_neighbor,
            "predicted_medcut": predicted_medcut,
        }

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
        return {
            "status": "continue",
            "source_is_neighbor": source_is_neighbor,
            "predicted_medcut": predicted_medcut,
        }

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
        return {
            "status": "continue",
            "source_is_neighbor": source_is_neighbor,
            "predicted_medcut": predicted_medcut,
        }

    return {
        "status": "ready",
        "predicted_medcut": predicted_medcut,
        "source_is_neighbor": source_is_neighbor,
    }


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
        return {
            "status": fast_stage["status"],
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

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
        return {
            "status": "continue",
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

    predicted_medcut = pred_stage["predicted_medcut"]
    source_is_neighbor = pred_stage["source_is_neighbor"]

    resized_adj, fast_size, _, predicted_medcut = _apply_iter0_adjustments(
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

    return {
        "status": "ready",
        "resized_frames": resized_frames,
        "fast_size": fast_size,
        "fast_bytes": fast_bytes,
        "predicted_medcut": predicted_medcut,
        "frames_raw": frames_raw,
        "durations": durations,
        "total_frames": total_frames,
    }
