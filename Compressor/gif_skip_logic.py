"""Skip decision logic for iteration optimization."""

from scale_strategy import ScaleStrategy


def _try_hard_skip(
    *,
    iteration,
    source,
    source_is_neighbor,
    fast_size,
    state,
    target_mid,
    bias_factor,
    stats_mgr,
    palette_limit,
    width,
    height,
    total_frames,
    gif_cfg,
    version,
):
    """Mutates state.scale and returns True if early hard-skip applies, else None."""
    if not (
        iteration == 0
        and (source == "formula (conservative)" or source_is_neighbor)
        and fast_size > gif_cfg.target_max_mb * gif_cfg.fast_probe_hard_skip_ratio
    ):
        return None

    state.high_scale = state.scale
    if source_is_neighbor:
        delta_for_skip = stats_mgr.find_delta(palette_limit, width, height, total_frames)
        if delta_for_skip is not None:
            target_fast = target_mid - delta_for_skip * bias_factor
            if target_fast > 0 and fast_size > 0:
                suggested_scale = state.scale * (target_fast / fast_size) ** 0.5
            else:
                suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 * 0.92
        else:
            suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 * 0.92 if fast_size > 0 else state.scale
    else:
        suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 if fast_size > 0 else state.scale
        suggested_scale *= 0.92

    suggested_scale = ScaleStrategy.apply_step_cap(state.scale, suggested_scale, max_step_ratio=0.55)
    suggested_scale = ScaleStrategy.clamp_to_bracket(suggested_scale, state.low_scale, state.high_scale)

    print(
        f"{version} | [gif.skip] Early hard-skip on iter 1: FASTOCTREE={fast_size:.2f} MB "
        f"(>{gif_cfg.fast_probe_hard_skip_ratio:.2f}x target_max)"
    )
    print(f"{version} | [gif.skip] -> next scale={suggested_scale:.3f}")
    state.scale = suggested_scale
    return True


def _try_formula_under_target_skip(
    *,
    iteration,
    source,
    predicted_medcut,
    fast_size,
    state,
    target_mid,
    gif_cfg,
    version,
):
    """Mutates state.scale and returns True if formula-under-target skip applies, else None."""
    if not (
        source == "formula (conservative)"
        and predicted_medcut < (gif_cfg.target_min_mb - 0.35)
        and fast_size < gif_cfg.target_min_mb
        and iteration < (gif_cfg.max_safe_iterations - 1)
    ):
        return None

    state.low_scale = max(state.low_scale, state.scale)
    suggested_scale = state.scale * (target_mid / max(predicted_medcut, 0.1)) ** 0.5

    max_up_step = state.scale * min(0.30, gif_cfg.max_scale_step_ratio * 2.0)
    suggested_scale = ScaleStrategy.apply_step_cap(state.scale, suggested_scale, max_step_ratio=(max_up_step / state.scale if state.scale > 0 else 0.30))
    suggested_scale = ScaleStrategy.clamp_to_bracket(suggested_scale, state.low_scale, state.high_scale)

    print(f"{version} | [gif.skip] | Skip decision: formula-under-target skip")
    print(
        f"{version} | [gif.skip] | Skipping MEDIANCUT on iter {iteration+1} "
        "(formula under-target pre-adjust)"
    )
    print(f"{version} | [gif.skip] | -> next scale={suggested_scale:.3f}")
    state.scale = suggested_scale
    return True
