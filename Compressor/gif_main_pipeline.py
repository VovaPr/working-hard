"""GIF main pipeline orchestration."""

import time

from gif_main_steps import (
    _build_debug_log,
    _build_runtime_context,
    _decode_gif_input,
    _run_balanced_loop,
)


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

    debug_log = _build_debug_log(version, log_level, debug_log_fn=debug_log_fn)

    decoded = _decode_gif_input(input_path, gif_cfg, version)
    runtime = _build_runtime_context(
        decoded=decoded,
        gif_cfg=gif_cfg,
        stats_file=stats_file,
        version=version,
        debug_log=debug_log,
    )

    converged = _run_balanced_loop(
        input_path=input_path,
        decoded=decoded,
        runtime=runtime,
        gif_cfg=gif_cfg,
        started_at=started_at,
        version=version,
        debug_log=debug_log,
    )
    if not converged:
        print(f"{version} | [gif.fail] Failed to converge after {gif_cfg.max_safe_iterations} iterations")
