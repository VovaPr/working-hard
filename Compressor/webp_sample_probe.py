import time


def _select_sample_indices(frame_count, sample_n):
    return [int(i * frame_count / sample_n) for i in range(sample_n)]


def _extrapolate_full_size(probe_size, sample_n, frame_count, bias):
    return probe_size / sample_n * frame_count * bias


def _compute_corrected_quality(
    quality,
    predicted_full,
    target_mid_bytes,
    max_upward_factor,
    max_upward_steps,
):
    if predicted_full <= 0:
        return None, None, False
    correction = (target_mid_bytes / predicted_full) ** 0.5
    raw_quality = max(45, min(100, int(quality * correction)))
    corrected_quality = raw_quality
    was_capped = False
    if raw_quality > quality:
        cap_by_factor = int(quality * max_upward_factor)
        cap_by_steps = quality + max_upward_steps
        corrected_quality = min(raw_quality, cap_by_factor, cap_by_steps, 100)
        was_capped = corrected_quality < raw_quality
    return corrected_quality, raw_quality, was_capped


def run_webp_sample_probe(*, frames, durations, quality, target_mid_bytes, frame_count, local_version, gif_cfg, save_webp_frames):
    """Encode a small evenly-spaced subset of frames, extrapolate full size, return calibrated quality.

    Returns the corrected quality (int) if it differs from the input quality by >= 3 units,
    otherwise returns None (caller keeps the original quality unchanged).
    """
    if not gif_cfg.webp.webp_sample_probe_enabled:
        return None, None
    if frame_count < gif_cfg.webp.webp_sample_probe_min_frames:
        return None, None

    sample_n = min(gif_cfg.webp.webp_sample_probe_sample_count, frame_count)
    indices = _select_sample_indices(frame_count, sample_n)
    sample_frames = [frames[i] for i in indices]
    sample_durations = [durations[i] for i in indices] if isinstance(durations, (list, tuple)) else durations

    probe_start = time.time()
    try:
        probe_buf = save_webp_frames(sample_frames, sample_durations, quality, method=2)
    except Exception as e:
        print(f"{local_version} | [webp.probe] | failed: {e}")
        return None, None
    probe_elapsed = time.time() - probe_start

    probe_size = len(probe_buf.getvalue())
    predicted_full = _extrapolate_full_size(probe_size, sample_n, frame_count, gif_cfg.webp.webp_sample_probe_bias)
    corrected_quality, raw_quality, was_capped = _compute_corrected_quality(
        quality,
        predicted_full,
        target_mid_bytes,
        gif_cfg.webp.webp_sample_probe_max_upward_factor,
        gif_cfg.webp.webp_sample_probe_max_upward_steps,
    )

    probe_observation = (quality, int(predicted_full))
    cap_note = f" (capped from q={raw_quality})" if was_capped else ""
    print(
        f"{local_version} | [webp.probe] | {sample_n}/{frame_count} frames "
        f"| probe={probe_size / 1024:.1f} KB predicted={predicted_full / 1024:.1f} KB "
        f"| q={quality} -> q={corrected_quality}{cap_note} | elapsed={probe_elapsed:.1f}s"
    )

    if corrected_quality is None or abs(corrected_quality - quality) < 3:
        print(f"{local_version} | [webp.probe] | change too small | keeping q={quality}")
        return None, probe_observation
    return corrected_quality, probe_observation
