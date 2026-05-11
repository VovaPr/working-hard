import time


def _is_timeout(elapsed, effective_max_seconds):
    return elapsed >= effective_max_seconds


def _can_use_bracket_rescue(under_target_q, over_target_q):
    return (
        under_target_q is not None
        and over_target_q is not None
        and over_target_q - under_target_q >= 1
    )


def _resolve_rescue_quality(*, under_target_q, over_target_q, quality, effective_size, target_min_bytes, target_max_bytes):
    rescue_q = (under_target_q + over_target_q) // 2
    if rescue_q == quality:
        if effective_size < target_min_bytes and rescue_q < over_target_q:
            rescue_q += 1
        elif effective_size > target_max_bytes and rescue_q > under_target_q:
            rescue_q -= 1
    return rescue_q


def _encode_rescue_candidate(*, frames, durations, rescue_q, webp_method, save_webp_frames):
    rescue_start = time.time()
    try:
        rescue_buf = save_webp_frames(frames, durations, rescue_q, method=webp_method)
        rescue_method = webp_method
    except ValueError:
        rescue_method = 0
        rescue_buf = save_webp_frames(frames, durations, rescue_q, method=rescue_method)
    rescue_elapsed = time.time() - rescue_start
    rescue_size = len(rescue_buf.getvalue())
    return rescue_buf, rescue_method, rescue_size, rescue_elapsed


def _persist_timeout_rescue_success(
    *,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    rescue_q,
    rescue_method,
    rescue_size,
    rescue_elapsed,
    path,
    rescue_buf,
    started_at,
    local_version,
):
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
    if not _is_timeout(elapsed, effective_max_seconds):
        return False

    if _can_use_bracket_rescue(under_target_q, over_target_q):
        rescue_q = _resolve_rescue_quality(
            under_target_q=under_target_q,
            over_target_q=over_target_q,
            quality=quality,
            effective_size=effective_size,
            target_min_bytes=target_min_bytes,
            target_max_bytes=target_max_bytes,
        )
        print(
            f"{local_version} | WEBP timeout-rescue | "
            f"bracket={under_target_q}-{over_target_q} -> verify q={rescue_q}"
        )

        rescue_buf, rescue_method, rescue_size, rescue_elapsed = _encode_rescue_candidate(
            frames=frames,
            durations=durations,
            rescue_q=rescue_q,
            webp_method=webp_method,
            save_webp_frames=save_webp_frames,
        )

        if target_min_bytes <= rescue_size <= target_max_bytes:
            _persist_timeout_rescue_success(
                stats_mgr_webp=stats_mgr_webp,
                width=width,
                height=height,
                frame_count=frame_count,
                init_size=init_size,
                rescue_q=rescue_q,
                rescue_method=rescue_method,
                rescue_size=rescue_size,
                rescue_elapsed=rescue_elapsed,
                path=path,
                rescue_buf=rescue_buf,
                started_at=started_at,
                local_version=local_version,
            )
            return True

    print(
        f"{local_version} | WEBP animated timeout {elapsed:.2f} sec; "
        f"file kept unchanged"
    )
    return True
