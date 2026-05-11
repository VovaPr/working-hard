import os
import hashlib
import shutil
import time

from PIL import Image, ImageSequence, UnidentifiedImageError

from webp_stats import AnimatedWebPStatsManager
from webp_animated_pipeline import _compress_animated_webp


def _sha1_file(path, chunk_size=1024 * 1024):
    hasher = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _cache_file_path(cache_dir, source_hash):
    return os.path.join(cache_dir, f"{source_hash}.webp")


def compress_animated_webp_until_under_target(path, gif_cfg, version, stats_file):
    """Compress animated WEBP files while preserving existing runtime behavior."""
    local_version = version
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

            print(f"{local_version} | [webp.startup] | Initial WEBP: {path}")
            print(
                f"{local_version} | [webp.startup] | WxH={img.width}x{img.height} | Animated=True "
                f"| Frames={frame_count} | Size={init_size/1024:.2f} KB "
                f"| Target={gif_cfg.target_min_mb:.2f}-{gif_cfg.target_max_mb:.2f} MB"
            )

            if target_min_bytes <= init_size <= target_max_bytes:
                print(f"{local_version} | вњ… WEBP already in target range, no compression needed")
                return

            source_hash = None
            cache_file = None
            if getattr(gif_cfg, "webp_animated_exact_input_cache_enabled", False):
                try:
                    source_hash = _sha1_file(path)
                    cache_file = _cache_file_path(gif_cfg.webp_animated_exact_input_cache_dir, source_hash)
                    if os.path.exists(cache_file):
                        cached_size = os.path.getsize(cache_file)
                        if target_min_bytes <= cached_size <= target_max_bytes:
                            shutil.copyfile(cache_file, path)
                            print(
                                f"{local_version} | [webp.cache] | hit | sha1={source_hash[:12]} "
                                f"| size={cached_size/1024:.2f} KB"
                            )
                            return
                        print(
                            f"{local_version} | [webp.cache] | stale | sha1={source_hash[:12]} "
                            f"| cached_size={cached_size/1024:.2f} KB"
                        )
                except Exception as e:
                    print(f"{local_version} | [webp.cache] | read failed: {e}")

            frames = []
            durations = []
            for frame in ImageSequence.Iterator(img):
                if frame.mode in ("RGB", "RGBA"):
                    prepared = frame.copy()
                else:
                    has_alpha_frame = "A" in frame.getbands()
                    prepared = frame.convert("RGBA" if has_alpha_frame else "RGB")
                frames.append(prepared)
                durations.append(frame.info.get("duration", 100))

            stats_mgr_webp = AnimatedWebPStatsManager(stats_file, local_version)
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

            if source_hash and cache_file:
                try:
                    final_size = os.path.getsize(path)
                    if target_min_bytes <= final_size <= target_max_bytes:
                        os.makedirs(gif_cfg.webp_animated_exact_input_cache_dir, exist_ok=True)
                        shutil.copyfile(path, cache_file)
                        print(
                            f"{local_version} | [webp.cache] | saved | sha1={source_hash[:12]} "
                            f"| size={final_size/1024:.2f} KB"
                        )
                except Exception as e:
                    print(f"{local_version} | [webp.cache] | save failed: {e}")

    except UnidentifiedImageError:
        print(f"{local_version} | Skipped corrupted WEBP: {path}")


