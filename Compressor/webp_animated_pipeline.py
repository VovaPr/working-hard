import io
import time

from PIL import Image

from webp_loop_steps import (
    encode_with_fallback,
    maybe_fallback_from_direct_fast,
    persist_best_effort,
    persist_success_result,
    resolve_runtime_settings,
    resolve_startup_quality,
    try_timeout_rescue,
)


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
    quality, source, direct_final_from_stats, known_result_size_mb = resolve_startup_quality(
        stats_mgr_webp,
        width,
        height,
        frame_count,
        init_size,
        target_mid_bytes,
        gif_cfg,
    )

    print(f"{local_version} | Prediction source: {source} -> initial quality={quality}")

    resize_count = 0
    runtime = resolve_runtime_settings(
        gif_cfg,
        frame_count,
        local_version,
        direct_final_from_stats,
        known_result_size_mb,
    )
    webp_method = runtime["webp_method"]
    webp_method_direct_fast = runtime["webp_method_direct_fast"]
    effective_max_seconds = runtime["effective_max_seconds"]
    can_use_direct_fast = runtime["can_use_direct_fast"]

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
        encoded_buf, quality, method_in_use = encode_with_fallback(
            frames,
            durations,
            quality,
            method_in_use,
            local_version,
            _save_webp_frames,
        )
        if encoded_buf is None:
            return

        encoded_size = len(encoded_buf.getvalue())
        step_encode_elapsed = time.time() - encode_start
        effective_size, effective_buf, effective_method, fallback_elapsed = maybe_fallback_from_direct_fast(
            direct_final_this_step=direct_final_this_step,
            method_in_use=method_in_use,
            webp_method=webp_method,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            encoded_size=encoded_size,
            encoded_buf=encoded_buf,
            frames=frames,
            durations=durations,
            quality=quality,
            local_version=local_version,
            save_webp_frames=_save_webp_frames,
        )
        step_encode_elapsed += fallback_elapsed

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
            persist_success_result(
                path=path,
                result_buf=effective_buf,
                result_size=effective_size,
                init_size=init_size,
                quality=quality,
                method=effective_method,
                resize_count=resize_count,
                local_version=local_version,
                started_at=started_at,
                stats_mgr_webp=stats_mgr_webp,
                width=width,
                height=height,
                frame_count=frame_count,
                encode_elapsed=step_encode_elapsed,
            )
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

        if (
            under_target_q is not None
            and over_target_q is not None
            and over_target_q - under_target_q <= 1
            and best_effort_buf is not None
        ):
            persisted = persist_best_effort(
                reason="bracket-tight",
                local_version=local_version,
                target_mid_bytes=target_mid_bytes,
                best_effort_buf=best_effort_buf,
                best_effort_size=best_effort_size,
                best_effort_q=best_effort_q,
                best_effort_method=best_effort_method,
                stats_mgr_webp=stats_mgr_webp,
                width=width,
                height=height,
                frame_count=frame_count,
                init_size=init_size,
                path=path,
                started_at=started_at,
                resize_count=resize_count,
                encode_elapsed=step_encode_elapsed,
            )
            if persisted:
                return
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
    persisted = persist_best_effort(
        reason="max-iterations",
        local_version=local_version,
        target_mid_bytes=target_mid_bytes,
        best_effort_buf=best_effort_buf,
        best_effort_size=best_effort_size,
        best_effort_q=best_effort_q,
        best_effort_method=best_effort_method,
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
