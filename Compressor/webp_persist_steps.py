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
    resize_count=0,
    final_width=None,
    final_height=None,
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
            resize_count,
            final_width,
            final_height,
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
    final_width=None,
    final_height=None,
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
        resize_count=resize_count,
        final_width=final_width,
        final_height=final_height,
    )

    _write_buffer(path, result_buf)

    elapsed = time.time() - started_at
    print(
        f"{local_version} | [webp.success] | {init_size/1024:.2f} KB -> {result_size/1024:.2f} KB "
        f"| q={quality} resized={resize_count}"
    )
    if stats_mgr_webp:
        print(f"{local_version} | [webp.success] | stats={stats_mgr_webp.stats_count()} records")
    print(f"{local_version} | [webp.success] | done in {elapsed:.2f}s")


def persist_success(
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
    target_min_bytes,
    target_max_bytes,
    final_width=None,
    final_height=None,
):
    print(
        f"{local_version} | [webp.success] | size={result_size/1024:.2f} KB "
        f"| target=[{target_min_bytes/1024:.2f}, {target_max_bytes/1024:.2f}] KB"
    )
    persist_success_result(
        path=path,
        result_buf=result_buf,
        result_size=result_size,
        init_size=init_size,
        quality=quality,
        method=method,
        resize_count=resize_count,
        local_version=local_version,
        started_at=started_at,
        stats_mgr_webp=stats_mgr_webp,
        width=width,
        height=height,
        frame_count=frame_count,
        encode_elapsed=encode_elapsed,
        final_width=final_width,
        final_height=final_height,
    )


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
        f"{local_version} | [webp.best] | {reason} | q={best_effort_q} size={best_effort_size/1024:.2f} KB miss={best_miss_pct:.2f}%"
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
        f"{local_version} | [webp.best] | {init_size/1024:.2f} KB -> {best_effort_size/1024:.2f} KB "
        f"| q={best_effort_q} resized={resize_count}"
    )
    print(f"{local_version} | [webp.best] | done in {elapsed:.2f}s")
    return True
