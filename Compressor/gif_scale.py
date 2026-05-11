"""Scale selection and advancement helpers for GIF compression."""

from gif_ops import _scale_key


def _choose_initial_scale(stats_mgr, palette_limit, width, height, total_frames, init_size, target_mid, bias_factor, gif_cfg):
    avg_scale = stats_mgr.average_scale_recent(palette_limit, width, height, total_frames)
    delta_avg = stats_mgr.find_delta(palette_limit, width, height, total_frames)
    neighbor_profile = stats_mgr.neighbor_scale_profile(palette_limit, width, height, total_frames)

    if avg_scale:
        return avg_scale, "stats"
    if neighbor_profile:
        neighbor_scale = neighbor_profile["scale"]
        neighbor_std = neighbor_profile["std"]
        neighbor_count = neighbor_profile["count"]

        is_confident_neighbor = (
            neighbor_count >= gif_cfg.prediction.neighbor_scale_confident_min_count
            and neighbor_std <= gif_cfg.prediction.neighbor_scale_confident_max_std
        )

        safety = (
            gif_cfg.prediction.neighbor_scale_safety_confident
            if is_confident_neighbor
            else gif_cfg.prediction.neighbor_scale_safety
        )

        safe_neighbor_scale = neighbor_scale * safety
        size_ratio_floor = (target_mid / init_size) ** 0.5 * 0.99
        if size_ratio_floor > safe_neighbor_scale:
            safe_neighbor_scale = size_ratio_floor

        return (
            safe_neighbor_scale,
            f"neighbor stats (safe x{safety:.3f}, n={neighbor_count}, std={neighbor_std:.3f})",
        )
    if delta_avg is not None:
        predicted_medcut = init_size + delta_avg * bias_factor
        scale_from_delta = (target_mid / predicted_medcut) ** 0.5
        return scale_from_delta * 0.97, "delta_avg (conservative)"
    scale_from_formula = (target_mid / (init_size * bias_factor)) ** 0.5
    return scale_from_formula * 0.95, "formula (conservative)"


def _next_scale(scale, low_scale, high_scale, med_cache, target_mid, max_step_ratio):
    new_scale = (low_scale + high_scale) / 2

    if abs(new_scale - scale) > scale * max_step_ratio:
        direction = 1 if new_scale > scale else -1
        new_scale = scale + direction * scale * max_step_ratio

    low_key = _scale_key(low_scale)
    high_key = _scale_key(high_scale)
    if low_key in med_cache and high_key in med_cache and low_scale != high_scale:
        med_low = med_cache[low_key][0]
        med_high = med_cache[high_key][0]
        if med_high != med_low:
            secant_scale = low_scale + (target_mid - med_low) * (high_scale - low_scale) / (med_high - med_low)
            if abs(secant_scale - scale) <= scale * max_step_ratio:
                new_scale = secant_scale

    return new_scale


def _advance_scale_after_medcut(*, state, med_size, target_mid, gif_cfg, med_cache, version):
    if med_size > gif_cfg.targets.target_max_mb:
        state.high_scale = state.scale
    else:
        state.low_scale = state.scale

    adaptive_scale = state.scale
    if med_size > 0:
        adaptive_scale = state.scale * (target_mid / med_size) ** 0.5

    max_adaptive_step_ratio = min(0.35, gif_cfg.runtime.max_scale_step_ratio * 2.5)
    adaptive_step = state.scale * max_adaptive_step_ratio
    if abs(adaptive_scale - state.scale) > adaptive_step:
        direction = 1 if adaptive_scale > state.scale else -1
        adaptive_scale = state.scale + direction * adaptive_step

    adaptive_in_bracket = state.low_scale < adaptive_scale < state.high_scale
    if adaptive_in_bracket:
        new_scale = adaptive_scale
    else:
        new_scale = _next_scale(
            scale=state.scale,
            low_scale=state.low_scale,
            high_scale=state.high_scale,
            med_cache=med_cache,
            target_mid=target_mid,
            max_step_ratio=gif_cfg.runtime.max_scale_step_ratio,
        )
    print(f"{version} | [gif.next-scale] Compute next scale")
    print(f"{version} | [gif.next-scale] Next scale={new_scale:.3f}")
    print(f"{version} | [gif.next-scale] -> bracket: low={state.low_scale:.3f}, high={state.high_scale:.3f}")
    state.scale = new_scale
