"""Static image facade: delegates to image static pipeline/steps modules."""

from image_static_pipeline import process_images
from image_static_steps import compress_static_webp_until_under_target, compress_until_under_target
