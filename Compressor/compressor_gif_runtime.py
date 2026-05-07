from dataclasses import dataclass


@dataclass
class GifRuntimeState:
    scale: float
    low_scale: float
    high_scale: float
    fast_cache: dict
    med_cache: dict
    micro_adjust_used: bool = False
    stall_count: int = 0
    last_signature: tuple = None
    temporal_applied: bool = False
    quality_retry_done: bool = False
    sample_probe_done: bool = False
    sample_ratio: float = None
    formula_extra_skip_used: bool = False
    medcut_overhead_hits: int = 0
    medcut_disabled: bool = False


@dataclass(frozen=True)
class SkipDecision:
    should_skip: bool
    reason: str = ""
    suggested_scale: float = 0.0
    next_low_scale: float = 0.0
    next_high_scale: float = 0.0
    mark_formula_extra_skip_used: bool = False


def is_in_target_range(size_mb, gif_cfg):
    return gif_cfg.target_min_mb <= size_mb <= gif_cfg.target_max_mb + 0.005


def is_in_preferred_range(size_mb, gif_cfg):
    return gif_cfg.preferred_min_mb <= size_mb <= gif_cfg.preferred_max_mb


def _apply_step_cap(current_scale, suggested_scale, max_step_ratio):
    max_step = current_scale * max_step_ratio
    if abs(suggested_scale - current_scale) <= max_step:
        return suggested_scale
    direction = 1 if suggested_scale > current_scale else -1
    return current_scale + direction * max_step


def _clamp_to_bracket(suggested_scale, low_scale, high_scale):
    if low_scale < suggested_scale < high_scale:
        return suggested_scale
    return (low_scale + high_scale) / 2


def predict_medcut_size(
    stats_mgr,
    palette_limit,
    width,
    height,
    total_frames,
    fast_size,
    bias_factor,
    source,
    gif_cfg,
    clamp_prediction_fn,
):
    predicted = stats_mgr.predict_mediancut(
        palette_limit,
        width,
        height,
        total_frames,
        fast_size,
        bias_factor,
    )
    predicted = clamp_prediction_fn(predicted, fast_size)
    if source == "stats":
        predicted *= gif_cfg.stats_source_bias_extra
        predicted = clamp_prediction_fn(predicted, fast_size)
    return predicted


def build_skip_decision(
    iteration,
    source,
    source_is_neighbor,
    should_probe_formula,
    should_probe_neighbor,
    sample_ratio,
    sample_probe_measured_this_iter,
    predicted_medcut,
    fast_size,
    current_scale,
    low_scale,
    high_scale,
    target_mid,
    formula_extra_skip_used,
    gif_cfg,
):
    fresh_probe = sample_probe_measured_this_iter and sample_ratio is not None
    overflow_margin = (
        gif_cfg.sample_probe_overflow_margin if fresh_probe
        else gif_cfg.probe_skip_overflow_margin
    )

    can_skip_first_med = (
        iteration == 0
        and (source == "formula (conservative)" or source_is_neighbor)
        and predicted_medcut > gif_cfg.target_max_mb * 1.20
        and fast_size > gif_cfg.target_max_mb * 0.90
    )
    can_skip_probe_overflow = (
        iteration <= 1
        and (should_probe_formula or should_probe_neighbor)
        and sample_ratio is not None
        and predicted_medcut > gif_cfg.target_max_mb * overflow_margin
    )
    can_skip_probe_underflow = (
        iteration <= 1
        and (should_probe_formula or should_probe_neighbor)
        and sample_ratio is not None
        and predicted_medcut < (gif_cfg.target_min_mb - gif_cfg.probe_skip_underflow_margin_mb)
    )
    can_skip_formula_extra = (
        iteration == 1
        and source == "formula (conservative)"
        and not formula_extra_skip_used
        and sample_ratio is not None
        and predicted_medcut > gif_cfg.target_max_mb * 1.10
        and fast_size > gif_cfg.target_min_mb * 0.90
    )

    should_skip = (
        can_skip_first_med
        or can_skip_probe_overflow
        or can_skip_probe_underflow
        or can_skip_formula_extra
    )
    if not should_skip:
        return SkipDecision(False)

    next_low_scale = low_scale
    next_high_scale = high_scale
    if can_skip_probe_underflow:
        next_low_scale = current_scale
        next_high_scale = max(next_high_scale, 4.0)
    elif can_skip_probe_overflow:
        next_high_scale = current_scale
    else:
        next_high_scale = current_scale

    suggested_scale = current_scale * (target_mid / predicted_medcut) ** 0.5 if predicted_medcut > 0 else current_scale
    suggested_scale = _apply_step_cap(current_scale, suggested_scale, max_step_ratio=0.45)
    suggested_scale = _clamp_to_bracket(suggested_scale, next_low_scale, next_high_scale)

    reason = "predicted too large" if (can_skip_first_med or can_skip_probe_overflow or can_skip_formula_extra) else "predicted too small"
    return SkipDecision(
        should_skip=True,
        reason=reason,
        suggested_scale=suggested_scale,
        next_low_scale=next_low_scale,
        next_high_scale=next_high_scale,
        mark_formula_extra_skip_used=can_skip_formula_extra,
    )
