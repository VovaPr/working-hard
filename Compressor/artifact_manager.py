"""Artifact management: centralized control of runtime files and directories."""

import os
import json


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
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_stats(self, data):
        """Save statistics to file."""
        try:
            with open(self._stats_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
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
