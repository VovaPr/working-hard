import io
import math
import time

from PIL import Image

from webp_loop_steps import (
    encode_with_fallback,
    maybe_fallback_from_direct_fast,
    persist_best_effort,
    persist_success,
    resolve_runtime_settings,
    resolve_startup_quality,
    try_timeout_rescue,
)
from webp_sample_probe import run_webp_sample_probe


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
    quality, source, direct_final_from_stats, known_result_size_mb, startup_pre_resize = resolve_startup_quality(
        stats_mgr_webp,
        width,
        height,
        frame_count,
        init_size,
        target_mid_bytes,
        gif_cfg,
    )
    print(f"{local_version} | [webp.startup] | prediction={source} | q={quality}")

    runtime = resolve_runtime_settings(
        gif_cfg,
        frame_count,
        local_version,
        direct_final_from_stats,
        known_result_size_mb,
    )
    runtime["quality"] = quality
    runtime["direct_final_from_stats"] = direct_final_from_stats
    runtime["startup_pre_resize"] = startup_pre_resize
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
        f"{local_version} | [webp.step] | step={step} q={quality} method={method_in_use} "
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
        f"{local_version} | [webp.step] | step={step} | size={effective_size/1024:.2f} KB | encode={step_encode_elapsed:.2f}s"
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
        f"{local_version} | [webp.success] size={effective_size/1024:.2f} KB "
        f"| target=[{target_min_bytes/1024:.2f}, {target_max_bytes/1024:.2f}] KB"
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
    print(f"{local_version} | [webp.bracket] | {bracket}")
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
        f"{local_version} | [webp.nudge] | miss={miss_ratio*100:.2f}% step={nudge_step} | q={quality} -> q={next_quality}"
    )
    return next_quality


def _try_resize_fallback(*, quality, effective_size, target_mid_bytes, frames, resize_count, local_version):
    if quality > 45:
        return None

    old_w, old_h = frames[0].width, frames[0].height
    dim_correction = (target_mid_bytes / effective_size) ** 0.5
    dim_correction = max(0.80, min(0.95, dim_correction))
    new_w = max(64, int(old_w * dim_correction))
    new_h = max(64, int(old_h * dim_correction))
    resized_frames = [fr.resize((new_w, new_h), Image.LANCZOS) for fr in frames]
    new_resize_count = resize_count + 1

    # Estimate size after resize and compute quality that should land near target.
    # Resize reduces area, so encoded size scales roughly with area ratio.
    area_ratio = (new_w * new_h) / max(1, old_w * old_h)
    estimated_new_size = effective_size * area_ratio
    q_correction = (target_mid_bytes / max(1, estimated_new_size)) ** 0.5
    initial_quality = max(45, min(95, int(quality * q_correction)))

    print(
        f"{local_version} | [webp.resize] | {old_w}x{old_h} -> {new_w}x{new_h} area={area_ratio:.2f} "
        f"| estimated={estimated_new_size/1024:.0f} KB | q={quality} -> q={initial_quality}"
    )
    return resized_frames, new_resize_count, initial_quality, None, None


def _fit_quality_model(observations):
    """Fit size = C * q^alpha from (quality, size_bytes) observations via log-linear regression.
    Uses at most the 3 most recent observations. Returns (C, alpha) or None if data is
    insufficient or alpha falls outside the plausible range [0.20, 5.00].
    """
    pts = [(q, s) for q, s in observations[-3:] if q > 0 and s > 0]
    if len(pts) < 2:
        return None
    if len(pts) == 2:
        q1, s1 = pts[0]
        q2, s2 = pts[1]
        if abs(q1 - q2) < 2:
            return None
        alpha = math.log(s2 / s1) / math.log(q2 / q1)
        if not (0.20 <= alpha <= 5.00):
            return None
        C = s1 / (q1 ** alpha)
        return C, alpha
    lqs = [math.log(q) for q, s in pts]
    lss = [math.log(s) for q, s in pts]
    n = len(pts)
    sum_lq = sum(lqs)
    sum_ls = sum(lss)
    sum_lq2 = sum(x * x for x in lqs)
    sum_lqls = sum(lqs[i] * lss[i] for i in range(n))
    denom = n * sum_lq2 - sum_lq ** 2
    if abs(denom) < 1e-10:
        return None
    alpha = (n * sum_lqls - sum_lq * sum_ls) / denom
    if not (0.20 <= alpha <= 5.00):
        return None
    log_C = (sum_ls - alpha * sum_lq) / n
    C = math.exp(log_C)
    return C, alpha


def _resolve_next_quality(*, under_target_q, over_target_q, quality, effective_size, target_mid_bytes, observations, local_version):
    if under_target_q is not None and over_target_q is not None and over_target_q - under_target_q > 1:
        next_quality = (under_target_q + over_target_q) // 2
        print(
            f"{local_version} | [webp.bracket] | binary-search | "
            f"under_q={under_target_q} over_q={over_target_q} -> q={next_quality}"
        )
        return next_quality

    if len(observations) >= 2:
        model = _fit_quality_model(observations)
        if model is not None:
            C, alpha = model
            raw_q = (target_mid_bytes / C) ** (1.0 / alpha)
            proposed = max(1, min(100, int(raw_q)))
            if under_target_q is not None:
                proposed = max(proposed, under_target_q + 1)
            if over_target_q is not None:
                proposed = min(proposed, over_target_q - 1)
            print(
                f"{local_version} | [webp.bracket] | model-fit alpha={alpha:.2f} | q={quality} -> q={proposed}"
            )
            return proposed

    correction = (target_mid_bytes / effective_size) ** 0.5
    raw_ratio = effective_size / target_mid_bytes
    if raw_ratio > 1.20 or raw_ratio < 0.80:
        # Far from target: allow large correction steps so we converge in fewer (expensive) encodes
        correction = max(0.70, min(1.30, correction))
    else:
        # Near target: clamp tightly to avoid overshooting the bracket
        correction = max(0.88, min(1.12, correction))

    proposed_quality = max(1, min(100, int(quality * correction)))
    if under_target_q is not None:
        proposed_quality = max(proposed_quality, under_target_q + 1)
    if over_target_q is not None:
        proposed_quality = min(proposed_quality, over_target_q - 1)
    print(
        f"{local_version} | [webp.bracket] | ratio-correction | q={quality} correction={correction:.3f} -> q={proposed_quality}"
    )
    return proposed_quality


def _run_sample_probe_if_needed(*, state, frames, durations, target_mid_bytes, frame_count, local_version, gif_cfg):
    """Run a cheap frame-subset probe to calibrate the initial quality when no stats profile exists."""
    if state["direct_final_from_stats"]:
        return
    corrected_quality, probe_observation = run_webp_sample_probe(
        frames=frames,
        durations=durations,
        quality=state["quality"],
        target_mid_bytes=target_mid_bytes,
        frame_count=frame_count,
        local_version=local_version,
        gif_cfg=gif_cfg,
        save_webp_frames=_save_webp_frames,
    )
    if probe_observation is not None:
        state["observations"].append(probe_observation)
    if corrected_quality is not None:
        state["quality"] = corrected_quality


def _build_animation_state(*, startup, frames):
    return {
        "frames": frames,
        "quality": startup["quality"],
        "direct_final_from_stats": startup["direct_final_from_stats"],
        "startup_pre_resize": startup.get("startup_pre_resize"),
        "resize_count": 0,
        "webp_method": startup["webp_method"],
        "webp_method_direct_fast": startup["webp_method_direct_fast"],
        "effective_max_seconds": startup["effective_max_seconds"],
        "can_use_direct_fast": startup["can_use_direct_fast"],
        "under_target_q": None,
        "over_target_q": None,
        "observations": [],
        "best_effort": {"buf": None, "size": None, "quality": None, "method": None},
    }


def _apply_startup_pre_resize_if_needed(*, state, local_version):
    target = state.get("startup_pre_resize")
    if not target or not state.get("frames"):
        return

    new_w, new_h = target
    old_w, old_h = state["frames"][0].width, state["frames"][0].height
    if old_w == new_w and old_h == new_h:
        state["resize_count"] = max(1, state.get("resize_count", 0))
        return

    state["frames"] = [fr.resize((new_w, new_h), Image.LANCZOS) for fr in state["frames"]]
    state["resize_count"] = max(1, state.get("resize_count", 0))
    print(f"{local_version} | [webp.startup] | replay-resize {old_w}x{old_h} -> {new_w}x{new_h}")


def _check_early_exits(
    *,
    state,
    effective_size,
    effective_buf,
    effective_method,
    step_encode_elapsed,
    durations,
    path,
    init_size,
    target_min_bytes,
    target_max_bytes,
    target_mid_bytes,
    local_version,
    gif_cfg,
    started_at,
    stats_mgr_webp,
    width,
    height,
    frame_count,
):
    _update_best_effort(
        best_effort=state["best_effort"],
        effective_size=effective_size,
        effective_buf=effective_buf,
        quality=state["quality"],
        effective_method=effective_method,
        target_mid_bytes=target_mid_bytes,
    )
    state["under_target_q"], state["over_target_q"] = _update_quality_bracket(
        under_target_q=state["under_target_q"],
        over_target_q=state["over_target_q"],
        effective_size=effective_size,
        quality=state["quality"],
        target_min_bytes=target_min_bytes,
        target_max_bytes=target_max_bytes,
        local_version=local_version,
    )

    elapsed = time.time() - started_at
    timed_out = try_timeout_rescue(
        elapsed=elapsed,
        effective_max_seconds=state["effective_max_seconds"],
        under_target_q=state["under_target_q"],
        over_target_q=state["over_target_q"],
        quality=state["quality"],
        effective_size=effective_size,
        target_min_bytes=target_min_bytes,
        target_max_bytes=target_max_bytes,
        frames=state["frames"],
        durations=durations,
        webp_method=state["webp_method"],
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
        return "done"

    if _try_persist_bracket_tight(
        under_target_q=state["under_target_q"],
        over_target_q=state["over_target_q"],
        best_effort=state["best_effort"],
        local_version=local_version,
        target_mid_bytes=target_mid_bytes,
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        path=path,
        started_at=started_at,
        resize_count=state["resize_count"],
        encode_elapsed=step_encode_elapsed,
    ):
        return "done"

    return None


def _pick_next_quality(
    *,
    state,
    effective_size,
    bracket_known,
    target_min_bytes,
    target_max_bytes,
    target_mid_bytes,
    gif_cfg,
    local_version,
):
    nudged_quality = _try_near_target_nudge(
        effective_size=effective_size,
        target_mid_bytes=target_mid_bytes,
        target_min_bytes=target_min_bytes,
        target_max_bytes=target_max_bytes,
        gif_cfg=gif_cfg,
        bracket_known=bracket_known,
        quality=state["quality"],
        local_version=local_version,
    )
    if nudged_quality is not None:
        state["quality"] = nudged_quality
        return "continue"

    resize_result = _try_resize_fallback(
        quality=state["quality"],
        effective_size=effective_size,
        target_mid_bytes=target_mid_bytes,
        frames=state["frames"],
        resize_count=state["resize_count"],
        local_version=local_version,
    )
    if resize_result is not None:
        state["frames"], state["resize_count"], state["quality"], state["under_target_q"], state["over_target_q"] = resize_result
        return "continue"

    raw_next_q = _resolve_next_quality(
        under_target_q=state["under_target_q"],
        over_target_q=state["over_target_q"],
        quality=state["quality"],
        effective_size=effective_size,
        target_mid_bytes=target_mid_bytes,
        observations=state["observations"],
        local_version=local_version,
    )
    if raw_next_q < 45:
        resize_result = _try_resize_fallback(
            quality=raw_next_q,
            effective_size=effective_size,
            target_mid_bytes=target_mid_bytes,
            frames=state["frames"],
            resize_count=state["resize_count"],
            local_version=local_version,
        )
        if resize_result is not None:
            state["frames"], state["resize_count"], state["quality"], state["under_target_q"], state["over_target_q"] = resize_result
            return "continue"
    state["quality"] = max(45, raw_next_q)
    return "continue"


def _handle_iteration_outcome(
    *,
    state,
    step_result,
    durations,
    path,
    init_size,
    target_min_bytes,
    target_max_bytes,
    target_mid_bytes,
    local_version,
    gif_cfg,
    started_at,
    stats_mgr_webp,
    width,
    height,
    frame_count,
):
    state["quality"] = step_result["quality"]
    effective_size = step_result["effective_size"]
    effective_buf = step_result["effective_buf"]
    effective_method = step_result["effective_method"]
    step_encode_elapsed = step_result["step_encode_elapsed"]
    bracket_known = step_result["bracket_known"]
    state["observations"].append((state["quality"], effective_size))
    final_width = state["frames"][0].width if state.get("frames") else width
    final_height = state["frames"][0].height if state.get("frames") else height

    if _is_in_target_range(
        effective_size=effective_size,
        target_min_bytes=target_min_bytes,
        target_max_bytes=target_max_bytes,
    ):
        persist_success(
            path=path,
            result_buf=effective_buf,
            result_size=effective_size,
            init_size=init_size,
            quality=state["quality"],
            method=effective_method,
            resize_count=state["resize_count"],
            local_version=local_version,
            started_at=started_at,
            stats_mgr_webp=stats_mgr_webp,
            width=width,
            height=height,
            frame_count=frame_count,
            encode_elapsed=step_encode_elapsed,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
            final_width=final_width,
            final_height=final_height,
        )
        return "done"

    early_exit = _check_early_exits(
        state=state,
        effective_size=effective_size,
        effective_buf=effective_buf,
        effective_method=effective_method,
        step_encode_elapsed=step_encode_elapsed,
        durations=durations,
        path=path,
        init_size=init_size,
        target_min_bytes=target_min_bytes,
        target_max_bytes=target_max_bytes,
        target_mid_bytes=target_mid_bytes,
        local_version=local_version,
        gif_cfg=gif_cfg,
        started_at=started_at,
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
    )
    if early_exit == "done":
        return "done"

    return _pick_next_quality(
        state=state,
        effective_size=effective_size,
        bracket_known=bracket_known,
        target_min_bytes=target_min_bytes,
        target_max_bytes=target_max_bytes,
        target_mid_bytes=target_mid_bytes,
        gif_cfg=gif_cfg,
        local_version=local_version,
    )


def _persist_max_iterations(
    *,
    state,
    target_mid_bytes,
    gif_cfg,
    local_version,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    path,
    started_at,
):
    final_msg = f"could not hit {gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB"
    persisted = persist_best_effort(
        reason="max-iterations",
        local_version=local_version,
        target_mid_bytes=target_mid_bytes,
        best_effort_buf=state["best_effort"]["buf"],
        best_effort_size=state["best_effort"]["size"],
        best_effort_q=state["best_effort"]["quality"],
        best_effort_method=state["best_effort"]["method"],
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        path=path,
        started_at=started_at,
        resize_count=state["resize_count"],
        encode_elapsed=0,
    )
    if persisted:
        return True

    print(
        f"{local_version} | [webp.best] | max-iter | file unchanged | {final_msg}"
    )
    return False
