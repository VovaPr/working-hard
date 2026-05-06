import io
import os
import time

from PIL import Image, ImageSequence, UnidentifiedImageError

from webp_stats import AnimatedWebPStatsManager


def _save_webp_frames(frames, durations, quality, method=6):
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        quality=quality,
        method=method,
    )
    return buf


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
    startup_plan = None
    if stats_mgr_webp and width and height and frame_count:
        startup_plan = stats_mgr_webp.select_startup_plan(
            width,
            height,
            frame_count,
            init_size / (1024 * 1024),
            gif_cfg.target_min_mb,
            gif_cfg.target_max_mb,
            gif_cfg,
        )

    known_result_size_mb = None
    if startup_plan is not None:
        quality = startup_plan["quality"]
        source = startup_plan["source"]
        direct_final_from_stats = startup_plan["direct_final"]
        known_result_size_mb = startup_plan.get("result_size_mb")
    elif stats_mgr_webp and width and height and frame_count:
        ratio = (target_mid_bytes / init_size) ** 0.5 if init_size > 0 else 1.0
        quality = max(60, min(95, int(95 * ratio * 1.02)))
        source = (
            f"default (no webp match, records={stats_mgr_webp.stats_count()}, "
            f"ratio-seeded q={quality})"
        )
        direct_final_from_stats = False
    else:
        ratio = (target_mid_bytes / init_size) ** 0.5 if init_size > 0 else 1.0
        quality = max(60, min(95, int(95 * ratio * 1.02)))
        source = f"default (stats unavailable, ratio-seeded q={quality})"
        direct_final_from_stats = False

    print(f"{local_version} | Prediction source: {source} -> initial quality={quality}")

    resize_count = 0
    webp_method = max(0, min(6, gif_cfg.webp_animated_method_default))
    webp_method_direct_fast = max(0, min(6, gif_cfg.webp_animated_direct_final_fast_method))
    direct_fast_growth = max(1.0, float(gif_cfg.webp_animated_direct_final_fast_max_growth))
    effective_max_seconds = max(
        gif_cfg.webp_file_max_seconds,
        (frame_count or 0) * gif_cfg.webp_animated_max_seconds_per_frame,
    )
    if effective_max_seconds > gif_cfg.webp_file_max_seconds:
        print(
            f"{local_version} | WEBP animated timeout: {effective_max_seconds:.0f}s "
            f"(frame-adjusted for {frame_count} frames, base={gif_cfg.webp_file_max_seconds:.0f}s)"
        )

    can_use_direct_fast = False
    if direct_final_from_stats and gif_cfg.webp_animated_direct_final_fast_enabled and known_result_size_mb is not None:
        can_use_direct_fast = (known_result_size_mb * direct_fast_growth) <= gif_cfg.target_max_mb

    if direct_final_from_stats:
        direct_mode = webp_method_direct_fast if can_use_direct_fast else webp_method
        print(
            f"{local_version} | WEBP animated direct-final enabled | "
            f"known profile -> method={direct_mode}"
        )
        if gif_cfg.webp_animated_direct_final_fast_enabled and not can_use_direct_fast:
            print(
                f"{local_version} | WEBP direct-fast skipped | "
                f"known={known_result_size_mb:.2f} MB, growth_limit={direct_fast_growth:.2f}x"
            )

    under_target_q = None
    over_target_q = None
    best_effort_buf = None
    best_effort_size = None
    best_effort_q = None
    best_effort_method = None

    for step in range(1, gif_cfg.webp_animated_max_iterations + 1):
        quality = max(1, min(100, int(quality)))
        bracket_known = under_target_q is not None and over_target_q is not None
        direct_final_this_step = bool(direct_final_from_stats and step == 1)
        if direct_final_this_step:
            method_in_use = webp_method_direct_fast if can_use_direct_fast else webp_method
        else:
            method_in_use = webp_method
        _step_elapsed = time.time() - started_at
        _bracket_str = f"{under_target_q}-{over_target_q}" if bracket_known else "none"
        print(
            f"{local_version} | WEBP animated step {step} | "
            f"Encoding... (q={quality}, method={method_in_use}) | "
            f"bracket={_bracket_str} | elapsed={_step_elapsed:.1f}s/{effective_max_seconds:.0f}s"
        )
        encode_start = time.time()
        try:
            encoded_buf = _save_webp_frames(frames, durations, quality, method=method_in_use)
        except ValueError as e:
            fallback_method = 0
            fallback_quality = max(1, min(100, quality))
            print(
                f"{local_version} | WEBP animated config error: {e} "
                f"| retry with q={fallback_quality}, method={fallback_method}"
            )
            try:
                encoded_buf = _save_webp_frames(frames, durations, fallback_quality, method=fallback_method)
                quality = fallback_quality
                method_in_use = fallback_method
            except ValueError as e2:
                print(f"{local_version} | вљ  WEBP animated encode failed: {e2}; file kept unchanged")
                return

        encoded_size = len(encoded_buf.getvalue())
        effective_size = encoded_size
        effective_buf = encoded_buf
        effective_method = method_in_use
        step_encode_elapsed = time.time() - encode_start

        if direct_final_this_step and method_in_use != webp_method:
            if target_min_bytes <= encoded_size <= target_max_bytes:
                print(
                    f"{local_version} | WEBP direct-fast accepted | "
                    f"Size={encoded_size/1024:.2f} KB | method={method_in_use}"
                )
            else:
                print(
                    f"{local_version} | WEBP direct-fast miss | "
                    f"Size={encoded_size/1024:.2f} KB -> fallback method={webp_method}"
                )
                fallback_start = time.time()
                try:
                    final_buf = _save_webp_frames(frames, durations, quality, method=webp_method)
                    final_method = webp_method
                except ValueError as e:
                    fallback_method = 0
                    print(
                        f"{local_version} | WEBP direct-fast fallback error: {e} "
                        f"| retry with method={fallback_method}"
                    )
                    final_buf = _save_webp_frames(frames, durations, quality, method=fallback_method)
                    final_method = fallback_method
                fallback_elapsed = time.time() - fallback_start
                final_size = len(final_buf.getvalue())
                effective_size = final_size
                effective_buf = final_buf
                effective_method = final_method
                step_encode_elapsed += fallback_elapsed
                print(
                    f"{local_version} | WEBP direct-fast fallback result | "
                    f"Size={final_size/1024:.2f} KB | method={final_method} | fallback={fallback_elapsed:.2f} sec"
                )

        print(
            f"{local_version} | WEBP animated step {step} | "
            f"Size={effective_size/1024:.2f} KB | encode={step_encode_elapsed:.2f} sec"
        )

        _in_target = target_min_bytes <= effective_size <= target_max_bytes
        if _in_target:
            print(
                f"{local_version} | WEBP animated success check: "
                f"size={effective_size/1024:.2f} KB in range [{target_min_bytes/1024:.2f}, {target_max_bytes/1024:.2f}] KB"
            )
            if stats_mgr_webp and width and height and frame_count:
                stats_mgr_webp.save_step(
                    width,
                    height,
                    frame_count,
                    init_size / (1024 * 1024),
                    quality,
                    effective_method,
                    effective_size / (1024 * 1024),
                    step_encode_elapsed,
                )
            with open(path, "wb") as f:
                f.write(effective_buf.getvalue())
            elapsed = time.time() - started_at
            print(
                f"{local_version} | вњ… WEBP success: {init_size/1024:.2f} KB -> {effective_size/1024:.2f} KB "
                f"| Quality={quality} | Resized {resize_count} times"
            )
            if stats_mgr_webp:
                print(
                    f"{local_version} | WEBP animated stats total: {stats_mgr_webp.stats_count()} records"
                )
            print(f"{local_version} | Finished in {elapsed:.2f} sec")
            return

        if not _in_target:
            _miss_abs = abs(effective_size - target_mid_bytes)
            if best_effort_size is None or _miss_abs < abs(best_effort_size - target_mid_bytes):
                best_effort_buf = effective_buf
                best_effort_size = effective_size
                best_effort_q = quality
                best_effort_method = effective_method

        if effective_size < target_min_bytes:
            under_target_q = quality if under_target_q is None else max(under_target_q, quality)
        elif effective_size > target_max_bytes:
            over_target_q = quality if over_target_q is None else min(over_target_q, quality)
        _new_bracket = f"{under_target_q}-{over_target_q}" if (under_target_q is not None and over_target_q is not None) else f"under={under_target_q} over={over_target_q}"
        print(f"{local_version} | WEBP animated bracket update | {_new_bracket}")

        elapsed = time.time() - started_at
        if elapsed >= effective_max_seconds:
            if under_target_q is not None and over_target_q is not None and over_target_q - under_target_q >= 1:
                rescue_q = (under_target_q + over_target_q) // 2
                if rescue_q == quality:
                    if effective_size < target_min_bytes and rescue_q < over_target_q:
                        rescue_q += 1
                    elif effective_size > target_max_bytes and rescue_q > under_target_q:
                        rescue_q -= 1
                print(
                    f"{local_version} | WEBP timeout-rescue | "
                    f"bracket={under_target_q}-{over_target_q} -> verify q={rescue_q}"
                )
                rescue_start = time.time()
                try:
                    rescue_buf = _save_webp_frames(frames, durations, rescue_q, method=webp_method)
                    rescue_method = webp_method
                except ValueError:
                    rescue_method = 0
                    rescue_buf = _save_webp_frames(frames, durations, rescue_q, method=rescue_method)
                rescue_elapsed = time.time() - rescue_start
                rescue_size = len(rescue_buf.getvalue())
                if target_min_bytes <= rescue_size <= target_max_bytes:
                    if stats_mgr_webp and width and height and frame_count:
                        stats_mgr_webp.save_step(
                            width,
                            height,
                            frame_count,
                            init_size / (1024 * 1024),
                            rescue_q,
                            rescue_method,
                            rescue_size / (1024 * 1024),
                            rescue_elapsed,
                        )
                    with open(path, "wb") as f:
                        f.write(rescue_buf.getvalue())
                    total_elapsed = time.time() - started_at
                    print(
                        f"{local_version} | вњ… WEBP success (timeout-rescue): "
                        f"{init_size/1024:.2f} KB -> {rescue_size/1024:.2f} KB "
                        f"| Quality={rescue_q} | method={rescue_method}"
                    )
                    print(f"{local_version} | Finished in {total_elapsed:.2f} sec")
                    return

            print(
                f"{local_version} | вљ  WEBP animated timeout {elapsed:.2f} sec; "
                f"file kept unchanged"
            )
            return

        if (
            under_target_q is not None
            and over_target_q is not None
            and over_target_q - under_target_q <= 1
            and best_effort_buf is not None
        ):
            _best_miss_pct = abs(best_effort_size - target_mid_bytes) / target_mid_bytes * 100
            print(
                f"{local_version} | WEBP best-effort accept | "
                f"bracket={under_target_q}-{over_target_q}, no integer solution | "
                f"q={best_effort_q} size={best_effort_size/1024:.2f} KB miss={_best_miss_pct:.2f}%"
            )
            if stats_mgr_webp and width and height and frame_count:
                stats_mgr_webp.save_step(
                    width, height, frame_count,
                    init_size / (1024 * 1024),
                    best_effort_q, best_effort_method,
                    best_effort_size / (1024 * 1024),
                    step_encode_elapsed,
                )
            with open(path, "wb") as f:
                f.write(best_effort_buf.getvalue())
            elapsed = time.time() - started_at
            print(
                f"{local_version} | вњ… WEBP best-effort: {init_size/1024:.2f} KB -> {best_effort_size/1024:.2f} KB "
                f"| Quality={best_effort_q} | Resized {resize_count} times"
            )
            print(f"{local_version} | Finished in {elapsed:.2f} sec")
            return

        near_mid_ratio = abs(effective_size - target_mid_bytes) / target_mid_bytes if target_mid_bytes > 0 else 0.0
        if near_mid_ratio <= gif_cfg.webp_animated_near_band_ratio and not bracket_known:
            miss_ratio = (
                (target_min_bytes - effective_size) / target_min_bytes
                if effective_size < target_min_bytes and target_min_bytes > 0
                else (effective_size - target_max_bytes) / target_max_bytes
                if effective_size > target_max_bytes and target_max_bytes > 0
                else 0.0
            )
            nudge_step = (
                gif_cfg.webp_animated_nudge_small_step
                if miss_ratio <= gif_cfg.webp_animated_nudge_small_ratio
                else gif_cfg.webp_animated_nudge_large_step
            )
            if effective_size < target_min_bytes:
                quality = min(100, quality + nudge_step)
            else:
                quality = max(45, quality - nudge_step)
            print(
                f"{local_version} | WEBP animated near-target nudge | "
                f"miss={miss_ratio*100:.2f}% | step={nudge_step} -> next_q={quality}"
            )
            continue

        correction = (target_mid_bytes / effective_size) ** 0.5
        correction = max(0.88, min(1.12, correction))

        if quality <= 45:
            new_w = max(1, int(frames[0].width * correction))
            new_h = max(1, int(frames[0].height * correction))
            frames = [fr.resize((new_w, new_h), Image.LANCZOS) for fr in frames]
            resize_count += 1
            quality = 95
            under_target_q = None
            over_target_q = None
            print(f"{local_version} | WEBP step {resize_count} | Resized to {new_w}x{new_h}, reset quality={quality}")
            continue

        if (
            under_target_q is not None
            and over_target_q is not None
            and over_target_q - under_target_q > 1
        ):
            quality = (under_target_q + over_target_q) // 2
            print(
                f"{local_version} | WEBP animated bracket | under_q={under_target_q}, "
                f"over_q={over_target_q} -> next_q={quality}"
            )
        else:
            proposed_quality = max(45, min(100, int(quality * correction)))

            if under_target_q is not None:
                proposed_quality = max(proposed_quality, under_target_q + 1)
            if over_target_q is not None:
                proposed_quality = min(proposed_quality, over_target_q - 1)

            quality = proposed_quality

        print(f"{local_version} | WEBP step {resize_count+1} | Quality={quality}")

    _final_msg = f"could not hit {gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB"
    if best_effort_buf is not None:
        _best_miss_pct = abs(best_effort_size - target_mid_bytes) / target_mid_bytes * 100
        print(
            f"{local_version} | WEBP best-effort accept (max iterations) | "
            f"q={best_effort_q} size={best_effort_size/1024:.2f} KB miss={_best_miss_pct:.2f}%"
        )
        if stats_mgr_webp and width and height and frame_count:
            stats_mgr_webp.save_step(
                width, height, frame_count,
                init_size / (1024 * 1024),
                best_effort_q, best_effort_method,
                best_effort_size / (1024 * 1024),
                0,
            )
        with open(path, "wb") as f:
            f.write(best_effort_buf.getvalue())
        elapsed = time.time() - started_at
        print(
            f"{local_version} | вњ… WEBP best-effort: {init_size/1024:.2f} KB -> {best_effort_size/1024:.2f} KB "
            f"| Quality={best_effort_q} | Resized {resize_count} times"
        )
        print(f"{local_version} | Finished in {elapsed:.2f} sec")
        return
    print(
        f"{local_version} | вљ  WEBP animated max iterations reached; "
        f"file kept unchanged ({_final_msg})"
    )


def compress_animated_webp_until_under_target(path, gif_cfg, version, stats_file):
    """Compress animated WEBP files while preserving existing runtime behavior."""
    local_version = version
    started_at = time.time()
    target_min_bytes = int(gif_cfg.target_min_mb * 1024 * 1024)
    target_max_bytes = int(gif_cfg.target_max_mb * 1024 * 1024)
    target_mid_bytes = int(((gif_cfg.target_min_mb + gif_cfg.target_max_mb) / 2.0) * 1024 * 1024)

    try:
        with Image.open(path) as img:
            init_size = os.path.getsize(path)
            is_animated = bool(getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1)
            frame_count = getattr(img, "n_frames", 1)

            if not is_animated:
                return

            print(f"{local_version} | Initial WEBP: {path}")
            print(
                f"{local_version} | WxH={img.width}x{img.height} | Animated=True "
                f"| Frames={frame_count} | Size={init_size/1024:.2f} KB "
                f"| Target={gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB"
            )

            if target_min_bytes <= init_size <= target_max_bytes:
                print(f"{local_version} | вњ… WEBP already in target range, no compression needed")
                return

            frames = []
            durations = []
            for frame in ImageSequence.Iterator(img):
                if frame.mode in ("RGB", "RGBA"):
                    prepared = frame.copy()
                else:
                    has_alpha_frame = "A" in frame.getbands()
                    prepared = frame.convert("RGBA" if has_alpha_frame else "RGB")
                frames.append(prepared)
                durations.append(frame.info.get("duration", 100))

            stats_mgr_webp = AnimatedWebPStatsManager(stats_file, local_version)
            _compress_animated_webp(
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
                stats_mgr_webp=stats_mgr_webp,
                width=img.width,
                height=img.height,
                frame_count=frame_count,
            )

    except UnidentifiedImageError:
        print(f"{local_version} | Skipped corrupted WEBP: {path}")


