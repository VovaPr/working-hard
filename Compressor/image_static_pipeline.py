import os

from PIL import UnidentifiedImageError

from image_static_steps import (
    _build_jpg_path_from_png,
    _convert_jfif_to_jpg,
    _convert_png_to_jpg,
    compress_static_webp_until_under_target,
    compress_until_under_target,
)


def _process_pngs(*, png_paths, version, target_size):
    worked = False
    for png_path in png_paths:
        worked = True
        jpg_path = _build_jpg_path_from_png(png_path)

        try:
            png_size, jpg_size = _convert_png_to_jpg(png_path, jpg_path, version)
            print(f"{version} | Converted size={jpg_size/1024:.2f} KB | Target={target_size/1024:.0f} KB")
            os.remove(png_path)

            if jpg_size <= target_size:
                print(
                    f"{version} | ✅ PNG success: {png_size/1024:.2f} KB -> {jpg_size/1024:.2f} KB "
                    "(no further compression needed)"
                )
                continue

            compress_until_under_target(jpg_path, version, target_size)
        except UnidentifiedImageError:
            print(f"{version} | Skipped corrupted PNG: {png_path}")
        except Exception as exc:
            print(f"{version} | Error processing PNG {png_path}: {exc}")

    return worked


def _process_jpgs(*, jpg_paths, version, target_size):
    worked = False
    for jpg_path in jpg_paths:
        worked = True
        try:
            ext = os.path.splitext(jpg_path)[1].lower()
            if ext == ".jfif":
                converted_jpg_path, jfif_size, converted_size = _convert_jfif_to_jpg(jpg_path, version)
                print(
                    f"{version} | Converted size={converted_size/1024:.2f} KB | "
                    f"Target={target_size/1024:.0f} KB"
                )
                os.remove(jpg_path)

                if converted_size <= target_size:
                    print(
                        f"{version} | ✅ JFIF success: {jfif_size/1024:.2f} KB -> "
                        f"{converted_size/1024:.2f} KB (no further compression needed)"
                    )
                    continue

                compress_until_under_target(converted_jpg_path, version, target_size)
                continue

            compress_until_under_target(jpg_path, version, target_size)
        except Exception as exc:
            print(f"{version} | Error processing JPG {jpg_path}: {exc}")

    return worked


def _process_static_webp(*, static_webp_paths, version, target_size, gif_cfg):
    worked = False
    for webp_path in static_webp_paths:
        worked = True
        try:
            compress_static_webp_until_under_target(webp_path, version, target_size, gif_cfg)
        except Exception as exc:
            print(f"{version} | Error processing WEBP {webp_path}: {exc}")

    return worked


def process_images(png_paths, jpg_paths, static_webp_paths, version, target_size, gif_cfg):
    """Image block: convert PNG to JPG, compress oversized JPG/JPEG, and compress static WEBP."""
    worked_png = _process_pngs(png_paths=png_paths, version=version, target_size=target_size)
    worked_jpg = _process_jpgs(jpg_paths=jpg_paths, version=version, target_size=target_size)
    worked_webp = _process_static_webp(
        static_webp_paths=static_webp_paths,
        version=version,
        target_size=target_size,
        gif_cfg=gif_cfg,
    )
    return worked_png or worked_jpg or worked_webp
