"""Facade module for GIF MEDIANCUT completion stage.

Heavy implementation is stored in `gif_complete_pipeline.py` to keep this
public entrypoint small and stable for imports.
"""

from gif_complete_pipeline import _complete_balanced_iteration
