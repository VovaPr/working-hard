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
    fast_direct_accept_enabled: bool = True
    fast_direct_min_frames: int = 120
    probe_skip_overflow_margin: float = 1.08
    probe_skip_underflow_margin_mb: float = 0.10
    process_pool_tasks_per_worker: int = 4
    fast_probe_hard_skip_ratio: float = 1.30
    stats_source_bias_extra: float = 1.08  # Extra conservative bias when predicting from stats source
    webp_animated_max_iterations: int = 12
    webp_static_max_iterations: int = 12
    webp_static_method_default: int = 4
    webp_animated_method_default: int = 2
    webp_animated_method_fast: int = 0
    webp_animated_direct_final_fast_enabled: bool = True
    webp_animated_direct_final_fast_method: int = 1
    # Use fast direct-final method only if known method=2 result has enough headroom.
    # If fast method is likely to inflate size beyond target, skip it and use method=2 directly.
    webp_animated_direct_final_fast_max_growth: float = 1.10
    webp_animated_probe_enabled: bool = True
    webp_animated_direct_final_enabled: bool = True
    webp_animated_direct_final_init_tolerance_mb: float = 0.35
    webp_animated_probe_verify_margin_ratio: float = 0.06
    webp_animated_probe_verify_margin_growth_per_step: float = 0.03
    webp_animated_probe_recalibrate_every: int = 3
    # Initial estimate of how much smaller method=2 output is vs method=0 (fast probe).
    # method=2 typically produces ~20-30% smaller files than method=0.
    # Seeding with a realistic value prevents over-aggressive quality drops on the first steps.
    webp_animated_probe_initial_method_ratio: float = 0.75
    webp_animated_slow_step_sec: float = 20.0
    # Animated WEBP often needs 3-4 expensive passes (probe + verify).
    # 90s is too tight for high-frame clips and causes premature aborts.
    webp_file_max_seconds: float = 150.0
    webp_animated_near_band_ratio: float = 0.10
    webp_animated_nudge_small_ratio: float = 0.04
    webp_animated_nudge_small_step: int = 1
    webp_animated_nudge_large_step: int = 2
    # Before method-ratio is calibrated, cap quality jumps to avoid aggressive overshoot.
    webp_animated_uncalibrated_max_quality_step: int = 6
    # As step count grows, allow larger jumps so we do not waste time on obviously low estimates.
    webp_animated_uncalibrated_max_quality_step_growth: int = 2
    # Ignore weak WEBP startup stats until the profile has been confirmed multiple times.
    webp_animated_startup_min_count: int = 2


@dataclass(frozen=True)
class AppConfig:
    version: str = "Compressor v8.59.27"
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


def _is_animated_webp_fast(path):
    """Cheap WEBP container check to avoid opening animated WEBP via Pillow in the static pass."""
    try:
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
                return False

            for _ in range(8):
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    return False

                chunk_type = chunk_header[:4]
                chunk_size = int.from_bytes(chunk_header[4:8], "little")

                if chunk_type == b"VP8X":
                    flags = f.read(1)
                    if len(flags) < 1:
                        return False
                    return bool(flags[0] & 0x02)

                if chunk_type == b"ANIM":
                    return True

                skip = chunk_size + (chunk_size % 2)
                f.seek(skip, os.SEEK_CUR)
    except Exception:
        return False

    return False


def scan_media_candidates(root_folder_path):
    """Single filesystem pass that classifies files for later processing."""
    png_paths = []
    jpg_paths = []
    static_webp_paths = []
    gif_paths = []
    animated_webp_paths = []
    started_at = time.time()

    # Standard filesystem traversal (os.walk)
    files = []
    for dirpath, dirnames, filenames in os.walk(root_folder_path):
        for filename in filenames:
            files.append(os.path.join(dirpath, filename))

    for file_path in files:
        lower = file_path.lower()
        if lower.endswith('.gif'):
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > CONFIG.gif.min_process_size_mb:
                gif_paths.append(file_path)
            continue
        if lower.endswith('.png'):
            png_paths.append(file_path)
            continue
        if lower.endswith(('.jpg', '.jpeg')):
            if os.path.getsize(file_path) > TARGET_SIZE:
                jpg_paths.append(file_path)
            continue
        if not lower.endswith('.webp'):
            continue
        size_bytes = os.path.getsize(file_path)
        if size_bytes <= TARGET_SIZE:
            continue
        if _is_animated_webp_fast(file_path):
            if (size_bytes / (1024 * 1024)) > CONFIG.gif.min_process_size_mb:
                animated_webp_paths.append(file_path)
            continue
        static_webp_paths.append(file_path)

    RUN_METRICS["scan_sec"] = time.time() - started_at
    RUN_METRICS["png_candidates"] = len(png_paths)
    RUN_METRICS["jpg_candidates"] = len(jpg_paths)
    RUN_METRICS["static_webp_candidates"] = len(static_webp_paths)
    RUN_METRICS["gif_candidates"] = len(gif_paths)
    RUN_METRICS["animated_webp_candidates"] = len(animated_webp_paths)
    return png_paths, jpg_paths, static_webp_paths, gif_paths, animated_webp_paths


def process_images(png_paths, jpg_paths, static_webp_paths):
    """Image block: convert PNG to JPG, compress oversized JPG/JPEG, and compress static WEBP."""
    # Main entry point for processing all static images (PNG, JPG, static WEBP)
    # Handles conversion, compression, and error reporting for each file type
    worked = False

    for png_path in png_paths:
        worked = True
        jpg_path = os.path.join(os.path.dirname(png_path), os.path.splitext(os.path.basename(png_path))[0] + ".jpg")

        try:
            with Image.open(png_path) as img:
                png_size = os.path.getsize(png_path)
                print(f"{VERSION} | Initial PNG: {png_path}")
                print(f"{VERSION} | WxH={img.width}x{img.height} | Size={png_size/1024:.2f} KB")

                # Preserve orientation and metadata while converting PNG alpha to an RGB background.
                prepared = ImageOps.exif_transpose(img)
                icc_profile = img.info.get("icc_profile")
                exif = img.getexif()
                exif_bytes = None
                if exif:
                    exif[274] = 1
                    exif_bytes = exif.tobytes()

                has_alpha = (
                    "A" in prepared.getbands()
                    or (prepared.mode == "P" and "transparency" in prepared.info)
                )
                if has_alpha:
                    rgba = prepared.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.getchannel("A"))
                    jpg_image = bg
                else:
                    jpg_image = prepared.convert("RGB")

                save_kwargs = {
                    "quality": 100,
                    "optimize": True,
                    "progressive": True,
                    "subsampling": 0,
                }
                if icc_profile:
                    save_kwargs["icc_profile"] = icc_profile
                if exif_bytes:
                    save_kwargs["exif"] = exif_bytes

                jpg_image.save(jpg_path, "JPEG", **save_kwargs)

            jpg_size = os.path.getsize(jpg_path)
            print(f"{VERSION} | Converted PNG -> JPG: {jpg_path}")
            print(f"{VERSION} | Converted size={jpg_size/1024:.2f} KB | Target={TARGET_SIZE/1024:.0f} KB")

            os.remove(png_path)

            if jpg_size <= TARGET_SIZE:
                print(
                    f"{VERSION} | ✅ PNG success: {png_size/1024:.2f} KB -> {jpg_size/1024:.2f} KB "
                    "(no further compression needed)"
                )
                continue

            compress_until_under_target(jpg_path)
        except UnidentifiedImageError:
            print(f"{VERSION} | Skipped corrupted PNG: {png_path}")
        except Exception as e:
            print(f"{VERSION} | Error processing PNG {png_path}: {e}")

    for jpg_path in jpg_paths:
        worked = True
        try:
            compress_until_under_target(jpg_path)
        except Exception as e:
            print(f"{VERSION} | Error processing JPG {jpg_path}: {e}")

    for webp_path in static_webp_paths:
        worked = True
        try:
            compress_static_webp_until_under_target(webp_path)
        except Exception as e:
            print(f"{VERSION} | Error processing WEBP {webp_path}: {e}")

    return worked


def compress_until_under_target(path, target_size=TARGET_SIZE):
    """
    Compress a JPEG file to fit under the target size by first reducing quality,
    then resizing if necessary. The process minimizes quality loss while ensuring
    the output file does not exceed the specified size.
    """
    local_version = VERSION
    started_at = time.time()
    min_quality_before_resize = 80

    def _encode_jpeg_buffer(image, quality):
        buf = io.BytesIO()
        image.save(
            buf,
            "JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=0,
        )
        return buf

    def _find_best_quality_buffer(image, size_limit, q_min, q_max):
        """Return the highest quality in [q_min, q_max] that fits size_limit."""
        low = q_min
        high = q_max
        best_quality = None
        best_buf = None
        best_size = None

        while low <= high:
            mid = (low + high) // 2
            mid_buf = _encode_jpeg_buffer(image, mid)
            mid_size = len(mid_buf.getvalue())
            if mid_size <= size_limit:
                best_quality = mid
                best_buf = mid_buf
                best_size = mid_size
                low = mid + 1
            else:
                high = mid - 1

        return best_quality, best_buf, best_size

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            resize_count = 0

            init_size = os.path.getsize(path)
            quality = 100
            print(f"{local_version} | Initial File: {path}")
            print(
                f"{local_version} | WxH={img.width}x{img.height} | Quality={quality} "
                f"| Size={init_size/1024:.2f} KB | Target={target_size/1024:.0f} KB"
            )

            if init_size <= target_size:
                print(f"{local_version} | ✅ Already under target, no compression needed")
                return

            while True:
                best_quality, best_buf, best_size = _find_best_quality_buffer(
                    img,
                    target_size,
                    min_quality_before_resize,
                    100,
                )
                if best_buf is not None:
                    with open(path, "wb") as f:
                        f.write(best_buf.getvalue())
                    elapsed = time.time() - started_at
                    print(
                        f"{local_version} | ✅ Success: {init_size/1024:.2f} KB -> {best_size/1024:.2f} KB "
                        f"| Quality={best_quality} | Resized {resize_count} times"
                    )
                    print(f"{local_version} | Finished in {elapsed:.2f} sec")
                    return

                # Even q_min does not fit target: reduce resolution slightly and retry quality search.
                min_q_buf = _encode_jpeg_buffer(img, min_quality_before_resize)
                min_q_size = len(min_q_buf.getvalue())
                correction = (target_size / max(min_q_size, 1)) ** 0.5
                correction = max(0.88, min(0.98, correction))
                new_w = max(1, int(img.width * correction))
                new_h = max(1, int(img.height * correction))
                img = img.resize((new_w, new_h), Image.LANCZOS)
                resize_count += 1
                print(
                    f"{local_version} | Step {resize_count} | Resized to {new_w}x{new_h}, "
                    f"q{min_quality_before_resize} size={min_q_size/1024:.2f} KB"
                )

    except UnidentifiedImageError:
        print(f"{local_version} | Skipped corrupted file: {path}")


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


def _compress_static_webp_like_jpg(
    image,
    target_size,
    local_version,
    gif_cfg,
    started_at,
):
    """JPG-like loop for static WEBP: target by bytes, quality first, then resize."""
    quality = 95
    resize_count = 0
    webp_method = max(0, min(6, gif_cfg.webp_static_method_default))

    for step in range(1, gif_cfg.webp_static_max_iterations + 1):
        quality = max(1, min(100, int(quality)))
        buf = io.BytesIO()
        image.save(buf, "WEBP", quality=quality, method=webp_method)
        file_size = len(buf.getvalue())
        elapsed = time.time() - started_at
        print(
            f"{local_version} | WEBP static step {step} | "
            f"Size={file_size/1024:.2f} KB | q={quality} | method={webp_method} | elapsed={elapsed:.2f} sec"
        )

        if file_size <= target_size:
            return buf, file_size, quality, resize_count, True

        if elapsed >= gif_cfg.webp_file_max_seconds:
            print(
                f"{local_version} | ⚠ WEBP static timeout {elapsed:.2f} sec; "
                f"file kept unchanged"
            )
            return None, None, quality, resize_count, False

        correction = (target_size / file_size) ** 0.5 if file_size > 0 else 1.0
        correction = max(0.75, min(1.25, correction))

        if quality <= 50:
            new_w = max(1, int(image.width * correction))
            new_h = max(1, int(image.height * correction))
            image = image.resize((new_w, new_h), Image.LANCZOS)
            resize_count += 1
            quality = 95
            print(f"{local_version} | WEBP step {resize_count} | Resized to {new_w}x{new_h}, reset quality={quality}")
            continue

        quality = max(50, min(100, int(quality * correction)))
        print(f"{local_version} | WEBP step {resize_count+1} | Quality={quality}")

    print(
        f"{local_version} | ⚠ WEBP static max iterations reached; "
        f"file kept unchanged (could not hit target <= {target_size/1024:.0f} KB)"
    )
    return None, None, quality, resize_count, False


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
    webp_method_fast = max(0, min(6, gif_cfg.webp_animated_method_fast))
    webp_method_direct_fast = max(0, min(6, gif_cfg.webp_animated_direct_final_fast_method))
    direct_fast_growth = max(1.0, float(gif_cfg.webp_animated_direct_final_fast_max_growth))
    probe_enabled = bool(gif_cfg.webp_animated_probe_enabled and webp_method_fast != webp_method)
    verify_margin_ratio = max(0.0, min(0.20, gif_cfg.webp_animated_probe_verify_margin_ratio))
    verify_margin_growth = max(0.0, min(0.10, gif_cfg.webp_animated_probe_verify_margin_growth_per_step))
    # Seed with realistic method=0→method=2 ratio so quality steps are not over-aggressive.
    method_ratio = max(0.5, min(1.0, gif_cfg.webp_animated_probe_initial_method_ratio))
    method_ratio_samples = 0

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
    if probe_enabled:
        print(
            f"{local_version} | WEBP animated probe enabled | "
            f"probe_method={webp_method_fast} -> final_method={webp_method}"
        )

    under_target_q = None
    over_target_q = None

    for step in range(1, gif_cfg.webp_animated_max_iterations + 1):
        quality = max(1, min(100, int(quality)))
        bracket_known = under_target_q is not None and over_target_q is not None
        direct_final_this_step = bool(direct_final_from_stats and step == 1)
        if direct_final_this_step:
            method_in_use = webp_method_direct_fast if can_use_direct_fast else webp_method
        elif bracket_known:
            # Once we have a real under/over bracket, stop using probe estimates.
            # Further decisions must be based on actual method=2 results only.
            method_in_use = webp_method
        else:
            method_in_use = webp_method_fast if probe_enabled else webp_method
        print(
            f"{local_version} | WEBP animated step {step} | "
            f"Encoding... (q={quality}, method={method_in_use})"
        )
        encode_start = time.time()
        try:
            probe_buf = _save_webp_frames(frames, durations, quality, method=method_in_use)
        except ValueError as e:
            fallback_method = 0
            fallback_quality = max(1, min(100, quality))
            print(
                f"{local_version} | WEBP animated config error: {e} "
                f"| retry with q={fallback_quality}, method={fallback_method}"
            )
            try:
                probe_buf = _save_webp_frames(frames, durations, fallback_quality, method=fallback_method)
                quality = fallback_quality
                method_in_use = fallback_method
            except ValueError as e2:
                print(f"{local_version} | ⚠ WEBP animated encode failed: {e2}; file kept unchanged")
                return

        probe_encode_elapsed = time.time() - encode_start
        probe_size = len(probe_buf.getvalue())
        effective_size = probe_size
        effective_buf = probe_buf
        effective_method = method_in_use
        step_encode_elapsed = probe_encode_elapsed
        has_actual_final_measurement = method_in_use == webp_method

        if direct_final_this_step and method_in_use != webp_method:
            if target_min_bytes <= probe_size <= target_max_bytes:
                print(
                    f"{local_version} | WEBP direct-fast accepted | "
                    f"Size={probe_size/1024:.2f} KB | method={method_in_use}"
                )
            else:
                print(
                    f"{local_version} | WEBP direct-fast miss | "
                    f"Size={probe_size/1024:.2f} KB -> fallback method={webp_method}"
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
                has_actual_final_measurement = True
                step_encode_elapsed += fallback_elapsed
                print(
                    f"{local_version} | WEBP direct-fast fallback result | "
                    f"Size={final_size/1024:.2f} KB | method={final_method} | fallback={fallback_elapsed:.2f} sec"
                )

        should_verify_final = False
        if probe_enabled and method_in_use != webp_method and not direct_final_this_step:
            predicted_final = max(1, int(probe_size * method_ratio))
            effective_size = predicted_final
            effective_verify_margin = min(0.20, verify_margin_ratio + verify_margin_growth * max(0, step - 1))
            lower_verify = int(target_min_bytes * (1.0 - effective_verify_margin))
            upper_verify = int(target_max_bytes * (1.0 + effective_verify_margin))

            should_verify_final = (
                target_min_bytes <= predicted_final <= target_max_bytes
                or lower_verify <= predicted_final <= upper_verify
                or (bracket_known and (over_target_q - under_target_q) <= 2)
                or step == gif_cfg.webp_animated_max_iterations
            )

            print(
                f"{local_version} | WEBP animated probe | "
                f"Size={probe_size/1024:.2f} KB -> est_final={predicted_final/1024:.2f} KB "
                f"| ratio={method_ratio:.3f} | verify_margin={effective_verify_margin:.2f}"
            )

            if should_verify_final:
                verify_start = time.time()
                try:
                    final_buf = _save_webp_frames(frames, durations, quality, method=webp_method)
                    final_method = webp_method
                except ValueError as e:
                    fallback_method = 0
                    print(
                        f"{local_version} | WEBP animated final verify error: {e} "
                        f"| retry with method={fallback_method}"
                    )
                    final_buf = _save_webp_frames(frames, durations, quality, method=fallback_method)
                    final_method = fallback_method

                verify_elapsed = time.time() - verify_start
                final_size = len(final_buf.getvalue())
                step_encode_elapsed += verify_elapsed

                if probe_size > 0:
                    measured_ratio = final_size / probe_size
                    measured_ratio = max(0.5, min(1.8, measured_ratio))
                    if method_ratio_samples == 0:
                        method_ratio = measured_ratio
                    else:
                        method_ratio = method_ratio * 0.7 + measured_ratio * 0.3
                    method_ratio_samples += 1

                effective_size = final_size
                effective_buf = final_buf
                effective_method = final_method
                has_actual_final_measurement = True
                print(
                    f"{local_version} | WEBP animated verify | "
                    f"final={final_size/1024:.2f} KB | method={final_method} "
                    f"| verify={verify_elapsed:.2f} sec | ratio_now={method_ratio:.3f}"
                )

        print(
            f"{local_version} | WEBP animated step {step} | "
            f"Size={effective_size/1024:.2f} KB | encode={step_encode_elapsed:.2f} sec"
        )

        # Success check MUST come before timeout: an in-target result must always be saved
        # regardless of how long the encode took. Timeout only discards out-of-range results.
        if target_min_bytes <= effective_size <= target_max_bytes:
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

        if has_actual_final_measurement:
            if effective_size < target_min_bytes:
                under_target_q = quality if under_target_q is None else max(under_target_q, quality)
            elif effective_size > target_max_bytes:
                over_target_q = quality if over_target_q is None else min(over_target_q, quality)

        elapsed = time.time() - started_at
        if elapsed >= gif_cfg.webp_file_max_seconds:
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

        # Near-target miss: nudge quality by 1-2 points to avoid overshooting and extra full re-encodes.
        near_mid_ratio = abs(effective_size - target_mid_bytes) / target_mid_bytes if target_mid_bytes > 0 else 0.0
        has_bracket = under_target_q is not None and over_target_q is not None
        if near_mid_ratio <= gif_cfg.webp_animated_near_band_ratio:
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

            # Uncalibrated probe ratio can be far off on new profiles; cap quality jump.
            if probe_enabled and method_ratio_samples == 0:
                base_step = max(1, int(gif_cfg.webp_animated_uncalibrated_max_quality_step))
                step_growth = max(0, int(gif_cfg.webp_animated_uncalibrated_max_quality_step_growth))
                max_step = min(12, base_step + step_growth * max(0, step - 1))
                if proposed_quality > quality + max_step:
                    proposed_quality = quality + max_step
                elif proposed_quality < quality - max_step:
                    proposed_quality = quality - max_step

            quality = proposed_quality

        print(f"{local_version} | WEBP step {resize_count+1} | Quality={quality}")

    print(
        f"{local_version} | ⚠ WEBP animated max iterations reached; "
        f"file kept unchanged (could not hit {gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB)"
    )
    return


def compress_static_webp_until_under_target(path, gif_cfg=CONFIG.gif):
    """Static WEBP path: image/JPG-style logic with WEBP output preserved."""
    # Handles static WEBP files using a similar approach as JPEG compression
    # Converts to RGB/RGBA as needed, then compresses with quality/resize loop
    local_version = VERSION
    started_at = time.time()

    try:
        with Image.open(path) as img:
            init_size = os.path.getsize(path)
            frame_count = getattr(img, "n_frames", 1)
            is_animated = bool(getattr(img, "is_animated", False) and frame_count > 1)
            if is_animated:
                return

            print(f"{local_version} | Initial WEBP: {path}")
            print(
                f"{local_version} | WxH={img.width}x{img.height} | Animated=False "
                f"| Frames={frame_count} | Size={init_size/1024:.2f} KB "
                f"| Target={TARGET_SIZE/1024:.0f} KB"
            )

            if init_size <= TARGET_SIZE:
                print(f"{local_version} | ✅ WEBP already in target range, no compression needed")
                return

            has_alpha = "A" in (img.mode or "")
            image = img.convert("RGBA" if has_alpha else "RGB")
            buf, file_size, quality, resize_count, success = _compress_static_webp_like_jpg(
                image,
                TARGET_SIZE,
                local_version,
                gif_cfg,
                started_at,
            )
            if not success:
                return

            with open(path, "wb") as f:
                f.write(buf.getvalue())
            elapsed = time.time() - started_at
            print(
                f"{local_version} | ✅ WEBP success (static-jpg-like): {init_size/1024:.2f} KB -> {file_size/1024:.2f} KB "
                f"| Quality={quality} | Resized {resize_count} times"
            )
            print(f"{local_version} | Finished in {elapsed:.2f} sec")

    except UnidentifiedImageError:
        print(f"{local_version} | Skipped corrupted WEBP: {path}")


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


def compress_webp_until_under_target(path, gif_cfg=CONFIG.gif):
    """Backward-compatible dispatcher by WEBP type."""
    if _is_animated_webp(path):
        return compress_animated_webp_until_under_target(path, gif_cfg=gif_cfg)
    return compress_static_webp_until_under_target(path, gif_cfg=gif_cfg)


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
    resized_frames = resize_frames(frames_raw, width, height, scale)
    key = _scale_key(scale)

    if key in fast_cache:
        fast_size = fast_cache[key]["size"]
        print(f"{VERSION} | Step {iteration+1}.0 ({stage_tag}, cached) | FASTOCTREE={fast_size:.2f} MB")
        return resized_frames, fast_size, fast_cache[key].get("bytes")

    step_start = time.time()
    frames_fast = [process_frame_fast_octree(fr, palette_limit) for fr in resized_frames]
    buf_fast, fast_size = save_gif(frames_fast, durations, optimize=False)
    fast_cache[key] = {"size": fast_size, "bytes": buf_fast.getvalue()}
    step_elapsed = time.time() - step_start
    print(f"{VERSION} | Step {iteration+1}.0 ({stage_tag}) | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
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
        f"{VERSION} | file: {os.path.basename(input_path)} "
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

    frames_raw, durations = [], []
    with Image.open(input_path) as img:
        width, height = img.size
        total_frames = img.n_frames
        colors_first = len(img.getcolors(maxcolors=256 * 256) or [])
        palette_limit = min(colors_first + gif_cfg.extra_palette, 256)

        print(f"{VERSION} | Starting file: {input_path}")
        init_size = os.path.getsize(input_path) / (1024 * 1024)
        print(f"{VERSION} | Initial Size: {init_size:.2f} MB | Frames={total_frames} | Palette={colors_first} | WxH={width}x{height}")

        for frame in ImageSequence.Iterator(img):
            frames_raw.append(frame.convert("RGB"))
            durations.append(frame.info.get("duration", 100))

    # ⚠️ DO NOT CHANGE workers — MEDIANCUT is memory-intensive per frame;
    # using more than half CPUs causes RAM pressure and slows down the whole system.
    # DO NOT add dynamic scaling, boost, or any frame-count-based adjustments here.
    workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"{VERSION} | Using {workers} workers for {total_frames} frames")
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

    print(f"{VERSION} | Prediction source: {source}")
    print(f"{VERSION} | -> initial scale={scale:.3f}")

    low_scale = 0.01
    high_scale = 4.0
    fast_cache = {}
    med_cache = {}
    micro_adjust_used = False
    stall_count = 0
    last_signature = None

    temporal_applied = False
    quality_retry_done = False
    sample_probe_done = False
    sample_ratio = None
    formula_extra_skip_used = False
    small_res_high_frames = (
        (width * height) <= gif_cfg.temporal_max_pixels
        and total_frames >= gif_cfg.temporal_min_frames
    )

    with ProcessPoolExecutor(max_workers=workers) as executor:
        for iteration in range(gif_cfg.max_safe_iterations):
            resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                iteration=iteration,
                scale=scale,
                frames_raw=frames_raw,
                width=width,
                height=height,
                palette_limit=palette_limit,
                durations=durations,
                fast_cache=fast_cache,
                stage_tag="base",
            )

            fast_in_preferred = gif_cfg.preferred_min_mb <= fast_size <= gif_cfg.preferred_max_mb
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
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_saved_size, scale)
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
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_size, scale)
                with open(input_path, "wb") as f:
                    f.write(fast_bytes)
                elapsed = time.time() - started_at
                _print_gif_result_header(input_path, total_frames, colors_first, width, height)
                print(
                    f"{VERSION} | ✅ Success (fast): {init_size:.2f} MB -> {fast_saved_size:.2f} MB "
                    f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                )
                return

            predicted_medcut = stats_mgr.predict_mediancut(
                palette_limit,
                width,
                height,
                total_frames,
                fast_size,
                bias_factor,
            )
            predicted_medcut = _clamp_prediction(predicted_medcut, fast_size)
            
            # ⚠️ Conservative bias: stats-based predictions tend to be optimistic.
            # Apply extra safety margin to avoid exceeding target on overcorrection.
            if source == "stats":
                predicted_medcut *= gif_cfg.stats_source_bias_extra
                predicted_medcut = _clamp_prediction(predicted_medcut, fast_size)

            # Hard early skip: if first FASTOCTREE is far above target,
            # skip sample-probe and jump down immediately.
            _is_neighbor_source = source.startswith("neighbor stats")
            if (
                iteration == 0
                and (source == "formula (conservative)" or _is_neighbor_source)
                and fast_size > gif_cfg.target_max_mb * gif_cfg.fast_probe_hard_skip_ratio
            ):
                high_scale = scale
                if _is_neighbor_source:
                    # Use stored delta to aim for the FASTOCTREE level that will yield MEDIANCUT in target.
                    # This is far more accurate than the blind (target_mid/fast_size)*0.92 formula.
                    _delta_for_skip = stats_mgr.find_delta(palette_limit, width, height, total_frames)
                    if _delta_for_skip is not None:
                        _target_fast = target_mid - _delta_for_skip * bias_factor
                        if _target_fast > 0 and fast_size > 0:
                            suggested_scale = scale * (_target_fast / fast_size) ** 0.5
                        else:
                            suggested_scale = scale * (target_mid / fast_size) ** 0.5 * 0.92
                    else:
                        suggested_scale = scale * (target_mid / fast_size) ** 0.5 * 0.92 if fast_size > 0 else scale
                else:
                    suggested_scale = scale * (target_mid / fast_size) ** 0.5 if fast_size > 0 else scale
                    suggested_scale *= 0.92
                max_skip_step_ratio = 0.55
                max_skip_step = scale * max_skip_step_ratio
                if abs(suggested_scale - scale) > max_skip_step:
                    direction = 1 if suggested_scale > scale else -1
                    suggested_scale = scale + direction * max_skip_step
                if not (low_scale < suggested_scale < high_scale):
                    suggested_scale = (low_scale + high_scale) / 2
                print(
                    f"{VERSION} | Early hard-skip on iter 1: FASTOCTREE={fast_size:.2f} MB "
                    f"(>{gif_cfg.fast_probe_hard_skip_ratio:.2f}x target_max)"
                )
                print(f"{VERSION} | -> next scale={suggested_scale:.3f}")
                scale = suggested_scale
                sample_probe_done = True
                continue

            if (
                gif_cfg.sample_probe_enabled
                and source == "formula (conservative)"
                and not sample_probe_done
                and iteration <= 1
                and total_frames >= 120
            ):
                probe_start = time.time()
                sample_ratio = _estimate_ratio_sample(
                    resized_frames,
                    durations,
                    palette_limit,
                    executor,
                    workers,
                    gif_cfg,
                )
                sample_probe_done = True
                probe_elapsed = time.time() - probe_start
                if sample_ratio and sample_ratio > 1.0:
                    calibrated_prediction = fast_size * sample_ratio
                    if calibrated_prediction > predicted_medcut:
                        predicted_medcut = calibrated_prediction
                    print(
                        f"{VERSION} | Probe ratio (sample)={sample_ratio:.3f} "
                        f"-> calibrated MEDIANCUT={predicted_medcut:.2f} MB "
                        f"| finished in {probe_elapsed:.2f} sec"
                    )

            print(f"{VERSION} | -> Predicted MEDIANCUT={predicted_medcut:.2f} MB | scale={scale:.3f}")
            print(f"{VERSION} | -> source: {source}")

            source_is_neighbor = source.startswith("neighbor stats")

            can_skip_first_med = (
                iteration == 0
                and (source == "formula (conservative)" or source_is_neighbor)
                and predicted_medcut > gif_cfg.target_max_mb * 1.20
                and fast_size > gif_cfg.target_max_mb * 0.90
            )
            can_skip_probe_overflow = (
                iteration == 0
                and source == "formula (conservative)"
                and sample_ratio is not None
                and predicted_medcut > gif_cfg.target_max_mb * gif_cfg.probe_skip_overflow_margin
            )
            can_skip_probe_underflow = (
                iteration == 0
                and source == "formula (conservative)"
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
            if can_skip_first_med or can_skip_probe_overflow or can_skip_probe_underflow or can_skip_formula_extra:
                debug_log("decision=skip_first_med | reason=formula prediction well above target")
                high_scale = scale
                suggested_scale = scale * (target_mid / predicted_medcut) ** 0.5 if predicted_medcut > 0 else scale

                if can_skip_probe_underflow:
                    low_scale = scale
                    high_scale = max(high_scale, 4.0)
                elif can_skip_probe_overflow:
                    high_scale = scale

                if can_skip_formula_extra:
                    formula_extra_skip_used = True

                max_skip_step_ratio = 0.45
                max_skip_step = scale * max_skip_step_ratio
                if abs(suggested_scale - scale) > max_skip_step:
                    direction = 1 if suggested_scale > scale else -1
                    suggested_scale = scale + direction * max_skip_step

                if not (low_scale < suggested_scale < high_scale):
                    suggested_scale = (low_scale + high_scale) / 2

                reason = "predicted too large" if (can_skip_first_med or can_skip_probe_overflow or can_skip_formula_extra) else "predicted too small"
                print(f"{VERSION} | Skipping MEDIANCUT on iter {iteration+1} ({reason})")
                print(f"{VERSION} | -> next scale={suggested_scale:.3f}")
                scale = suggested_scale
                continue

            can_pre_correct = (
                iteration == 0
                and source in {"delta_avg (conservative)"}
                and fast_size < target_mid * 0.80
                and predicted_medcut < gif_cfg.target_min_mb * 0.92
            )
            if can_pre_correct:
                debug_log("decision=pre_correction | reason=iter0/formula_or_delta and prediction well below target")
                scale *= 0.92
                print(f"{VERSION} | Pre-correction (iter 0) -> scale={scale:.3f}")
                resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                    iteration=iteration,
                    scale=scale,
                    frames_raw=frames_raw,
                    width=width,
                    height=height,
                    palette_limit=palette_limit,
                    durations=durations,
                    fast_cache=fast_cache,
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
                suggested_scale = scale * (target_mid / predicted_medcut) ** 0.5 if predicted_medcut > 0 else scale
                suggested_scale *= 0.99

                max_soft_step_ratio = 0.12
                max_soft_step = scale * max_soft_step_ratio
                if abs(suggested_scale - scale) > max_soft_step:
                    direction = 1 if suggested_scale > scale else -1
                    suggested_scale = scale + direction * max_soft_step

                if low_scale < suggested_scale < high_scale and abs(suggested_scale - scale) > 0.005:
                    debug_log("decision=soft_pre_shrink | reason=formula near upper target bound")
                    scale = suggested_scale
                    print(f"{VERSION} | Soft pre-shrink (iter 0) -> scale={scale:.3f}")
                    resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                        iteration=iteration,
                        scale=scale,
                        frames_raw=frames_raw,
                        width=width,
                        height=height,
                        palette_limit=palette_limit,
                        durations=durations,
                        fast_cache=fast_cache,
                        stage_tag="soft-corrected",
                    )
                    predicted_medcut = stats_mgr.predict_mediancut(
                        palette_limit,
                        width,
                        height,
                        total_frames,
                        fast_size,
                        bias_factor,
                    )
                    predicted_medcut = _clamp_prediction(predicted_medcut, fast_size)
                    # ⚠️ Apply conservative bias to stats predictions.
                    if source == "stats":
                        predicted_medcut *= gif_cfg.stats_source_bias_extra
                        predicted_medcut = _clamp_prediction(predicted_medcut, fast_size)
                    print(
                        f"{VERSION} | -> Updated predicted MEDIANCUT={predicted_medcut:.2f} MB "
                        f"| scale={scale:.3f}"
                    )

            can_micro_adjust = (
                source_is_neighbor
                and predicted_medcut < gif_cfg.target_min_mb
                and fast_size < target_mid * 0.9
                and not micro_adjust_used
                and iteration <= 1
                and total_frames >= 80
                and high_scale >= 3.9
                and stall_count < 1
            )
            if can_micro_adjust:
                adj_scale = scale * (target_mid / (fast_size + 4.0)) ** 0.5 if source_is_neighbor else scale
                if abs(adj_scale - scale) > 0.01:
                    # Allow a meaningful correction, but cap the jump to keep stability.
                    max_micro_step_ratio = min(0.30, gif_cfg.max_scale_step_ratio * 2.0)
                    max_micro_step = scale * max_micro_step_ratio
                    if abs(adj_scale - scale) > max_micro_step:
                        direction = 1 if adj_scale > scale else -1
                        adj_scale = scale + direction * max_micro_step

                    debug_log("decision=micro_adjust | reason=neighbor_stats and fast below 0.9*target_mid")
                    scale = adj_scale
                    micro_adjust_used = True
                    print(f"{VERSION} | Micro-adjusting scale -> {scale:.3f}")
                    resized_frames, fast_size, fast_bytes = _run_fastoctree_trial(
                        iteration=iteration,
                        scale=scale,
                        frames_raw=frames_raw,
                        width=width,
                        height=height,
                        palette_limit=palette_limit,
                        durations=durations,
                        fast_cache=fast_cache,
                        stage_tag="adjusted",
                    )

            scale_key = _scale_key(scale)
            if scale_key in med_cache:
                med_size, med_bytes = med_cache[scale_key]
                print(f"{VERSION} | Step {iteration+1}.1 (cached) | MEDIANCUT={med_size:.2f} MB")
                debug_log(f"cache=med | hit | key={scale_key}")
            else:
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
                med_cache[scale_key] = (med_size, med_bytes)
                step_elapsed = time.time() - step_start
                print(f"{VERSION} | Step {iteration+1}.1 | MEDIANCUT={med_size:.2f} MB | finished in {step_elapsed:.2f} sec")
                debug_log(f"cache=med | miss | key={scale_key}")

            pred_error = med_size - predicted_medcut
            pred_error_pct = (pred_error / predicted_medcut * 100.0) if predicted_medcut > 0 else 0.0
            debug_log(f"prediction_error={pred_error:+.2f} MB ({pred_error_pct:+.2f}%)")

            signature = (_scale_key(scale), round(med_size, 2))
            if signature == last_signature:
                stall_count += 1
            else:
                stall_count = 0
                last_signature = signature
            if stall_count >= 2:
                debug_log("stall_guard=active | repeated (scale, med_size) signature")

            print(f"{VERSION} | Delta vs FASTOCTREE = {med_size - fast_size:+.2f} MB")

            can_try_temporal_preserve = (
                gif_cfg.temporal_preserve_enabled
                and not temporal_applied
                and iteration == 0
                and med_size > gif_cfg.target_max_mb
                and total_frames >= gif_cfg.temporal_min_frames
                and (width * height) <= gif_cfg.temporal_max_pixels
                and scale < 0.85
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
                        f"{VERSION} | Temporal preserve probe | keep_every={keep_every} "
                        f"| frames {len(frames_raw)}->{len(t_frames)} | MEDIANCUT={t_med_size:.2f} MB "
                        f"| finished in {t_elapsed:.2f} sec"
                    )

                    if gif_cfg.target_min_mb <= t_med_size <= gif_cfg.target_max_mb + 0.005:
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
                        fast_cache.clear()
                        med_cache.clear()
                        low_scale = 0.01
                        high_scale = min(high_scale, 1.0)
                        scale = min(1.0, scale / 0.92)
                        temporal_applied = True
                        print(
                            f"{VERSION} | Temporal preserve enabled -> continue with original WxH and "
                            f"{total_frames} frames"
                        )
                        continue

            in_preferred_corridor = (
                iteration >= 1
                and gif_cfg.preferred_min_mb <= med_size <= gif_cfg.preferred_max_mb
            )
            # ⚠️ IMMUTABLE TARGET RANGE: [13.5 MB, 14.99 MB]
            # This is the contract. Results MUST fit here. Never relax, widen, or negotiate.
            in_target = gif_cfg.target_min_mb <= med_size <= gif_cfg.target_max_mb + 0.005

            can_try_quality_retry = (
                gif_cfg.quality_retry_small_res_enabled
                and not quality_retry_done
                and not temporal_applied
                and iteration == 0
                and in_target
                and small_res_high_frames
                and scale < gif_cfg.quality_retry_min_scale
            )
            if can_try_quality_retry:
                quality_retry_done = True
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
                        f"{VERSION} | Quality retry (temporal) | keep_every={keep_every} "
                        f"| frames {len(frames_raw)}->{len(q_frames)} | MEDIANCUT={q_med_size:.2f} MB "
                        f"| finished in {q_elapsed:.2f} sec"
                    )

                    # ⚠️ SACRED BOUNDARIES: Result must be within [13.5, 14.99] MB. Never relax.
                    if gif_cfg.target_min_mb <= q_med_size <= gif_cfg.target_max_mb + 0.005:
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
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, med_size, scale)
                with open(input_path, "wb") as f:
                    f.write(med_bytes)
                elapsed = time.time() - started_at
                _print_gif_result_header(input_path, total_frames, colors_first, width, height)
                print(
                    f"{VERSION} | ✅ Success: {init_size:.2f} MB -> {med_size:.2f} MB "
                    f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                )
                return

            if med_size > gif_cfg.target_max_mb:
                high_scale = scale
            else:
                low_scale = scale

            # Use measured MEDIANCUT size to jump closer to target midpoint.
            adaptive_scale = scale
            if med_size > 0:
                adaptive_scale = scale * (target_mid / med_size) ** 0.5

            max_adaptive_step_ratio = min(0.35, gif_cfg.max_scale_step_ratio * 2.5)
            adaptive_step = scale * max_adaptive_step_ratio
            if abs(adaptive_scale - scale) > adaptive_step:
                direction = 1 if adaptive_scale > scale else -1
                adaptive_scale = scale + direction * adaptive_step

            adaptive_in_bracket = low_scale < adaptive_scale < high_scale
            if adaptive_in_bracket:
                new_scale = adaptive_scale
            else:
                new_scale = _next_scale(
                scale=scale,
                low_scale=low_scale,
                high_scale=high_scale,
                med_cache=med_cache,
                target_mid=target_mid,
                max_step_ratio=gif_cfg.max_scale_step_ratio,
                )
            print(f"{VERSION} | Next scale={new_scale:.3f}")
            print(f"{VERSION} | -> bracket: low={low_scale:.3f}, high={high_scale:.3f}")
            scale = new_scale

    print(f"{VERSION} | Failed to converge after {gif_cfg.max_safe_iterations} iterations")


def process_gifs(gif_paths, animated_webp_paths):
    """GIF block: process queued oversized GIFs and oversized animated WEBPs."""
    worked = False
    for file_path in gif_paths:
        worked = True
        try:
            balanced_compress_gif(file_path)
        except Exception as e:
            print(f"{VERSION} | Error processing {file_path}: {e}")

    for file_path in animated_webp_paths:
        worked = True
        try:
            compress_animated_webp_until_under_target(file_path)
        except Exception as e:
            print(f"{VERSION} | Error processing {file_path}: {e}")

    return worked


if __name__ == "__main__":
    png_paths, jpg_paths, static_webp_paths, gif_paths, animated_webp_paths = scan_media_candidates(ROOT_FOLDER_PATH)

    images_started_at = time.time()
    images_worked = process_images(png_paths, jpg_paths, static_webp_paths)

    images_elapsed = time.time() - images_started_at

    gifs_started_at = time.time()
    gifs_worked = process_gifs(gif_paths, animated_webp_paths)
    gifs_elapsed = time.time() - gifs_started_at

    print(
        f"{VERSION} | ✅ Scan complete: scan_media={RUN_METRICS['scan_sec']:.2f} sec "
        f"(png={RUN_METRICS['png_candidates']}, "
        f"jpg={RUN_METRICS['jpg_candidates']}, "
        f"static_webp={RUN_METRICS['static_webp_candidates']}, "
        f"gif={RUN_METRICS['gif_candidates']}, "
        f"animated_webp={RUN_METRICS['animated_webp_candidates']})"
    )

    # Note for maintenance: if stats file grows beyond soft limit, consider cleanup/aggregation.
    try:
        stats_size_mb = os.path.getsize(STATS_FILE) / (1024 * 1024)
        if stats_size_mb >= CONFIG.stats_soft_limit_mb:
            print(
                f"{VERSION} | ⚠ Stats note: {os.path.basename(STATS_FILE)} is {stats_size_mb:.2f} MB "
                f"(>= {CONFIG.stats_soft_limit_mb:.0f} MB). Consider rotating/compressing stats."
            )
    except OSError:
        pass

    stats_script = os.path.join(os.path.dirname(__file__), "StatsCompressor.py")
    stats_started_at = time.time()
    try:
        subprocess.run(["python", stats_script, STATS_FILE], check=True)
    except Exception as e:
        print(f"StatsCompressor failed: {e}")
    stats_elapsed = time.time() - stats_started_at

    print(f"{VERSION} | stats_compressor={stats_elapsed:.2f} sec")

    # Output the number of statistics entries for GIF and WEBP
    try:
        with open(STATS_FILE, "r", encoding="utf-8-sig") as f:
            stats_data = json.load(f)
        gif_count = len(stats_data.get("gif_stats", []))
        webp_count = len(stats_data.get("webp_animated_stats", []))
        print(f"{VERSION} | GIF — {gif_count} items | WEBP — {webp_count} items")
        # Total number of files in the root folder for scan (before filtering)
        import subprocess
        def count_files_in_dir(root_folder):
            try:
                # Try to use Everything CLI (es.exe) for fast file counting
                result = subprocess.run([
                    "es.exe",
                    "-count",
                    f"-path={root_folder}"
                ], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    count_str = result.stdout.strip()
                    if count_str.isdigit():
                        return int(count_str)
            except Exception:
                pass
            # Fallback to os.walk if es.exe is not available or fails
            count = 0
            for _, _, files in os.walk(root_folder):
                count += len(files)
            return count
        total_files_in_dir = count_files_in_dir(ROOT_FOLDER_PATH)
    except Exception as e:
        print(f"{VERSION} | Stats count error: {e}")

    # Final output for user
    scan_time_str = f"ℹ️ Scan time: {RUN_METRICS['scan_sec']:.2f} sec. Total number of files in folder: {total_files_in_dir}"
    print(scan_time_str)
    print("✅ All images converted/compressed and oversized GIFs, Webps compressed.")
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"Total execution time: {elapsed:.2f} sec. Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

