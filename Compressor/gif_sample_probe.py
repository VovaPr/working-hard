"""Sample probe calibration for MEDIANCUT prediction."""

import time

from gif_ops import _estimate_ratio_sample


def _run_sample_probe(
    *,
    iteration,
    should_probe_formula,
    should_probe_neighbor,
    resized_frames,
    durations,
    palette_limit,
    executor,
    workers,
    gif_cfg,
    state,
    predicted_medcut,
    fast_size,
    total_frames,
    version,
):
    """Run sample probe and apply carry-over ratio. Returns (predicted_medcut, sample_probe_measured_this_iter)."""
    sample_probe_measured_this_iter = False

    if (
        gif_cfg.sample_probe.sample_probe_enabled
        and not state.sample_probe_done
        and iteration <= 1
        and (should_probe_formula or should_probe_neighbor)
        and total_frames >= 120
    ):
        probe_start = time.time()
        state.sample_ratio = _estimate_ratio_sample(
            resized_frames,
            durations,
            palette_limit,
            executor,
            workers,
            gif_cfg,
        )
        sample_probe_measured_this_iter = True
        state.sample_probe_done = True
        probe_elapsed = time.time() - probe_start
        if state.sample_ratio and state.sample_ratio > 1.0:
            calibrated_prediction = fast_size * state.sample_ratio
            if calibrated_prediction > predicted_medcut:
                predicted_medcut = calibrated_prediction
            print(
                f"{version} | [gif.predict] Probe ratio (sample)={state.sample_ratio:.3f} "
                f"-> calibrated MEDIANCUT={predicted_medcut:.2f} MB "
                f"| finished in {probe_elapsed:.2f} sec"
            )

    if state.sample_ratio and state.sample_ratio > 1.0:
        calibrated_prediction = fast_size * state.sample_ratio
        if calibrated_prediction > predicted_medcut:
            predicted_medcut = calibrated_prediction
            print(
                f"{version} | [gif.predict] Probe carry-over ratio={state.sample_ratio:.3f} "
                f"-> adjusted MEDIANCUT={predicted_medcut:.2f} MB"
            )

    return predicted_medcut, sample_probe_measured_this_iter
