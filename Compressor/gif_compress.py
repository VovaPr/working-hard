"""GIF facade module for batch processing and main pipeline delegation."""

from gif_main_pipeline import balanced_compress_gif


def process_gifs(
    gif_paths,
    animated_webp_paths,
    *,
    gif_cfg,
    version,
    stats_file,
    log_level,
    compress_animated_webp_until_under_target,
    debug_log_fn=None,
):
    worked = False
    for file_path in gif_paths:
        worked = True
        try:
            balanced_compress_gif(
                file_path,
                gif_cfg=gif_cfg,
                version=version,
                stats_file=stats_file,
                log_level=log_level,
                debug_log_fn=debug_log_fn,
            )
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    for file_path in animated_webp_paths:
        worked = True
        try:
            compress_animated_webp_until_under_target(file_path)
        except Exception as exc:
            print(f"{version} | [gif.error] Error processing {file_path}: {exc}")

    return worked
