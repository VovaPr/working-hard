from compressor_gif_runtime import (
    build_skip_decision,
    is_in_preferred_range,
    is_in_target_range,
    predict_medcut_size,
)
from gif_loop_steps import (
    _apply_iter0_adjustments,
    _run_medcut_step,
    _run_sample_probe,
    _try_formula_under_target_skip,
    _try_hard_skip,
)
from gif_ops import _clamp_prediction, _scale_key
from gif_probe import _run_fastoctree_trial
from gif_scale import _advance_scale_after_medcut

from gif_balanced_result import _finalize_medcut_success, _save_success_result, _try_fast_accept
from gif_balanced_temporal import _try_quality_retry, _try_temporal_preserve


def _advance_fast_only_scale(*, state, fast_size, target_mid, gif_cfg, version):
    if fast_size < gif_cfg.target_min_mb:
        state.low_scale = max(state.low_scale, state.scale)
    elif fast_size > gif_cfg.target_max_mb:
        state.high_scale = min(state.high_scale, state.scale)

    suggested_scale = state.scale * (target_mid / max(fast_size, 0.1)) ** 0.5 if fast_size > 0 else state.scale
    max_step = state.scale * max(0.30, gif_cfg.max_scale_step_ratio * 2.0)
    if abs(suggested_scale - state.scale) > max_step:
        direction = 1 if suggested_scale > state.scale else -1
        suggested_scale = state.scale + direction * max_step
    if not (state.low_scale < suggested_scale < state.high_scale):
        suggested_scale = (state.low_scale + state.high_scale) / 2

    print(f"{version} | [gif.guard] FAST-only next scale={suggested_scale:.3f}")
    state.scale = suggested_scale


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
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
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
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
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
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

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

    if gif_cfg.medcut_overhead_guard_enabled and state.medcut_overhead_hits >= gif_cfg.medcut_overhead_guard_max_hits:
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
            "done": False,
            "frames_raw": frames_raw,
            "durations": durations,
            "total_frames": total_frames,
        }

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
