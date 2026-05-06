import os
import time


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


def scan_media_candidates(root_folder_path, target_size, min_process_size_mb, run_metrics):
    """Single filesystem pass that classifies files for later processing."""
    png_paths = []
    jpg_paths = []
    static_webp_paths = []
    gif_paths = []
    animated_webp_paths = []
    started_at = time.time()

    files = []
    for dirpath, dirnames, filenames in os.walk(root_folder_path):
        for filename in filenames:
            files.append(os.path.join(dirpath, filename))

    for file_path in files:
        lower = file_path.lower()
        if lower.endswith(".gif"):
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > min_process_size_mb:
                gif_paths.append(file_path)
            continue

        if lower.endswith(".png"):
            png_paths.append(file_path)
            continue

        if lower.endswith((".jpg", ".jpeg")):
            if os.path.getsize(file_path) > target_size:
                jpg_paths.append(file_path)
            continue

        if lower.endswith(".jfif"):
            # Always convert JFIF to JPG so this extension is not skipped by the pipeline.
            jpg_paths.append(file_path)
            continue

        if not lower.endswith(".webp"):
            continue

        size_bytes = os.path.getsize(file_path)
        if size_bytes <= target_size:
            continue

        if _is_animated_webp_fast(file_path):
            if (size_bytes / (1024 * 1024)) > min_process_size_mb:
                animated_webp_paths.append(file_path)
            continue

        static_webp_paths.append(file_path)

    run_metrics["scan_sec"] = time.time() - started_at
    run_metrics["png_candidates"] = len(png_paths)
    run_metrics["jpg_candidates"] = len(jpg_paths)
    run_metrics["static_webp_candidates"] = len(static_webp_paths)
    run_metrics["gif_candidates"] = len(gif_paths)
    run_metrics["animated_webp_candidates"] = len(animated_webp_paths)
    return png_paths, jpg_paths, static_webp_paths, gif_paths, animated_webp_paths
