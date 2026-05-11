import os
import time

from compressor_gif_runtime import is_in_preferred_range, is_in_target_range


def _print_gif_result_header(input_path, total_frames, palette_count, width, height, version):
    print(
        f"{version} | [gif.result] | file: {os.path.basename(input_path)} "
        f"| Frames={total_frames} | Palette={palette_count} | WxH={width}x{height}"
    )


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
    fast_in_target = is_in_target_range(fast_size, gif_cfg)

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
    print(f"{version} | [gif.finalize] | Save final result and stats")
    save_start = time.time()
    stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, med_size, state.scale)
    with open(input_path, "wb") as f:
        f.write(med_bytes)
    print(f"{version} | [gif.diag] | save+stats={time.time() - save_start:.2f}s")
    elapsed = time.time() - started_at
    _print_gif_result_header(input_path, total_frames, colors_first, width, height, version)
    print(
        f"{version} | ✅ Success: {init_size:.2f} MB -> {med_size:.2f} MB "
        f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
    )
