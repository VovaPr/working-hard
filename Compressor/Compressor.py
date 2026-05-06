"""
GOAL: Reliably compress GIF files to the target size of 13.5–14.99 MB
in 30 seconds on average, without quality degradation.
Strategy: Accurate initial scale prediction from historical run stats
→ at most 1–2 MEDIANCUT calls per file.
"""

# Standard library imports
import os, sys, time, io, json, subprocess
from datetime import datetime
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor
from static_pipeline import process_images as static_process_images
from scan_pipeline import scan_media_candidates as scan_media_candidates_impl
from compressor_gif_runtime import (
    GifRuntimeState,
    build_skip_decision,
    is_in_preferred_range,
    is_in_target_range,
    predict_medcut_size,
)

# Third-party imports
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

start_time = time.time()

@dataclass(frozen=True)
class JPGConfig:
    target_size: int = 999 * 1024
    quality_max: int = 95


@dataclass(frozen=True)
class GIFConfig:
    """GIF compression parameters. Change values only here — used everywhere in the code.
    
    ⚠️ CRITICAL: target_min_mb and target_max_mb are SACRED constraints.
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
    sample_probe_neighbor_min_frames: int = 120
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
    webp_animated_max_iterations: int = 12
    webp_static_max_iterations: int = 12
    webp_static_method_default: int = 4
    webp_animated_method_default: int = 2
    webp_animated_direct_final_fast_enabled: bool = True
    webp_animated_direct_final_fast_method: int = 1
    # Use fast direct-final method only if known method=2 result has enough headroom.
    # If fast method is likely to inflate size beyond target, skip it and use method=2 directly.
    webp_animated_direct_final_fast_max_growth: float = 1.10
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


@dataclass(frozen=True)
class AppConfig:
    version: str = "2.0.1"
    root_folder_path: str = r"C:\other\lab\pic"
    stats_file: str = field(default_factory=lambda: os.path.join(os.path.dirname(__file__), "CompressorStats.JSON"))
    stats_soft_limit_mb: float = 50.0
    jpg: JPGConfig = field(default_factory=JPGConfig)
    gif: GIFConfig = field(default_factory=GIFConfig)


CONFIG = AppConfig()

# Backward-compatible aliases
ROOT_FOLDER_PATH = CONFIG.root_folder_path
VERSION = CONFIG.version
STATS_FILE = CONFIG.stats_file
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


def _is_animated_webp(path):
    try:
        with Image.open(path) as img:
            return bool(getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1)
    except Exception:
        return False


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


def _save_webp_frames(frames, durations, quality, method=6):
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        quality=quality,
        method=method,
    )
    return buf


def _compress_animated_webp(
    frames,
    durations,
    path,
    init_size,
    target_min_bytes,
    target_max_bytes,
    target_mid_bytes,
    local_version,
    gif_cfg,
    started_at,
    stats_mgr_webp=None,
    width=None,
    height=None,
    frame_count=None,
):
    """Animated WEBP compression with bracketed quality search and guarded runtime."""
    startup_plan = None
    if stats_mgr_webp and width and height and frame_count:
        startup_plan = stats_mgr_webp.select_startup_plan(
            width,
            height,
            frame_count,
            init_size / (1024 * 1024),
            gif_cfg.target_min_mb,
            gif_cfg.target_max_mb,
            gif_cfg,
        )

    known_result_size_mb = None
    if startup_plan is not None:
        quality = startup_plan["quality"]
        source = startup_plan["source"]
        direct_final_from_stats = startup_plan["direct_final"]
        known_result_size_mb = startup_plan.get("result_size_mb")
    elif stats_mgr_webp and width and height and frame_count:
        # Cold-start fallback: estimate startup quality from size ratio instead of hardcoded q=95.
        ratio = (target_mid_bytes / init_size) ** 0.5 if init_size > 0 else 1.0
        quality = max(60, min(95, int(95 * ratio * 1.02)))
        source = (
            f"default (no webp match, records={stats_mgr_webp.stats_count()}, "
            f"ratio-seeded q={quality})"
        )
        direct_final_from_stats = False
    else:
        ratio = (target_mid_bytes / init_size) ** 0.5 if init_size > 0 else 1.0
        quality = max(60, min(95, int(95 * ratio * 1.02)))
        source = f"default (stats unavailable, ratio-seeded q={quality})"
        direct_final_from_stats = False

    print(f"{local_version} | Prediction source: {source} -> initial quality={quality}")

    resize_count = 0
    webp_method = max(0, min(6, gif_cfg.webp_animated_method_default))
    webp_method_direct_fast = max(0, min(6, gif_cfg.webp_animated_direct_final_fast_method))
    direct_fast_growth = max(1.0, float(gif_cfg.webp_animated_direct_final_fast_max_growth))
    effective_max_seconds = max(
        gif_cfg.webp_file_max_seconds,
        (frame_count or 0) * gif_cfg.webp_animated_max_seconds_per_frame,
    )
    if effective_max_seconds > gif_cfg.webp_file_max_seconds:
        print(
            f"{local_version} | WEBP animated timeout: {effective_max_seconds:.0f}s "
            f"(frame-adjusted for {frame_count} frames, base={gif_cfg.webp_file_max_seconds:.0f}s)"
        )

    can_use_direct_fast = False
    if direct_final_from_stats and gif_cfg.webp_animated_direct_final_fast_enabled and known_result_size_mb is not None:
        can_use_direct_fast = (known_result_size_mb * direct_fast_growth) <= gif_cfg.target_max_mb

    if direct_final_from_stats:
        direct_mode = webp_method_direct_fast if can_use_direct_fast else webp_method
        print(
            f"{local_version} | WEBP animated direct-final enabled | "
            f"known profile -> method={direct_mode}"
        )
        if gif_cfg.webp_animated_direct_final_fast_enabled and not can_use_direct_fast:
            print(
                f"{local_version} | WEBP direct-fast skipped | "
                f"known={known_result_size_mb:.2f} MB, growth_limit={direct_fast_growth:.2f}x"
            )

    under_target_q = None
    over_target_q = None
    best_effort_buf = None
    best_effort_size = None
    best_effort_q = None
    best_effort_method = None

    for step in range(1, gif_cfg.webp_animated_max_iterations + 1):
        quality = max(1, min(100, int(quality)))
        bracket_known = under_target_q is not None and over_target_q is not None
        direct_final_this_step = bool(direct_final_from_stats and step == 1)
        if direct_final_this_step:
            method_in_use = webp_method_direct_fast if can_use_direct_fast else webp_method
        else:
            # Always method=2 for accurate measurements — binary search requires real sizes.
            method_in_use = webp_method
        _step_elapsed = time.time() - started_at
        _bracket_str = f"{under_target_q}-{over_target_q}" if bracket_known else "none"
        print(
            f"{local_version} | WEBP animated step {step} | "
            f"Encoding... (q={quality}, method={method_in_use}) | "
            f"bracket={_bracket_str} | elapsed={_step_elapsed:.1f}s/{effective_max_seconds:.0f}s"
        )
        encode_start = time.time()
        try:
            encoded_buf = _save_webp_frames(frames, durations, quality, method=method_in_use)
        except ValueError as e:
            fallback_method = 0
            fallback_quality = max(1, min(100, quality))
            print(
                f"{local_version} | WEBP animated config error: {e} "
                f"| retry with q={fallback_quality}, method={fallback_method}"
            )
            try:
                encoded_buf = _save_webp_frames(frames, durations, fallback_quality, method=fallback_method)
                quality = fallback_quality
                method_in_use = fallback_method
            except ValueError as e2:
                print(f"{local_version} | ⚠ WEBP animated encode failed: {e2}; file kept unchanged")
                return

        encoded_size = len(encoded_buf.getvalue())
        effective_size = encoded_size
        effective_buf = encoded_buf
        effective_method = method_in_use
        step_encode_elapsed = time.time() - encode_start

        if direct_final_this_step and method_in_use != webp_method:
            if target_min_bytes <= encoded_size <= target_max_bytes:
                print(
                    f"{local_version} | WEBP direct-fast accepted | "
                    f"Size={encoded_size/1024:.2f} KB | method={method_in_use}"
                )
            else:
                print(
                    f"{local_version} | WEBP direct-fast miss | "
                    f"Size={encoded_size/1024:.2f} KB -> fallback method={webp_method}"
                )
                fallback_start = time.time()
                try:
                    final_buf = _save_webp_frames(frames, durations, quality, method=webp_method)
                    final_method = webp_method
                except ValueError as e:
                    fallback_method = 0
                    print(
                        f"{local_version} | WEBP direct-fast fallback error: {e} "
                        f"| retry with method={fallback_method}"
                    )
                    final_buf = _save_webp_frames(frames, durations, quality, method=fallback_method)
                    final_method = fallback_method
                fallback_elapsed = time.time() - fallback_start
                final_size = len(final_buf.getvalue())
                effective_size = final_size
                effective_buf = final_buf
                effective_method = final_method
                step_encode_elapsed += fallback_elapsed
                print(
                    f"{local_version} | WEBP direct-fast fallback result | "
                    f"Size={final_size/1024:.2f} KB | method={final_method} | fallback={fallback_elapsed:.2f} sec"
                )

        print(
            f"{local_version} | WEBP animated step {step} | "
            f"Size={effective_size/1024:.2f} KB | encode={step_encode_elapsed:.2f} sec"
        )

        # Success check MUST come before timeout: an in-target result must always be saved
        # regardless of how long the encode took. Timeout only discards out-of-range results.
        _in_target = target_min_bytes <= effective_size <= target_max_bytes
        if _in_target:
            print(
                f"{local_version} | WEBP animated success check: "
                f"size={effective_size/1024:.2f} KB in range [{target_min_bytes/1024:.2f}, {target_max_bytes/1024:.2f}] KB"
            )
            if stats_mgr_webp and width and height and frame_count:
                stats_mgr_webp.save_step(
                    width,
                    height,
                    frame_count,
                    init_size / (1024 * 1024),
                    quality,
                    effective_method,
                    effective_size / (1024 * 1024),
                    step_encode_elapsed,
                )
            with open(path, "wb") as f:
                f.write(effective_buf.getvalue())
            elapsed = time.time() - started_at
            print(
                f"{local_version} | ✅ WEBP success: {init_size/1024:.2f} KB -> {effective_size/1024:.2f} KB "
                f"| Quality={quality} | Resized {resize_count} times"
            )
            if stats_mgr_webp:
                print(
                    f"{local_version} | WEBP animated stats total: {stats_mgr_webp.stats_count()} records"
                )
            print(f"{local_version} | Finished in {elapsed:.2f} sec")
            return

        # Track best near-miss: closest result to target_mid (only from real method=2 measurements).
        if not _in_target:
            _miss_abs = abs(effective_size - target_mid_bytes)
            if best_effort_size is None or _miss_abs < abs(best_effort_size - target_mid_bytes):
                best_effort_buf = effective_buf
                best_effort_size = effective_size
                best_effort_q = quality
                best_effort_method = effective_method

        if effective_size < target_min_bytes:
            under_target_q = quality if under_target_q is None else max(under_target_q, quality)
        elif effective_size > target_max_bytes:
            over_target_q = quality if over_target_q is None else min(over_target_q, quality)
        _new_bracket = f"{under_target_q}-{over_target_q}" if (under_target_q is not None and over_target_q is not None) else f"under={under_target_q} over={over_target_q}"
        print(f"{local_version} | WEBP animated bracket update | {_new_bracket}")

        elapsed = time.time() - started_at
        if elapsed >= effective_max_seconds:
            # Rescue attempt: if bracket is known, do one final method=2 encode at midpoint.
            if under_target_q is not None and over_target_q is not None and over_target_q - under_target_q >= 1:
                rescue_q = (under_target_q + over_target_q) // 2
                if rescue_q == quality:
                    if effective_size < target_min_bytes and rescue_q < over_target_q:
                        rescue_q += 1
                    elif effective_size > target_max_bytes and rescue_q > under_target_q:
                        rescue_q -= 1
                print(
                    f"{local_version} | WEBP timeout-rescue | "
                    f"bracket={under_target_q}-{over_target_q} -> verify q={rescue_q}"
                )
                rescue_start = time.time()
                try:
                    rescue_buf = _save_webp_frames(frames, durations, rescue_q, method=webp_method)
                    rescue_method = webp_method
                except ValueError:
                    rescue_method = 0
                    rescue_buf = _save_webp_frames(frames, durations, rescue_q, method=rescue_method)
                rescue_elapsed = time.time() - rescue_start
                rescue_size = len(rescue_buf.getvalue())
                if target_min_bytes <= rescue_size <= target_max_bytes:
                    if stats_mgr_webp and width and height and frame_count:
                        stats_mgr_webp.save_step(
                            width,
                            height,
                            frame_count,
                            init_size / (1024 * 1024),
                            rescue_q,
                            rescue_method,
                            rescue_size / (1024 * 1024),
                            rescue_elapsed,
                        )
                    with open(path, "wb") as f:
                        f.write(rescue_buf.getvalue())
                    total_elapsed = time.time() - started_at
                    print(
                        f"{local_version} | ✅ WEBP success (timeout-rescue): "
                        f"{init_size/1024:.2f} KB -> {rescue_size/1024:.2f} KB "
                        f"| Quality={rescue_q} | method={rescue_method}"
                    )
                    print(f"{local_version} | Finished in {total_elapsed:.2f} sec")
                    return

            print(
                f"{local_version} | ⚠ WEBP animated timeout {elapsed:.2f} sec; "
                f"file kept unchanged"
            )
            return

        # When bracket is fully known and gap is 1, no integer q exists in range.
        # Accept best-effort result (closest to target_mid) rather than looping forever.
        if (
            under_target_q is not None
            and over_target_q is not None
            and over_target_q - under_target_q <= 1
            and best_effort_buf is not None
        ):
            _best_miss_pct = abs(best_effort_size - target_mid_bytes) / target_mid_bytes * 100
            print(
                f"{local_version} | WEBP best-effort accept | "
                f"bracket={under_target_q}-{over_target_q}, no integer solution | "
                f"q={best_effort_q} size={best_effort_size/1024:.2f} KB miss={_best_miss_pct:.2f}%"
            )
            if stats_mgr_webp and width and height and frame_count:
                stats_mgr_webp.save_step(
                    width, height, frame_count,
                    init_size / (1024 * 1024),
                    best_effort_q, best_effort_method,
                    best_effort_size / (1024 * 1024),
                    step_encode_elapsed,
                )
            with open(path, "wb") as f:
                f.write(best_effort_buf.getvalue())
            elapsed = time.time() - started_at
            print(
                f"{local_version} | ✅ WEBP best-effort: {init_size/1024:.2f} KB -> {best_effort_size/1024:.2f} KB "
                f"| Quality={best_effort_q} | Resized {resize_count} times"
            )
            print(f"{local_version} | Finished in {elapsed:.2f} sec")
            return

        # Near-target miss: nudge quality by 1-2 points.
        # Only fires when bracket is NOT yet known — once bracket is set, binary search takes over.
        near_mid_ratio = abs(effective_size - target_mid_bytes) / target_mid_bytes if target_mid_bytes > 0 else 0.0
        if near_mid_ratio <= gif_cfg.webp_animated_near_band_ratio and not bracket_known:
            miss_ratio = (
                (target_min_bytes - effective_size) / target_min_bytes
                if effective_size < target_min_bytes and target_min_bytes > 0
                else (effective_size - target_max_bytes) / target_max_bytes
                if effective_size > target_max_bytes and target_max_bytes > 0
                else 0.0
            )
            nudge_step = (
                gif_cfg.webp_animated_nudge_small_step
                if miss_ratio <= gif_cfg.webp_animated_nudge_small_ratio
                else gif_cfg.webp_animated_nudge_large_step
            )
            if effective_size < target_min_bytes:
                quality = min(100, quality + nudge_step)
            else:
                quality = max(45, quality - nudge_step)
            print(
                f"{local_version} | WEBP animated near-target nudge | "
                f"miss={miss_ratio*100:.2f}% | step={nudge_step} -> next_q={quality}"
            )
            continue

        correction = (target_mid_bytes / effective_size) ** 0.5
        correction = max(0.88, min(1.12, correction))

        if quality <= 45:
            new_w = max(1, int(frames[0].width * correction))
            new_h = max(1, int(frames[0].height * correction))
            frames = [fr.resize((new_w, new_h), Image.LANCZOS) for fr in frames]
            resize_count += 1
            quality = 95
            under_target_q = None
            over_target_q = None
            print(f"{local_version} | WEBP step {resize_count} | Resized to {new_w}x{new_h}, reset quality={quality}")
            continue

        if (
            under_target_q is not None
            and over_target_q is not None
            and over_target_q - under_target_q > 1
        ):
            quality = (under_target_q + over_target_q) // 2
            print(
                f"{local_version} | WEBP animated bracket | under_q={under_target_q}, "
                f"over_q={over_target_q} -> next_q={quality}"
            )
        else:
            proposed_quality = max(45, min(100, int(quality * correction)))

            # Never go backwards past a known partial bracket — prevent oscillation.
            if under_target_q is not None:
                proposed_quality = max(proposed_quality, under_target_q + 1)
            if over_target_q is not None:
                proposed_quality = min(proposed_quality, over_target_q - 1)

            quality = proposed_quality

        print(f"{local_version} | WEBP step {resize_count+1} | Quality={quality}")

    _final_msg = f"could not hit {gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB"
    if best_effort_buf is not None:
        _best_miss_pct = abs(best_effort_size - target_mid_bytes) / target_mid_bytes * 100
        print(
            f"{local_version} | WEBP best-effort accept (max iterations) | "
            f"q={best_effort_q} size={best_effort_size/1024:.2f} KB miss={_best_miss_pct:.2f}%"
        )
        if stats_mgr_webp and width and height and frame_count:
            stats_mgr_webp.save_step(
                width, height, frame_count,
                init_size / (1024 * 1024),
                best_effort_q, best_effort_method,
                best_effort_size / (1024 * 1024),
                0,
            )
        with open(path, "wb") as f:
            f.write(best_effort_buf.getvalue())
        elapsed = time.time() - started_at
        print(
            f"{local_version} | ✅ WEBP best-effort: {init_size/1024:.2f} KB -> {best_effort_size/1024:.2f} KB "
            f"| Quality={best_effort_q} | Resized {resize_count} times"
        )
        print(f"{local_version} | Finished in {elapsed:.2f} sec")
        return
    print(
        f"{local_version} | ⚠ WEBP animated max iterations reached; "
        f"file kept unchanged ({_final_msg})"
    )
    return


def compress_animated_webp_until_under_target(path, gif_cfg=CONFIG.gif):
    """
    Compresses animated WEBP files by extracting frames, applying a GIF-style compression strategy,
    and saving the result. Uses a stats manager to optimize quality selection based on previous runs.
    """
    local_version = VERSION
    started_at = time.time()
    target_min_bytes = int(gif_cfg.target_min_mb * 1024 * 1024)
    target_max_bytes = int(gif_cfg.target_max_mb * 1024 * 1024)
    target_mid_bytes = int(((gif_cfg.target_min_mb + gif_cfg.target_max_mb) / 2.0) * 1024 * 1024)

    try:
        with Image.open(path) as img:
            init_size = os.path.getsize(path)
            is_animated = bool(getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1)
            frame_count = getattr(img, "n_frames", 1)

            if not is_animated:
                return

            print(f"{local_version} | Initial WEBP: {path}")
            print(
                f"{local_version} | WxH={img.width}x{img.height} | Animated=True "
                f"| Frames={frame_count} | Size={init_size/1024:.2f} KB "
                f"| Target={gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB"
            )

            if target_min_bytes <= init_size <= target_max_bytes:
                print(f"{local_version} | ✅ WEBP already in target range, no compression needed")
                return

            frames = []
            durations = []
            for frame in ImageSequence.Iterator(img):
                # Avoid expensive convert() when frame is already in a WEBP-friendly mode.
                # copy() is still needed because ImageSequence frames are lazy-backed by source image.
                if frame.mode in ("RGB", "RGBA"):
                    prepared = frame.copy()
                else:
                    has_alpha_frame = "A" in frame.getbands()
                    prepared = frame.convert("RGBA" if has_alpha_frame else "RGB")
                frames.append(prepared)
                durations.append(frame.info.get("duration", 100))

            stats_mgr_webp = AnimatedWebPStatsManager(STATS_FILE)
            _compress_animated_webp(
                frames,
                durations,
                path,
                init_size,
                target_min_bytes,
                target_max_bytes,
                target_mid_bytes,
                local_version,
                gif_cfg,
                started_at,
                stats_mgr_webp=stats_mgr_webp,
                width=img.width,
                height=img.height,
                frame_count=frame_count,
            )

    except UnidentifiedImageError:
        print(f"{local_version} | Skipped corrupted WEBP: {path}")


class AnimatedWebPStatsManager:
    """
    Manages statistics for animated WEBP compression:
    Tracks quality → result_size mappings to predict optimal startup quality.
    Stores in JSON under 'webp_animated_stats' key.
    Used to speed up future compressions by learning from previous results.
    """
    def __init__(self, stats_file):
        self.stats_file = stats_file
        self.webp_stats = []
        self._load_webp_stats()

    def stats_count(self):
        return len(self.webp_stats)

    def _load_webp_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.webp_stats = data.get("webp_animated_stats", [])
                    else:
                        self.webp_stats = []
                    self.webp_stats = self._merge_duplicate_webp_stats(self.webp_stats)
            except Exception:
                self.webp_stats = []
        else:
            self.webp_stats = []

    def _merge_duplicate_webp_stats(self, entries):
        """Merge repeated successful runs for the same WEBP profile into a single record."""
        merged = {}
        for entry in entries:
            key = (
                entry.get("width"),
                entry.get("height"),
                entry.get("frames"),
                entry.get("init_size_mb"),
                entry.get("quality"),
                entry.get("method"),
            )
            cnt = int(entry.get("count", 1) or 1)
            if key not in merged:
                base = entry.copy()
                base["count"] = cnt
                merged[key] = base
                continue

            cur = merged[key]
            cur_cnt = int(cur.get("count", 1) or 1)
            total_cnt = cur_cnt + cnt

            cur_result = float(cur.get("result_size_mb", 0.0))
            new_result = float(entry.get("result_size_mb", cur_result))
            cur_encode = float(cur.get("encode_sec", 0.0))
            new_encode = float(entry.get("encode_sec", cur_encode))

            cur["result_size_mb"] = round((cur_result * cur_cnt + new_result * cnt) / max(total_cnt, 1), 2)
            cur["encode_sec"] = round((cur_encode * cur_cnt + new_encode * cnt) / max(total_cnt, 1), 2)
            cur["count"] = total_cnt
            cur["timestamp"] = max(float(cur.get("timestamp", 0)), float(entry.get("timestamp", 0)))

        return sorted(merged.values(), key=lambda e: e.get("timestamp", 0))

    def save_step(self, width, height, frames, init_size_mb, quality, method, result_size_mb, encode_sec):
        """Record one compression step attempt."""
        init_size_mb = round(init_size_mb, 2)
        result_size_mb = round(result_size_mb, 2)
        encode_sec = round(encode_sec, 2)
        now_ts = time.time()

        merged = False
        for entry in self.webp_stats:
            if (
                entry.get("width") == width
                and entry.get("height") == height
                and entry.get("frames") == frames
                and entry.get("init_size_mb") == init_size_mb
                and entry.get("quality") == quality
                and entry.get("method") == method
            ):
                cnt = int(entry.get("count", 1) or 1)
                entry["result_size_mb"] = round((float(entry.get("result_size_mb", result_size_mb)) * cnt + result_size_mb) / (cnt + 1), 2)
                entry["encode_sec"] = round((float(entry.get("encode_sec", encode_sec)) * cnt + encode_sec) / (cnt + 1), 2)
                entry["count"] = cnt + 1
                entry["timestamp"] = now_ts
                merged = True
                break

        if not merged:
            self.webp_stats.append(
                {
                    "width": width,
                    "height": height,
                    "frames": frames,
                    "init_size_mb": init_size_mb,
                    "quality": quality,
                    "method": method,
                    "result_size_mb": result_size_mb,
                    "encode_sec": encode_sec,
                    "timestamp": now_ts,
                    "count": 1,
                }
            )

        self.webp_stats = self._merge_duplicate_webp_stats(self.webp_stats)
        self._persist_webp_stats()

    def _persist_webp_stats(self):
        """Write webp_animated_stats back to JSON file."""
        try:
            data = {}
            if os.path.exists(self.stats_file):
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    content = json.load(f)
                    # If old list format (GIF stats only), migrate into dict schema.
                    if isinstance(content, list):
                        data = {"gif_stats": content}
                    else:
                        data = content
            data["webp_animated_stats"] = self.webp_stats
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"{VERSION} | Warning: failed to save webp_animated_stats: {e}")

    def select_startup_plan(self, width, height, frames, init_size_mb, target_min_mb, target_max_mb, gif_cfg):
        """Return startup strategy for animated WEBP, including whether direct-final is safe."""
        max_diff_ratio = 0.20
        target_mid_mb = (target_min_mb + target_max_mb) / 2.0
        exact_init_tolerance = max(0.05, float(gif_cfg.webp_animated_direct_final_init_tolerance_mb))
        candidates = []

        min_count = max(1, int(gif_cfg.webp_animated_startup_min_count))

        for entry in self.webp_stats:
            width_diff = abs(entry["width"] - width) / max(width, 1)
            height_diff = abs(entry["height"] - height) / max(height, 1)
            frame_diff = abs(entry["frames"] - frames) / max(frames, 1)
            entry_count = int(entry.get("count", 1) or 1)

            if width_diff > max_diff_ratio or height_diff > max_diff_ratio or frame_diff > max_diff_ratio:
                continue
            if entry_count < min_count:
                continue

            result_size = entry["result_size_mb"]
            if not (target_min_mb - 0.3 <= result_size <= target_max_mb + 0.3):
                continue

            init_diff = abs(entry["init_size_mb"] - init_size_mb)
            mid_diff = abs(result_size - target_mid_mb)
            exact_profile = (
                entry["width"] == width
                and entry["height"] == height
                and entry["frames"] == frames
            )
            direct_final = bool(
                gif_cfg.webp_animated_direct_final_enabled
                and exact_profile
                and init_diff <= exact_init_tolerance
            )
            candidates.append(
                {
                    "quality": entry["quality"],
                    "method": entry.get("method", gif_cfg.webp_animated_method_default),
                    "result_size_mb": result_size,
                    "count": entry_count,
                    "init_diff": init_diff,
                    "mid_diff": mid_diff,
                    "timestamp": entry.get("timestamp", 0),
                    "direct_final": direct_final,
                    "exact_profile": exact_profile,
                }
            )

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                0 if item["direct_final"] else 1,
                0 if item["exact_profile"] else 1,
                item["init_diff"],
                item["mid_diff"],
                -item["timestamp"],
            )
        )
        best = candidates[0]
        source_prefix = "webp exact stats" if best["exact_profile"] else "webp stats"
        source_suffix = "direct-final" if best["direct_final"] else "probe-guided"
        return {
            "quality": best["quality"],
            "method": best["method"],
            "result_size_mb": best.get("result_size_mb"),
            "count": best.get("count", 1),
            "direct_final": best["direct_final"],
            "source": f"{source_prefix} ({source_suffix}, records={self.stats_count()}, count={best.get('count', 1)})",
        }

    def predict_startup_quality(self, width, height, frames, init_size_mb, target_min_mb, target_max_mb, gif_cfg):
        """
        Find similar files and their successful quality values.
        Return quality q that landed in target range, or None to use default (95).
        """
        plan = self.select_startup_plan(
            width,
            height,
            frames,
            init_size_mb,
            target_min_mb,
            target_max_mb,
            gif_cfg,
        )
        return None if plan is None else plan["quality"]


class CompressorStatsManager:
    """
    Persists successful compression history in a JSON file and provides
    methods to predict the optimal scale for a new file:
      - average_scale_recent: recency-weighted mean (recent runs weighted higher)
      - neighbor_scale:        mean across similar files (close size/resolution/frames)
      - regression_coefficients: linear regression fast_size → med_size
      - predict_mediancut:    estimates post-MEDIANCUT size before running it
    Used for GIF compression to improve initial scale prediction and reduce trial/error.
    """
    def __init__(self, stats_file):
        self.stats_file = stats_file
        self.stats = []
        self._load_stats()

    def _load_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.stats = data.get("gif_stats", [])
                    else:
                        self.stats = data
            except Exception:
                self.stats = []
        else:
            self.stats = []

    def save_stats(self, palette, width, height, frames, fast_size, med_size, scale):
        entry = {
            "palette": palette,
            "width": width,
            "height": height,
            "frames": frames,
            "fast_size": fast_size,
            "med_size": med_size,
            "scale": scale,
            "timestamp": time.time(),
        }
        self.stats.append(entry)
        try:
            data = {}
            if os.path.exists(self.stats_file):
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    existing = json.load(f)
                    if isinstance(existing, dict):
                        data = existing
                    else:
                        data = {"gif_stats": existing}

            data["gif_stats"] = self.stats
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"{VERSION} | Warning: failed to save stats: {e}")

    def _filter_matches(self, palette, width, height, frames):
        return [
            entry
            for entry in self.stats
            if entry["palette"] == palette
            and entry["width"] == width
            and entry["height"] == height
            and entry["frames"] == frames
        ]

    def average_scale(self, palette, width, height, frames):
        matches = self._filter_matches(palette, width, height, frames)
        scales = [entry["scale"] for entry in matches if entry.get("scale", 0) > 0]
        return (sum(scales) / len(scales)) if scales else None

    def average_scale_recent(self, palette, width, height, frames, decay_half_life=86400.0):
        """Recency-weighted average scale (recent runs count more; half-life = 1 day by default)."""
        matches = self._filter_matches(palette, width, height, frames)
        now = time.time()
        weighted_sum = 0.0
        weight_total = 0.0
        for entry in matches:
            if entry.get("scale", 0) <= 0:
                continue
            age = now - entry.get("timestamp", now)
            weight = 2.0 ** (-age / decay_half_life)
            weighted_sum += entry["scale"] * weight
            weight_total += weight
        return (weighted_sum / weight_total) if weight_total > 0 else None

    def find_delta(self, palette, width, height, frames):
        matches = self._filter_matches(palette, width, height, frames)
        deltas = [entry["med_size"] - entry["fast_size"] for entry in matches]
        return (sum(deltas) / len(deltas)) if deltas else None

    def predict_mediancut(self, palette, width, height, frames, fast_size, bias_factor):
        coeff = self.regression_coefficients(palette, width, height, frames)
        if coeff is not None:
            a, b = coeff
            return a * fast_size + b

        delta_avg = self.find_delta(palette, width, height, frames)
        if delta_avg is not None:
            return fast_size + delta_avg * bias_factor

        return fast_size * bias_factor

    def regression_coefficients(self, palette, width, height, frames, max_diff_ratio=0.15):
        xs, ys = [], []
        for entry in self.stats:
            if abs(entry["palette"] - palette) > 8:
                continue
            if abs(entry["width"] - width) / width > max_diff_ratio:
                continue
            if abs(entry["height"] - height) / height > max_diff_ratio:
                continue
            if abs(entry["frames"] - frames) / frames > max_diff_ratio:
                continue
            xs.append(entry.get("fast_size", 0))
            ys.append(entry.get("med_size", 0))

        if len(xs) < 2:
            return None

        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        den = sum((x - x_mean) ** 2 for x in xs)
        if den == 0:
            return None

        a = num / den
        b = y_mean - a * x_mean
        return a, b

    def neighbor_scale(self, palette, width, height, frames, max_diff_ratio=0.15):
        profile = self.neighbor_scale_profile(palette, width, height, frames, max_diff_ratio=max_diff_ratio)
        return profile["scale"] if profile else None

    def neighbor_scale_profile(self, palette, width, height, frames, max_diff_ratio=0.15):
        candidates = []
        weights = []
        for entry in self.stats:
            if abs(entry["palette"] - palette) > 8:
                continue

            width_diff = abs(entry["width"] - width) / width
            height_diff = abs(entry["height"] - height) / height
            frame_diff = abs(entry["frames"] - frames) / frames
            palette_diff = abs(entry["palette"] - palette) / 32.0

            if width_diff <= max_diff_ratio and height_diff <= max_diff_ratio and frame_diff <= max_diff_ratio:
                if entry.get("scale", 0) > 0:
                    candidates.append(entry["scale"])
                    distance = width_diff + height_diff + frame_diff + palette_diff
                    weights.append(1.0 / (0.05 + distance))

        if not candidates:
            return None

        weight_sum = sum(weights)
        if weight_sum <= 0:
            mean_scale = sum(candidates) / len(candidates)
            variance = sum((s - mean_scale) ** 2 for s in candidates) / len(candidates)
        else:
            mean_scale = sum(s * w for s, w in zip(candidates, weights)) / weight_sum
            variance = sum(w * (s - mean_scale) ** 2 for s, w in zip(candidates, weights)) / weight_sum

        std_scale = variance ** 0.5
        return {
            "scale": mean_scale,
            "std": std_scale,
            "count": len(candidates),
        }


def process_frame_med_cut(args):
    frame, palette_colors = args
    q = frame.quantize(colors=palette_colors, method=Image.MEDIANCUT)
    q.info.pop("transparency", None)
    return q


def process_frame_fast_octree(frame, palette_colors):
    q = frame.quantize(colors=palette_colors, method=Image.FASTOCTREE)
    q.info.pop("transparency", None)
    return q


def save_gif(frames, durations, optimize=False):
    buf = io.BytesIO()
    frames[0].save(
        buf,
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=durations,
        disposal=2,
        optimize=optimize,
        format="GIF",
    )
    size_mb = len(buf.getvalue()) / (1024 * 1024)
    return buf, size_mb


def resize_frames(frames_raw, width, height, scale):
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))
    return [fr.resize((new_w, new_h), Image.LANCZOS) for fr in frames_raw]


def _process_pool_chunksize(frame_count, workers, gif_cfg):
    if frame_count <= 0:
        return 1

    tasks_per_worker = max(1, int(gif_cfg.process_pool_tasks_per_worker))
    return max(1, frame_count // max(1, workers * tasks_per_worker))


def _sample_probe_frame_limit(total_frames, gif_cfg):
    max_frames = max(2, int(gif_cfg.sample_probe_max_frames))
    min_frames = max(2, min(max_frames, int(gif_cfg.sample_probe_min_frames)))

    if total_frames <= min_frames:
        return total_frames

    adaptive_frames = int((total_frames ** 0.5) * 1.25)
    return max(min_frames, min(max_frames, adaptive_frames))


def temporal_reduce(frames, durations, keep_every):
    """
    Keep every N-th frame while preserving playback speed by accumulating durations.
    This reduces GIF size without changing frame dimensions.
    """
    if keep_every <= 1:
        return frames, durations

    reduced_frames = []
    reduced_durations = []

    bucket_duration = 0
    bucket_start_idx = None

    for idx, (frame, dur) in enumerate(zip(frames, durations)):
        if bucket_start_idx is None:
            bucket_start_idx = idx
            bucket_duration = 0

        bucket_duration += dur
        is_bucket_end = ((idx - bucket_start_idx + 1) >= keep_every)

        if is_bucket_end:
            reduced_frames.append(frames[bucket_start_idx])
            reduced_durations.append(max(20, bucket_duration))
            bucket_start_idx = None
            bucket_duration = 0

    if bucket_start_idx is not None:
        reduced_frames.append(frames[bucket_start_idx])
        reduced_durations.append(max(20, bucket_duration))

    return reduced_frames, reduced_durations


def compress_med_cut(frames, durations, palette_colors, executor, workers, gif_cfg, final=False):
    args = [(fr, palette_colors) for fr in frames]
    chunksize = _process_pool_chunksize(len(frames), workers, gif_cfg)
    frames_q = list(executor.map(process_frame_med_cut, args, chunksize=chunksize))
    return save_gif(frames_q, durations, optimize=final)


def _estimate_ratio_sample(frames, durations, palette_colors, executor, workers, gif_cfg):
    """
    Fast quality probe: estimate MEDIANCUT/FASTOCTREE size ratio on a frame sample.
    Used to avoid expensive full MEDIANCUT passes at obviously bad scales.
    """
    total = len(frames)
    if total < 2:
        return None

    sample_n = _sample_probe_frame_limit(total, gif_cfg)
    stride = max(1, total // sample_n)
    sample_frames = frames[::stride][:sample_n]
    sample_durations = durations[::stride][:sample_n]

    if len(sample_frames) < 2:
        return None

    sample_fast = [process_frame_fast_octree(fr, palette_colors) for fr in sample_frames]
    _, fast_size = save_gif(sample_fast, sample_durations, optimize=False)
    if fast_size <= 0:
        return None

    _, med_size = compress_med_cut(
        sample_frames,
        sample_durations,
        palette_colors,
        executor,
        workers,
        gif_cfg,
        final=False,
    )
    return med_size / fast_size


def _scale_key(scale):
    return round(scale, 4)


def _clamp_prediction(predicted_medcut, fast_size):
    min_pred = max(fast_size * 0.3, 0.1)
    max_pred = fast_size * 2.0
    return max(min(predicted_medcut, max_pred), min_pred)


def _run_fastoctree_trial(
    *,
    iteration,
    scale,
    frames_raw,
    width,
    height,
    palette_limit,
    durations,
    fast_cache,
    stage_tag="base",
):
    """
    Fast probe run using FASTOCTREE quantization.
    Result is cached by scale: a repeated call with the same scale is free.
    Used to estimate output size before launching the expensive MEDIANCUT pass.
    """
    _resize_start = time.time()
    resized_frames = resize_frames(frames_raw, width, height, scale)
    print(f"{VERSION} | [gif.diag] resize scale={scale:.3f} elapsed={time.time() - _resize_start:.2f}s")
    key = _scale_key(scale)

    if key in fast_cache:
        fast_size = fast_cache[key]["size"]
        print(f"{VERSION} | [gif.fast] Step {iteration+1}.0 ({stage_tag}, cached) | FASTOCTREE={fast_size:.2f} MB")
        return resized_frames, fast_size, fast_cache[key].get("bytes")

    step_start = time.time()
    frames_fast = [process_frame_fast_octree(fr, palette_limit) for fr in resized_frames]
    buf_fast, fast_size = save_gif(frames_fast, durations, optimize=False)
    fast_cache[key] = {"size": fast_size, "bytes": buf_fast.getvalue()}
    step_elapsed = time.time() - step_start
    print(f"{VERSION} | [gif.fast] Step {iteration+1}.0 ({stage_tag}) | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
    return resized_frames, fast_size, fast_cache[key]["bytes"]


def _choose_initial_scale(stats_mgr, palette_limit, width, height, total_frames, init_size, target_mid, bias_factor, gif_cfg):
    """
    Selects the initial scale from the best available source (priority by descending accuracy):
      1. stats         — recency-weighted mean from history for this exact file
      2. neighbor stats — mean from similar files
      3. delta_avg     — scale derived from average fast→med size delta
      4. formula       — approximate scale from file size ratio
    This function helps avoid unnecessary MEDIANCUT runs by starting with a good guess.
    """
    avg_scale = stats_mgr.average_scale_recent(palette_limit, width, height, total_frames)
    delta_avg = stats_mgr.find_delta(palette_limit, width, height, total_frames)
    neighbor_profile = stats_mgr.neighbor_scale_profile(palette_limit, width, height, total_frames)

    if avg_scale:
        return avg_scale, "stats"
    if neighbor_profile:
        # Neighbor-derived scale can be optimistic for unseen GIFs.
        # Confidence-aware safety: use less shrink for dense/low-variance neighborhood.
        neighbor_scale = neighbor_profile["scale"]
        neighbor_std = neighbor_profile["std"]
        neighbor_count = neighbor_profile["count"]

        is_confident_neighbor = (
            neighbor_count >= gif_cfg.neighbor_scale_confident_min_count
            and neighbor_std <= gif_cfg.neighbor_scale_confident_max_std
        )
        safety = (
            gif_cfg.neighbor_scale_safety_confident
            if is_confident_neighbor
            else gif_cfg.neighbor_scale_safety
        )

        safe_neighbor_scale = neighbor_scale * safety

        # Size-ratio floor: prevent over-compressing files already near target.
        # Neighbor stats are calibrated for their own init_size; if our file is close
        # to target but neighbors were large, their scale is too aggressive for us.
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
    """
    Computes the next scale: binary search with step size capped at max_step_ratio.
    When two points exist in med_cache, applies the secant method for faster convergence.
    Used in GIF compression loop to efficiently converge on the correct scale.
    """
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


def _print_gif_result_header(input_path, total_frames, palette_count, width, height):
    print(
        f"{VERSION} | [gif.result] file: {os.path.basename(input_path)} "
        f"| Frames={total_frames} | Palette={palette_count} | WxH={width}x{height}"
    )


def balanced_compress_gif(input_path, gif_cfg=CONFIG.gif):
    """
    Core GIF compression algorithm. Iteratively finds the right scale factor:
      1. FASTOCTREE probe (cheap, ~1 sec)  → size estimate
      2. MEDIANCUT       (quality, ~8-15 sec) → exact size after quantization
      3. If result is within the target range — save and return
      4. Otherwise — narrow [low_scale, high_scale] and continue
    Loop protection: one-shot micro_adjust + stall-guard on (scale, med_size) signature.
    """
    started_at = time.time()
    print(f"{VERSION} | [gif.prepare] Read and decode frames")

    frames_raw, durations = [], []
    with Image.open(input_path) as img:
        width, height = img.size
        total_frames = img.n_frames
        colors_first = len(img.getcolors(maxcolors=256 * 256) or [])
        palette_limit = min(colors_first + gif_cfg.extra_palette, 256)

        print(f"{VERSION} | [gif.prepare] Starting file: {input_path}")
        init_size = os.path.getsize(input_path) / (1024 * 1024)
        print(f"{VERSION} | [gif.prepare] Initial Size: {init_size:.2f} MB | Frames={total_frames} | Palette={colors_first} | WxH={width}x{height}")

        _decode_start = time.time()
        for frame in ImageSequence.Iterator(img):
            frames_raw.append(frame.convert("RGB"))
            durations.append(frame.info.get("duration", 100))
        print(f"{VERSION} | [gif.diag] decode={time.time() - _decode_start:.2f}s ({total_frames} frames)")

    # ⚠️ DO NOT CHANGE workers — MEDIANCUT is memory-intensive per frame;
    # using more than half CPUs causes RAM pressure and slows down the whole system.
    # DO NOT add dynamic scaling, boost, or any frame-count-based adjustments here.
    workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"{VERSION} | [gif.prepare] Using {workers} workers for {total_frames} frames")
    debug_log(f"log_level={LOG_LEVEL} | max_safe_iterations={gif_cfg.max_safe_iterations}")

    target_mid = (gif_cfg.target_min_mb + gif_cfg.target_max_mb) / 2
    bias_factor = 1.1 + 0.05 * (palette_limit / 256.0)

    stats_mgr = CompressorStatsManager(STATS_FILE)
    scale, source = _choose_initial_scale(
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

    print(f"{VERSION} | [gif.predict] Prediction source: {source}")
    print(f"{VERSION} | [gif.predict] -> initial scale={scale:.3f}")

    state = GifRuntimeState(
        scale=scale,
        low_scale=0.01,
        high_scale=4.0,
        fast_cache={},
        med_cache={},
    )
    small_res_high_frames = (
        (width * height) <= gif_cfg.temporal_max_pixels
        and total_frames >= gif_cfg.temporal_min_frames
    )

    _pool_start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        print(f"{VERSION} | [gif.diag] pool_startup={time.time() - _pool_start:.2f}s")
        for iteration in range(gif_cfg.max_safe_iterations):
            print(f"{VERSION} | [gif.fast] Iteration {iteration+1}: FASTOCTREE trial")
            resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                iteration=iteration,
                scale=state.scale,
                frames_raw=frames_raw,
                width=width,
                height=height,
                palette_limit=palette_limit,
                durations=durations,
                fast_cache=state.fast_cache,
                stage_tag="base",
            )

            fast_in_preferred = is_in_preferred_range(fast_size, gif_cfg)
            # ⚠️ IMMUTABLE: fast_in_target MUST check against sacred bounds [13.5, 14.99] MB.
            # Never expand, contract, or relax this range. Period.
            fast_in_target = gif_cfg.target_min_mb <= fast_size <= gif_cfg.target_max_mb

            can_fast_direct_accept = (
                gif_cfg.fast_direct_accept_enabled
                and iteration == 0
                and fast_in_target
                and total_frames >= gif_cfg.fast_direct_min_frames
            )
            if can_fast_direct_accept:
                fast_saved_size = len(fast_bytes) / (1024 * 1024)
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_saved_size, state.scale)
                with open(input_path, "wb") as f:
                    f.write(fast_bytes)
                elapsed = time.time() - started_at
                _print_gif_result_header(input_path, total_frames, colors_first, width, height)
                print(
                    f"{VERSION} | ✅ Success (fast-direct): {init_size:.2f} MB -> {fast_saved_size:.2f} MB "
                    f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                )
                return

            if iteration >= 1 and fast_in_preferred:
                # Persist exactly the quantized FASTOCTREE result, not raw resized RGB frames.
                fast_saved_size = len(fast_bytes) / (1024 * 1024)
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_size, state.scale)
                with open(input_path, "wb") as f:
                    f.write(fast_bytes)
                elapsed = time.time() - started_at
                _print_gif_result_header(input_path, total_frames, colors_first, width, height)
                print(
                    f"{VERSION} | ✅ Success (fast): {init_size:.2f} MB -> {fast_saved_size:.2f} MB "
                    f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                )
                return

            predicted_medcut = predict_medcut_size(
                stats_mgr=stats_mgr,
                palette_limit=palette_limit,
                width=width,
                height=height,
                total_frames=total_frames,
                fast_size=fast_size,
                bias_factor=bias_factor,
                source=source,
                gif_cfg=gif_cfg,
                clamp_prediction_fn=_clamp_prediction,
            )

            # Hard early skip: if first FASTOCTREE is far above target,
            # skip sample-probe and jump down immediately.
            _is_neighbor_source = source.startswith("neighbor stats")
            if (
                iteration == 0
                and (source == "formula (conservative)" or _is_neighbor_source)
                and fast_size > gif_cfg.target_max_mb * gif_cfg.fast_probe_hard_skip_ratio
            ):
                state.high_scale = state.scale
                if _is_neighbor_source:
                    # Use stored delta to aim for the FASTOCTREE level that will yield MEDIANCUT in target.
                    # This is far more accurate than the blind (target_mid/fast_size)*0.92 formula.
                    _delta_for_skip = stats_mgr.find_delta(palette_limit, width, height, total_frames)
                    if _delta_for_skip is not None:
                        _target_fast = target_mid - _delta_for_skip * bias_factor
                        if _target_fast > 0 and fast_size > 0:
                            suggested_scale = state.scale * (_target_fast / fast_size) ** 0.5
                        else:
                            suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 * 0.92
                    else:
                        suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 * 0.92 if fast_size > 0 else state.scale
                else:
                    suggested_scale = state.scale * (target_mid / fast_size) ** 0.5 if fast_size > 0 else state.scale
                    suggested_scale *= 0.92
                max_skip_step_ratio = 0.55
                max_skip_step = state.scale * max_skip_step_ratio
                if abs(suggested_scale - state.scale) > max_skip_step:
                    direction = 1 if suggested_scale > state.scale else -1
                    suggested_scale = state.scale + direction * max_skip_step
                if not (state.low_scale < suggested_scale < state.high_scale):
                    suggested_scale = (state.low_scale + state.high_scale) / 2
                print(
                    f"{VERSION} | [gif.skip] Early hard-skip on iter 1: FASTOCTREE={fast_size:.2f} MB "
                    f"(>{gif_cfg.fast_probe_hard_skip_ratio:.2f}x target_max)"
                )
                print(f"{VERSION} | [gif.skip] -> next scale={suggested_scale:.3f}")
                state.scale = suggested_scale
                continue

            source_is_neighbor = source.startswith("neighbor stats")
            should_probe_formula = source == "formula (conservative)"
            should_probe_neighbor = (
                source_is_neighbor
                and colors_first >= gif_cfg.sample_probe_neighbor_min_palette
                and total_frames >= gif_cfg.sample_probe_neighbor_min_frames
            )
            sample_probe_measured_this_iter = False

            if (
                gif_cfg.sample_probe_enabled
                and not state.sample_probe_done
                and iteration <= 1
                and (should_probe_formula or should_probe_neighbor)
                and total_frames >= 120
            ):
                probe_start = time.time()
                state.sample_ratio = _estimate_ratio_sample(
                    resized_frames,
                    durations,
                    palette_limit,
                    executor,
                    workers,
                    gif_cfg,
                )
                sample_probe_measured_this_iter = True
                state.sample_probe_done = True
                probe_elapsed = time.time() - probe_start
                if state.sample_ratio and state.sample_ratio > 1.0:
                    calibrated_prediction = fast_size * state.sample_ratio
                    if calibrated_prediction > predicted_medcut:
                        predicted_medcut = calibrated_prediction
                    print(
                        f"{VERSION} | [gif.predict] Probe ratio (sample)={state.sample_ratio:.3f} "
                        f"-> calibrated MEDIANCUT={predicted_medcut:.2f} MB "
                        f"| finished in {probe_elapsed:.2f} sec"
                    )

            # Reuse measured sample ratio on subsequent iteration(s) before first MEDIANCUT hit.
            # This helps avoid expensive overshoot when stats under-estimate MEDIANCUT for dense GIFs.
            if state.sample_ratio and state.sample_ratio > 1.0:
                calibrated_prediction = fast_size * state.sample_ratio
                if calibrated_prediction > predicted_medcut:
                    predicted_medcut = calibrated_prediction
                    print(
                        f"{VERSION} | [gif.predict] Probe carry-over ratio={state.sample_ratio:.3f} "
                        f"-> adjusted MEDIANCUT={predicted_medcut:.2f} MB"
                    )

            print(f"{VERSION} | [gif.predict] -> Predicted MEDIANCUT={predicted_medcut:.2f} MB | scale={state.scale:.3f}")
            print(f"{VERSION} | [gif.predict] -> source: {source}")

            skip_decision = build_skip_decision(
                iteration=iteration,
                source=source,
                source_is_neighbor=source_is_neighbor,
                should_probe_formula=should_probe_formula,
                should_probe_neighbor=should_probe_neighbor,
                sample_ratio=state.sample_ratio,
                sample_probe_measured_this_iter=sample_probe_measured_this_iter,
                predicted_medcut=predicted_medcut,
                fast_size=fast_size,
                current_scale=state.scale,
                low_scale=state.low_scale,
                high_scale=state.high_scale,
                target_mid=target_mid,
                formula_extra_skip_used=state.formula_extra_skip_used,
                gif_cfg=gif_cfg,
            )
            if skip_decision.should_skip:
                print(f"{VERSION} | [gif.skip] Skip decision accepted")
                debug_log("decision=skip_first_med | reason=formula prediction well above target")
                state.low_scale = skip_decision.next_low_scale
                state.high_scale = skip_decision.next_high_scale
                if skip_decision.mark_formula_extra_skip_used:
                    state.formula_extra_skip_used = True
                print(f"{VERSION} | [gif.skip] Skipping MEDIANCUT on iter {iteration+1} ({skip_decision.reason})")
                print(f"{VERSION} | [gif.skip] -> next scale={skip_decision.suggested_scale:.3f}")
                state.scale = skip_decision.suggested_scale
                continue

            can_pre_correct = (
                iteration == 0
                and source in {"delta_avg (conservative)"}
                and fast_size < target_mid * 0.80
                and predicted_medcut < gif_cfg.target_min_mb * 0.92
            )
            if can_pre_correct:
                debug_log("decision=pre_correction | reason=iter0/formula_or_delta and prediction well below target")
                state.scale *= 0.92
                print(f"{VERSION} | [gif.adjust] Pre-correction (iter 0) -> scale={state.scale:.3f}")
                resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                    iteration=iteration,
                    scale=state.scale,
                    frames_raw=frames_raw,
                    width=width,
                    height=height,
                    palette_limit=palette_limit,
                    durations=durations,
                    fast_cache=state.fast_cache,
                    stage_tag="corrected",
                )

            can_soft_preshrink_formula = (
                iteration == 0
                and source == "formula (conservative)"
                and predicted_medcut > gif_cfg.target_max_mb * 0.985
                and predicted_medcut <= gif_cfg.target_max_mb * 1.20
                and fast_size > gif_cfg.target_max_mb * 0.80
            )
            if can_soft_preshrink_formula:
                # Cheap pre-adjustment to avoid a borderline overshoot (e.g. 15.00 MB) on cold starts.
                suggested_scale = state.scale * (target_mid / predicted_medcut) ** 0.5 if predicted_medcut > 0 else state.scale
                suggested_scale *= 0.99

                max_soft_step_ratio = 0.12
                max_soft_step = state.scale * max_soft_step_ratio
                if abs(suggested_scale - state.scale) > max_soft_step:
                    direction = 1 if suggested_scale > state.scale else -1
                    suggested_scale = state.scale + direction * max_soft_step

                if state.low_scale < suggested_scale < state.high_scale and abs(suggested_scale - state.scale) > 0.005:
                    debug_log("decision=soft_pre_shrink | reason=formula near upper target bound")
                    state.scale = suggested_scale
                    print(f"{VERSION} | [gif.adjust] Soft pre-shrink (iter 0) -> scale={state.scale:.3f}")
                    resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                        iteration=iteration,
                        scale=state.scale,
                        frames_raw=frames_raw,
                        width=width,
                        height=height,
                        palette_limit=palette_limit,
                        durations=durations,
                        fast_cache=state.fast_cache,
                        stage_tag="soft-corrected",
                    )
                    predicted_medcut = predict_medcut_size(
                        stats_mgr=stats_mgr,
                        palette_limit=palette_limit,
                        width=width,
                        height=height,
                        total_frames=total_frames,
                        fast_size=fast_size,
                        bias_factor=bias_factor,
                        source=source,
                        gif_cfg=gif_cfg,
                        clamp_prediction_fn=_clamp_prediction,
                    )
                    print(
                        f"{VERSION} | -> Updated predicted MEDIANCUT={predicted_medcut:.2f} MB "
                        f"| scale={state.scale:.3f}"
                    )

            can_micro_adjust = (
                source_is_neighbor
                and predicted_medcut < gif_cfg.target_min_mb
                and fast_size < target_mid * 0.9
                and not state.micro_adjust_used
                and iteration <= 1
                and total_frames >= 80
                and state.high_scale >= 3.9
                and state.stall_count < 1
            )
            if can_micro_adjust:
                adj_scale = state.scale * (target_mid / (fast_size + 4.0)) ** 0.5 if source_is_neighbor else state.scale
                if abs(adj_scale - state.scale) > 0.01:
                    # Allow a meaningful correction, but cap the jump to keep stability.
                    max_micro_step_ratio = min(0.30, gif_cfg.max_scale_step_ratio * 2.0)
                    max_micro_step = state.scale * max_micro_step_ratio
                    if abs(adj_scale - state.scale) > max_micro_step:
                        direction = 1 if adj_scale > state.scale else -1
                        adj_scale = state.scale + direction * max_micro_step

                    debug_log("decision=micro_adjust | reason=neighbor_stats and fast below 0.9*target_mid")
                    state.scale = adj_scale
                    state.micro_adjust_used = True
                    print(f"{VERSION} | [gif.adjust] Micro-adjusting scale -> {state.scale:.3f}")
                    resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                        iteration=iteration,
                        scale=state.scale,
                        frames_raw=frames_raw,
                        width=width,
                        height=height,
                        palette_limit=palette_limit,
                        durations=durations,
                        fast_cache=state.fast_cache,
                        stage_tag="adjusted",
                    )

            scale_key = _scale_key(state.scale)
            if scale_key in state.med_cache:
                print(f"{VERSION} | [gif.medcut] Use cached MEDIANCUT result")
                med_size, med_bytes = state.med_cache[scale_key]
                print(f"{VERSION} | [gif.medcut] Step {iteration+1}.1 (cached) | MEDIANCUT={med_size:.2f} MB")
                debug_log(f"cache=med | hit | key={scale_key}")
            else:
                print(f"{VERSION} | [gif.medcut] Execute MEDIANCUT")
                step_start = time.time()
                buf_med, med_size = compress_med_cut(
                    resized_frames,
                    durations,
                    palette_limit,
                    executor,
                    workers,
                    gif_cfg,
                    final=False,
                )
                med_bytes = buf_med.getvalue()
                state.med_cache[scale_key] = (med_size, med_bytes)
                step_elapsed = time.time() - step_start
                print(f"{VERSION} | [gif.medcut] Step {iteration+1}.1 | MEDIANCUT={med_size:.2f} MB | finished in {step_elapsed:.2f} sec")
                debug_log(f"cache=med | miss | key={scale_key}")

            pred_error = med_size - predicted_medcut
            pred_error_pct = (pred_error / predicted_medcut * 100.0) if predicted_medcut > 0 else 0.0
            debug_log(f"prediction_error={pred_error:+.2f} MB ({pred_error_pct:+.2f}%)")

            signature = (_scale_key(state.scale), round(med_size, 2))
            if signature == state.last_signature:
                state.stall_count += 1
            else:
                state.stall_count = 0
                state.last_signature = signature
            if state.stall_count >= 2:
                debug_log("stall_guard=active | repeated (scale, med_size) signature")

            print(f"{VERSION} | [gif.compare] Delta vs FASTOCTREE = {med_size - fast_size:+.2f} MB")

            can_try_temporal_preserve = (
                gif_cfg.temporal_preserve_enabled
                and not state.temporal_applied
                and iteration == 0
                and med_size > gif_cfg.target_max_mb
                and total_frames >= gif_cfg.temporal_min_frames
                and (width * height) <= gif_cfg.temporal_max_pixels
                and state.scale < 0.85
            )
            if can_try_temporal_preserve:
                target_ratio = med_size / target_mid if target_mid > 0 else 1.0
                keep_every = max(2, min(gif_cfg.temporal_max_keep_every, int(round(target_ratio))))
                t_frames, t_durations = temporal_reduce(frames_raw, durations, keep_every)

                if len(t_frames) < len(frames_raw):
                    t_start = time.time()
                    t_resized = resize_frames(t_frames, width, height, 1.0)
                    t_buf, t_med_size = compress_med_cut(
                        t_resized,
                        t_durations,
                        palette_limit,
                        executor,
                        workers,
                        gif_cfg,
                        final=False,
                    )
                    t_elapsed = time.time() - t_start
                    print(
                        f"{VERSION} | [gif.temporal] Temporal preserve probe | keep_every={keep_every} "
                        f"| frames {len(frames_raw)}->{len(t_frames)} | MEDIANCUT={t_med_size:.2f} MB "
                        f"| finished in {t_elapsed:.2f} sec"
                    )

                    if is_in_target_range(t_med_size, gif_cfg):
                        # ⚠️ Result falls within sacred bounds. Safe to accept.
                        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, t_med_size, 1.0)
                        with open(input_path, "wb") as f:
                            f.write(t_buf.getvalue())
                        elapsed = time.time() - started_at
                        _print_gif_result_header(input_path, total_frames, colors_first, width, height)
                        print(
                            f"{VERSION} | ✅ Success (temporal-preserve): {init_size:.2f} MB -> {t_med_size:.2f} MB "
                            f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                        )
                        return

                    if t_med_size < med_size:
                        # Rebase the search on temporally reduced frames to preserve dimensions.
                        frames_raw = t_frames
                        durations = t_durations
                        total_frames = len(frames_raw)
                        state.fast_cache.clear()
                        state.med_cache.clear()
                        state.low_scale = 0.01
                        state.high_scale = min(state.high_scale, 1.0)
                        state.scale = min(1.0, state.scale / 0.92)
                        state.temporal_applied = True
                        print(
                            f"{VERSION} | [gif.temporal] Temporal preserve enabled -> continue with original WxH and "
                            f"{total_frames} frames"
                        )
                        continue

            in_preferred_corridor = (
                iteration >= 1
                and is_in_preferred_range(med_size, gif_cfg)
            )
            # ⚠️ IMMUTABLE TARGET RANGE: [13.5 MB, 14.99 MB]
            # This is the contract. Results MUST fit here. Never relax, widen, or negotiate.
            in_target = is_in_target_range(med_size, gif_cfg)

            can_try_quality_retry = (
                gif_cfg.quality_retry_small_res_enabled
                and not state.quality_retry_done
                and not state.temporal_applied
                and iteration == 0
                and in_target
                and small_res_high_frames
                and state.scale < gif_cfg.quality_retry_min_scale
            )
            if can_try_quality_retry:
                state.quality_retry_done = True
                target_ratio = med_size / target_mid if target_mid > 0 else 1.0
                keep_every = max(2, min(gif_cfg.temporal_max_keep_every, int(round(target_ratio))))
                q_frames, q_durations = temporal_reduce(frames_raw, durations, keep_every)

                if len(q_frames) < len(frames_raw):
                    q_start = time.time()
                    q_resized = resize_frames(q_frames, width, height, 1.0)
                    q_buf, q_med_size = compress_med_cut(
                        q_resized,
                        q_durations,
                        palette_limit,
                        executor,
                        workers,
                        gif_cfg,
                        final=False,
                    )
                    q_elapsed = time.time() - q_start
                    print(
                        f"{VERSION} | [gif.temporal] Quality retry (temporal) | keep_every={keep_every} "
                        f"| frames {len(frames_raw)}->{len(q_frames)} | MEDIANCUT={q_med_size:.2f} MB "
                        f"| finished in {q_elapsed:.2f} sec"
                    )

                    # ⚠️ SACRED BOUNDARIES: Result must be within [13.5, 14.99] MB. Never relax.
                    if is_in_target_range(q_med_size, gif_cfg):
                        stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, q_med_size, 1.0)
                        with open(input_path, "wb") as f:
                            f.write(q_buf.getvalue())
                        elapsed = time.time() - started_at
                        _print_gif_result_header(input_path, len(q_frames), colors_first, width, height)
                        print(
                            f"{VERSION} | ✅ Success (quality-preserve temporal): {init_size:.2f} MB -> {q_med_size:.2f} MB "
                            f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                        )
                        return

            # ⚠️ FINAL CHECK: The only acceptable outcome is strictly within [13.5–14.99] MB.
            # in_target enforces this IMMUTABLE contract. If size is out of bounds, loop continues.
            if in_preferred_corridor or in_target:
                print(f"{VERSION} | [gif.finalize] Save final result and stats")
                _save_start = time.time()
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, med_size, state.scale)
                with open(input_path, "wb") as f:
                    f.write(med_bytes)
                print(f"{VERSION} | [gif.diag] save+stats={time.time() - _save_start:.2f}s")
                elapsed = time.time() - started_at
                _print_gif_result_header(input_path, total_frames, colors_first, width, height)
                print(
                    f"{VERSION} | ✅ Success: {init_size:.2f} MB -> {med_size:.2f} MB "
                    f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                )
                return

            if med_size > gif_cfg.target_max_mb:
                state.high_scale = state.scale
            else:
                state.low_scale = state.scale

            # Use measured MEDIANCUT size to jump closer to target midpoint.
            adaptive_scale = state.scale
            if med_size > 0:
                adaptive_scale = state.scale * (target_mid / med_size) ** 0.5

            max_adaptive_step_ratio = min(0.35, gif_cfg.max_scale_step_ratio * 2.5)
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
                med_cache=state.med_cache,
                target_mid=target_mid,
                max_step_ratio=gif_cfg.max_scale_step_ratio,
                )
            print(f"{VERSION} | [gif.next-scale] Compute next scale")
            print(f"{VERSION} | [gif.next-scale] Next scale={new_scale:.3f}")
            print(f"{VERSION} | [gif.next-scale] -> bracket: low={state.low_scale:.3f}, high={state.high_scale:.3f}")
            state.scale = new_scale

    print(f"{VERSION} | [gif.fail] Failed to converge after {gif_cfg.max_safe_iterations} iterations")


def process_gifs(gif_paths, animated_webp_paths):
    """GIF block: process queued oversized GIFs and oversized animated WEBPs."""
    worked = False
    for file_path in gif_paths:
        worked = True
        try:
            balanced_compress_gif(file_path)
        except Exception as e:
            print(f"{VERSION} | [gif.error] Error processing {file_path}: {e}")

    for file_path in animated_webp_paths:
        worked = True
        try:
            compress_animated_webp_until_under_target(file_path)
        except Exception as e:
            print(f"{VERSION} | [gif.error] Error processing {file_path}: {e}")

    return worked


if __name__ == "__main__":
    from pipeline_runner import PipelineApi, run_pipeline

    print(
        "Compressor 2.0.1 | Formats: PNG/JPG/JPEG/JFIF/static WEBP -> <= 999 KB; "
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

