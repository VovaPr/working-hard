"""Artifact management: centralized control of runtime files and directories."""

import os
import json
import time


# Schema version for stats file format. Increment when structure changes.
STATS_SCHEMA_VERSION = 1

# Size limit for stats file before rotation (not yet implemented, but reserved).
# TODO: When stats file reaches 5 MB, implement rotation to archive older stats.
# See /memories/repo/stats-rotation-todo.md
STATS_ROTATION_SIZE_MB = 5


class ArtifactManager:
    """Centralized manager for runtime artifacts (stats, temp directories, etc.)."""

    def __init__(self, base_dir=None):
        """
        Initialize artifact manager.
        
        Args:
            base_dir: Base directory for artifacts. Defaults to Compressor folder.
        """
        if base_dir is None:
            base_dir = os.path.dirname(__file__)
        self.base_dir = base_dir
        self._stats_path = os.path.join(self.base_dir, "compressor_stats.json")

    def get_stats_path(self):
        """Get path to statistics JSON file."""
        return self._stats_path

    def load_stats(self):
        """Load statistics from file. Returns empty dict if file doesn't exist."""
        if not os.path.exists(self._stats_path):
            return {}
        try:
            with open(self._stats_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            # Handle both versioned and legacy formats
            if isinstance(data, dict) and "_schema_version" in data:
                # Versioned format - validate version if needed
                schema_version = data.get("_schema_version", 1)
                if schema_version != STATS_SCHEMA_VERSION:
                    # Migration logic can go here in future
                    pass
                return data
            elif isinstance(data, dict):
                # Legacy format (no version) - wrap in new structure
                return {
                    "_schema_version": STATS_SCHEMA_VERSION,
                    "_created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "gif_stats": data.get("gif_stats", []),
                    "webp_animated_stats": data.get("webp_animated_stats", [])
                }
            return {}
        except (json.JSONDecodeError, IOError):
            return {}

    def save_stats(self, data):
        """Save statistics to file."""
        try:
            # Ensure versioning and metadata
            if not isinstance(data, dict):
                data = {"gif_stats": data if isinstance(data, list) else []}
            if "_schema_version" not in data:
                data["_schema_version"] = STATS_SCHEMA_VERSION
            if "_created" not in data:
                data["_created"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            
            with open(self._stats_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            # Check file size for future rotation (currently logged only)
            file_size_mb = os.path.getsize(self._stats_path) / (1024 * 1024)
            if file_size_mb > STATS_ROTATION_SIZE_MB:
                # TODO: Implement rotation when size exceeds limit
                # See /memories/repo/stats-rotation-todo.md
                pass
        except IOError as e:
            print(f"Failed to save stats to {self._stats_path}: {e}")

    def ensure_base_dir_exists(self):
        """Ensure base directory exists."""
        os.makedirs(self.base_dir, exist_ok=True)


# Global singleton instance
_artifact_manager = None


def get_artifact_manager(base_dir=None):
    """Get or create the global artifact manager instance."""
    global _artifact_manager
    if _artifact_manager is None:
        _artifact_manager = ArtifactManager(base_dir)
    return _artifact_manager
