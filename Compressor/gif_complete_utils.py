def _advance_fast_only_scale(*, state, fast_size, target_mid, gif_cfg, version):
    """Advance scale for FAST-only search mode when MEDIANCUT is disabled."""
    if fast_size < gif_cfg.target_min_mb:
        state.low_scale = max(state.low_scale, state.scale)
    elif fast_size > gif_cfg.target_max_mb:
        state.high_scale = min(state.high_scale, state.scale)

    suggested_scale = state.scale * (target_mid / max(fast_size, 0.1)) ** 0.5 if fast_size > 0 else state.scale
    max_step = state.scale * max(0.30, gif_cfg.max_scale_step_ratio * 2.0)
    if abs(suggested_scale - state.scale) > max_step:
        direction = 1 if suggested_scale > state.scale else -1
        suggested_scale = state.scale + direction * max_step
    if not (state.low_scale < suggested_scale < state.high_scale):
        suggested_scale = (state.low_scale + state.high_scale) / 2

    print(f"{version} | [gif.guard] FAST-only next scale={suggested_scale:.3f}")
    state.scale = suggested_scale
