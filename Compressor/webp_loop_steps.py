import time


def resolve_startup_quality(
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    target_mid_bytes,
    gif_cfg,
):
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

    return quality, source, direct_final_from_stats, known_result_size_mb


def resolve_runtime_settings(gif_cfg, frame_count, local_version, direct_final_from_stats, known_result_size_mb):
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
    if (
        direct_final_from_stats
        and gif_cfg.webp_animated_direct_final_fast_enabled
        and known_result_size_mb is not None
    ):
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

    return {
        "webp_method": webp_method,
        "webp_method_direct_fast": webp_method_direct_fast,
        "effective_max_seconds": effective_max_seconds,
        "can_use_direct_fast": can_use_direct_fast,
    }


def encode_with_fallback(frames, durations, quality, method_in_use, local_version, save_webp_frames):
    try:
        encoded_buf = save_webp_frames(frames, durations, quality, method=method_in_use)
    except ValueError as e:
        fallback_method = 0
        fallback_quality = max(1, min(100, quality))
        print(
            f"{local_version} | WEBP animated config error: {e} "
            f"| retry with q={fallback_quality}, method={fallback_method}"
        )
        try:
            encoded_buf = save_webp_frames(frames, durations, fallback_quality, method=fallback_method)
            quality = fallback_quality
            method_in_use = fallback_method
        except ValueError as e2:
            print(f"{local_version} | WEBP animated encode failed: {e2}; file kept unchanged")
            return None, quality, method_in_use

    return encoded_buf, quality, method_in_use


def maybe_fallback_from_direct_fast(
    *,
    direct_final_this_step,
    method_in_use,
    webp_method,
    target_min_bytes,
    target_max_bytes,
    encoded_size,
    encoded_buf,
    frames,
    durations,
    quality,
    local_version,
    save_webp_frames,
):
    effective_size = encoded_size
    effective_buf = encoded_buf
    effective_method = method_in_use
    fallback_elapsed = 0.0

    if direct_final_this_step and method_in_use != webp_method:
        if target_min_bytes <= encoded_size <= target_max_bytes:
            print(
                f"{local_version} | WEBP direct-fast accepted | "
                f"Size={encoded_size/1024:.2f} KB | method={method_in_use}"
            )
            return effective_size, effective_buf, effective_method, fallback_elapsed

        print(
            f"{local_version} | WEBP direct-fast miss | "
            f"Size={encoded_size/1024:.2f} KB -> fallback method={webp_method}"
        )
        fallback_start = time.time()
        try:
            final_buf = save_webp_frames(frames, durations, quality, method=webp_method)
            final_method = webp_method
        except ValueError as e:
            fallback_method = 0
            print(
                f"{local_version} | WEBP direct-fast fallback error: {e} "
                f"| retry with method={fallback_method}"
            )
            final_buf = save_webp_frames(frames, durations, quality, method=fallback_method)
            final_method = fallback_method

        fallback_elapsed = time.time() - fallback_start
        final_size = len(final_buf.getvalue())
        effective_size = final_size
        effective_buf = final_buf
        effective_method = final_method
        print(
            f"{local_version} | WEBP direct-fast fallback result | "
            f"Size={final_size/1024:.2f} KB | method={final_method} | fallback={fallback_elapsed:.2f} sec"
        )

    return effective_size, effective_buf, effective_method, fallback_elapsed


def persist_success_result(
    *,
    path,
    result_buf,
    result_size,
    init_size,
    quality,
    method,
    resize_count,
    local_version,
    started_at,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    encode_elapsed,
):
    if stats_mgr_webp and width and height and frame_count:
        stats_mgr_webp.save_step(
            width,
            height,
            frame_count,
            init_size / (1024 * 1024),
            quality,
            method,
            result_size / (1024 * 1024),
            encode_elapsed,
        )

    with open(path, "wb") as f:
        f.write(result_buf.getvalue())

    elapsed = time.time() - started_at
    print(
        f"{local_version} | WEBP success: {init_size/1024:.2f} KB -> {result_size/1024:.2f} KB "
        f"| Quality={quality} | Resized {resize_count} times"
    )
    if stats_mgr_webp:
        print(f"{local_version} | WEBP animated stats total: {stats_mgr_webp.stats_count()} records")
    print(f"{local_version} | Finished in {elapsed:.2f} sec")


def try_timeout_rescue(
    *,
    elapsed,
    effective_max_seconds,
    under_target_q,
    over_target_q,
    quality,
    effective_size,
    target_min_bytes,
    target_max_bytes,
    frames,
    durations,
    webp_method,
    local_version,
    save_webp_frames,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    path,
    started_at,
):
    if elapsed < effective_max_seconds:
        return False

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
            rescue_buf = save_webp_frames(frames, durations, rescue_q, method=webp_method)
            rescue_method = webp_method
        except ValueError:
            rescue_method = 0
            rescue_buf = save_webp_frames(frames, durations, rescue_q, method=rescue_method)
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
                f"{local_version} | WEBP success (timeout-rescue): "
                f"{init_size/1024:.2f} KB -> {rescue_size/1024:.2f} KB "
                f"| Quality={rescue_q} | method={rescue_method}"
            )
            print(f"{local_version} | Finished in {total_elapsed:.2f} sec")
            return True

    print(
        f"{local_version} | WEBP animated timeout {elapsed:.2f} sec; "
        f"file kept unchanged"
    )
    return True


def persist_best_effort(
    *,
    reason,
    local_version,
    target_mid_bytes,
    best_effort_buf,
    best_effort_size,
    best_effort_q,
    best_effort_method,
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
    if best_effort_buf is None:
        return False

    best_miss_pct = abs(best_effort_size - target_mid_bytes) / target_mid_bytes * 100
    print(
        f"{local_version} | WEBP best-effort accept ({reason}) | "
        f"q={best_effort_q} size={best_effort_size/1024:.2f} KB miss={best_miss_pct:.2f}%"
    )

    if stats_mgr_webp and width and height and frame_count:
        stats_mgr_webp.save_step(
            width,
            height,
            frame_count,
            init_size / (1024 * 1024),
            best_effort_q,
            best_effort_method,
            best_effort_size / (1024 * 1024),
            encode_elapsed,
        )

    with open(path, "wb") as f:
        f.write(best_effort_buf.getvalue())

    elapsed = time.time() - started_at
    print(
        f"{local_version} | WEBP best-effort: {init_size/1024:.2f} KB -> {best_effort_size/1024:.2f} KB "
        f"| Quality={best_effort_q} | Resized {resize_count} times"
    )
    print(f"{local_version} | Finished in {elapsed:.2f} sec")
    return True
