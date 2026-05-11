"""Compressor runtime entrypoint.

What this compressor does:
- Converts PNG/JFIF images to JPG and compresses static images to <= 999 KB.
- Compresses GIF and animated WEBP files to the strict target range: 13.5-14.99 MB.
- Uses historical stats to predict startup parameters and reduce costly iterations.
"""

# Single source of truth for the application version.
APP_VERSION = "2.0.48"

# Standard library imports
import os, sys, time, subprocess
from datetime import datetime
from dataclasses import dataclass, field
from typing import ClassVar
from image_compress import process_images as static_process_images
from scanner import scan_media_candidates as scan_media_candidates_impl
from webp_compress import compress_animated_webp_until_under_target as webp_compress_animated
from gif_compress import process_gifs as gif_process_gifs
from artifact_manager import get_artifact_manager

start_time = time.time()

@dataclass(frozen=True)
class JPGConfig:
    target_size: int = 999 * 1024
    quality_max: int = 95


@dataclass(frozen=True)
class GIFTargetsConfig:
    # SACRED: do not change these target bounds.
    target_min_mb: float = 13.5
    target_max_mb: float = 14.99
    preferred_min_mb: float = 13.8
    preferred_max_mb: float = 14.6
    min_process_size_mb: float = 15.0


@dataclass(frozen=True)
class GIFRuntimeConfig:
    max_safe_iterations: int = 10
    extra_palette: int = 4
    process_pool_tasks_per_worker: int = 4
    max_scale_step_ratio: float = 0.15


@dataclass(frozen=True)
class GIFPredictionConfig:
    neighbor_scale_safety: float = 0.95
    neighbor_scale_safety_confident: float = 0.985
    neighbor_scale_confident_min_count: int = 4
    neighbor_scale_confident_max_std: float = 0.035
    stats_source_bias_extra: float = 1.08
    neighbor_source_bias_extra: float = 1.04


@dataclass(frozen=True)
class GIFTemporalConfig:
    temporal_preserve_enabled: bool = True
    temporal_min_frames: int = 360
    temporal_max_pixels: int = 100000
    temporal_max_keep_every: int = 3
    quality_retry_small_res_enabled: bool = True
    quality_retry_min_scale: float = 0.70


@dataclass(frozen=True)
class GIFSampleProbeConfig:
    sample_probe_enabled: bool = True
    sample_probe_max_frames: int = 36
    sample_probe_min_frames: int = 12
    sample_probe_neighbor_min_palette: int = 220
    sample_probe_neighbor_min_frames: int = 100


@dataclass(frozen=True)
class GIFSkipConfig:
    fast_direct_accept_enabled: bool = True
    fast_direct_min_frames: int = 120
    probe_skip_overflow_margin: float = 1.08
    sample_probe_overflow_margin: float = 1.005
    probe_skip_underflow_margin_mb: float = 0.10
    fast_probe_hard_skip_ratio: float = 1.30


@dataclass(frozen=True)
class GIFGuardConfig:
    medcut_overhead_guard_enabled: bool = True
    medcut_overhead_guard_margin_mb: float = 6.0
    medcut_overhead_guard_max_hits: int = 2


@dataclass(frozen=True)
class WEBPConfig:
    webp_animated_max_iterations: int = 12
    webp_static_max_iterations: int = 12
    webp_static_method_default: int = 4
    webp_animated_method_default: int = 2
    webp_animated_direct_final_fast_enabled: bool = True
    webp_animated_direct_final_fast_method: int = 1
    webp_animated_direct_final_fast_max_growth: float = 1.10
    webp_animated_direct_final_fast_safety_ratio: float = 0.96
    webp_animated_direct_final_enabled: bool = True
    webp_animated_direct_final_init_tolerance_mb: float = 0.35
    webp_file_max_seconds: float = 3600.0
    webp_animated_near_band_ratio: float = 0.10
    webp_animated_nudge_small_ratio: float = 0.04
    webp_animated_nudge_small_step: int = 1
    webp_animated_nudge_large_step: int = 2
    webp_animated_startup_min_count: int = 2
    webp_animated_max_seconds_per_frame: float = 0.52
    webp_sample_probe_enabled: bool = True
    webp_sample_probe_min_frames: int = 60
    webp_sample_probe_sample_count: int = 20
    webp_sample_probe_bias: float = 1.02
    webp_animated_new_file_fastpath_enabled: bool = True
    webp_animated_new_file_fastpath_overflow_ratio: float = 1.20
    webp_animated_new_file_fastpath_resize_q_threshold: int = 48


@dataclass(frozen=True)
class GIFConfig:
    """Grouped GIF/WEBP compression config with backward-compatible legacy attribute access."""

    targets: GIFTargetsConfig = field(default_factory=GIFTargetsConfig)
    runtime: GIFRuntimeConfig = field(default_factory=GIFRuntimeConfig)
    prediction: GIFPredictionConfig = field(default_factory=GIFPredictionConfig)
    temporal: GIFTemporalConfig = field(default_factory=GIFTemporalConfig)
    sample_probe: GIFSampleProbeConfig = field(default_factory=GIFSampleProbeConfig)
    skip: GIFSkipConfig = field(default_factory=GIFSkipConfig)
    guard: GIFGuardConfig = field(default_factory=GIFGuardConfig)
    webp: WEBPConfig = field(default_factory=WEBPConfig)

    # Legacy flat access compatibility: gif_cfg.target_min_mb, gif_cfg.max_safe_iterations, etc.
    _legacy_aliases: ClassVar[dict] = {
        "target_min_mb": ("targets", "target_min_mb"),
        "target_max_mb": ("targets", "target_max_mb"),
        "preferred_min_mb": ("targets", "preferred_min_mb"),
        "preferred_max_mb": ("targets", "preferred_max_mb"),
        "min_process_size_mb": ("targets", "min_process_size_mb"),
        "max_safe_iterations": ("runtime", "max_safe_iterations"),
        "extra_palette": ("runtime", "extra_palette"),
        "process_pool_tasks_per_worker": ("runtime", "process_pool_tasks_per_worker"),
        "max_scale_step_ratio": ("runtime", "max_scale_step_ratio"),
        "neighbor_scale_safety": ("prediction", "neighbor_scale_safety"),
        "neighbor_scale_safety_confident": ("prediction", "neighbor_scale_safety_confident"),
        "neighbor_scale_confident_min_count": ("prediction", "neighbor_scale_confident_min_count"),
        "neighbor_scale_confident_max_std": ("prediction", "neighbor_scale_confident_max_std"),
        "stats_source_bias_extra": ("prediction", "stats_source_bias_extra"),
        "neighbor_source_bias_extra": ("prediction", "neighbor_source_bias_extra"),
        "temporal_preserve_enabled": ("temporal", "temporal_preserve_enabled"),
        "temporal_min_frames": ("temporal", "temporal_min_frames"),
        "temporal_max_pixels": ("temporal", "temporal_max_pixels"),
        "temporal_max_keep_every": ("temporal", "temporal_max_keep_every"),
        "quality_retry_small_res_enabled": ("temporal", "quality_retry_small_res_enabled"),
        "quality_retry_min_scale": ("temporal", "quality_retry_min_scale"),
        "sample_probe_enabled": ("sample_probe", "sample_probe_enabled"),
        "sample_probe_max_frames": ("sample_probe", "sample_probe_max_frames"),
        "sample_probe_min_frames": ("sample_probe", "sample_probe_min_frames"),
        "sample_probe_neighbor_min_palette": ("sample_probe", "sample_probe_neighbor_min_palette"),
        "sample_probe_neighbor_min_frames": ("sample_probe", "sample_probe_neighbor_min_frames"),
        "fast_direct_accept_enabled": ("skip", "fast_direct_accept_enabled"),
        "fast_direct_min_frames": ("skip", "fast_direct_min_frames"),
        "probe_skip_overflow_margin": ("skip", "probe_skip_overflow_margin"),
        "sample_probe_overflow_margin": ("skip", "sample_probe_overflow_margin"),
        "probe_skip_underflow_margin_mb": ("skip", "probe_skip_underflow_margin_mb"),
        "fast_probe_hard_skip_ratio": ("skip", "fast_probe_hard_skip_ratio"),
        "medcut_overhead_guard_enabled": ("guard", "medcut_overhead_guard_enabled"),
        "medcut_overhead_guard_margin_mb": ("guard", "medcut_overhead_guard_margin_mb"),
        "medcut_overhead_guard_max_hits": ("guard", "medcut_overhead_guard_max_hits"),
        "webp_animated_max_iterations": ("webp", "webp_animated_max_iterations"),
        "webp_static_max_iterations": ("webp", "webp_static_max_iterations"),
        "webp_static_method_default": ("webp", "webp_static_method_default"),
        "webp_animated_method_default": ("webp", "webp_animated_method_default"),
        "webp_animated_direct_final_fast_enabled": ("webp", "webp_animated_direct_final_fast_enabled"),
        "webp_animated_direct_final_fast_method": ("webp", "webp_animated_direct_final_fast_method"),
        "webp_animated_direct_final_fast_max_growth": ("webp", "webp_animated_direct_final_fast_max_growth"),
        "webp_animated_direct_final_fast_safety_ratio": ("webp", "webp_animated_direct_final_fast_safety_ratio"),
        "webp_animated_direct_final_enabled": ("webp", "webp_animated_direct_final_enabled"),
        "webp_animated_direct_final_init_tolerance_mb": ("webp", "webp_animated_direct_final_init_tolerance_mb"),
        "webp_file_max_seconds": ("webp", "webp_file_max_seconds"),
        "webp_animated_near_band_ratio": ("webp", "webp_animated_near_band_ratio"),
        "webp_animated_nudge_small_ratio": ("webp", "webp_animated_nudge_small_ratio"),
        "webp_animated_nudge_small_step": ("webp", "webp_animated_nudge_small_step"),
        "webp_animated_nudge_large_step": ("webp", "webp_animated_nudge_large_step"),
        "webp_animated_startup_min_count": ("webp", "webp_animated_startup_min_count"),
        "webp_animated_max_seconds_per_frame": ("webp", "webp_animated_max_seconds_per_frame"),
        "webp_sample_probe_enabled": ("webp", "webp_sample_probe_enabled"),
        "webp_sample_probe_min_frames": ("webp", "webp_sample_probe_min_frames"),
        "webp_sample_probe_sample_count": ("webp", "webp_sample_probe_sample_count"),
        "webp_sample_probe_bias": ("webp", "webp_sample_probe_bias"),
        "webp_animated_new_file_fastpath_enabled": ("webp", "webp_animated_new_file_fastpath_enabled"),
        "webp_animated_new_file_fastpath_overflow_ratio": ("webp", "webp_animated_new_file_fastpath_overflow_ratio"),
        "webp_animated_new_file_fastpath_resize_q_threshold": ("webp", "webp_animated_new_file_fastpath_resize_q_threshold"),
    }

    def __getattr__(self, name):
        alias = self._legacy_aliases.get(name)
        if alias is None:
            raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")
        section_name, attr_name = alias
        return getattr(getattr(self, section_name), attr_name)


@dataclass(frozen=True)
class AppConfig:
    version: str = APP_VERSION
    root_folder_path: str = r"C:\other\lab\pic"
    stats_file: str = field(default_factory=lambda: os.path.join(os.path.dirname(__file__), "compressor_stats.json"))
    stats_soft_limit_mb: float = 50.0
    jpg: JPGConfig = field(default_factory=JPGConfig)
    gif: GIFConfig = field(default_factory=GIFConfig)


CONFIG = AppConfig()

# Initialize artifact manager
_artifact_mgr = get_artifact_manager(os.path.dirname(__file__))

# Backward-compatible aliases
ROOT_FOLDER_PATH = CONFIG.root_folder_path
VERSION = CONFIG.version
STATS_FILE = _artifact_mgr.get_stats_path()
TARGET_SIZE = CONFIG.jpg.target_size
QUALITY_MAX = CONFIG.jpg.quality_max

RUN_METRICS = {
    "scan_sec": 0.0,
    "png_candidates": 0,
    "jpg_candidates": 0,
    "static_webp_candidates": 0,
    "gif_candidates": 0,
    "animated_webp_candidates": 0,
}


def _parse_log_level(argv):
    """Read optional CLI argument: log=INFO|DEBUG (or --log=INFO|DEBUG)."""
    level = "INFO"
    for arg in argv[1:]:
        lower = arg.lower()
        if lower.startswith("log="):
            level = arg.split("=", 1)[1].strip().upper()
        elif lower.startswith("--log="):
            level = arg.split("=", 1)[1].strip().upper()

    return level if level in {"INFO", "DEBUG"} else "INFO"


LOG_LEVEL = _parse_log_level(sys.argv)


def debug_log(message):
    if LOG_LEVEL == "DEBUG":
        print(f"{VERSION} | Debug | {message}")


def scan_media_candidates(root_folder_path):
    """Single filesystem pass that classifies files for later processing."""
    return scan_media_candidates_impl(
        root_folder_path=root_folder_path,
        target_size=TARGET_SIZE,
        min_process_size_mb=CONFIG.gif.min_process_size_mb,
        run_metrics=RUN_METRICS,
    )


def process_images(png_paths, jpg_paths, static_webp_paths):
    """Image block: convert PNG to JPG, compress oversized JPG/JPEG, and compress static WEBP."""
    return static_process_images(
        png_paths,
        jpg_paths,
        static_webp_paths,
        version=VERSION,
        target_size=TARGET_SIZE,
        gif_cfg=CONFIG.gif,
    )


def compress_animated_webp_until_under_target(path, gif_cfg=CONFIG.gif):
    return webp_compress_animated(
        path=path,
        gif_cfg=gif_cfg,
        version=VERSION,
        stats_file=STATS_FILE,
    )



def process_gifs(gif_paths, animated_webp_paths):
    """GIF block: process queued oversized GIFs and oversized animated WEBPs."""
    return gif_process_gifs(
        gif_paths,
        animated_webp_paths,
        gif_cfg=CONFIG.gif,
        version=VERSION,
        stats_file=STATS_FILE,
        log_level=LOG_LEVEL,
        compress_animated_webp_until_under_target=compress_animated_webp_until_under_target,
        debug_log_fn=debug_log,
    )

if __name__ == "__main__":
    from runner import PipelineApi, run_pipeline

    print(
        f"Compressor {APP_VERSION} | Formats: PNG/JPG/JPEG/JFIF/static WEBP -> <= 999 KB; "
        "GIF/animated WEBP -> 13.5-14.99 MB"
    )

    run_pipeline(
        PipelineApi(
            version=VERSION,
            root_folder_path=ROOT_FOLDER_PATH,
            stats_file=STATS_FILE,
            stats_soft_limit_mb=CONFIG.stats_soft_limit_mb,
            run_metrics=RUN_METRICS,
            start_time=start_time,
            scan_media_candidates=scan_media_candidates,
            process_images=process_images,
            process_gifs=process_gifs,
            log_level=LOG_LEVEL,
        )
    )

