import time

from webp_persist_steps import persist_best_effort, persist_success, persist_success_result
from webp_timeout_steps import try_timeout_rescue
from webp_tuner import resolve_runtime_settings, resolve_startup_quality


def encode_with_fallback(frames, durations, quality, method_in_use, local_version, save_webp_frames):
    try:
        encoded_buf = save_webp_frames(frames, durations, quality, method=method_in_use)
    except ValueError as e:
        fallback_method = 0
        fallback_quality = max(1, min(100, quality))
        print(
            f"{local_version} | [webp.encode] | config error: {e} "
            f"| retry q={fallback_quality} method={fallback_method}"
        )
        try:
            encoded_buf = save_webp_frames(frames, durations, fallback_quality, method=fallback_method)
            quality = fallback_quality
            method_in_use = fallback_method
        except ValueError as e2:
            print(f"{local_version} | [webp.encode] | failed: {e2} | file unchanged")
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
                f"{local_version} | [webp.direct] | accepted | size={encoded_size/1024:.2f} KB | method={method_in_use}"
            )
            return effective_size, effective_buf, effective_method, fallback_elapsed

        print(
            f"{local_version} | [webp.direct] | miss | size={encoded_size/1024:.2f} KB -> fallback method={webp_method}"
        )
        fallback_start = time.time()
        try:
            final_buf = save_webp_frames(frames, durations, quality, method=webp_method)
            final_method = webp_method
        except ValueError as e:
            fallback_method = 0
            print(
                f"{local_version} | [webp.direct] | fallback error: {e} | retry method={fallback_method}"
            )
            final_buf = save_webp_frames(frames, durations, quality, method=fallback_method)
            final_method = fallback_method

        fallback_elapsed = time.time() - fallback_start
        final_size = len(final_buf.getvalue())
        effective_size = final_size
        effective_buf = final_buf
        effective_method = final_method
        print(
            f"{local_version} | [webp.direct] | fallback result | size={final_size/1024:.2f} KB method={final_method} | elapsed={fallback_elapsed:.2f}s"
        )

    return effective_size, effective_buf, effective_method, fallback_elapsed
