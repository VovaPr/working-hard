"""GIF processing pipeline extracted from the main launcher module."""

import os
import time
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageSequence

from compressor_gif_runtime import (
    GifRuntimeState,
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
from gif_ops import (
    _clamp_prediction,
    _scale_key,
    compress_med_cut,
    resize_frames,
    temporal_reduce,
)
from gif_probe import _run_fastoctree_trial
from gif_scale import _advance_scale_after_medcut, _choose_initial_scale
from gif_stats import CompressorStatsManager


def _print_gif_result_header(input_path, total_frames, palette_count, width, height, version):
    print(
        f"{version} | [gif.result] file: {os.path.basename(input_path)} "
        f"| Frames={total_frames} | Palette={palette_count} | WxH={width}x{height}"
    )


def _decode_gif_input(input_path, gif_cfg, version):
    frames_raw, durations = [], []
    with Image.open(input_path) as img:
        width, height = img.size
        total_frames = img.n_frames
        colors_first = len(img.getcolors(maxcolors=256 * 256) or [])
        palette_limit = min(colors_first + gif_cfg.extra_palette, 256)

        print(f"{version} | [gif.prepare] Starting file: {input_path}")
        init_size = os.path.getsize(input_path) / (1024 * 1024)
        print(f"{version} | [gif.prepare] Initial Size: {init_size:.2f} MB | Frames={total_frames} | Palette={colors_first} | WxH={width}x{height}")

        decode_start = time.time()
        for frame in ImageSequence.Iterator(img):
            frames_raw.append(frame.convert("RGB"))
            durations.append(frame.info.get("duration", 100))
        print(f"{version} | [gif.diag] decode={time.time() - decode_start:.2f}s ({total_frames} frames)")

    return {
        "frames_raw": frames_raw,
        "durations": durations,
        "width": width,
        "height": height,
        "total_frames": total_frames,
        "colors_first": colors_first,
        "palette_limit": palette_limit,
        "init_size": init_size,
    }


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


def balanced_compress_gif(
    input_path,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    debug_log_fn=None,
):
    started_at = time.time()
    print(f"{version} | [gif.prepare] Read and decode frames")

    def debug_log(message):
        if debug_log_fn is not None:
            debug_log_fn(message)
        elif log_level == "DEBUG":
            print(f"{version} | Debug | {message}")

    decoded = _decode_gif_input(input_path, gif_cfg, version)
    frames_raw = decoded["frames_raw"]
    durations = decoded["durations"]
    width = decoded["width"]
    height = decoded["height"]
    total_frames = decoded["total_frames"]
    colors_first = decoded["colors_first"]
    palette_limit = decoded["palette_limit"]
    init_size = decoded["init_size"]

    workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"{version} | [gif.prepare] Using {workers} workers for {total_frames} frames")
    debug_log(f"log_level={log_level} | max_safe_iterations={gif_cfg.max_safe_iterations}")

    target_mid = (gif_cfg.target_min_mb + gif_cfg.target_max_mb) / 2
    bias_factor = 1.1 + 0.05 * (palette_limit / 256.0)

    stats_mgr = CompressorStatsManager(stats_file, version)
    scale, source = _choose_initial_scale(
        stats_mgr,
        palette_limit,
        width,
        height,
        total_frames,
        init_size,
        target_mid,
        bias_factor,
        gif_cfg,
    )

    print(f"{version} | [gif.predict] Prediction source: {source}")
    print(f"{version} | [gif.predict] -> initial scale={scale:.3f}")

    state = GifRuntimeState(
        scale=scale,
        low_scale=0.01,
        high_scale=4.0,
        fast_cache={},
        med_cache={},
    )
    small_res_high_frames = (
        (width * height) <= gif_cfg.temporal_max_pixels
        and total_frames >= gif_cfg.temporal_min_frames
    )

    pool_start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        print(f"{version} | [gif.diag] pool_startup={time.time() - pool_start:.2f}s")
        for iteration in range(gif_cfg.max_safe_iterations):
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
                return

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
                continue

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
                continue

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
                continue

            resized_adj, fast_size, fast_bytes, predicted_medcut = _apply_iter0_adjustments(
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

            med_size, med_bytes = _run_medcut_step(
                iteration=iteration,
                resized_frames=resized_frames,
                durations=durations,
                palette_limit=palette_limit,
                executor=executor,
                workers=workers,
                gif_cfg=gif_cfg,
                state=state,
                debug_log=debug_log,
                version=version,
            )

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

            print(f"{version} | [gif.compare] Delta vs FASTOCTREE = {med_size - fast_size:+.2f} MB")

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
                fast_size=fast_size,
                input_path=input_path,
                init_size=init_size,
                started_at=started_at,
                colors_first=colors_first,
                version=version,
            )
            if temporal_result["handled"]:
                if temporal_result["succeeded"]:
                    return
                frames_raw = temporal_result["frames_raw"]
                durations = temporal_result["durations"]
                total_frames = temporal_result["total_frames"]
                continue

            in_preferred_corridor = (
                iteration >= 1
                and is_in_preferred_range(med_size, gif_cfg)
            )
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
                fast_size=fast_size,
                input_path=input_path,
                init_size=init_size,
                started_at=started_at,
                colors_first=colors_first,
                version=version,
            ):
                return

            if in_preferred_corridor or in_target:
                _finalize_medcut_success(
                    input_path=input_path,
                    stats_mgr=stats_mgr,
                    palette_limit=palette_limit,
                    width=width,
                    height=height,
                    total_frames=total_frames,
                    colors_first=colors_first,
                    fast_size=fast_size,
                    med_size=med_size,
                    med_bytes=med_bytes,
                    state=state,
                    init_size=init_size,
                    iteration=iteration,
                    started_at=started_at,
                    version=version,
                )
                return

            _advance_scale_after_medcut(
                state=state,
                med_size=med_size,
                target_mid=target_mid,
                gif_cfg=gif_cfg,
                med_cache=state.med_cache,
                version=version,
            )

    print(f"{version} | [gif.fail] Failed to converge after {gif_cfg.max_safe_iterations} iterations")


def process_gifs(
    gif_paths,
    animated_webp_paths,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    compress_animated_webp_until_under_target,
    debug_log_fn=None,
):
    worked = False
    for file_path in gif_paths:
        worked = True
        try:
            balanced_compress_gif(
                file_path,
                gif_cfg=gif_cfg,
                version=version,
                stats_file=stats_file,
                log_level=log_level,
                debug_log_fn=debug_log_fn,
            )
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    for file_path in animated_webp_paths:
        worked = True
        try:
            compress_animated_webp_until_under_target(file_path)
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    return worked
