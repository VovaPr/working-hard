"""Animated WEBP compression statistics manager."""

import json
import os
import time


class AnimatedWebPStatsManager:
    """Tracks and persists animated WEBP compression statistics."""

    def __init__(self, stats_file, version):
        self.stats_file = stats_file
        self.version = version
        self.webp_stats = []
        self._load_webp_stats()

    def stats_count(self):
        return len(self.webp_stats)

    def _load_webp_stats(self):
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.webp_stats = data.get("webp_animated_stats", [])
                    else:
                        self.webp_stats = []
                    self.webp_stats = self._merge_duplicate_webp_stats(self.webp_stats)
            except Exception:
                self.webp_stats = []
        else:
            self.webp_stats = []

    def _merge_duplicate_webp_stats(self, entries):
        merged = {}
        for entry in entries:
            key = (
                entry.get("width"),
                entry.get("height"),
                entry.get("frames"),
                entry.get("init_size_mb"),
                entry.get("method"),
            )
            if key not in merged:
                merged[key] = entry.copy()
            else:
                # Оставляем запись с максимальным timestamp
                if float(entry.get("timestamp", 0)) > float(merged[key].get("timestamp", 0)):
                    merged[key] = entry.copy()
        return sorted(merged.values(), key=lambda e: e.get("timestamp", 0))

    def save_step(
        self,
        width,
        height,
        frames,
        init_size_mb,
        quality,
        method,
        result_size_mb,
        encode_sec,
        resize_count=0,
        final_width=None,
        final_height=None,
    ):
        init_size_mb = round(init_size_mb, 2)
        result_size_mb = round(result_size_mb, 2)
        encode_sec = round(encode_sec, 2)
        now_ts = time.time()

        # Новый ключ без quality
        merged = False
        for entry in self.webp_stats:
            if (
                entry.get("width") == width
                and entry.get("height") == height
                and entry.get("frames") == frames
                and entry.get("init_size_mb") == init_size_mb
                and entry.get("method") == method
            ):
                # Обновляем только если новая запись "свежее"
                if now_ts > float(entry.get("timestamp", 0)):
                    entry.update({
                        "quality": quality,
                        "result_size_mb": result_size_mb,
                        "encode_sec": encode_sec,
                        "timestamp": now_ts,
                        "resize_count": int(resize_count or 0),
                        "final_width": final_width,
                        "final_height": final_height,
                    })
                merged = True
                break

        if not merged:
            self.webp_stats.append(
                {
                    "width": width,
                    "height": height,
                    "frames": frames,
                    "init_size_mb": init_size_mb,
                    "quality": quality,
                    "method": method,
                    "result_size_mb": result_size_mb,
                    "encode_sec": encode_sec,
                    "timestamp": now_ts,
                    "count": 1,
                    "resize_count": int(resize_count or 0),
                    "final_width": final_width,
                    "final_height": final_height,
                }
            )

        self.webp_stats = self._merge_duplicate_webp_stats(self.webp_stats)
        self._persist_webp_stats()

    def _persist_webp_stats(self):
        try:
            data = {}
            if os.path.exists(self.stats_file):
                with open(self.stats_file, "r", encoding="utf-8-sig") as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        data = {"gif_stats": content}
                    else:
                        data = content
            data["webp_animated_stats"] = self.webp_stats
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"{self.version} | Warning: failed to save webp_animated_stats: {e}")


    def select_startup_plan(self, width, height, frames, init_size_mb, target_min_mb, target_max_mb, gif_cfg):
        # Fuzzy matching tolerances
        max_diff_ratio = 0.03  # 3% для размеров и кадров
        init_tolerance = max(0.15, float(gif_cfg.webp_animated_direct_final_init_tolerance_mb))  # 0.15 MB или config
        target_mid_mb = (target_min_mb + target_max_mb) / 2.0
        candidates = []

        min_count = max(1, int(gif_cfg.webp_animated_startup_min_count))

        for entry in self.webp_stats:
            width_diff = abs(entry["width"] - width) / max(width, 1)
            height_diff = abs(entry["height"] - height) / max(height, 1)
            frame_diff = abs(entry["frames"] - frames) / max(frames, 1)
            entry_count = int(entry.get("count", 1) or 1)
            init_diff = abs(entry["init_size_mb"] - init_size_mb)
            strong_profile_match = (
                entry.get("width") == width
                and entry.get("height") == height
                and entry.get("frames") == frames
                and init_diff <= init_tolerance
            )

            if width_diff > max_diff_ratio or height_diff > max_diff_ratio or frame_diff > max_diff_ratio:
                continue
            if init_diff > init_tolerance:
                continue
            if entry_count < min_count and not strong_profile_match:
                continue

            result_size = entry["result_size_mb"]
            if not (target_min_mb - 0.3 <= result_size <= target_max_mb + 0.3):
                continue

            mid_diff = abs(result_size - target_mid_mb)
            exact_profile = (
                width_diff < 1e-6 and height_diff < 1e-6 and frame_diff < 1e-6 and init_diff < 1e-6
            )
            direct_final = bool(
                gif_cfg.webp_animated_direct_final_enabled
                and strong_profile_match
            )
            candidates.append(
                {
                    "quality": entry["quality"],
                    "method": entry.get("method", gif_cfg.webp_animated_method_default),
                    "result_size_mb": result_size,
                    "count": entry_count,
                    "init_diff": init_diff,
                    "mid_diff": mid_diff,
                    "timestamp": entry.get("timestamp", 0),
                    "direct_final": direct_final,
                    "exact_profile": exact_profile,
                    "resize_count": int(entry.get("resize_count", 0) or 0),
                    "final_width": entry.get("final_width"),
                    "final_height": entry.get("final_height"),
                }
            )

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                0 if item["direct_final"] else 1,
                0 if item["exact_profile"] else 1,
                item["init_diff"],
                item["mid_diff"],
                -item["timestamp"],
            )
        )
        best = candidates[0]
        pre_resize = None
        if best.get("resize_count", 0) > 0:
            fw = best.get("final_width")
            fh = best.get("final_height")
            if isinstance(fw, int) and isinstance(fh, int) and fw > 0 and fh > 0 and fw <= width and fh <= height:
                pre_resize = (fw, fh)

        source_prefix = "webp exact stats" if best["exact_profile"] else "webp stats"
        source_suffix = "direct-final" if best["direct_final"] else "probe-guided"
        return {
            "quality": best["quality"],
            "method": best["method"],
            "result_size_mb": best.get("result_size_mb"),
            "count": best.get("count", 1),
            "direct_final": best["direct_final"] and (best.get("resize_count", 0) == 0 or pre_resize is not None),
            "pre_resize": pre_resize,
            "source": f"{source_prefix} ({source_suffix}, records={self.stats_count()}, count={best.get('count', 1)})",
        }

    def predict_startup_quality(self, width, height, frames, init_size_mb, target_min_mb, target_max_mb, gif_cfg):
        plan = self.select_startup_plan(
            width,
            height,
            frames,
            init_size_mb,
            target_min_mb,
            target_max_mb,
            gif_cfg,
        )
        return None if plan is None else plan["quality"]
