"""GIF-specific tuning adapter built on top of shared tuner helpers."""

from gif_scale import _choose_initial_scale


def resolve_startup_scale(stats_mgr, palette_limit, width, height, total_frames, init_size, target_mid, bias_factor, gif_cfg):
    return _choose_initial_scale(
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
