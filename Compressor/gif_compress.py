"""GIF processing pipeline extracted from the main launcher module."""

import os
import time
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageSequence

from compressor_gif_runtime import GifRuntimeState
from gif_balanced_steps import _run_balanced_iteration
from gif_scale import _choose_initial_scale
from gif_stats import CompressorStatsManager


def _decode_gif_input(input_path, gif_cfg, version):
    frames_raw, durations = [], []
    with Image.open(input_path) as img:
        width, height = img.size
        total_frames = img.n_frames
        colors_first = len(img.getcolors(maxcolors=256 * 256) or [])
        palette_limit = min(colors_first + gif_cfg.extra_palette, 256)

        print(f"{version} | [gif.prepare] Starting file: {input_path}")
        init_size = os.path.getsize(input_path) / (1024 * 1024)
        print(f"{version} | [gif.prepare] Initial Size: {init_size:.2f} MB | Frames={total_frames} | Palette={colors_first} | WxH={width}x{height}")

        decode_start = time.time()
        for frame in ImageSequence.Iterator(img):
            frames_raw.append(frame.convert("RGB"))
            durations.append(frame.info.get("duration", 100))
        print(f"{version} | [gif.diag] decode={time.time() - decode_start:.2f}s ({total_frames} frames)")

    return {
        "frames_raw": frames_raw,
        "durations": durations,
        "width": width,
        "height": height,
        "total_frames": total_frames,
        "colors_first": colors_first,
        "palette_limit": palette_limit,
        "init_size": init_size,
    }


def balanced_compress_gif(
    input_path,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    debug_log_fn=None,
):
    started_at = time.time()
    print(f"{version} | [gif.prepare] Read and decode frames")

    def debug_log(message):
        if debug_log_fn is not None:
            debug_log_fn(message)
        elif log_level == "DEBUG":
            print(f"{version} | Debug | {message}")

    decoded = _decode_gif_input(input_path, gif_cfg, version)
    frames_raw = decoded["frames_raw"]
    durations = decoded["durations"]
    width = decoded["width"]
    height = decoded["height"]
    total_frames = decoded["total_frames"]
    colors_first = decoded["colors_first"]
    palette_limit = decoded["palette_limit"]
    init_size = decoded["init_size"]

    workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"{version} | [gif.prepare] Using {workers} workers for {total_frames} frames")
    debug_log(f"log_level={log_level} | max_safe_iterations={gif_cfg.max_safe_iterations}")

    target_mid = (gif_cfg.target_min_mb + gif_cfg.target_max_mb) / 2
    bias_factor = 1.1 + 0.05 * (palette_limit / 256.0)

    stats_mgr = CompressorStatsManager(stats_file, version)
    scale, source = _choose_initial_scale(
        stats_mgr,
        palette_limit,
        width,
        height,
        total_frames,
        init_size,
        target_mid,
        bias_factor,
        gif_cfg,
    )

    print(f"{version} | [gif.predict] Prediction source: {source}")
    print(f"{version} | [gif.predict] -> initial scale={scale:.3f}")

    state = GifRuntimeState(
        scale=scale,
        low_scale=0.01,
        high_scale=4.0,
        fast_cache={},
        med_cache={},
    )
    small_res_high_frames = (
        (width * height) <= gif_cfg.temporal_max_pixels
        and total_frames >= gif_cfg.temporal_min_frames
    )

    pool_start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        print(f"{version} | [gif.diag] pool_startup={time.time() - pool_start:.2f}s")
        for iteration in range(gif_cfg.max_safe_iterations):
            result = _run_balanced_iteration(
                iteration=iteration,
                source=source,
                state=state,
                frames_raw=frames_raw,
                durations=durations,
                width=width,
                height=height,
                palette_limit=palette_limit,
                total_frames=total_frames,
                colors_first=colors_first,
                init_size=init_size,
                input_path=input_path,
                stats_mgr=stats_mgr,
                executor=executor,
                workers=workers,
                target_mid=target_mid,
                bias_factor=bias_factor,
                small_res_high_frames=small_res_high_frames,
                gif_cfg=gif_cfg,
                started_at=started_at,
                version=version,
                debug_log=debug_log,
            )
            if result["done"]:
                return

            frames_raw = result["frames_raw"]
            durations = result["durations"]
            total_frames = result["total_frames"]

    print(f"{version} | [gif.fail] Failed to converge after {gif_cfg.max_safe_iterations} iterations")


def process_gifs(
    gif_paths,
    animated_webp_paths,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    compress_animated_webp_until_under_target,
    debug_log_fn=None,
):
    worked = False
    for file_path in gif_paths:
        worked = True
        try:
            balanced_compress_gif(
                file_path,
                gif_cfg=gif_cfg,
                version=version,
                stats_file=stats_file,
                log_level=log_level,
                debug_log_fn=debug_log_fn,
            )
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    for file_path in animated_webp_paths:
        worked = True
        try:
            compress_animated_webp_until_under_target(file_path)
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    return worked
