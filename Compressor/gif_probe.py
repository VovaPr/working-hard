"""FASTOCTREE probing helpers for GIF compression."""

import time

from gif_ops import _scale_key, process_frame_fast_octree, resize_frames, save_gif


def _run_fastoctree_trial(
    *,
    iteration,
    scale,
    frames_raw,
    width,
    height,
    palette_limit,
    durations,
    fast_cache,
    version,
    stage_tag="base",
):
    """Run FASTOCTREE probe with cache by scale."""
    resize_start = time.time()
    resized_frames = resize_frames(frames_raw, width, height, scale)
    print(f"{version} | [gif.diag] | resize scale={scale:.3f} elapsed={time.time() - resize_start:.2f}s")
    key = _scale_key(scale)

    if key in fast_cache:
        fast_size = fast_cache[key]["size"]
        print(f"{version} | [gif.fast] | Step {iteration+1}.0 ({stage_tag}, cached) | FASTOCTREE={fast_size:.2f} MB")
        return resized_frames, fast_size, fast_cache[key].get("bytes")

    step_start = time.time()
    frames_fast = [process_frame_fast_octree(frame, palette_limit) for frame in resized_frames]
    buf_fast, fast_size = save_gif(frames_fast, durations, optimize=False)
    fast_cache[key] = {"size": fast_size, "bytes": buf_fast.getvalue()}
    step_elapsed = time.time() - step_start
    print(f"{version} | [gif.fast] | Step {iteration+1}.0 ({stage_tag}) | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
    return resized_frames, fast_size, fast_cache[key]["bytes"]
