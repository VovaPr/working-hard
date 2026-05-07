import os
import time

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
from gif_ops import _clamp_prediction, _scale_key, compress_med_cut, resize_frames, temporal_reduce
from gif_probe import _run_fastoctree_trial
from gif_scale import _advance_scale_after_medcut


def _print_gif_result_header(input_path, total_frames, palette_count, width, height, version):
    print(
        f"{version} | [gif.result] file: {os.path.basename(input_path)} "
        f"| Frames={total_frames} | Palette={palette_count} | WxH={width}x{height}"
    )


def _save_success_result(
    *,
    input_path,
    output_bytes,
    init_size,
    result_size,
    iteration,
    started_at,
    total_frames,
    colors_first,
    width,
    height,
    version,
    success_label,
):
    with open(input_path, "wb") as f:
        f.write(output_bytes)
    elapsed = time.time() - started_at
    _print_gif_result_header(input_path, total_frames, colors_first, width, height, version)
    print(
        f"{version} | ✅ Success ({success_label}): {init_size:.2f} MB -> {result_size:.2f} MB "
        f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
    )


def _try_fast_accept(
    *,
    iteration,
    fast_size,
    fast_bytes,
    state,
    stats_mgr,
    palette_limit,
    width,
    height,
    total_frames,
    colors_first,
    input_path,
    init_size,
    started_at,
    gif_cfg,
    version,
):
    fast_in_preferred = is_in_preferred_range(fast_size, gif_cfg)
    fast_in_target = gif_cfg.target_min_mb <= fast_size <= gif_cfg.target_max_mb

    can_fast_direct_accept = (
        gif_cfg.fast_direct_accept_enabled
        and iteration == 0
        and fast_in_target
        and total_frames >= gif_cfg.fast_direct_min_frames
    )
    if can_fast_direct_accept:
        fast_saved_size = len(fast_bytes) / (1024 * 1024)
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_saved_size, state.scale)
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
            success_label="fast-direct",
        )
        return True

    if iteration >= 1 and fast_in_preferred:
        fast_saved_size = len(fast_bytes) / (1024 * 1024)
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_size, state.scale)
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
            success_label="fast",
        )
        return True

    return False


def _finalize_medcut_success(
    *,
    input_path,
    stats_mgr,
    palette_limit,
    width,
    height,
    total_frames,
    colors_first,
    fast_size,
    med_size,
    med_bytes,
    state,
    init_size,
    iteration,
    started_at,
    version,
):
    print(f"{version} | [gif.finalize] Save final result and stats")
    save_start = time.time()
    stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, med_size, state.scale)
    with open(input_path, "wb") as f:
        f.write(med_bytes)
    print(f"{version} | [gif.diag] save+stats={time.time() - save_start:.2f}s")
    elapsed = time.time() - started_at
    _print_gif_result_header(input_path, total_frames, colors_first, width, height, version)
    print(
        f"{version} | ✅ Success: {init_size:.2f} MB -> {med_size:.2f} MB "
        f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
    )


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
        gif_cfg.temporal_preserve_enabled
        and not state.temporal_applied
        and iteration == 0
        and med_size > gif_cfg.target_max_mb
        and total_frames >= gif_cfg.temporal_min_frames
        and (width * height) <= gif_cfg.temporal_max_pixels
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
    keep_every = max(2, min(gif_cfg.temporal_max_keep_every, int(round(target_ratio))))
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
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, t_med_size, 1.0)
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
        gif_cfg.quality_retry_small_res_enabled
        and not state.quality_retry_done
        and not state.temporal_applied
        and iteration == 0
        and in_target
        and small_res_high_frames
        and state.scale < gif_cfg.quality_retry_min_scale
    )
    if not can_try_quality_retry:
        return False

    state.quality_retry_done = True
    target_ratio = med_size / target_mid if target_mid > 0 else 1.0
    keep_every = max(2, min(gif_cfg.temporal_max_keep_every, int(round(target_ratio))))
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
        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, q_med_size, 1.0)
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

    print(f"{version} | [gif.compare] Delta vs FASTOCTREE = {med_size - med_input['fast_size']:+.2f} MB")

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


def _run_balanced_iteration(
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
    small_res_high_frames,
    gif_cfg,
    started_at,
    version,
    debug_log,
):
    med_input = _prepare_balanced_medcut_context(
        iteration=iteration,
        source=source,
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
        executor=executor,
        workers=workers,
        target_mid=target_mid,
        bias_factor=bias_factor,
        gif_cfg=gif_cfg,
        started_at=started_at,
        version=version,
        debug_log=debug_log,
    )
    if med_input["status"] == "done":
        return {
            "done": True,
            "frames_raw": med_input["frames_raw"],
            "durations": med_input["durations"],
            "total_frames": med_input["total_frames"],
        }
    if med_input["status"] == "continue":
        return {
            "done": False,
            "frames_raw": med_input["frames_raw"],
            "durations": med_input["durations"],
            "total_frames": med_input["total_frames"],
        }

    return _complete_balanced_iteration(
        iteration=iteration,
        state=state,
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
        debug_log=debug_log,
    )
