"""GIF facade module for batch processing and main pipeline delegation."""

import json
import math
import os
import shutil
import subprocess
import time

from gif_main_pipeline import balanced_compress_gif
from video_webp_stats import VideoWebPStatsManager


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


def _resolve_ffprobe_executable(ffmpeg_exe):
    ffprobe_exe = os.path.join(os.path.dirname(ffmpeg_exe), "ffprobe.exe")
    return ffprobe_exe if os.path.exists(ffprobe_exe) else None


def _describe_ffmpeg_source(ffmpeg_exe):
    lower = ffmpeg_exe.lower()
    if "winget" in lower:
        return "ffmpeg.exe (winget)"
    return os.path.basename(ffmpeg_exe) or "ffmpeg.exe"


def _probe_video_metadata(source_path, ffprobe_exe):
    cmd = [
        ffprobe_exe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate:format=duration",
        "-of",
        "json",
        source_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout)
        stream = (payload.get("streams") or [{}])[0]
        fmt = payload.get("format") or {}
        width = int(stream.get("width"))
        height = int(stream.get("height"))
        fps_text = str(stream.get("avg_frame_rate") or "0/1")
        fps_num, fps_den = fps_text.split("/", 1)
        fps = float(fps_num) / max(1.0, float(fps_den))
        duration = float(fmt.get("duration") or 0.0)
        return {
            "width": width,
            "height": height,
            "fps": fps,
            "duration_sec": duration,
        }
    except Exception:
        return None


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


def _probe_video_proxy_size(
    *,
    source_path,
    ffmpeg_exe,
    fps,
    width,
    scale_flags,
    quality,
    compression_level,
):
    proxy_output = os.path.splitext(source_path)[0] + ".probe.tmp.webp"
    if os.path.exists(proxy_output):
        try:
            os.remove(proxy_output)
        except OSError:
            pass

    probe_fps = max(2, int(fps) // 4)
    probe_width = max(128, int(width) // 4)
    result = _encode_video_to_webp(
        source_path=source_path,
        output_webp=proxy_output,
        ffmpeg_exe=ffmpeg_exe,
        fps=probe_fps,
        width=probe_width,
        scale_flags=scale_flags,
        quality=max(20, min(100, int(quality))),
        compression_level=max(1, int(compression_level) - 1),
    )
    if result.returncode != 0:
        try:
            if os.path.exists(proxy_output):
                os.remove(proxy_output)
        except OSError:
            pass
        return None

    try:
        proxy_size = os.path.getsize(proxy_output)
    except OSError:
        proxy_size = None

    try:
        if os.path.exists(proxy_output):
            os.remove(proxy_output)
    except OSError:
        pass

    return {
        "size_bytes": proxy_size,
        "fps": probe_fps,
        "width": probe_width,
    }


def _estimate_video_full_size_mb(*, proxy_probe, proxy_size_bytes, target_width, target_fps, proxy_full_scale_bias):
    proxy_mb = proxy_size_bytes / (1024 * 1024)
    probe_scale = (
        (target_width / max(1, proxy_probe["width"]))
        * (target_fps / max(1.0, proxy_probe["fps"]))
        * float(proxy_full_scale_bias)
    )
    estimated_full_mb = proxy_mb * probe_scale
    return proxy_mb, probe_scale, estimated_full_mb


def _choose_proxy_probe_qualities(current_quality, *, min_quality, max_quality):
    step = 8
    candidates = [
        int(current_quality),
        int(current_quality) - step,
        int(current_quality) + step,
    ]
    result = []
    for quality in candidates:
        quality = max(min_quality, min(max_quality, int(quality)))
        if quality not in result:
            result.append(quality)
    return result


def _estimate_quality_from_proxy_points(points, *, target_mid_mb, min_quality, max_quality):
    if len(points) < 2:
        return None

    ordered = sorted(points, key=lambda item: item["quality"])
    target_mid_mb = max(0.01, float(target_mid_mb))

    def _fit_between(left, right):
        if left["quality"] == right["quality"]:
            return left["quality"]
        left_log = math.log(max(0.01, float(left["estimate_mb"])))
        right_log = math.log(max(0.01, float(right["estimate_mb"])))
        if abs(right_log - left_log) < 1e-9:
            return int(round((left["quality"] + right["quality"]) / 2.0))
        target_log = math.log(target_mid_mb)
        ratio = (target_log - left_log) / (right_log - left_log)
        return int(round(left["quality"] + ratio * (right["quality"] - left["quality"])))

    for left, right in zip(ordered, ordered[1:]):
        left_mb = float(left["estimate_mb"])
        right_mb = float(right["estimate_mb"])
        if (left_mb <= target_mid_mb <= right_mb) or (right_mb <= target_mid_mb <= left_mb):
            return max(min_quality, min(max_quality, _fit_between(left, right)))

    if ordered[-1]["estimate_mb"] < target_mid_mb:
        return max(min_quality, min(max_quality, int(ordered[-1]["quality"]) + 6))

    if ordered[0]["estimate_mb"] > target_mid_mb:
        return max(min_quality, min(max_quality, int(ordered[0]["quality"]) - 6))

    left = ordered[0]
    right = ordered[-1]
    return max(min_quality, min(max_quality, _fit_between(left, right)))


def _run_video_preflight(
    *,
    source_path,
    version,
    ffmpeg_exe,
    target_fps,
    target_width,
    scale_flags,
    start_quality,
    start_width,
    compression_level,
    target_min_mb,
    target_max_mb,
    min_quality,
    min_width,
    max_width,
    resize_step_ratio,
    max_attempts,
    proxy_full_scale_bias,
    preflight_max_attempts,
    preflight_close_ratio,
):
    target_mid_mb = (target_min_mb + target_max_mb) / 2.0
    current_quality = max(min_quality, min(100, int(start_quality)))
    current_width = max(min_width, int(start_width))
    best_estimate_mb = None
    best_point = None
    probed_points = []
    probed_keys = set()

    def _set_quality_window(seed_quality, *, step=4):
        seed_quality = max(min_quality, min(100, int(seed_quality)))
        return seed_quality, max(min_quality, seed_quality - step), min(100, seed_quality + step)

    for probe_attempt in range(1, max(1, int(preflight_max_attempts)) + 1):
        probe_qualities = _choose_proxy_probe_qualities(current_quality, min_quality=min_quality, max_quality=100)
        round_points = []
        for probe_quality in probe_qualities:
            key = (current_width, probe_quality)
            if key in probed_keys:
                continue
            probed_keys.add(key)
            proxy_probe = _probe_video_proxy_size(
                source_path=source_path,
                ffmpeg_exe=ffmpeg_exe,
                fps=target_fps,
                width=current_width,
                scale_flags=scale_flags,
                quality=probe_quality,
                compression_level=compression_level,
            )
            if not proxy_probe or not proxy_probe.get("size_bytes"):
                print(f"{version} | [video.webp] preflight failed: proxy encode unavailable")
                continue

            proxy_mb, probe_scale, estimated_full_mb = _estimate_video_full_size_mb(
                proxy_probe=proxy_probe,
                proxy_size_bytes=proxy_probe["size_bytes"],
                target_width=current_width,
                target_fps=target_fps,
                proxy_full_scale_bias=proxy_full_scale_bias,
            )
            point = {
                "quality": probe_quality,
                "estimate_mb": estimated_full_mb,
                "proxy_mb": proxy_mb,
                "width": current_width,
                "fps": proxy_probe["fps"],
                "scale": probe_scale,
            }
            probed_points.append(point)
            round_points.append(point)
            print(
                f"{version} | [video.webp] preflight={probe_attempt} probe={proxy_mb:.2f} MB "
                f"proxy={proxy_probe['width']}x? fps={proxy_probe['fps']} scale={probe_scale:.1f} "
                f"est={estimated_full_mb:.2f} MB q={probe_quality} width={current_width}"
            )

            if target_min_mb <= estimated_full_mb <= target_max_mb:
                best_estimate_mb = estimated_full_mb
                best_point = point
                break

        if best_point is not None:
            break

        if not probed_points:
            break

        best_point = min(probed_points, key=lambda item: abs(item["estimate_mb"] - target_mid_mb))
        best_estimate_mb = best_point["estimate_mb"]

        next_quality = _estimate_quality_from_proxy_points(
            probed_points,
            target_mid_mb=target_mid_mb,
            min_quality=min_quality,
            max_quality=100,
        )
        if next_quality is not None:
            current_quality = next_quality

        best_point_is_near = abs(best_estimate_mb - target_mid_mb) / max(target_mid_mb, 0.01) <= float(preflight_close_ratio)
        if best_point_is_near:
            break

        if best_estimate_mb < target_min_mb and current_width < max_width and current_quality >= 100:
            current_width = min(max_width, current_width + max(1, current_width // 20))
            current_quality, _, _ = _set_quality_window(current_quality + 2)
            probed_points = []
            probed_keys = set()
            best_point = None
            best_estimate_mb = None
            continue

        if best_estimate_mb > target_max_mb and current_width > min_width and current_quality <= min_quality:
            current_width = max(min_width, int(current_width * float(resize_step_ratio)))
            current_quality, _, _ = _set_quality_window(current_quality - 2)
            probed_points = []
            probed_keys = set()
            best_point = None
            best_estimate_mb = None
            continue

        if len(round_points) == 0:
            break

    if best_estimate_mb is None or best_point is None:
        return None

    return {
        "quality": int(best_point["quality"]),
        "width": int(best_point["width"]),
        "source": (
            f"preflight proxy (attempts={max(1, int(preflight_max_attempts))}, est={best_estimate_mb:.2f} MB, "
            f"q={int(best_point['quality'])}, width={int(best_point['width'])})"
        ),
        "estimate_mb": best_estimate_mb,
        "at_hard_limits": bool(
            (best_estimate_mb < target_min_mb and int(best_point["quality"]) >= 100 and int(best_point["width"]) >= int(max_width))
            or (best_estimate_mb > target_max_mb and int(best_point["quality"]) <= int(min_quality) and int(best_point["width"]) <= int(min_width))
        ),
        "converged": bool(
            target_min_mb <= best_estimate_mb <= target_max_mb
            and abs(best_estimate_mb - target_mid_mb) / max(target_mid_mb, 0.01) <= float(preflight_close_ratio)
        ),
    }


def _convert_video_to_webp(
    source_path,
    *,
    version,
    ffmpeg_exe,
    fps,
    width,
    scale_flags,
    initial_quality,
    initial_width,
    compression_level,
    target_min_mb,
    target_max_mb,
    min_quality,
    min_width,
    max_width,
    resize_step_ratio,
    max_attempts,
    stats_mgr,
    video_meta,
    proxy_full_scale_bias,
    preflight_max_attempts,
    preflight_close_ratio,
    skip_preflight,
    continue_after_first_target,
    target_mid_tolerance_ratio,
):
    allow_in_target_refine = bool(continue_after_first_target and not skip_preflight)

    def _reset_quality_window(seed_quality, *, step=4):
        seed_quality = max(min_quality, min(100, int(seed_quality)))
        lower = max(min_quality, seed_quality - step)
        upper = min(100, seed_quality + step)
        return seed_quality, lower, upper

    output_webp = os.path.splitext(source_path)[0] + ".webp"
    temp_output_webp = os.path.splitext(output_webp)[0] + ".tmp.webp"
    if os.path.exists(temp_output_webp):
        try:
            os.remove(temp_output_webp)
        except OSError:
            pass

    started_at = time.time()
    try:
        src_size_mb = os.path.getsize(source_path) / (1024 * 1024)
    except OSError:
        src_size_mb = None

    current_quality = max(min_quality, min(100, int(initial_quality)))
    current_width = max(min_width, int(initial_width))
    max_width = max(min_width, int(max_width))
    target_bytes = int(float(target_max_mb) * 1024 * 1024)
    target_min_bytes = int(float(target_min_mb) * 1024 * 1024)
    target_mid_bytes = int(((float(target_min_mb) + float(target_max_mb)) / 2.0) * 1024 * 1024)
    best_size = None
    lower_q = min_quality
    upper_q = 100
    had_target_hit = False
    best_target_size = None
    best_target_output_webp = os.path.splitext(output_webp)[0] + ".best.tmp.webp"
    if os.path.exists(best_target_output_webp):
        try:
            os.remove(best_target_output_webp)
        except OSError:
            pass

    preflight_plan = None
    if skip_preflight:
        print(f"{version} | [video.webp] startup=exact stats match; preflight skipped")
    else:
        preflight_plan = _run_video_preflight(
            source_path=source_path,
            version=version,
            ffmpeg_exe=ffmpeg_exe,
            target_fps=fps,
            target_width=current_width,
            scale_flags=scale_flags,
            start_quality=current_quality,
            start_width=current_width,
            compression_level=compression_level,
            target_min_mb=target_min_mb,
            target_max_mb=target_max_mb,
            min_quality=min_quality,
            min_width=min_width,
            max_width=max_width,
            resize_step_ratio=resize_step_ratio,
            max_attempts=max_attempts,
            proxy_full_scale_bias=proxy_full_scale_bias,
            preflight_max_attempts=preflight_max_attempts,
            preflight_close_ratio=preflight_close_ratio,
        )
        if preflight_plan:
            current_quality = int(preflight_plan["quality"])
            current_width = int(preflight_plan["width"])
            print(f"{version} | [video.webp] startup={preflight_plan['source']}")
            if not preflight_plan.get("converged"):
                if preflight_plan.get("at_hard_limits"):
                    print(
                        f"{version} | [video.webp] preflight not converged but at hard limits; "
                        f"proceeding with full encode (estimate={preflight_plan['estimate_mb']:.2f} MB, "
                        f"target={target_min_mb:.2f}-{target_max_mb:.2f} MB)"
                    )
                else:
                    print(
                        f"{version} | [video.webp] preflight not converged; skipping full encode "
                        f"(estimate={preflight_plan['estimate_mb']:.2f} MB, target={target_min_mb:.2f}-{target_max_mb:.2f} MB)"
                    )
                    return {"status": "failed", "output_webp": None}

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        attempt_started_at = time.time()
        result = _encode_video_to_webp(
            source_path=source_path,
            output_webp=temp_output_webp,
            ffmpeg_exe=ffmpeg_exe,
            fps=fps,
            width=current_width,
            scale_flags=scale_flags,
            quality=current_quality,
            compression_level=compression_level,
        )
        if result.returncode != 0:
            try:
                if os.path.exists(temp_output_webp):
                    os.remove(temp_output_webp)
            except OSError:
                pass
            stderr = (result.stderr or "").strip()
            if stderr:
                print(
                    f"{version} | [video.webp] encode failed: {source_path} (attempt={attempt}) | {stderr.splitlines()[-1]}"
                )
            else:
                print(f"{version} | [video.webp] encode failed: {source_path} (attempt={attempt})")
            return {"status": "failed", "output_webp": None}

        try:
            size_bytes = os.path.getsize(temp_output_webp)
        except OSError:
            print(f"{version} | [video.webp] output missing after encode: {temp_output_webp}")
            return {"status": "failed", "output_webp": None}

        try:
            os.replace(temp_output_webp, output_webp)
        except OSError:
            print(f"{version} | [video.webp] failed to replace output: {output_webp}")
            return {"status": "failed", "output_webp": None}

        best_size = size_bytes if best_size is None else min(best_size, size_bytes)
        size_mb = size_bytes / (1024 * 1024)
        attempt_elapsed = time.time() - attempt_started_at
        print(
            f"{version} | [video.webp] attempt={attempt} q={current_quality} width={current_width} "
            f"-> {size_mb:.2f} MB | elapsed={attempt_elapsed:.2f} sec"
        )
        if target_min_bytes <= size_bytes <= target_bytes:
            had_target_hit = True
            best_target_size = size_bytes
            try:
                shutil.copyfile(output_webp, best_target_output_webp)
            except OSError:
                pass

            should_finalize = True
            if allow_in_target_refine:
                mid_miss_ratio = abs(size_bytes - target_mid_bytes) / max(target_mid_bytes, 1)
                if size_bytes < target_mid_bytes:
                    lower_q = max(lower_q, current_quality)
                elif size_bytes > target_mid_bytes:
                    upper_q = min(upper_q, current_quality)

                if attempt < max(1, int(max_attempts)) and mid_miss_ratio > float(target_mid_tolerance_ratio):
                    next_quality = None
                    if upper_q - lower_q > 1:
                        next_quality = (lower_q + upper_q) // 2
                    if next_quality is not None and next_quality != current_quality:
                        print(
                            f"{version} | [video.webp] in-target refine: q={current_quality} "
                            f"mid_miss={mid_miss_ratio*100:.2f}% -> next_q={next_quality}"
                        )
                        current_quality = next_quality
                        should_finalize = False

            if not should_finalize:
                continue

            elapsed = time.time() - started_at
            if src_size_mb is not None:
                print(
                    f"{version} | [video.webp] ✅ Success: {src_size_mb:.2f} MB -> {size_mb:.2f} MB "
                    f"({elapsed:.2f} sec)"
                )
            else:
                print(f"{version} | [video.webp] ✅ Success: {output_webp} ({elapsed:.2f} sec)")
            if stats_mgr and video_meta:
                stats_mgr.save_attempt(
                    profile=video_meta["profile"],
                    source_width=video_meta["width"],
                    source_height=video_meta["height"],
                    source_fps=video_meta["fps"],
                    source_duration_sec=video_meta["duration_sec"],
                    source_size_mb=src_size_mb or 0.0,
                    quality=current_quality,
                    width=current_width,
                    result_size_mb=size_mb,
                    encode_sec=elapsed,
                    attempts=attempt,
                    success=True,
                )
            try:
                if os.path.exists(best_target_output_webp):
                    os.remove(best_target_output_webp)
            except OSError:
                pass
            return {"status": "converted", "output_webp": output_webp}

        if size_bytes < target_min_bytes:
            lower_q = max(lower_q, current_quality)
        else:
            upper_q = min(upper_q, current_quality)

        if upper_q - lower_q > 1:
            next_quality = (lower_q + upper_q) // 2
            if next_quality != current_quality:
                current_quality = next_quality
                continue

        break

    elapsed = time.time() - started_at
    if had_target_hit:
        try:
            if os.path.exists(best_target_output_webp):
                os.replace(best_target_output_webp, output_webp)
        except OSError:
            pass
        restored_mb = (best_target_size / (1024 * 1024)) if best_target_size else -1
        print(
            f"{version} | [video.webp] restored best in-target result after refinement; "
            f"size={restored_mb:.2f} MB"
        )
        if stats_mgr and video_meta and best_target_size is not None:
            stats_mgr.save_attempt(
                profile=video_meta["profile"],
                source_width=video_meta["width"],
                source_height=video_meta["height"],
                source_fps=video_meta["fps"],
                source_duration_sec=video_meta["duration_sec"],
                source_size_mb=src_size_mb or 0.0,
                quality=current_quality,
                width=current_width,
                result_size_mb=restored_mb,
                encode_sec=elapsed,
                attempts=max(1, int(max_attempts)),
                success=True,
            )
        return {"status": "converted", "output_webp": output_webp}

    final_mb = (best_size / (1024 * 1024)) if best_size else -1
    try:
        if os.path.exists(temp_output_webp):
            os.remove(temp_output_webp)
    except OSError:
        pass
    try:
        if os.path.exists(best_target_output_webp):
            os.remove(best_target_output_webp)
    except OSError:
        pass
    if stats_mgr and video_meta and best_size is not None:
        stats_mgr.save_attempt(
            profile=video_meta["profile"],
            source_width=video_meta["width"],
            source_height=video_meta["height"],
            source_fps=video_meta["fps"],
            source_duration_sec=video_meta["duration_sec"],
            source_size_mb=src_size_mb or 0.0,
            quality=current_quality,
            width=current_width,
            result_size_mb=final_mb,
            encode_sec=elapsed,
            attempts=max(1, int(max_attempts)),
            success=False,
        )
    print(
        f"{version} | [video.webp] failed to reach {target_min_mb:.2f}-{target_max_mb:.2f} MB; "
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
            ffprobe_exe = _resolve_ffprobe_executable(ffmpeg_exe)
            stats_mgr_video = VideoWebPStatsManager(stats_file, version)
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

            target_min_mb = float(getattr(gif_cfg.mp4_gif, "target_min_mb", 13.5))
            target_max_mb = float(getattr(gif_cfg.mp4_gif, "target_max_mb", 14.99))
            min_quality = int(getattr(gif_cfg.mp4_gif, "webp_min_quality", 42))
            min_width = int(getattr(gif_cfg.mp4_gif, "webp_min_width", 420))
            max_width = int(getattr(gif_cfg.mp4_gif, "quality_width", width))
            resize_step_ratio = float(getattr(gif_cfg.mp4_gif, "webp_resize_step_ratio", 0.90))
            max_attempts = int(getattr(gif_cfg.mp4_gif, "webp_max_attempts", 10))

            print(f"{version} | [video.webp] ffmpeg={_describe_ffmpeg_source(ffmpeg_exe)}")
            print(
                f"{version} | [video.webp] profile={profile} fps={fps} width={width} "
                f"q={webp_quality} level={compression_level} target={target_min_mb:.2f}-{target_max_mb:.2f} MB"
            )
            print(f"{version} | [video.webp] converting {len(mp4_paths)} MP4/MOV file(s)")
            converted_count = 0
            exists_count = 0
            failed_count = 0
            deleted_source_count = 0
            delete_failed_count = 0
            delete_after_success = bool(getattr(gif_cfg.mp4_gif, "delete_source_after_success", True))
            for video_path in mp4_paths:
                print(f"{version} | [video.startup] | Starting file: {video_path}")
                video_meta = None
                if ffprobe_exe:
                    video_meta = _probe_video_metadata(video_path, ffprobe_exe)
                if not video_meta:
                    video_meta = video_meta or {
                        "width": width,
                        "height": width,
                        "fps": float(fps),
                        "duration_sec": 0.0,
                        "profile": profile,
                    }
                else:
                    video_meta["profile"] = profile

                startup_plan = stats_mgr_video.select_startup_plan(
                    source_width=video_meta["width"],
                    source_height=video_meta["height"],
                    source_fps=video_meta["fps"],
                    source_duration_sec=video_meta["duration_sec"],
                    source_size_mb=(os.path.getsize(video_path) / (1024 * 1024)) if os.path.exists(video_path) else 0.0,
                    profile=profile,
                    target_min_mb=target_min_mb,
                    target_max_mb=target_max_mb,
                    default_quality=webp_quality,
                    default_width=width,
                )
                initial_quality = startup_plan["quality"] if startup_plan else webp_quality
                initial_width = startup_plan["width"] if startup_plan else width
                startup_score = float(startup_plan.get("score", 999.0)) if startup_plan else 999.0
                if startup_plan:
                    print(f"{version} | [video.webp] startup={startup_plan['source']}")
                convert_result = _convert_video_to_webp(
                    video_path,
                    version=version,
                    ffmpeg_exe=ffmpeg_exe,
                    fps=fps,
                    width=width,
                    scale_flags=scale_flags,
                    initial_quality=initial_quality,
                    initial_width=initial_width,
                    compression_level=compression_level,
                    target_min_mb=target_min_mb,
                    target_max_mb=target_max_mb,
                    min_quality=min_quality,
                    min_width=min_width,
                    max_width=max_width,
                    resize_step_ratio=resize_step_ratio,
                    max_attempts=max_attempts,
                    stats_mgr=stats_mgr_video,
                    video_meta=video_meta,
                    proxy_full_scale_bias=float(getattr(gif_cfg.mp4_gif, "proxy_full_scale_bias", 2.0)),
                    preflight_max_attempts=int(getattr(gif_cfg.mp4_gif, "webp_preflight_max_attempts", 4)),
                    preflight_close_ratio=float(getattr(gif_cfg.mp4_gif, "webp_preflight_close_ratio", 0.10)),
                    skip_preflight=(
                        bool(getattr(gif_cfg.mp4_gif, "webp_skip_preflight_on_exact_stats", True))
                        and startup_plan is not None
                        and startup_score <= float(getattr(gif_cfg.mp4_gif, "webp_exact_stats_score_threshold", 0.001))
                    ),
                    continue_after_first_target=bool(getattr(gif_cfg.mp4_gif, "webp_continue_after_first_target", True)),
                    target_mid_tolerance_ratio=float(getattr(gif_cfg.mp4_gif, "webp_target_mid_tolerance_ratio", 0.04)),
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
