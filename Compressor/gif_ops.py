import io

from PIL import Image


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

    tasks_per_worker = max(1, int(gif_cfg.runtime.process_pool_tasks_per_worker))
    return max(1, frame_count // max(1, workers * tasks_per_worker))


def _sample_probe_frame_limit(total_frames, gif_cfg):
    max_frames = max(2, int(gif_cfg.sample_probe.sample_probe_max_frames))
    min_frames = max(2, min(max_frames, int(gif_cfg.sample_probe.sample_probe_min_frames)))

    if total_frames <= min_frames:
        return total_frames

    adaptive_frames = int((total_frames ** 0.5) * 1.25)
    return max(min_frames, min(max_frames, adaptive_frames))


def temporal_reduce(frames, durations, keep_every):
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
