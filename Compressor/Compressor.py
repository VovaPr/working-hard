import os, time, io, psutil, json
start_time = time.time()
from PIL import Image, ImageSequence, UnidentifiedImageError
from concurrent.futures import ProcessPoolExecutor

# 🔧 Root folder path
ROOT_FOLDER_PATH = r"C:\other\lab\pic"

# -------------------------------
# Global Constants
# -------------------------------
VERSION = "Compressor v8.50.0"
STATS_FILE = os.path.join(os.path.dirname(__file__), "CompressorStats.JSON")

# JPG settings
TARGET_SIZE = 999 * 1024  # Target size: 999 KB
QUALITY_MAX = 95          # Maximum quality for JPG compression

# -------------------------------
# JPG Compression (PNG → JPG + shrink oversized JPGs)
# -------------------------------
def process_images(root_folder_path):
    """Main function: convert PNG → JPG, then compress oversized JPGs."""
    worked = False  # flag to check if any file was processed

    # --- PNG section ---
    for folder_path, _, filenames in os.walk(root_folder_path):
        for filename in filenames:
            if filename.lower().endswith('.png'):
                if not worked:
                    print(f"{VERSION} | Starting PNG→JPG conversion and compression")
                    worked = True

                png_path = os.path.join(folder_path, filename)
                jpg_name = filename.rsplit('.', 1)[0] + '.jpg'
                jpg_path = os.path.join(folder_path, jpg_name)

                try:
                    with Image.open(png_path) as img:
                        png_size = os.path.getsize(png_path)
                        print(f"{VERSION} | Initial File: {png_path}")
                        print(f"{VERSION} | WxH={img.width}x{img.height} | Size={png_size/1024:.2f} KB")

                        img = img.convert('RGB')
                        img.save(jpg_path, 'JPEG', quality=100, optimize=True, progressive=True)
                        jpg_size = os.path.getsize(jpg_path)
                        print(f"{VERSION} | Final JPG Converted: {jpg_path}")
                        print(f"{VERSION} | WxH={img.width}x{img.height} | Quality=100 | Size={jpg_size/1024:.2f} KB")

                    os.remove(png_path)

                    if jpg_size <= TARGET_SIZE:
                        print(f"{VERSION} | ✅ Success: {png_size/1024:.2f} KB → {jpg_size/1024:.2f} KB (no further compression needed)")
                        continue

                except UnidentifiedImageError:
                    print(f"{VERSION} | ❌ Skipped corrupted PNG: {png_path}")

    # --- JPG section ---
    for folder_path, _, filenames in os.walk(root_folder_path):
        for filename in filenames:
            if filename.lower().endswith(('.jpg', '.jpeg')):
                jpg_path = os.path.join(folder_path, filename)
                file_size = os.path.getsize(jpg_path)
                if file_size > TARGET_SIZE:
                    if not worked:
                        print(f"{VERSION} | Starting PNG→JPG conversion and compression")
                        worked = True
                    compress_until_under_target(jpg_path)


def compress_until_under_target(path, target_size=TARGET_SIZE):
    VERSION = "Converter to JPG v2.4.4"
    start_time = time.time()

    try:
        with Image.open(path) as img:
            img = img.convert('RGB')
            quality = QUALITY_MAX
            resize_count = 0

            init_size = os.path.getsize(path)
            print(f"{VERSION} | Initial File: {path}")
            print(f"{VERSION} | WxH={img.width}x{img.height} | Quality={quality} | Size={init_size/1024:.2f} KB | Target={target_size/1024:.0f} KB")

            if init_size <= target_size:
                print(f"{VERSION} | ✅ Already under target, no compression needed")
                return

            while True:
                buf = io.BytesIO()
                img.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
                file_size = len(buf.getvalue())

                if file_size <= target_size:
                    with open(path, "wb") as f:
                        f.write(buf.getvalue())
                    elapsed = time.time() - start_time
                    print(f"{VERSION} | ✅ Success: {init_size/1024:.2f} KB → {file_size/1024:.2f} KB | Quality={quality} | Resized {resize_count} times")
                    print(f"{VERSION} | Finished in {elapsed:.2f} sec")
                    break

                if quality <= 50:
                    correction = (target_size / file_size) ** 0.5
                    new_w = int(img.width * correction)
                    new_h = int(img.height * correction)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    resize_count += 1
                    quality = QUALITY_MAX
                    print(f"{VERSION} | Step {resize_count} | Resized to {new_w}x{new_h}, reset quality={quality}")
                    continue

                correction = (target_size / file_size) ** 0.5
                predicted_quality = max(50, int(quality * correction))
                print(f"{VERSION} | Step {resize_count+1} | Quality={predicted_quality} | Predicted next size ≈ {file_size * (correction**2)/1024:.2f} KB")
                quality = predicted_quality

    except UnidentifiedImageError:
        print(f"{VERSION} | ❌ Skipped corrupted file: {path}")

# ------------------------------
# GIF Compression (only >15 MB)
# ------------------------------
class CompressorStatsManager:
    def __init__(self, stats_file):
        self.stats_file = stats_file
        self.stats = []
        self._load_stats()

    def _load_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r") as f:
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
            "timestamp": time.time()
        }
        self.stats.append(entry)
        try:
            with open(self.stats_file, "w") as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            print(f"{VERSION} | Warning: failed to save stats: {e}")

    def _filter_matches(self, palette, width, height, frames):
        return [
            entry for entry in self.stats
            if entry["palette"] == palette
            and entry["width"] == width
            and entry["height"] == height
            and entry["frames"] == frames
        ]

    def average_scale(self, palette, width, height, frames):
        matches = self._filter_matches(palette, width, height, frames)
        scales = [entry["scale"] for entry in matches if "scale" in entry and entry["scale"] > 0]
        if scales:
            return sum(scales) / len(scales)
        return None

    def find_delta(self, palette, width, height, frames):
        matches = self._filter_matches(palette, width, height, frames)
        deltas = [entry["med_size"] - entry["fast_size"] for entry in matches]
        if deltas:
            return sum(deltas) / len(deltas)
        return None

    def predict_mediancut(self, palette, width, height, frames, fast_size, bias_factor):
        # first try regression if available
        coeff = self.regression_coefficients(palette, width, height, frames)
        if coeff is not None:
            a, b = coeff
            return a * fast_size + b
        # fallback to delta-based estimate
        delta_avg = self.find_delta(palette, width, height, frames)
        if delta_avg is not None:
            return fast_size + delta_avg * bias_factor
        return fast_size * bias_factor

    def regression_coefficients(self, palette, width, height, frames, max_diff_ratio=0.15):
        """
        Compute simple linear regression (med_size = a * fast_size + b) using
        matching stats entries (within tolerance). Return (a,b) or None.
        """
        xs, ys = [], []
        for entry in self.stats:
            # simple feature tolerance
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
        # compute least squares coeffs
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        den = sum((x - x_mean) ** 2 for x in xs)
        if den == 0:
            return None
        a = num / den
        b = y_mean - a * x_mean
        return (a, b)

    def neighbor_scale(self, palette, width, height, frames, max_diff_ratio=0.15):
        """
        Find average scale from neighbor stats if exact match is not available.
        max_diff_ratio defines tolerance for width/height/frames difference (e.g. 15%).
        """
        candidates = []
        for entry in self.stats:
            # Palette tolerance
            if abs(entry["palette"] - palette) > 8:
                continue

            # Relative differences
            width_diff = abs(entry["width"] - width) / width
            height_diff = abs(entry["height"] - height) / height
            frame_diff = abs(entry["frames"] - frames) / frames

            if width_diff <= max_diff_ratio and height_diff <= max_diff_ratio and frame_diff <= max_diff_ratio:
                if "scale" in entry and entry["scale"] > 0:
                    candidates.append(entry["scale"])

        if candidates:
            avg_scale = sum(candidates) / len(candidates)
            return avg_scale
        return None

def process_frame_med_cut(args):
    # Quantize frame using MEDIANCUT method
    frame, width, height, palette_colors = args
    q = frame.quantize(colors=palette_colors, method=Image.MEDIANCUT)
    q.info.pop("transparency", None)
    return q

def process_frame_fast_octree(frame, palette_colors):
    # Quantize frame using FASTOCTREE method
    q = frame.quantize(colors=palette_colors, method=Image.FASTOCTREE)
    q.info.pop("transparency", None)
    return q

def save_gif(frames, durations, optimize=False):
    # Save GIF to memory buffer and return size in MB
    buf = io.BytesIO()
    frames[0].save(
        buf,
        save_all=True,
        append_images=frames[1:],
        loop=0,
        duration=durations,
        disposal=2,
        optimize=optimize,
        format="GIF"
    )
    size_mb = len(buf.getvalue()) / (1024 * 1024)
    return buf, size_mb

def resize_frames(frames_raw, width, height, scale):
    # Resize all frames according to scale factor
    return [fr.resize((int(width*scale), int(height*scale)), Image.LANCZOS) for fr in frames_raw]

def compress_med_cut(frames, durations, palette_colors, executor, final=False):
    # MEDIANCUT quantization for all frames
    args = [(fr, fr.width, fr.height, palette_colors) for fr in frames]
    frames_q = list(executor.map(process_frame_med_cut, args))
    buf, size_mb = save_gif(frames_q, durations, optimize=final)
    return buf, size_mb, frames_q

def balanced_compress_gif(input_path, target_min_mb=13.5, target_max_mb=14.99,
                          max_safe_iterations=10, extra_palette=4):
    """
    - Always run FASTOCTREE first (cheap baseline).
    - Use prediction source: stats, neighbor stats, Δ_avg, or formula.
    - Run MEDIANCUT (optimize=false) to check actual size.
    - If trial result is within target → run MEDIANCUT (optimize=true).
    - If not → adjust scale and repeat.
    """

    start_time = time.time()

    # --- Load GIF frames ---
    frames_raw, durations = [], []
    with Image.open(input_path) as img:
        width, height = img.size
        total_frames = img.n_frames
        colors_first = len(img.getcolors(maxcolors=256*256) or [])
        palette_limit = min(colors_first + extra_palette, 256)

        print(f"{VERSION} | Starting file: {input_path}")
        init_size = os.path.getsize(input_path) / (1024 * 1024)
        print(f"{VERSION} | Initial Size: {init_size:.2f} MB | Frames={total_frames} | Palette={colors_first} | WxH={width}x{height}")

        for frame in ImageSequence.Iterator(img):
            frames_raw.append(frame.convert("RGB"))
            durations.append(frame.info.get("duration", 100))

    workers = max(1, (os.cpu_count() or 4) // 2)
    print(f"{VERSION} | Using {workers} workers for {total_frames} frames")

    # --- Target parameters ---
    target_mid = (target_min_mb + target_max_mb) / 2
    bias_factor = 1.1 + 0.05 * (palette_limit / 256.0)

    stats_mgr = CompressorStatsManager(STATS_FILE)

    # --- Prediction source selection ---
    matches = stats_mgr._filter_matches(palette_limit, width, height, total_frames)
    avg_scale = stats_mgr.average_scale(palette_limit, width, height, total_frames)
    delta_avg = stats_mgr.find_delta(palette_limit, width, height, total_frames)
    neighbor_scale = stats_mgr.neighbor_scale(palette_limit, width, height, total_frames)

    if avg_scale:
        scale = avg_scale
        source = "stats"
    elif neighbor_scale:
        scale = neighbor_scale
        source = "neighbor stats"
    elif delta_avg is not None:
        # use delta_avg to adjust starting scale closer to target
        predicted_medcut = init_size + delta_avg * bias_factor
        scale = (target_mid / predicted_medcut) ** 0.5
        source = "Δ_avg"
    else:
        # no useful stats; start with formula but be prepared to binary-search
        scale = (target_mid / (init_size * bias_factor)) ** 0.5
        source = "formula"

    print(f"{VERSION} | Prediction source: {source} → initial scale={scale:.3f}")

    resized_frames = resize_frames(frames_raw, width, height, scale)

    scale_cache = {}  # remember med_size for each scale tried
    # set wide initial bounds for search
    low_scale = 0.01
    high_scale = 4.0

    fast_cache = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for iteration in range(max_safe_iterations):
            # --- Step X.0 FASTOCTREE ---
            if scale in fast_cache:
                fast_size = fast_cache[scale]
                print(f"{VERSION} | Step {iteration+1}.0 | FASTOCTREE quantization (cached) | FASTOCTREE={fast_size:.2f} MB")
            else:
                step_start = time.time()
                frames_fast = [process_frame_fast_octree(fr, palette_limit) for fr in resized_frames]
                buf_fast, fast_size = save_gif(frames_fast, durations, optimize=False)
                step_elapsed = time.time() - step_start
                print(f"{VERSION} | Step {iteration+1}.0 | FASTOCTREE quantization")
                print(f"{VERSION} | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
                fast_cache[scale] = fast_size

            # --- Prediction ---
            predicted_medcut = stats_mgr.predict_mediancut(
                palette_limit, width, height, total_frames, fast_size, bias_factor
            )
            # clamp prediction to reasonable positive range
            min_pred = max(fast_size * 0.3, 0.1)
            max_pred = fast_size * 1.6
            if predicted_medcut < min_pred or predicted_medcut > max_pred:
                predicted_medcut = max(min(predicted_medcut, max_pred), min_pred)
            print(f"{VERSION} | → Predicted MEDIANCUT={predicted_medcut:.2f} MB | scale={scale:.3f} (source: {source})")
            
            # pre-correction for first iteration if using non-exact stats
            if iteration == 0 and source != "stats" and fast_size < target_mid * 0.85:
                # FASTOCTREE is well below target, expect large delta
                # reduce scale preemptively to avoid overshooting on first MEDIANCUT
                corr_scale = scale * 0.92
                print(f"{VERSION} | Pre-correction (iter 0): scale {scale:.3f} → {corr_scale:.3f} to reduce first overshooting")
                scale = corr_scale
                resized_frames = resize_frames(frames_raw, width, height, scale)
                # re-run FASTOCTREE on corrected scale
                if scale not in fast_cache:
                    step_start = time.time()
                    frames_fast = [process_frame_fast_octree(fr, palette_limit) for fr in resized_frames]
                    buf_fast, fast_size = save_gif(frames_fast, durations, optimize=False)
                    step_elapsed = time.time() - step_start
                    print(f"{VERSION} | Step {iteration+1}.0 (corrected) | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
                    fast_cache[scale] = fast_size
                else:
                    fast_size = fast_cache[scale]
                    print(f"{VERSION} | Step {iteration+1}.0 (corrected, cached) | FASTOCTREE={fast_size:.2f} MB")

            
            # if from neighbor/formula and prediction is significantly off,
            # pre-adjust scale to reduce overshooting
            if source != "stats" and fast_size < target_mid * 0.9:
                # FASTOCTREE is small relative to target → expect large delta
                # reduce scale to get closer to target on first try
                adj_scale = scale * (target_mid / (fast_size + 4.0)) ** 0.5 if source == "neighbor stats" else scale
                if abs(adj_scale - scale) < 0.05:  # only apply if small adjustment
                    print(f"{VERSION} | Micro-adjusting scale {scale:.3f} → {adj_scale:.3f} based on FASTOCTREE baseline")
                    scale = adj_scale
                    resized_frames = resize_frames(frames_raw, width, height, scale)
                    # re-run FASTOCTREE on adjusted scale
                    if scale not in fast_cache:
                        step_start = time.time()
                        frames_fast = [process_frame_fast_octree(fr, palette_limit) for fr in resized_frames]
                        buf_fast, fast_size = save_gif(frames_fast, durations, optimize=False)
                        step_elapsed = time.time() - step_start
                        print(f"{VERSION} | Step {iteration+1}.0 (adjusted) | FASTOCTREE={fast_size:.2f} MB | finished in {step_elapsed:.2f} sec")
                        fast_cache[scale] = fast_size
                    else:
                        fast_size = fast_cache[scale]
                        print(f"{VERSION} | Step {iteration+1}.0 (adjusted, cached) | FASTOCTREE={fast_size:.2f} MB")


            # --- Step X.1 MEDIANCUT (optimize=false) ---
            if scale in scale_cache:
                med_size = scale_cache[scale]
                print(f"{VERSION} | Step {iteration+1}.1 | MEDIANCUT quantization (cached) | Size={med_size:.2f} MB")
            else:
                step_start = time.time()
                buf_med, med_size, frames_med = compress_med_cut(resized_frames, durations, palette_limit, executor, final=False)
                step_elapsed = time.time() - step_start
                print(f"{VERSION} | Step {iteration+1}.1 | MEDIANCUT quantization (optimize=false)")
                print(f"{VERSION} | MEDIANCUT (optimize=false) | Size={med_size:.2f} MB | finished in {step_elapsed:.2f} sec")
                scale_cache[scale] = med_size
            print(f"{VERSION} | Δ vs FASTOCTREE = {med_size - fast_size:+.2f} MB")

            # --- Decision ---
            if iteration >= 1 and 13.8 <= med_size <= 14.6:
                print(f"{VERSION} | Early accept after iteration {iteration+1} in preferred corridor 13.8-14.6 MB")
                stats_mgr.save_stats(palette_limit, width, height, total_frames, init_size, med_size, scale)
                with open(input_path, "wb") as f:
                    f.write(buf_med.getvalue())
                elapsed = time.time() - start_time
                print(f"{VERSION} | ✅ Success: {init_size:.2f} MB → {med_size:.2f} MB (after {iteration+1} iterations, {elapsed:.2f} sec total)")
                return

            if target_min_mb <= med_size <= target_max_mb:
                # early accept without running optimize=true if already within upper bound
                if med_size <= target_max_mb:
                    print(f"{VERSION} | Early accept after optimize=false (within target)")
                    stats_mgr.save_stats(palette_limit, width, height, total_frames, init_size, med_size, scale)
                    with open(input_path, "wb") as f:
                        f.write(buf_med.getvalue())
                    elapsed = time.time() - start_time
                    print(f"{VERSION} | ✅ Success: {init_size:.2f} MB → {med_size:.2f} MB (after {iteration+1} iterations, {elapsed:.2f} sec total)")
                    return
                # Step X.2 MEDIANCUT (optimize=true)
                step_start = time.time()
                buf_med, med_size, frames_med = compress_med_cut(resized_frames, durations, palette_limit, executor, final=True)
                step_elapsed = time.time() - step_start
                print(f"{VERSION} | Step {iteration+1}.2 | MEDIANCUT quantization (optimize=true)")
                print(f"{VERSION} | MEDIANCUT (optimize=true) | Size={med_size:.2f} MB | finished in {step_elapsed:.2f} sec")
                stats_mgr.save_stats(palette_limit, width, height, total_frames, init_size, med_size, scale)
                with open(input_path, "wb") as f:
                    f.write(buf_med.getvalue())
                elapsed = time.time() - start_time
                print(f"{VERSION} | ✅ Success: {init_size:.2f} MB → {med_size:.2f} MB (after {iteration+1} iterations, {elapsed:.2f} sec total)")
                return
            else:
                # update search bounds based on trial result
                if med_size > target_max_mb:
                    high_scale = scale
                else:
                    low_scale = scale
                
                # limit step size to prevent wild jumps
                max_step = 0.15
                if low_scale != 0.01 and high_scale != 4.0:
                    # refine midpoint within bounds if both are reasonable
                    new_scale = (low_scale + high_scale) / 2
                    # prevent jump larger than max_step factor
                    if abs(new_scale - scale) > scale * max_step:
                        direction = 1 if new_scale > scale else -1
                        new_scale = scale + direction * scale * max_step
                else:
                    # at least one bound is still at initialization;
                    # be more conservative, don't jump too far
                    new_scale = (low_scale + high_scale) / 2
                    if abs(new_scale - scale) > scale * max_step:
                        direction = 1 if new_scale > scale else -1
                        new_scale = scale + direction * scale * max_step
                
                if low_scale in scale_cache and high_scale in scale_cache and low_scale != high_scale:
                    med_low = scale_cache[low_scale]
                    med_high = scale_cache[high_scale]
                    if med_high != med_low:
                        secant_scale = low_scale + (target_mid - med_low) * (high_scale - low_scale) / (med_high - med_low)
                        # clamp secant step to max_step as well
                        if abs(secant_scale - scale) <= scale * max_step:
                            new_scale = secant_scale
                            print(f"{VERSION} | Secant step → new scale={new_scale:.3f} from bounds low={low_scale:.3f} med={med_low:.2f}, high={high_scale:.3f} med={med_high:.2f}")
                        else:
                            direction = 1 if secant_scale > scale else -1
                            new_scale = scale + direction * scale * max_step
                            print(f"{VERSION} | Secant clamped to step limit → new scale={new_scale:.3f}")
                    else:
                        print(f"{VERSION} | Bounded midpoint scale={new_scale:.3f} (low={low_scale:.3f}, high={high_scale:.3f})")
                else:
                    print(f"{VERSION} | Bounded midpoint scale={new_scale:.3f} (low={low_scale:.3f}, high={high_scale:.3f})")
                
                scale = new_scale
                resized_frames = resize_frames(frames_raw, width, height, scale)

    print(f"{VERSION} | ❌ Failed to converge after {max_safe_iterations} iterations")

def process_gifs(root_folder):
    # Scan all GIF files in the folder, compress only those larger than 15 MB
    for root, _, files in os.walk(root_folder):
        for file in files:
            if file.lower().endswith(".gif"):
                gif_path = os.path.join(root, file)
                size_mb = os.path.getsize(gif_path) / (1024 * 1024)
                if size_mb > 15.0:  # Only process GIFs larger than 15 MB
                    try:
                        balanced_compress_gif(gif_path)
                    except Exception as e:
                        print(f"❌ Error processing {gif_path}: {e}")

if __name__ == "__main__":
    # Run main image and GIF processing
    process_images(ROOT_FOLDER_PATH)
    process_gifs(ROOT_FOLDER_PATH)

    print("✅ All PNGs converted/compressed, oversized JPGs shrunk, and oversized GIFs compressed.")

    # Run StatsCompressor.py after successful completion of main compressor
    import subprocess
    # run stats compressor on the same file we are using
    stats_script = os.path.join(os.path.dirname(__file__), "StatsCompressor.py")
    try:
        subprocess.run(["python", stats_script, STATS_FILE], check=True)
    except Exception as e:
        print(f"⚠️ StatsCompressor failed: {e}")

    # Final execution time output
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"Total execution time: {elapsed:.2f} sec")
