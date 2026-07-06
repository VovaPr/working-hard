"""Shared tuning helpers for compression pipelines.

This module keeps only generic math and normalization helpers that can be reused
by GIF and WEBP adapters.
"""


def seed_ratio_quality(*, init_size_bytes, target_mid_bytes, min_quality, max_quality, bias=1.02):
    ratio = (target_mid_bytes / init_size_bytes) ** 0.5 if init_size_bytes > 0 else 1.0
    return max(min_quality, min(max_quality, int(max_quality * ratio * bias)))


def frame_adjusted_timeout(*, frame_count, base_seconds, min_seconds, per_frame_seconds):
    frame_adjusted_seconds = max(min_seconds, (frame_count or 0) * per_frame_seconds)
    return min(base_seconds, frame_adjusted_seconds)
