import json, os, time

class StatsCompressor:
    VERSION = "StatsCompressor v2.2"

    def __init__(self, path):
        self.path = path
        self.data = self._load()

    def _load(self):
        """Load statistics from JSON file if it exists. Supports {\"gif_stats\": [...]} format."""
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and "gif_stats" in data:
                    return data["gif_stats"]
                return data
        return []

    def save(self):
        """Save current statistics back to JSON file in {\"gif_stats\": [...]} format."""
        with open(self.path, "w") as f:
            json.dump({"gif_stats": self.data}, f, indent=2)

    def compress(self, max_records_per_group=20):
        """
        Aggregate statistics:
        - Group by (palette, width, height, frames).
        - Keep the most recent entry as representative.
        - Add 'count' field with number of merged entries.
        - Preserve 'scale' from the best entry.
        """
        grouped = {}
        for e in self.data:
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

        self.data = new_data
        self.save()
        return len(self.data)


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
    final_count = compressor.compress()
    elapsed = time.time() - start_time
    # Не выводим ничего, только возвращаем значения для использования в Compressor.py