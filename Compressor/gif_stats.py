import json
import os
import time


class CompressorStatsManager:
    """Stores and serves GIF compression history for scale prediction."""

    def __init__(self, stats_file, version):
        self.stats_file = stats_file
        self.version = version
        self.stats = []
        self._load_stats()

    def _load_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.stats = data.get("gif_stats", [])
                    else:
                        self.stats = data
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
            data = {}
            if os.path.exists(self.stats_file):
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    existing = json.load(f)
                    if isinstance(existing, dict):
                        data = existing
                    else:
                        data = {"gif_stats": existing}

            data["gif_stats"] = self.stats
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"{self.version} | Warning: failed to save stats: {e}")

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

    def average_scale_recent(self, palette, width, height, frames, decay_half_life=86400.0):
        matches = self._filter_matches(palette, width, height, frames)
        now = time.time()
        weighted_sum = 0.0
        weight_total = 0.0
        for entry in matches:
            if entry.get("scale", 0) <= 0:
                continue
            age = now - entry.get("timestamp", now)
            weight = 2.0 ** (-age / decay_half_life)
            weighted_sum += entry["scale"] * weight
            weight_total += weight
        return (weighted_sum / weight_total) if weight_total > 0 else None

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
        profile = self.neighbor_scale_profile(palette, width, height, frames, max_diff_ratio=max_diff_ratio)
        return profile["scale"] if profile else None

    def neighbor_scale_profile(self, palette, width, height, frames, max_diff_ratio=0.15):
        candidates = []
        weights = []
        for entry in self.stats:
            if abs(entry["palette"] - palette) > 8:
                continue

            width_diff = abs(entry["width"] - width) / width
            height_diff = abs(entry["height"] - height) / height
            frame_diff = abs(entry["frames"] - frames) / frames
            palette_diff = abs(entry["palette"] - palette) / 32.0

            if width_diff <= max_diff_ratio and height_diff <= max_diff_ratio and frame_diff <= max_diff_ratio:
                if entry.get("scale", 0) > 0:
                    candidates.append(entry["scale"])
                    distance = width_diff + height_diff + frame_diff + palette_diff
                    weights.append(1.0 / (0.05 + distance))

        if not candidates:
            return None

        weight_sum = sum(weights)
        if weight_sum <= 0:
            mean_scale = sum(candidates) / len(candidates)
            variance = sum((s - mean_scale) ** 2 for s in candidates) / len(candidates)
        else:
            mean_scale = sum(s * w for s, w in zip(candidates, weights)) / weight_sum
            variance = sum(w * (s - mean_scale) ** 2 for s, w in zip(candidates, weights)) / weight_sum

        std_scale = variance ** 0.5
        return {
            "scale": mean_scale,
            "std": std_scale,
            "count": len(candidates),
        }
