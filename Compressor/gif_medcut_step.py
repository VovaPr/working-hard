"""MEDIANCUT compression step execution."""

import time

from gif_ops import _scale_key, compress_med_cut


def _run_medcut_step(
    *,
    iteration,
    resized_frames,
    durations,
    palette_limit,
    executor,
    workers,
    gif_cfg,
    state,
    debug_log,
    version,
):
    """Run MEDIANCUT with cache. Returns (med_size, med_bytes)."""
    scale_key = _scale_key(state.scale)
    if scale_key in state.med_cache:
        print(f"{version} | [gif.medcut] | Use cached MEDIANCUT result")
        med_size, med_bytes = state.med_cache[scale_key]
        print(f"{version} | [gif.medcut] | Step {iteration+1}.1 (cached) | MEDIANCUT={med_size:.2f} MB")
        debug_log(f"cache=med | hit | key={scale_key}")
    else:
        print(f"{version} | [gif.medcut] | Execute MEDIANCUT")
        step_start = time.time()
        buf_med, med_size = compress_med_cut(
            resized_frames,
            durations,
            palette_limit,
            executor,
            workers,
            gif_cfg,
            final=False,
        )
        med_bytes = buf_med.getvalue()
        state.med_cache[scale_key] = (med_size, med_bytes)
        step_elapsed = time.time() - step_start
        print(f"{version} | [gif.medcut] | Step {iteration+1}.1 | MEDIANCUT={med_size:.2f} MB | finished in {step_elapsed:.2f} sec")
        debug_log(f"cache=med | miss | key={scale_key}")
    return med_size, med_bytes
