"""Facade module for GIF MEDIANCUT prepare stage.

Heavy implementation is stored in `gif_prepare_pipeline.py` to keep this public
entrypoint small and stable for imports.
"""

from gif_prepare_pipeline import _prepare_balanced_medcut_context
