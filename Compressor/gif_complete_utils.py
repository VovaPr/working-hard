from scale_strategy import ScaleStrategy


def _advance_fast_only_scale(*, state, fast_size, target_mid, gif_cfg, version):
    """Advance scale for FAST-only search mode when MEDIANCUT is disabled."""
    if fast_size < gif_cfg.target_min_mb:
        state.low_scale = max(state.low_scale, state.scale)
    elif fast_size > gif_cfg.target_max_mb:
        state.high_scale = min(state.high_scale, state.scale)

    suggested_scale = state.scale * (target_mid / max(fast_size, 0.1)) ** 0.5 if fast_size > 0 else state.scale
    max_step_ratio = max(0.30, gif_cfg.max_scale_step_ratio * 2.0) / state.scale if state.scale > 0 else 0.30
    suggested_scale = ScaleStrategy.apply_step_cap(state.scale, suggested_scale, max_step_ratio=max_step_ratio)
    suggested_scale = ScaleStrategy.clamp_to_bracket(suggested_scale, state.low_scale, state.high_scale)

    print(f"{version} | [gif.guard] FAST-only next scale={suggested_scale:.3f}")
    state.scale = suggested_scale
