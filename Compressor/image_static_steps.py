import io
import os
import time

from PIL import Image, ImageOps, UnidentifiedImageError


def _build_jpg_path_from_png(png_path):
    return os.path.join(
        os.path.dirname(png_path),
        os.path.splitext(os.path.basename(png_path))[0] + ".jpg",
    )


def _convert_png_to_jpg(png_path, jpg_path, version):
    with Image.open(png_path) as img:
        png_size = os.path.getsize(png_path)
        print(f"{version} | Initial PNG: {png_path}")
        print(f"{version} | WxH={img.width}x{img.height} | Size={png_size/1024:.2f} KB")

        prepared = ImageOps.exif_transpose(img)
        icc_profile = img.info.get("icc_profile")
        exif = img.getexif()
        exif_bytes = None
        if exif:
            exif[274] = 1
            exif_bytes = exif.tobytes()

        has_alpha = (
            "A" in prepared.getbands()
            or (prepared.mode == "P" and "transparency" in prepared.info)
        )
        if has_alpha:
            rgba = prepared.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            jpg_image = bg
        else:
            jpg_image = prepared.convert("RGB")

        save_kwargs = {
            "quality": 100,
            "optimize": True,
            "progressive": True,
            "subsampling": 0,
        }
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes

        jpg_image.save(jpg_path, "JPEG", **save_kwargs)

    jpg_size = os.path.getsize(jpg_path)
    print(f"{version} | Converted PNG -> JPG: {jpg_path}")
    return png_size, jpg_size


def _convert_jfif_to_jpg(jfif_path, version):
    converted_jpg_path = os.path.join(
        os.path.dirname(jfif_path),
        os.path.splitext(os.path.basename(jfif_path))[0] + ".jpg",
    )
    with Image.open(jfif_path) as img:
        jfif_size = os.path.getsize(jfif_path)
        prepared = ImageOps.exif_transpose(img)
        rgb = prepared.convert("RGB")
        rgb.save(
            converted_jpg_path,
            "JPEG",
            quality=100,
            optimize=True,
            progressive=True,
            subsampling=0,
        )

    converted_size = os.path.getsize(converted_jpg_path)
    print(f"{version} | Converted JFIF -> JPG: {converted_jpg_path}")
    return converted_jpg_path, jfif_size, converted_size


def _encode_jpeg_buffer(image, quality):
    buf = io.BytesIO()
    image.save(
        buf,
        "JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    return buf


def _find_best_quality_buffer(image, size_limit, q_min, q_max):
    low = q_min
    high = q_max
    best_quality = None
    best_buf = None
    best_size = None

    while low <= high:
        mid = (low + high) // 2
        mid_buf = _encode_jpeg_buffer(image, mid)
        mid_size = len(mid_buf.getvalue())
        if mid_size <= size_limit:
            best_quality = mid
            best_buf = mid_buf
            best_size = mid_size
            low = mid + 1
        else:
            high = mid - 1

    return best_quality, best_buf, best_size


def compress_until_under_target(path, version, target_size):
    started_at = time.time()
    min_quality_before_resize = 80

    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            resize_count = 0

            init_size = os.path.getsize(path)
            quality = 100
            print(f"{version} | Initial File: {path}")
            print(
                f"{version} | WxH={img.width}x{img.height} | Quality={quality} "
                f"| Size={init_size/1024:.2f} KB | Target={target_size/1024:.0f} KB"
            )

            if init_size <= target_size:
                print(f"{version} | ✅ Already under target, no compression needed")
                return

            while True:
                best_quality, best_buf, best_size = _find_best_quality_buffer(
                    img,
                    target_size,
                    min_quality_before_resize,
                    100,
                )
                if best_buf is not None:
                    with open(path, "wb") as f:
                        f.write(best_buf.getvalue())
                    elapsed = time.time() - started_at
                    print(
                        f"{version} | ✅ Success: {init_size/1024:.2f} KB -> {best_size/1024:.2f} KB "
                        f"| Quality={best_quality} | Resized {resize_count} times"
                    )
                    print(f"{version} | Finished in {elapsed:.2f} sec")
                    return

                min_q_buf = _encode_jpeg_buffer(img, min_quality_before_resize)
                min_q_size = len(min_q_buf.getvalue())
                correction = (target_size / max(min_q_size, 1)) ** 0.5
                correction = max(0.88, min(0.98, correction))
                new_w = max(1, int(img.width * correction))
                new_h = max(1, int(img.height * correction))
                img = img.resize((new_w, new_h), Image.LANCZOS)
                resize_count += 1
                print(
                    f"{version} | Step {resize_count} | Resized to {new_w}x{new_h}, "
                    f"q{min_quality_before_resize} size={min_q_size/1024:.2f} KB"
                )

    except UnidentifiedImageError:
        print(f"{version} | Skipped corrupted file: {path}")


def _compress_static_webp_like_jpg(image, target_size, version, gif_cfg, started_at):
    quality = 95
    resize_count = 0
    webp_method = max(0, min(6, gif_cfg.webp.webp_static_method_default))

    for step in range(1, gif_cfg.webp.webp_static_max_iterations + 1):
        quality = max(1, min(100, int(quality)))
        buf = io.BytesIO()
        image.save(buf, "WEBP", quality=quality, method=webp_method)
        file_size = len(buf.getvalue())
        elapsed = time.time() - started_at
        print(
            f"{version} | WEBP static step {step} | "
            f"Size={file_size/1024:.2f} KB | q={quality} | method={webp_method} | elapsed={elapsed:.2f} sec"
        )

        if file_size <= target_size:
            return buf, file_size, quality, resize_count, True

        if elapsed >= gif_cfg.webp.webp_file_max_seconds:
            print(
                f"{version} | ⚠ WEBP static timeout {elapsed:.2f} sec; "
                "file kept unchanged"
            )
            return None, None, quality, resize_count, False

        correction = (target_size / file_size) ** 0.5 if file_size > 0 else 1.0
        correction = max(0.75, min(1.25, correction))

        if quality <= 50:
            new_w = max(1, int(image.width * correction))
            new_h = max(1, int(image.height * correction))
            image = image.resize((new_w, new_h), Image.LANCZOS)
            resize_count += 1
            quality = 95
            print(f"{version} | WEBP step {resize_count} | Resized to {new_w}x{new_h}, reset quality={quality}")
            continue

        quality = max(50, min(100, int(quality * correction)))
        print(f"{version} | WEBP step {resize_count+1} | Quality={quality}")

    print(
        f"{version} | ⚠ WEBP static max iterations reached; "
        f"file kept unchanged (could not hit target <= {target_size/1024:.0f} KB)"
    )
    return None, None, quality, resize_count, False


def compress_static_webp_until_under_target(path, version, target_size, gif_cfg):
    started_at = time.time()

    try:
        with Image.open(path) as img:
            init_size = os.path.getsize(path)
            frame_count = getattr(img, "n_frames", 1)
            is_animated = bool(getattr(img, "is_animated", False) and frame_count > 1)
            if is_animated:
                return

            print(f"{version} | Initial WEBP: {path}")
            print(
                f"{version} | WxH={img.width}x{img.height} | Animated=False "
                f"| Frames={frame_count} | Size={init_size/1024:.2f} KB "
                f"| Target={target_size/1024:.0f} KB"
            )

            if init_size <= target_size:
                print(f"{version} | ✅ WEBP already in target range, no compression needed")
                return

            has_alpha = "A" in (img.mode or "")
            image = img.convert("RGBA" if has_alpha else "RGB")
            buf, file_size, quality, resize_count, success = _compress_static_webp_like_jpg(
                image,
                target_size,
                version,
                gif_cfg,
                started_at,
            )
            if not success:
                return

            with open(path, "wb") as f:
                f.write(buf.getvalue())
            elapsed = time.time() - started_at
            print(
                f"{version} | ✅ WEBP success (static-jpg-like): {init_size/1024:.2f} KB -> {file_size/1024:.2f} KB "
                f"| Quality={quality} | Resized {resize_count} times"
            )
            print(f"{version} | Finished in {elapsed:.2f} sec")

    except UnidentifiedImageError:
        print(f"{version} | Skipped corrupted WEBP: {path}")
