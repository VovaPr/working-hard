"""Calibration statistics for MP4/MOV -> animated WEBP conversions."""

import os
import time

from artifact_manager import get_artifact_manager


class VideoWebPStatsManager:
    def __init__(self, stats_file, version):
        self.stats_file = stats_file
        self.version = version
        self._artifact_mgr = get_artifact_manager(os.path.dirname(stats_file))
        self.video_stats = []
        self._load_video_stats()

    def stats_count(self):
        return len(self.video_stats)

    def _load_video_stats(self):
        try:
            data = self._artifact_mgr.load_stats()
            if isinstance(data, dict):
                self.video_stats = data.get("video_webp_stats", [])
            else:
                self.video_stats = []
            self.video_stats = self._merge_duplicate_video_stats(self.video_stats)
        except Exception:
            self.video_stats = []

    def _merge_duplicate_video_stats(self, entries):
        merged = {}
        for entry in entries:
            key = (
                entry.get("source_width"),
                entry.get("source_height"),
                entry.get("source_fps_bucket"),
                entry.get("source_duration_bucket"),
                entry.get("source_size_bucket"),
                entry.get("profile"),
            )
            if key not in merged or float(entry.get("timestamp", 0)) > float(merged[key].get("timestamp", 0)):
                merged[key] = entry.copy()
        return sorted(merged.values(), key=lambda e: e.get("timestamp", 0))

    @staticmethod
    def _bucket(value, step):
        if value is None:
            return None
        return round(float(value) / step) * step

    def select_startup_plan(
        self,
        *,
        source_width,
        source_height,
        source_fps,
        source_duration_sec,
        source_size_mb,
        profile,
        target_min_mb,
        target_max_mb,
        default_quality,
        default_width,
    ):
        if not self.video_stats:
            return None

        fps_bucket = self._bucket(source_fps, 1.0)
        duration_bucket = self._bucket(source_duration_sec, 5.0)
        size_bucket = self._bucket(source_size_mb, 2.0)
        target_mid_mb = (target_min_mb + target_max_mb) / 2.0

        best = None
        best_score = None
        for entry in self.video_stats:
            if entry.get("profile") != profile:
                continue

            width = entry.get("source_width")
            height = entry.get("source_height")
            fps_entry = entry.get("source_fps_bucket")
            duration_entry = entry.get("source_duration_bucket")
            size_entry = entry.get("source_size_bucket")
            if not all(v is not None for v in (width, height, fps_entry, duration_entry, size_entry)):
                continue

            score = (
                abs(width - source_width) / max(source_width, 1)
                + abs(height - source_height) / max(source_height, 1)
                + abs(float(fps_entry) - float(fps_bucket)) / max(float(fps_bucket or 1.0), 1.0)
                + abs(float(duration_entry) - float(duration_bucket)) / max(float(duration_bucket or 1.0), 1.0)
                + abs(float(size_entry) - float(size_bucket)) / max(float(size_bucket or 1.0), 1.0)
            )

            if best_score is None or score < best_score:
                best_score = score
                best = entry

        if best is None:
            return None

        return {
            "quality": int(best.get("quality", default_quality)),
            "width": int(best.get("width", default_width)),
            "score": float(best_score),
            "source": (
                f"video stats (records={self.stats_count()}, "
                f"score={best_score:.3f}, q={best.get('quality')}, width={best.get('width')})"
            ),
            "target_mid_mb": target_mid_mb,
        }

    def save_attempt(
        self,
        *,
        profile,
        source_width,
        source_height,
        source_fps,
        source_duration_sec,
        source_size_mb,
        quality,
        width,
        result_size_mb,
        encode_sec,
        attempts,
        success,
    ):
        now_ts = time.time()
        entry = {
            "profile": profile,
            "source_width": int(source_width),
            "source_height": int(source_height),
            "source_fps_bucket": self._bucket(source_fps, 1.0),
            "source_duration_bucket": self._bucket(source_duration_sec, 5.0),
            "source_size_bucket": self._bucket(source_size_mb, 2.0),
            "source_size_mb": round(float(source_size_mb), 2),
            "quality": int(quality),
            "width": int(width),
            "result_size_mb": round(float(result_size_mb), 2),
            "encode_sec": round(float(encode_sec), 2),
            "attempts": int(attempts),
            "success": bool(success),
            "timestamp": now_ts,
            "count": 1,
        }

        merged = False
        for existing in self.video_stats:
            if (
                existing.get("profile") == entry["profile"]
                and existing.get("source_width") == entry["source_width"]
                and existing.get("source_height") == entry["source_height"]
                and existing.get("source_fps_bucket") == entry["source_fps_bucket"]
                and existing.get("source_duration_bucket") == entry["source_duration_bucket"]
                and existing.get("source_size_bucket") == entry["source_size_bucket"]
            ):
                if now_ts > float(existing.get("timestamp", 0)):
                    existing.update(entry)
                merged = True
                break

        if not merged:
            self.video_stats.append(entry)

        self.video_stats = self._merge_duplicate_video_stats(self.video_stats)
        self._persist_video_stats()

    def _persist_video_stats(self):
        try:
            data = self._artifact_mgr.load_stats()
            if isinstance(data, list):
                data = {"gif_stats": data}
            elif not isinstance(data, dict):
                data = {}
            data["video_webp_stats"] = self.video_stats
            self._artifact_mgr.save_stats(data)
        except Exception as exc:
            print(f"{self.version} | Warning: failed to save video_webp_stats: {exc}")