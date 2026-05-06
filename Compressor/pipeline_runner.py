import os
import json
import time
import subprocess
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PipelineApi:
    version: str
    root_folder_path: str
    stats_file: str
    stats_soft_limit_mb: float
    run_metrics: dict
    start_time: float
    scan_media_candidates: object
    process_images: object
    process_gifs: object
    log_level: str


def _phase(version, component, message):
    print(f"{version} | [{component}] {message}")


def _phase_if_debug(api: PipelineApi, component, message):
    if api.log_level == "DEBUG":
        _phase(api.version, component, message)


def _count_files_in_dir(root_folder):
    try:
        result = subprocess.run(
            ["es.exe", "-count", f"-path={root_folder}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            count_str = result.stdout.strip()
            if count_str.isdigit():
                return int(count_str)
    except Exception:
        pass

    count = 0
    for _, _, files in os.walk(root_folder):
        count += len(files)
    return count


def run_pipeline(api: PipelineApi):
    _phase_if_debug(api, "core.scan", "Start media scan")
    png_paths, jpg_paths, static_webp_paths, gif_paths, animated_webp_paths = api.scan_media_candidates(api.root_folder_path)

    _phase_if_debug(api, "core.images", "Start static image processing")
    images_started_at = time.time()
    api.process_images(png_paths, jpg_paths, static_webp_paths)
    images_elapsed = time.time() - images_started_at
    _phase_if_debug(api, "core.images", f"Done in {images_elapsed:.2f}s")

    _phase_if_debug(api, "core.gif", "Start GIF/animated WEBP processing")
    gifs_started_at = time.time()
    api.process_gifs(gif_paths, animated_webp_paths)
    gifs_elapsed = time.time() - gifs_started_at
    _phase_if_debug(api, "core.gif", f"Done in {gifs_elapsed:.2f}s")

    print(
        f"{api.version} | ✅ Scan complete: scan_media={api.run_metrics['scan_sec']:.2f} sec "
        f"(png={api.run_metrics['png_candidates']}, "
        f"jpg={api.run_metrics['jpg_candidates']}, "
        f"static_webp={api.run_metrics['static_webp_candidates']}, "
        f"gif={api.run_metrics['gif_candidates']}, "
        f"animated_webp={api.run_metrics['animated_webp_candidates']})"
    )

    try:
        stats_size_mb = os.path.getsize(api.stats_file) / (1024 * 1024)
        if stats_size_mb >= api.stats_soft_limit_mb:
            print(
                f"{api.version} | ⚠ Stats note: {os.path.basename(api.stats_file)} is {stats_size_mb:.2f} MB "
                f"(>= {api.stats_soft_limit_mb:.0f} MB). Consider rotating/compressing stats."
            )
    except OSError:
        pass

    stats_script = os.path.join(os.path.dirname(__file__), "StatsCompressor.py")
    stats_started_at = time.time()
    try:
        _phase_if_debug(api, "core.stats", "Run stats compressor")
        subprocess.run(["python", stats_script, api.stats_file], check=True)
    except Exception as exc:
        print(f"{api.version} | StatsCompressor failed: {exc}")
    stats_elapsed = time.time() - stats_started_at

    print(f"{api.version} | stats_compressor={stats_elapsed:.2f} sec")

    total_files_in_dir = 0
    try:
        with open(api.stats_file, "r", encoding="utf-8-sig") as f:
            stats_data = json.load(f)
        gif_count = len(stats_data.get("gif_stats", []))
        webp_count = len(stats_data.get("webp_animated_stats", []))
        print(f"{api.version} | GIF — {gif_count} items | WEBP — {webp_count} items")
        total_files_in_dir = _count_files_in_dir(api.root_folder_path)
    except Exception as exc:
        print(f"{api.version} | Stats count error: {exc}")

    print(
        f"{api.version} | ℹ️ Scan time: {api.run_metrics['scan_sec']:.2f} sec. "
        f"Total number of files in folder: {total_files_in_dir}"
    )
    print(f"{api.version} | ✅ All images converted/compressed and oversized GIFs, Webps compressed.")
    elapsed = time.time() - api.start_time
    print(
        f"{api.version} | Total execution time: {elapsed:.2f} sec. "
        f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
