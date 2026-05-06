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
                entry.get("quality"),
                entry.get("method"),
            )
            cnt = int(entry.get("count", 1) or 1)
            if key not in merged:
                base = entry.copy()
                base["count"] = cnt
                merged[key] = base
                continue

            cur = merged[key]
            cur_cnt = int(cur.get("count", 1) or 1)
            total_cnt = cur_cnt + cnt

            cur_result = float(cur.get("result_size_mb", 0.0))
            new_result = float(entry.get("result_size_mb", cur_result))
            cur_encode = float(cur.get("encode_sec", 0.0))
            new_encode = float(entry.get("encode_sec", cur_encode))

            cur["result_size_mb"] = round((cur_result * cur_cnt + new_result * cnt) / max(total_cnt, 1), 2)
            cur["encode_sec"] = round((cur_encode * cur_cnt + new_encode * cnt) / max(total_cnt, 1), 2)
            cur["count"] = total_cnt
            cur["timestamp"] = max(float(cur.get("timestamp", 0)), float(entry.get("timestamp", 0)))

        return sorted(merged.values(), key=lambda e: e.get("timestamp", 0))

    def save_step(self, width, height, frames, init_size_mb, quality, method, result_size_mb, encode_sec):
        init_size_mb = round(init_size_mb, 2)
        result_size_mb = round(result_size_mb, 2)
        encode_sec = round(encode_sec, 2)
        now_ts = time.time()

        merged = False
        for entry in self.webp_stats:
            if (
                entry.get("width") == width
                and entry.get("height") == height
                and entry.get("frames") == frames
                and entry.get("init_size_mb") == init_size_mb
                and entry.get("quality") == quality
                and entry.get("method") == method
            ):
                cnt = int(entry.get("count", 1) or 1)
                entry["result_size_mb"] = round((float(entry.get("result_size_mb", result_size_mb)) * cnt + result_size_mb) / (cnt + 1), 2)
                entry["encode_sec"] = round((float(entry.get("encode_sec", encode_sec)) * cnt + encode_sec) / (cnt + 1), 2)
                entry["count"] = cnt + 1
                entry["timestamp"] = now_ts
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
        max_diff_ratio = 0.20
        target_mid_mb = (target_min_mb + target_max_mb) / 2.0
        exact_init_tolerance = max(0.05, float(gif_cfg.webp_animated_direct_final_init_tolerance_mb))
        candidates = []

        min_count = max(1, int(gif_cfg.webp_animated_startup_min_count))

        for entry in self.webp_stats:
            width_diff = abs(entry["width"] - width) / max(width, 1)
            height_diff = abs(entry["height"] - height) / max(height, 1)
            frame_diff = abs(entry["frames"] - frames) / max(frames, 1)
            entry_count = int(entry.get("count", 1) or 1)

            if width_diff > max_diff_ratio or height_diff > max_diff_ratio or frame_diff > max_diff_ratio:
                continue
            if entry_count < min_count:
                continue

            result_size = entry["result_size_mb"]
            if not (target_min_mb - 0.3 <= result_size <= target_max_mb + 0.3):
                continue

            init_diff = abs(entry["init_size_mb"] - init_size_mb)
            mid_diff = abs(result_size - target_mid_mb)
            exact_profile = (
                entry["width"] == width
                and entry["height"] == height
                and entry["frames"] == frames
            )
            direct_final = bool(
                gif_cfg.webp_animated_direct_final_enabled
                and exact_profile
                and init_diff <= exact_init_tolerance
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
        source_prefix = "webp exact stats" if best["exact_profile"] else "webp stats"
        source_suffix = "direct-final" if best["direct_final"] else "probe-guided"
        return {
            "quality": best["quality"],
            "method": best["method"],
            "result_size_mb": best.get("result_size_mb"),
            "count": best.get("count", 1),
            "direct_final": best["direct_final"],
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
