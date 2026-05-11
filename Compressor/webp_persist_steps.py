import time


def _save_step_stats(
    *,
    stats_mgr_webp,
    width,
    height,
    frame_count,
    init_size,
    quality,
    method,
    result_size,
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


def _write_buffer(path, result_buf):
    with open(path, "wb") as f:
        f.write(result_buf.getvalue())


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
    _save_step_stats(
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        quality=quality,
        method=method,
        result_size=result_size,
        encode_elapsed=encode_elapsed,
    )

    _write_buffer(path, result_buf)

    elapsed = time.time() - started_at
    print(
        f"{local_version} | WEBP success: {init_size/1024:.2f} KB -> {result_size/1024:.2f} KB "
        f"| Quality={quality} | Resized {resize_count} times"
    )
    if stats_mgr_webp:
        print(f"{local_version} | WEBP animated stats total: {stats_mgr_webp.stats_count()} records")
    print(f"{local_version} | Finished in {elapsed:.2f} sec")


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

    _save_step_stats(
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        init_size=init_size,
        quality=best_effort_q,
        method=best_effort_method,
        result_size=best_effort_size,
        encode_elapsed=encode_elapsed,
    )

    _write_buffer(path, best_effort_buf)

    elapsed = time.time() - started_at
    print(
        f"{local_version} | WEBP best-effort: {init_size/1024:.2f} KB -> {best_effort_size/1024:.2f} KB "
        f"| Quality={best_effort_q} | Resized {resize_count} times"
    )
    print(f"{local_version} | Finished in {elapsed:.2f} sec")
    return True
