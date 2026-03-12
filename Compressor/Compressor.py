import io, json, os, subprocess, time
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageSequence, UnidentifiedImageError

start_time = time.time()


@dataclass(frozen=True)
class JPGConfig:
    target_size: int = 999 * 1024
    quality_max: int = 95


@dataclass(frozen=True)
class GIFConfig:
    target_min_mb: float = 13.5
    target_max_mb: float = 14.99
    preferred_min_mb: float = 13.8
    preferred_max_mb: float = 14.6
    max_safe_iterations: int = 10
    extra_palette: int = 4
    min_process_size_mb: float = 15.0
    max_scale_step_ratio: float = 0.15


@dataclass(frozen=True)
class AppConfig:
    version: str = "Compressor v8.53.0"
    root_folder_path: str = r"C:\other\lab\pic"
    stats_file: str = field(default_factory=lambda: os.path.join(os.path.dirname(__file__), "CompressorStats.JSON"))
    jpg: JPGConfig = field(default_factory=JPGConfig)
    gif: GIFConfig = field(default_factory=GIFConfig)


CONFIG = AppConfig()

# Backward-compatible aliases
ROOT_FOLDER_PATH = CONFIG.root_folder_path
VERSION = CONFIG.version
STATS_FILE = CONFIG.stats_file
TARGET_SIZE = CONFIG.jpg.target_size
QUALITY_MAX = CONFIG.jpg.quality_max


def process_images(root_folder_path):
    """Convert PNG -> JPG, then compress oversized JPGs."""
    worked = False

    for folder_path, _, filenames in os.walk(root_folder_path):
        for filename in filenames:
            if not filename.lower().endswith(".png"):
                continue

            if not worked:
                print(f"{VERSION} | Starting PNG->JPG conversion and compression")
                worked = True

            png_path = os.path.join(folder_path, filename)
            jpg_path = os.path.join(folder_path, filename.rsplit(".", 1)[0] + ".jpg")

            try:
                with Image.open(png_path) as img:
                    png_size = os.path.getsize(png_path)
                    print(f"{VERSION} | Initial File: {png_path}")
                    print(f"{VERSION} | WxH={img.width}x{img.height} | Size={png_size/1024:.2f} KB")

                    img = img.convert("RGB")
                    img.save(jpg_path, "JPEG", quality=100, optimize=True, progressive=True)

                jpg_size = os.path.getsize(jpg_path)
                print(f"{VERSION} | Final JPG Converted: {jpg_path}")
                print(f"{VERSION} | Size={jpg_size/1024:.2f} KB")

                os.remove(png_path)

                if jpg_size <= TARGET_SIZE:
                    print(
                        f"{VERSION} | ✅ Success: {png_size/1024:.2f} KB -> {jpg_size/1024:.2f} KB "
                        "(no further compression needed)"
                    )
                    continue

                compress_until_under_target(jpg_path)

            except UnidentifiedImageError:
                print(f"{VERSION} | Skipped corrupted PNG: {png_path}")

    for folder_path, _, filenames in os.walk(root_folder_path):
        for filename in filenames:
            if filename.lower().endswith((".jpg", ".jpeg")):
                jpg_path = os.path.join(folder_path, filename)
                if os.path.getsize(jpg_path) > TARGET_SIZE:
                    if not worked:
                        print(f"{VERSION} | Starting PNG->JPG conversion and compression")
                        worked = True
                    compress_until_under_target(jpg_path)


def compress_until_under_target(path, target_size=TARGET_SIZE):
    local_version = "Converter to JPG v2.4.4"
    started_at = time.time()

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            quality = QUALITY_MAX
            resize_count = 0

            init_size = os.path.getsize(path)
            print(f"{local_version} | Initial File: {path}")
            print(
                f"{local_version} | WxH={img.width}x{img.height} | Quality={quality} "
                f"| Size={init_size/1024:.2f} KB | Target={target_size/1024:.0f} KB"
            )

            if init_size <= target_size:
                print(f"{local_version} | ✅ Already under target, no compression needed")
                return

            while True:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
                file_size = len(buf.getvalue())

                if file_size <= target_size:
                    with open(path, "wb") as f:
                        f.write(buf.getvalue())
                    elapsed = time.time() - started_at
                    print(
                        f"{local_version} | ✅ Success: {init_size/1024:.2f} KB -> {file_size/1024:.2f} KB "
                        f"| Quality={quality} | Resized {resize_count} times"
                    )
                    print(f"{local_version} | Finished in {elapsed:.2f} sec")
                    return

                correction = (target_size / file_size) ** 0.5
                if quality <= 50:
                    new_w = max(1, int(img.width * correction))
                    new_h = max(1, int(img.height * correction))
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    resize_count += 1
                    quality = QUALITY_MAX
                    print(f"{local_version} | Step {resize_count} | Resized to {new_w}x{new_h}, reset quality={quality}")
                    continue

                quality = max(50, int(quality * correction))
                print(f"{local_version} | Step {resize_count+1} | Quality={quality}")

    except UnidentifiedImageError:
        print(f"{local_version} | Skipped corrupted file: {path}")


class CompressorStatsManager:
    def __init__(self, stats_file):
        self.stats_file = stats_file
        self.stats = []
        self._load_stats()

    def _load_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    self.stats = json.load(f)
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
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
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
        candidates = []
        for entry in self.stats:
            if abs(entry["palette"] - palette) > 8:
                continue

            width_diff = abs(entry["width"] - width) / width
            height_diff = abs(entry["height"] - height) / height
            frame_diff = abs(entry["frames"] - frames) / frames

            if width_diff <= max_diff_ratio and height_diff <= max_diff_ratio and frame_diff <= max_diff_ratio:
                if entry.get("scale", 0) > 0:
                    candidates.append(entry["scale"])

        return (sum(candidates) / len(candidates)) if candidates else None


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


def compress_med_cut(frames, durations, palette_colors, executor, final=False):
    args = [(fr, palette_colors) for fr in frames]
    frames_q = list(executor.map(process_frame_med_cut, args))
    return save_gif(frames_q, durations, optimize=final)


def _scale_key(scale):
    return round(scale, 4)


def _clamp_prediction(predicted_medcut, fast_size):
    min_pred = max(fast_size * 0.3, 0.1)
    max_pred = fast_size * 1.6
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
    resized_frames = resize_frames(frames_raw, width, height, scale)
    key = _scale_key(scale)

    if key in fast_cache:
        fast_size = fast_cache[key]
        print(f"{VERSION} | Step {iteration+1}.0 ({stage_tag}, cached) | FASTOCTREE={fast_size:.2f} MB")
        return resized_frames, fast_size

    step_start = time.time()
    frames_fast = [process_frame_fast_octree(fr, palette_limit) for fr in resized_frames]
    _, fast_size = save_gif(frames_fast, durations, optimize=False)
    fast_cache[key] = fast_size
    step_elapsed = time.time() - step_start
    print(f"{VERSION} | Step {iteration+1}.0 ({stage_tag}) | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
    return resized_frames, fast_size


def _choose_initial_scale(stats_mgr, palette_limit, width, height, total_frames, init_size, target_mid, bias_factor):
    avg_scale = stats_mgr.average_scale(palette_limit, width, height, total_frames)
    delta_avg = stats_mgr.find_delta(palette_limit, width, height, total_frames)
    neighbor_scale = stats_mgr.neighbor_scale(palette_limit, width, height, total_frames)

    if avg_scale:
        return avg_scale, "stats"
    if neighbor_scale:
        return neighbor_scale, "neighbor stats"
    if delta_avg is not None:
        predicted_medcut = init_size + delta_avg * bias_factor
        scale_from_delta = (target_mid / predicted_medcut) ** 0.5
        return scale_from_delta * 0.95, "delta_avg (conservative)"
    scale_from_formula = (target_mid / (init_size * bias_factor)) ** 0.5
    return scale_from_formula * 0.90, "formula (conservative)"


def _next_scale(scale, low_scale, high_scale, med_cache, target_mid, max_step_ratio):
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


def balanced_compress_gif(input_path, gif_cfg=CONFIG.gif):
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

    workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"{VERSION} | Using {workers} workers for {total_frames} frames")

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
    )

    print(f"{VERSION} | Prediction source: {source} -> initial scale={scale:.3f}")

    low_scale = 0.01
    high_scale = 4.0
    fast_cache = {}
    med_cache = {}

    with ProcessPoolExecutor(max_workers=workers) as executor:
        for iteration in range(gif_cfg.max_safe_iterations):
            resized_frames, fast_size = _run_fastoctree_trial(
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
            if iteration >= 1 and fast_in_preferred:
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, fast_size, scale)
                with open(input_path, "wb") as f:
                    buf_fast, _ = save_gif(resized_frames, durations, optimize=True)
                    f.write(buf_fast.getvalue())
                elapsed = time.time() - started_at
                print(f"{VERSION} | ✅ Success (fast): {init_size:.2f} MB -> {fast_size:.2f} MB (after {iteration+1} iterations, {elapsed:.2f} sec total)")
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
            print(f"{VERSION} | -> Predicted MEDIANCUT={predicted_medcut:.2f} MB | scale={scale:.3f} (source: {source})")

            if iteration == 0 and source != "stats" and fast_size < target_mid * 0.85:
                scale *= 0.92
                print(f"{VERSION} | Pre-correction (iter 0) -> scale={scale:.3f}")
                resized_frames, fast_size = _run_fastoctree_trial(
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

            if source != "stats" and fast_size < target_mid * 0.9:
                adj_scale = scale * (target_mid / (fast_size + 4.0)) ** 0.5 if source == "neighbor stats" else scale
                if abs(adj_scale - scale) < 0.05:
                    scale = adj_scale
                    print(f"{VERSION} | Micro-adjusting scale -> {scale:.3f}")
                    resized_frames, fast_size = _run_fastoctree_trial(
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
            else:
                step_start = time.time()
                buf_med, med_size = compress_med_cut(resized_frames, durations, palette_limit, executor, final=False)
                med_bytes = buf_med.getvalue()
                med_cache[scale_key] = (med_size, med_bytes)
                step_elapsed = time.time() - step_start
                print(f"{VERSION} | Step {iteration+1}.1 | MEDIANCUT={med_size:.2f} MB | finished in {step_elapsed:.2f} sec")

            print(f"{VERSION} | Delta vs FASTOCTREE = {med_size - fast_size:+.2f} MB")

            in_preferred_corridor = (
                iteration >= 1
                and gif_cfg.preferred_min_mb <= med_size <= gif_cfg.preferred_max_mb
            )
            in_target = gif_cfg.target_min_mb <= med_size <= gif_cfg.target_max_mb

            if in_preferred_corridor or in_target:
                stats_mgr.save_stats(palette_limit, width, height, total_frames, fast_size, med_size, scale)
                with open(input_path, "wb") as f:
                    f.write(med_bytes)
                elapsed = time.time() - started_at
                print(
                    f"{VERSION} | ✅ Success: {init_size:.2f} MB -> {med_size:.2f} MB "
                    f"(after {iteration+1} iterations, {elapsed:.2f} sec total)"
                )
                return

            if med_size > gif_cfg.target_max_mb:
                high_scale = scale
            else:
                low_scale = scale

            new_scale = _next_scale(
                scale=scale,
                low_scale=low_scale,
                high_scale=high_scale,
                med_cache=med_cache,
                target_mid=target_mid,
                max_step_ratio=gif_cfg.max_scale_step_ratio,
            )
            print(f"{VERSION} | Next scale={new_scale:.3f} (low={low_scale:.3f}, high={high_scale:.3f})")
            scale = new_scale

    print(f"{VERSION} | Failed to converge after {gif_cfg.max_safe_iterations} iterations")


def process_gifs(root_folder):
    for root, _, files in os.walk(root_folder):
        for file in files:
            if not file.lower().endswith(".gif"):
                continue

            gif_path = os.path.join(root, file)
            size_mb = os.path.getsize(gif_path) / (1024 * 1024)
            if size_mb <= CONFIG.gif.min_process_size_mb:
                continue

            try:
                balanced_compress_gif(gif_path)
            except Exception as e:
                print(f"Error processing {gif_path}: {e}")


if __name__ == "__main__":
    process_images(ROOT_FOLDER_PATH)
    process_gifs(ROOT_FOLDER_PATH)

    print("✅ All PNGs converted/compressed, oversized JPGs shrunk, and oversized GIFs compressed.")

    stats_script = os.path.join(os.path.dirname(__file__), "StatsCompressor.py")
    try:
        subprocess.run(["python", stats_script, STATS_FILE], check=True)
    except Exception as e:
        print(f"StatsCompressor failed: {e}")

    end_time = time.time()
    elapsed = end_time - start_time
    print(f"Total execution time: {elapsed:.2f} sec")
