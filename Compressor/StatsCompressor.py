import json, os, time

class StatsCompressor:
    VERSION = "StatsCompressor v2.2"

    def __init__(self, path):
        self.path = path
        self.data = self._load()

    def _load(self):
        """Load statistics in either legacy list format or new dict schema."""
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    data = dict(raw)
                    data["gif_stats"] = raw.get("gif_stats", [])
                    data["webp_animated_stats"] = raw.get("webp_animated_stats", [])
                    data["scan_cache"] = raw.get("scan_cache", {})
                    return data
                return {
                    "gif_stats": raw,
                    "webp_animated_stats": [],
                    "scan_cache": {},
                }
        return {"gif_stats": [], "webp_animated_stats": [], "scan_cache": {}}

    def save(self):
        """Save current statistics back to JSON file using dict schema."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def _cleanup_scan_cache(self):
        """Remove scan cache entries for files that no longer exist on disk."""
        scan_cache = self.data.get("scan_cache", {})
        if not isinstance(scan_cache, dict):
            self.data["scan_cache"] = {}
            return 0, 0

        before_count = len(scan_cache)
        cleaned_cache = {
            path: signature
            for path, signature in scan_cache.items()
            if os.path.exists(path)
        }
        self.data["scan_cache"] = cleaned_cache
        return before_count, len(cleaned_cache)

    def _scan_cache_size_kb(self):
        scan_cache = self.data.get("scan_cache", {})
        payload = json.dumps(scan_cache, ensure_ascii=False)
        return len(payload.encode("utf-8")) / 1024.0

    def compress(self, max_records_per_group=20):
        """
        Aggregate statistics:
        - Group by (palette, width, height, frames).
        - Keep the most recent entry as representative.
        - Add 'count' field with number of merged entries.
        - Preserve 'scale' from the best entry.
        """
        gif_stats = self.data.get("gif_stats", [])

        grouped = {}
        for e in gif_stats:
            key = (e["palette"], e["width"], e["height"], e["frames"])
            grouped.setdefault(key, []).append(e)

        new_data = []
        for key, entries in grouped.items():
            if len(entries) > 1:
                # Pick the most recent entry
                best = max(entries, key=lambda e: e.get("timestamp", 0))
                best = best.copy()
                best["count"] = len(entries)
                # Ensure scale is preserved
                if "scale" not in best:
                    scales = [e.get("scale", 0.75) for e in entries]
                    best["scale"] = sum(scales) / len(scales)
                new_data.append(best)
            else:
                new_data.extend(entries)

        self.data["gif_stats"] = new_data

        # Compress animated WEBP stats by file profile.
        # Keep the most recent successful entry for each profile and annotate merged count.
        webp_stats = self.data.get("webp_animated_stats", [])
        grouped_webp = {}
        for e in webp_stats:
            key = (
                e.get("width"),
                e.get("height"),
                e.get("frames"),
                round(float(e.get("init_size_mb", 0.0)), 2),
            )
            grouped_webp.setdefault(key, []).append(e)

        new_webp = []
        for _, entries in grouped_webp.items():
            best = max(entries, key=lambda x: x.get("timestamp", 0))
            best = best.copy()
            if len(entries) > 1:
                best["count"] = len(entries)
            new_webp.append(best)

        self.data["webp_animated_stats"] = new_webp
        scan_cache_before, scan_cache_after = self._cleanup_scan_cache()
        scan_cache_pruned = max(0, scan_cache_before - scan_cache_after)
        scan_cache_size_kb = self._scan_cache_size_kb()
        self.save()
        return {
            "gif_count": len(self.data["gif_stats"]),
            "webp_count": len(self.data["webp_animated_stats"]),
            "scan_cache_count": scan_cache_after,
            "scan_cache_pruned": scan_cache_pruned,
            "scan_cache_size_kb": scan_cache_size_kb,
        }


if __name__ == "__main__":
    # determine stats file location: use argument if provided, otherwise local directory
    import argparse
    parser = argparse.ArgumentParser(description="Compress GIF stats file")
    parser.add_argument("path", nargs="?", help="Path to stats JSON file")
    args = parser.parse_args()

    if args.path:
        stats_file = args.path
    else:
        stats_file = os.path.join(os.path.dirname(__file__), "CompressorStats.JSON")

    compressor = StatsCompressor(stats_file)

    start_time = time.time()
    counts = compressor.compress()
    elapsed = time.time() - start_time

    print(
        f"{StatsCompressor.VERSION} | GIF stats count={counts['gif_count']} | "
        f"Animated WEBP stats count={counts['webp_count']} | "
        f"Scan cache count={counts['scan_cache_count']} | "
        f"pruned={counts['scan_cache_pruned']} | "
        f"size={counts['scan_cache_size_kb']:.2f} KB | finished in {elapsed:.2f} sec"
    )