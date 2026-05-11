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


def _resolve_animation_startup(
    *,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    target_mid_bytes,
    gif_cfg,
    local_version,
):
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

    runtime = resolve_runtime_settings(
        gif_cfg,
        frame_count,
        local_version,
        direct_final_from_stats,
        known_result_size_mb,
    )
    runtime["quality"] = quality
    runtime["direct_final_from_stats"] = direct_final_from_stats
    return runtime


def _run_encode_step(
    *,
    step,
    quality,
    direct_final_from_stats,
    under_target_q,
    over_target_q,
    frames,
    durations,
    webp_method,
    webp_method_direct_fast,
    can_use_direct_fast,
    target_min_bytes,
    target_max_bytes,
    effective_max_seconds,
    started_at,
    local_version,
):
    quality = max(1, min(100, int(quality)))
    bracket_known = under_target_q is not None and over_target_q is not None
    direct_final_this_step = bool(direct_final_from_stats and step == 1)
    method_in_use = webp_method_direct_fast if direct_final_this_step and can_use_direct_fast else webp_method
    step_elapsed = time.time() - started_at
    bracket_str = f"{under_target_q}-{over_target_q}" if bracket_known else "none"
    print(
        f"{local_version} | WEBP animated step {step} | "
        f"Encoding... (q={quality}, method={method_in_use}) | "
        f"bracket={bracket_str} | elapsed={step_elapsed:.1f}s/{effective_max_seconds:.0f}s"
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
        return None

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
    return {
        "quality": quality,
        "effective_size": effective_size,
        "effective_buf": effective_buf,
        "effective_method": effective_method,
        "step_encode_elapsed": step_encode_elapsed,
        "bracket_known": bracket_known,
    }


def _is_in_target_range(*, effective_size, target_min_bytes, target_max_bytes):
    return target_min_bytes <= effective_size <= target_max_bytes


def _persist_success(
    *,
    path,
    effective_buf,
    effective_size,
    init_size,
    quality,
    effective_method,
    resize_count,
    local_version,
    started_at,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    step_encode_elapsed,
    target_min_bytes,
    target_max_bytes,
):
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


def _update_best_effort(*, best_effort, effective_size, effective_buf, quality, effective_method, target_mid_bytes):
    miss_abs = abs(effective_size - target_mid_bytes)
    if best_effort["size"] is None or miss_abs < abs(best_effort["size"] - target_mid_bytes):
        best_effort["buf"] = effective_buf
        best_effort["size"] = effective_size
        best_effort["quality"] = quality
        best_effort["method"] = effective_method


def _update_quality_bracket(*, under_target_q, over_target_q, effective_size, quality, target_min_bytes, target_max_bytes, local_version):
    if effective_size < target_min_bytes:
        under_target_q = quality if under_target_q is None else max(under_target_q, quality)
    elif effective_size > target_max_bytes:
        over_target_q = quality if over_target_q is None else min(over_target_q, quality)
    bracket = (
        f"{under_target_q}-{over_target_q}"
        if under_target_q is not None and over_target_q is not None
        else f"under={under_target_q} over={over_target_q}"
    )
    print(f"{local_version} | WEBP animated bracket update | {bracket}")
    return under_target_q, over_target_q


def _try_persist_bracket_tight(
    *,
    under_target_q,
    over_target_q,
    best_effort,
    local_version,
    target_mid_bytes,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    path,
    started_at,
    resize_count,
    encode_elapsed,
):
    if not (
        under_target_q is not None
        and over_target_q is not None
        and over_target_q - under_target_q <= 1
        and best_effort["buf"] is not None
    ):
        return False

    persist_best_effort(
        reason="bracket-tight",
        local_version=local_version,
        target_mid_bytes=target_mid_bytes,
        best_effort_buf=best_effort["buf"],
        best_effort_size=best_effort["size"],
        best_effort_q=best_effort["quality"],
        best_effort_method=best_effort["method"],
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        path=path,
        started_at=started_at,
        resize_count=resize_count,
        encode_elapsed=encode_elapsed,
    )
    return True


def _try_near_target_nudge(
    *,
    effective_size,
    target_mid_bytes,
    target_min_bytes,
    target_max_bytes,
    gif_cfg,
    bracket_known,
    quality,
    local_version,
):
    near_mid_ratio = abs(effective_size - target_mid_bytes) / target_mid_bytes if target_mid_bytes > 0 else 0.0
    if near_mid_ratio > gif_cfg.webp_animated_near_band_ratio or bracket_known:
        return None

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
    next_quality = min(100, quality + nudge_step) if effective_size < target_min_bytes else max(45, quality - nudge_step)
    print(
        f"{local_version} | WEBP animated near-target nudge | "
        f"miss={miss_ratio*100:.2f}% | step={nudge_step} -> next_q={next_quality}"
    )
    return next_quality


def _try_resize_fallback(*, quality, effective_size, target_mid_bytes, frames, resize_count, local_version):
    if quality > 45:
        return None

    correction = (target_mid_bytes / effective_size) ** 0.5
    correction = max(0.88, min(1.12, correction))
    new_w = max(1, int(frames[0].width * correction))
    new_h = max(1, int(frames[0].height * correction))
    resized_frames = [fr.resize((new_w, new_h), Image.LANCZOS) for fr in frames]
    new_resize_count = resize_count + 1
    print(f"{local_version} | WEBP step {new_resize_count} | Resized to {new_w}x{new_h}, reset quality=95")
    return resized_frames, new_resize_count, 95, None, None


def _resolve_next_quality(*, under_target_q, over_target_q, quality, effective_size, target_mid_bytes, local_version):
    correction = (target_mid_bytes / effective_size) ** 0.5
    correction = max(0.88, min(1.12, correction))

    if under_target_q is not None and over_target_q is not None and over_target_q - under_target_q > 1:
        next_quality = (under_target_q + over_target_q) // 2
        print(
            f"{local_version} | WEBP animated bracket | under_q={under_target_q}, "
            f"over_q={over_target_q} -> next_q={next_quality}"
        )
        return next_quality

    proposed_quality = max(45, min(100, int(quality * correction)))
    if under_target_q is not None:
        proposed_quality = max(proposed_quality, under_target_q + 1)
    if over_target_q is not None:
        proposed_quality = min(proposed_quality, over_target_q - 1)
    return proposed_quality
