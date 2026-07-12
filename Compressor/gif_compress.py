"""GIF facade module for batch processing and main pipeline delegation."""

import os
import shutil
import subprocess
import tempfile
import time

from gif_main_pipeline import balanced_compress_gif


def _resolve_ffmpeg_executable():
    from pathlib import Path

    ffmpeg_from_path = shutil.which("ffmpeg")
    if ffmpeg_from_path:
        return ffmpeg_from_path

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_root.exists():
            candidates = sorted(
                winget_root.glob("Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/**/bin/ffmpeg.exe"),
                reverse=True,
            )
            if candidates:
                return str(candidates[0])

    return None


def _describe_ffmpeg_source(ffmpeg_exe):
    lower = ffmpeg_exe.lower()
    if "winget" in lower:
        return "ffmpeg.exe (winget)"
    return os.path.basename(ffmpeg_exe) or "ffmpeg.exe"


def _convert_mp4_to_gif(mp4_path, *, version, ffmpeg_exe, fps, width, scale_flags):
    output_gif = os.path.splitext(mp4_path)[0] + ".gif"
    if os.path.exists(output_gif):
        print(f"{version} | [mp4.gif] skip (exists): {output_gif}")
        return {"status": "exists", "output_gif": output_gif}

    started_at = time.time()
    try:
        mp4_size_mb = os.path.getsize(mp4_path) / (1024 * 1024)
    except OSError:
        mp4_size_mb = None

    with tempfile.NamedTemporaryFile(prefix="palette_", suffix=".png", delete=False) as tmp:
        palette_path = tmp.name

    try:
        palettegen = [
            ffmpeg_exe,
            "-y",
            "-i",
            mp4_path,
            "-vf",
            f"fps={fps},scale={width}:-1:flags={scale_flags},palettegen",
            palette_path,
        ]
        result = subprocess.run(palettegen, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"{version} | [mp4.gif] palettegen failed: {mp4_path}")
            return {"status": "failed", "output_gif": None}

        paletteuse = [
            ffmpeg_exe,
            "-y",
            "-i",
            mp4_path,
            "-i",
            palette_path,
            "-lavfi",
            f"fps={fps},scale={width}:-1:flags={scale_flags}[x];[x][1:v]paletteuse",
            output_gif,
        ]
        result = subprocess.run(paletteuse, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"{version} | [mp4.gif] paletteuse failed: {mp4_path}")
            return {"status": "failed", "output_gif": None}

        try:
            gif_size_mb = os.path.getsize(output_gif) / (1024 * 1024)
        except OSError:
            gif_size_mb = None
        elapsed = time.time() - started_at
        if mp4_size_mb is not None and gif_size_mb is not None:
            print(
                f"{version} | [mp4.gif] ✅ Success: {mp4_size_mb:.2f} MB -> {gif_size_mb:.2f} MB "
                f"({elapsed:.2f} sec)"
            )
        else:
            print(f"{version} | [mp4.gif] converted: {mp4_path} -> {output_gif}")
        return {"status": "converted", "output_gif": output_gif}
    finally:
        try:
            if os.path.exists(palette_path):
                os.remove(palette_path)
        except OSError:
            pass


def _try_delete_source_mp4(mp4_path, *, version):
    try:
        os.remove(mp4_path)
        print(f"{version} | [mp4.gif] deleted source: {mp4_path}")
        return True
    except OSError as exc:
        print(f"{version} | [mp4.gif] source delete failed: {mp4_path} | {exc}")
        return False


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
    gif_seen = set(gif_queue)

    if mp4_paths:
        ffmpeg_exe = _resolve_ffmpeg_executable()
        if not ffmpeg_exe:
            print(f"{version} | [mp4.gif] ffmpeg not found; skipping {len(mp4_paths)} MP4 file(s)")
        else:
            profile = str(getattr(gif_cfg.mp4_gif, "profile", "fast")).strip().lower()
            if profile == "quality":
                fps = int(getattr(gif_cfg.mp4_gif, "quality_fps", 12))
                width = int(getattr(gif_cfg.mp4_gif, "quality_width", 720))
                scale_flags = str(getattr(gif_cfg.mp4_gif, "quality_scale_flags", "lanczos"))
            else:
                profile = "fast"
                fps = int(getattr(gif_cfg.mp4_gif, "fast_fps", 8))
                width = int(getattr(gif_cfg.mp4_gif, "fast_width", 540))
                scale_flags = str(getattr(gif_cfg.mp4_gif, "fast_scale_flags", "bicubic"))

            print(f"{version} | [mp4.gif] ffmpeg={_describe_ffmpeg_source(ffmpeg_exe)}")
            print(f"{version} | [mp4.gif] profile={profile} fps={fps} width={width} scale={scale_flags}")
            print(f"{version} | [mp4.gif] converting {len(mp4_paths)} MP4 file(s)")
            converted_count = 0
            exists_count = 0
            failed_count = 0
            deleted_source_count = 0
            delete_failed_count = 0
            delete_after_success = bool(getattr(gif_cfg.mp4_gif, "delete_source_after_success", True))
            for mp4_path in mp4_paths:
                convert_result = _convert_mp4_to_gif(
                    mp4_path,
                    version=version,
                    ffmpeg_exe=ffmpeg_exe,
                    fps=fps,
                    width=width,
                    scale_flags=scale_flags,
                )
                if not convert_result:
                    failed_count += 1
                    continue

                status = convert_result.get("status")
                gif_out = convert_result.get("output_gif")
                if status == "converted":
                    converted_count += 1
                elif status == "exists":
                    exists_count += 1
                else:
                    failed_count += 1
                    continue

                if not gif_out:
                    failed_count += 1
                    continue

                # Route converted GIF into existing heavy-compression stage only when oversized.
                try:
                    size_mb = os.path.getsize(gif_out) / (1024 * 1024)
                    if size_mb > gif_cfg.targets.min_process_size_mb and gif_out not in gif_seen:
                        gif_queue.append(gif_out)
                        gif_seen.add(gif_out)
                except OSError:
                    pass

                if delete_after_success:
                    deleted = _try_delete_source_mp4(mp4_path, version=version)
                    if deleted:
                        deleted_source_count += 1
                    else:
                        delete_failed_count += 1

            print(
                f"{version} | [mp4.gif] summary: converted={converted_count} "
                f"exists={exists_count} failed={failed_count} "
                f"source_deleted={deleted_source_count} source_delete_failed={delete_failed_count}"
            )

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
