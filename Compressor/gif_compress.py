"""GIF facade module for batch processing and main pipeline delegation."""

import os
import shutil
import subprocess
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


def _encode_video_to_webp(
    *,
    source_path,
    output_webp,
    ffmpeg_exe,
    fps,
    width,
    scale_flags,
    quality,
    compression_level,
):
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        source_path,
        "-vf",
        f"fps={fps},scale={width}:-1:flags={scale_flags}",
        "-an",
        "-loop",
        "0",
        "-c:v",
        "libwebp",
        "-lossless",
        "0",
        "-q:v",
        str(quality),
        "-compression_level",
        str(compression_level),
        output_webp,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _convert_video_to_webp(
    source_path,
    *,
    version,
    ffmpeg_exe,
    fps,
    width,
    scale_flags,
    initial_quality,
    compression_level,
    target_max_mb,
    min_quality,
    min_width,
    resize_step_ratio,
    max_attempts,
):
    output_webp = os.path.splitext(source_path)[0] + ".webp"
    if os.path.exists(output_webp):
        print(f"{version} | [video.webp] skip (exists): {output_webp}")
        return {"status": "exists", "output_webp": output_webp}

    started_at = time.time()
    try:
        src_size_mb = os.path.getsize(source_path) / (1024 * 1024)
    except OSError:
        src_size_mb = None

    current_quality = max(min_quality, min(100, int(initial_quality)))
    current_width = max(min_width, int(width))
    target_bytes = int(float(target_max_mb) * 1024 * 1024)
    best_size = None

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        attempt_started_at = time.time()
        result = _encode_video_to_webp(
            source_path=source_path,
            output_webp=output_webp,
            ffmpeg_exe=ffmpeg_exe,
            fps=fps,
            width=current_width,
            scale_flags=scale_flags,
            quality=current_quality,
            compression_level=compression_level,
        )
        if result.returncode != 0:
            print(f"{version} | [video.webp] encode failed: {source_path} (attempt={attempt})")
            return {"status": "failed", "output_webp": None}

        try:
            size_bytes = os.path.getsize(output_webp)
        except OSError:
            print(f"{version} | [video.webp] output missing after encode: {output_webp}")
            return {"status": "failed", "output_webp": None}

        best_size = size_bytes if best_size is None else min(best_size, size_bytes)
        size_mb = size_bytes / (1024 * 1024)
        attempt_elapsed = time.time() - attempt_started_at
        print(
            f"{version} | [video.webp] attempt={attempt} q={current_quality} width={current_width} "
            f"-> {size_mb:.2f} MB | elapsed={attempt_elapsed:.2f} sec"
        )
        if size_bytes <= target_bytes:
            elapsed = time.time() - started_at
            if src_size_mb is not None:
                print(
                    f"{version} | [video.webp] ✅ Success: {src_size_mb:.2f} MB -> {size_mb:.2f} MB "
                    f"({elapsed:.2f} sec)"
                )
            else:
                print(f"{version} | [video.webp] ✅ Success: {output_webp} ({elapsed:.2f} sec)")
            return {"status": "converted", "output_webp": output_webp}

        overflow_ratio = size_bytes / max(1, target_bytes)
        if current_quality > min_quality:
            quality_drop = max(3, min(14, int((overflow_ratio - 1.0) * 20)))
            next_quality = max(min_quality, current_quality - quality_drop)
            if next_quality != current_quality:
                current_quality = next_quality
                continue

        if current_width > min_width:
            next_width = max(min_width, int(current_width * float(resize_step_ratio)))
            if next_width >= current_width:
                next_width = current_width - 1
            current_width = max(min_width, next_width)
            continue

        break

    elapsed = time.time() - started_at
    final_mb = (best_size / (1024 * 1024)) if best_size else -1
    print(
        f"{version} | [video.webp] failed to reach <= {target_max_mb:.2f} MB; "
        f"best={final_mb:.2f} MB ({elapsed:.2f} sec)"
    )
    return {"status": "failed", "output_webp": output_webp if os.path.exists(output_webp) else None}


def _try_delete_source_video(video_path, *, version):
    try:
        os.remove(video_path)
        print(f"{version} | [video.webp] deleted source: {video_path}")
        return True
    except OSError as exc:
        print(f"{version} | [video.webp] source delete failed: {video_path} | {exc}")
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

    if mp4_paths:
        ffmpeg_exe = _resolve_ffmpeg_executable()
        if not ffmpeg_exe:
            print(f"{version} | [video.webp] ffmpeg not found; skipping {len(mp4_paths)} MP4/MOV file(s)")
        else:
            profile = str(getattr(gif_cfg.mp4_gif, "profile", "fast")).strip().lower()
            if profile == "quality":
                fps = int(getattr(gif_cfg.mp4_gif, "quality_fps", 12))
                width = int(getattr(gif_cfg.mp4_gif, "quality_width", 840))
                scale_flags = str(getattr(gif_cfg.mp4_gif, "quality_scale_flags", "lanczos"))
                webp_quality = int(getattr(gif_cfg.mp4_gif, "quality_webp_quality", 84))
                compression_level = int(getattr(gif_cfg.mp4_gif, "quality_webp_compression_level", 5))
            else:
                profile = "fast"
                fps = int(getattr(gif_cfg.mp4_gif, "fast_fps", 12))
                width = int(getattr(gif_cfg.mp4_gif, "fast_width", 720))
                scale_flags = str(getattr(gif_cfg.mp4_gif, "fast_scale_flags", "bicubic"))
                webp_quality = int(getattr(gif_cfg.mp4_gif, "fast_webp_quality", 78))
                compression_level = int(getattr(gif_cfg.mp4_gif, "fast_webp_compression_level", 4))

            target_max_mb = float(getattr(gif_cfg.mp4_gif, "target_max_mb", 10.0))
            min_quality = int(getattr(gif_cfg.mp4_gif, "webp_min_quality", 42))
            min_width = int(getattr(gif_cfg.mp4_gif, "webp_min_width", 420))
            resize_step_ratio = float(getattr(gif_cfg.mp4_gif, "webp_resize_step_ratio", 0.90))
            max_attempts = int(getattr(gif_cfg.mp4_gif, "webp_max_attempts", 10))

            print(f"{version} | [video.webp] ffmpeg={_describe_ffmpeg_source(ffmpeg_exe)}")
            print(
                f"{version} | [video.webp] profile={profile} fps={fps} width={width} "
                f"q={webp_quality} level={compression_level} target<={target_max_mb:.2f} MB"
            )
            print(f"{version} | [video.webp] converting {len(mp4_paths)} MP4/MOV file(s)")
            converted_count = 0
            exists_count = 0
            failed_count = 0
            deleted_source_count = 0
            delete_failed_count = 0
            delete_after_success = bool(getattr(gif_cfg.mp4_gif, "delete_source_after_success", True))
            for video_path in mp4_paths:
                convert_result = _convert_video_to_webp(
                    video_path,
                    version=version,
                    ffmpeg_exe=ffmpeg_exe,
                    fps=fps,
                    width=width,
                    scale_flags=scale_flags,
                    initial_quality=webp_quality,
                    compression_level=compression_level,
                    target_max_mb=target_max_mb,
                    min_quality=min_quality,
                    min_width=min_width,
                    resize_step_ratio=resize_step_ratio,
                    max_attempts=max_attempts,
                )
                if not convert_result:
                    failed_count += 1
                    continue

                status = convert_result.get("status")
                if status == "converted":
                    converted_count += 1
                elif status == "exists":
                    exists_count += 1
                else:
                    failed_count += 1
                    continue

                if delete_after_success:
                    deleted = _try_delete_source_video(video_path, version=version)
                    if deleted:
                        deleted_source_count += 1
                    else:
                        delete_failed_count += 1

            print(
                f"{version} | [video.webp] summary: converted={converted_count} "
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
