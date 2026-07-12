"""GIF facade module for batch processing and main pipeline delegation."""

import os
import shutil
import subprocess
import tempfile

from gif_main_pipeline import balanced_compress_gif


def _ffmpeg_exists():
    return shutil.which("ffmpeg") is not None


def _convert_mp4_to_gif(mp4_path, *, version, fps=12, width=720):
    output_gif = os.path.splitext(mp4_path)[0] + ".gif"
    if os.path.exists(output_gif):
        print(f"{version} | [mp4.gif] skip (exists): {output_gif}")
        return output_gif

    with tempfile.NamedTemporaryFile(prefix="palette_", suffix=".png", delete=False) as tmp:
        palette_path = tmp.name

    try:
        palettegen = [
            "ffmpeg",
            "-y",
            "-i",
            mp4_path,
            "-vf",
            f"fps={fps},scale={width}:-1:flags=lanczos,palettegen",
            palette_path,
        ]
        result = subprocess.run(palettegen, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"{version} | [mp4.gif] palettegen failed: {mp4_path}")
            return None

        paletteuse = [
            "ffmpeg",
            "-y",
            "-i",
            mp4_path,
            "-i",
            palette_path,
            "-lavfi",
            f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse",
            output_gif,
        ]
        result = subprocess.run(paletteuse, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"{version} | [mp4.gif] paletteuse failed: {mp4_path}")
            return None

        print(f"{version} | [mp4.gif] converted: {mp4_path} -> {output_gif}")
        return output_gif
    finally:
        try:
            if os.path.exists(palette_path):
                os.remove(palette_path)
        except OSError:
            pass


def process_gifs(
    gif_paths,
    animated_webp_paths,
    mp4_paths,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    compress_animated_webp_until_under_target,
    debug_log_fn=None,
):
    worked = False
    gif_queue = list(gif_paths)

    if mp4_paths:
        if not _ffmpeg_exists():
            print(f"{version} | [mp4.gif] ffmpeg not found; skipping {len(mp4_paths)} MP4 file(s)")
        else:
            print(f"{version} | [mp4.gif] converting {len(mp4_paths)} MP4 file(s)")
            for mp4_path in mp4_paths:
                gif_out = _convert_mp4_to_gif(mp4_path, version=version)
                if not gif_out:
                    continue

                # Route converted GIF into existing heavy-compression stage only when oversized.
                try:
                    size_mb = os.path.getsize(gif_out) / (1024 * 1024)
                    if size_mb > gif_cfg.targets.min_process_size_mb:
                        gif_queue.append(gif_out)
                except OSError:
                    pass

    for file_path in gif_queue:
        worked = True
        try:
            balanced_compress_gif(
                file_path,
                gif_cfg=gif_cfg,
                version=version,
                stats_file=stats_file,
                log_level=log_level,
                debug_log_fn=debug_log_fn,
            )
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    for file_path in animated_webp_paths:
        worked = True
        try:
            compress_animated_webp_until_under_target(file_path)
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    return worked
