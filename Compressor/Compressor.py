"""Compressor runtime entrypoint.

What this compressor does:
- Converts PNG/JFIF images to JPG and compresses static images to <= 999 KB.
- Compresses GIF and animated WEBP files to the strict target range: 13.5-14.99 MB.
- Uses historical stats to predict startup parameters and reduce costly iterations.
"""

# Single source of truth for the application version.
APP_VERSION = "2.0.43"

# Standard library imports
import os, sys, time, subprocess
from datetime import datetime
from dataclasses import dataclass, field
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
class GIFConfig:
    """GIF compression parameters. Change values only here — used everywhere in the code.
    
    ?? CRITICAL: target_min_mb and target_max_mb are SACRED constraints.
    NEVER widen, narrow, or relax these bounds under ANY circumstances.
    These define the exact deliverable GIF size spec and must be met strictly.
    Quality, iteration speed, and all predictions are tuned around these IMMUTABLE limits.
    If requirements conflict with other goals, optimize the algorithm around them — never move them.
    """
    target_min_mb: float = 13.5  # SACRED: minimum GIF size (13.5 MB) — DO NOT CHANGE
    target_max_mb: float = 14.99  # SACRED: maximum GIF size (14.99 MB) — DO NOT CHANGE
    preferred_min_mb: float = 13.8
    preferred_max_mb: float = 14.6
    max_safe_iterations: int = 10
    extra_palette: int = 4
    min_process_size_mb: float = 15.0
    max_scale_step_ratio: float = 0.15
    neighbor_scale_safety: float = 0.95
    neighbor_scale_safety_confident: float = 0.985
    neighbor_scale_confident_min_count: int = 4
    neighbor_scale_confident_max_std: float = 0.035
    temporal_preserve_enabled: bool = True
    temporal_min_frames: int = 360
    temporal_max_pixels: int = 100000
    temporal_max_keep_every: int = 3
    quality_retry_small_res_enabled: bool = True
    quality_retry_min_scale: float = 0.70
    sample_probe_enabled: bool = True
    sample_probe_max_frames: int = 36
    sample_probe_min_frames: int = 12
    # For dense palettes, neighbor-based predictions can under-estimate MEDIANCUT output.
    # Use sample probe in these high-risk cases to skip costly bad first MEDIANCUT runs.
    sample_probe_neighbor_min_palette: int = 220
    sample_probe_neighbor_min_frames: int = 100
    fast_direct_accept_enabled: bool = True
    fast_direct_min_frames: int = 120
    probe_skip_overflow_margin: float = 1.08
    # Tight overflow skip margin used when sample probe was freshly measured.
    # Sample probe is real MEDIANCUT on N frames, so it can use near-target thresholding.
    sample_probe_overflow_margin: float = 1.005
    probe_skip_underflow_margin_mb: float = 0.10
    process_pool_tasks_per_worker: int = 4
    fast_probe_hard_skip_ratio: float = 1.30
    stats_source_bias_extra: float = 1.08  # Extra conservative bias when predicting from stats source
    neighbor_source_bias_extra: float = 1.04  # Extra conservative bias for neighbor-based predictions to account for variance
    webp_animated_max_iterations: int = 12
    webp_static_max_iterations: int = 12
    webp_static_method_default: int = 4
    webp_animated_method_default: int = 2
    webp_animated_direct_final_fast_enabled: bool = True
    webp_animated_direct_final_fast_method: int = 1
    # Use fast direct-final method only if known method=2 result has enough headroom.
    # If fast method is likely to inflate size beyond target, skip it and use method=2 directly.
    webp_animated_direct_final_fast_max_growth: float = 1.10
    # Extra guard for fast direct-final: require predicted fast size to stay comfortably
    # below the hard upper target bound to avoid costly fallback re-encode.
    webp_animated_direct_final_fast_safety_ratio: float = 0.96
    webp_animated_direct_final_enabled: bool = True
    webp_animated_direct_final_init_tolerance_mb: float = 0.35
    # Max wall-clock seconds per file. Effective limit = max(this, frames * per_frame).
    webp_file_max_seconds: float = 3600.0
    webp_animated_near_band_ratio: float = 0.10
    webp_animated_nudge_small_ratio: float = 0.04
    webp_animated_nudge_small_step: int = 1
    webp_animated_nudge_large_step: int = 2
    # Ignore weak WEBP startup stats until the profile has been confirmed multiple times.
    webp_animated_startup_min_count: int = 2
    # Allow more time for large files: effective_max = max(webp_file_max_seconds, frames * per_frame).
    webp_animated_max_seconds_per_frame: float = 0.52
    # Sample probe: encode a small subset of frames to predict full size and calibrate initial quality.
    # Only runs when no stats profile exists (direct_final_from_stats=False) and frame_count >= min_frames.
    webp_sample_probe_enabled: bool = True
    webp_sample_probe_min_frames: int = 60
    webp_sample_probe_sample_count: int = 20
    webp_sample_probe_bias: float = 1.02  # Slight conservative factor to avoid underestimating full size
    # Conservative new-file fast-path (with strict guards): can trigger earlier resize
    # only when first pass is far above the target and predicted quality drops low.
    webp_animated_new_file_fastpath_enabled: bool = True
    webp_animated_new_file_fastpath_overflow_ratio: float = 1.20
    webp_animated_new_file_fastpath_resize_q_threshold: int = 48
    medcut_overhead_guard_enabled: bool = True
    medcut_overhead_guard_margin_mb: float = 6.0
    medcut_overhead_guard_max_hits: int = 2


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

