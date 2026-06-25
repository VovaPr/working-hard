"""
find_duplicates.py — Find duplicate image files by content.

Two modes:
  --exact    (default) SHA256 hash — exact byte-for-byte duplicates
  --visual   Perceptual hash (pHash) — visually identical images,
             even if encoded differently or slightly resized.
             Requires: pip install Pillow imagehash

Usage:
  python find_duplicates.py C:\path\to\images
  python find_duplicates.py C:\path\to\images --visual
  python find_duplicates.py C:\path\to\images --visual --threshold 8
  python find_duplicates.py C:\path\to\images --output dupes.txt
    python find_duplicates.py C:\path\to\images --visual --across-all
    python find_duplicates.py C:\path\to\images --visual --delete-older
    python find_duplicates.py C:\path\to\images --visual --delete-older --dry-run-delete
"""

import argparse
import hashlib
import sys
from itertools import combinations
from collections import defaultdict
from pathlib import Path

IMAGE_EXTENSIONS = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def phash_of(path: Path):
    import imagehash
    from PIL import Image

    with Image.open(path) as img:
        return imagehash.phash(img)


def _progress(i: int, total: int, path: Path):
    pct = i / total * 100
    name = path.name[:50].ljust(50)
    print(f"\r  [{pct:5.1f}%] {i}/{total}  {name}", end="", flush=True)


def _folder_progress(i: int, total: int, folder: Path):
    pct = i / total * 100 if total else 100
    name = str(folder)
    if len(name) > 70:
        name = "..." + name[-67:]
    print(f"\r[folders] [{pct:5.1f}%] {i}/{total}  {name.ljust(70)}", end="", flush=True)


def find_exact_duplicates(paths: list[Path]) -> list[list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    total = len(paths)
    for i, p in enumerate(paths, 1):
        _progress(i, total, p)
        try:
            groups[sha256_of(p)].append(p)
        except OSError as e:
            print(f"\n  [skip] {p}: {e}")
    print()
    return [group for group in groups.values() if len(group) > 1]


def find_visual_duplicates(paths: list[Path], threshold: int) -> list[list[Path]]:
    hashes: list[tuple] = []  # (hash, path)
    total = len(paths)
    for i, p in enumerate(paths, 1):
        _progress(i, total, p)
        try:
            hashes.append((phash_of(p), p))
        except Exception as e:
            print(f"\n  [skip] {p}: {e}")
    print()

    # Fast path: exact hash matches (O(n))
    if threshold == 0:
        exact: dict[str, list[int]] = defaultdict(list)
        for i, (h, _) in enumerate(hashes):
            exact[str(h)].append(i)
        return [[hashes[j][1] for j in idx_list] for idx_list in exact.values() if len(idx_list) > 1]

    # Near-duplicate grouping using numpy vectorized Hamming distance
    import numpy as np

    n = len(hashes)
    hash_ints = np.array([int(str(h), 16) for h, _ in hashes], dtype=np.uint64)

    print(f"  Grouping {n} hashes (vectorized)...")
    used: set[int] = set()
    groups: list[list[Path]] = []
    for i in range(n):
        if i in used:
            continue
        if i % 500 == 0:
            print(f"\r  Grouping {i}/{n} ({i*100//n}%) groups={len(groups)} ...", end="", flush=True)

        xor = hash_ints ^ hash_ints[i]
        distances = np.array([bin(int(x)).count("1") for x in xor], dtype=np.int32)
        matches = [j for j in np.where(distances <= threshold)[0].tolist() if j != i and j not in used]

        if matches:
            group_indices = [i] + matches
            used.update(group_indices)
            groups.append([hashes[j][1] for j in group_indices])

    print(f"\r  Grouping done. Found {len(groups)} group(s).          ")
    return groups


def collect_images(root: Path, recursive: bool) -> list[Path]:
    glob = "**/*" if recursive else "*"
    return [
        p for p in root.glob(glob)
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]


def format_size(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1024:.1f} KB"
    except OSError:
        return "? KB"


def bucket_by_folder(paths: list[Path]) -> dict[Path, list[Path]]:
    buckets: dict[Path, list[Path]] = defaultdict(list)
    for p in paths:
        buckets[p.parent].append(p)
    return dict(sorted(buckets.items(), key=lambda item: str(item[0]).lower()))


def resolve_output_path(output_arg: str) -> Path:
    p = Path(output_arg)
    if p.is_absolute():
        return p.resolve()
    script_dir = Path(__file__).resolve().parent
    return (script_dir / p).resolve()


def groups_to_pairs(groups: list[list[Path]]) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for group in groups:
        sorted_group = sorted(group)
        pairs.extend((a, b) for a, b in combinations(sorted_group, 2))
    return pairs


def plan_delete_older(groups: list[list[Path]]) -> list[tuple[Path, Path]]:
    plans: list[tuple[Path, Path]] = []
    for group in groups:
        if len(group) < 2:
            continue

        # Keep the newest file by mtime; if equal, keep lexicographically first path.
        ordered = sorted(group, key=lambda p: (-p.stat().st_mtime, str(p).lower()))
        keep = ordered[0]
        for dup in ordered[1:]:
            plans.append((keep, dup))
    return plans


def apply_delete_plan(plans: list[tuple[Path, Path]]) -> tuple[int, int, list[str]]:
    deleted_count = 0
    freed_bytes = 0
    errors: list[str] = []
    for _, dup in plans:
        try:
            size = dup.stat().st_size
            dup.unlink()
            deleted_count += 1
            freed_bytes += size
        except Exception as e:
            errors.append(f"[delete-error] {dup}: {e}")
    return deleted_count, freed_bytes, errors


def main():
    parser = argparse.ArgumentParser(description="Find duplicate image files.")
    parser.add_argument("directory", help="Root directory to scan")
    parser.add_argument("--exact", action="store_true", default=False,
                        help="Exact byte match via SHA256 (default mode)")
    parser.add_argument("--visual", action="store_true", default=False,
                        help="Visual similarity via perceptual hash (pHash)")
    parser.add_argument("--threshold", type=int, default=6,
                        help="pHash distance threshold for --visual (default: 6, range 0-64)")
    parser.add_argument("--no-recursive", action="store_true", default=False,
                        help="Do not recurse into subdirectories")
    parser.add_argument("--across-all", action="store_true", default=False,
                        help="Compare all files together instead of per-folder")
    parser.add_argument("--output", help="Write results to file instead of stdout")
    parser.add_argument("--delete-older", action="store_true", default=False,
                        help="Delete older duplicates: keep newest file in each duplicate group")
    parser.add_argument("--dry-run-delete", action="store_true", default=False,
                        help="With --delete-older, write plan only without deleting files")
    parser.add_argument("--apply-delete", action="store_true", default=False,
                        help="Deprecated compatibility flag; deletion is automatic with --delete-older")
    args = parser.parse_args()

    # Default to exact if neither flag set
    if not args.visual:
        args.exact = True

    root = Path(args.directory)
    if not root.is_dir():
        print(f"Error: '{root}' is not a directory.")
        sys.exit(1)

    recursive = not args.no_recursive
    print(f"Scanning {'recursively ' if recursive else ''}in: {root}")
    paths = collect_images(root, recursive)
    print(f"Found {len(paths)} image file(s).")

    if not paths:
        print("Nothing to compare.")
        return

    if args.output:
        print(f"Output file: {resolve_output_path(args.output)}")

    mode = "visual (pHash)" if args.visual else "exact (SHA256)"
    print(f"Mode: {mode}")
    if args.visual:
        print(f"Threshold: {args.threshold}")
    print(f"Scope: {'all files together' if args.across_all else 'each folder separately'}")
    if args.apply_delete and not args.delete_older:
        print("Error: --apply-delete requires --delete-older")
        sys.exit(2)

    delete_enabled = args.delete_older and not args.dry_run_delete
    if args.apply_delete:
        delete_enabled = True

    if args.delete_older:
        print(f"Delete mode: {'APPLY' if delete_enabled else 'DRY-RUN'}")

    if args.visual:
        try:
            import imagehash  # noqa: F401
        except ImportError:
            print("\nERROR: 'imagehash' is not installed.")
            print("Run: pip install imagehash Pillow")
            sys.exit(1)

    out_path = resolve_output_path(args.output) if args.output else None
    writer = open(out_path, "w", encoding="utf-8") if out_path else None

    def _write(line: str):
        if writer is not None:
            writer.write(line + "\n")
        else:
            print(line)

    try:
        if args.across_all:
            _write("Scope: all files together")
            if args.visual:
                groups = find_visual_duplicates(paths, args.threshold)
            else:
                groups = find_exact_duplicates(paths)

            pairs = groups_to_pairs(groups)
            _write(f"Total pairs: {len(pairs)}")
            for idx, (a, b) in enumerate(pairs, 1):
                _write(f"{idx}. {a}  <=>  {b}")

            if args.delete_older:
                plans = plan_delete_older(groups)
                _write("")
                _write(f"Delete plan entries: {len(plans)}")
                for idx, (keep, dup) in enumerate(plans, 1):
                    _write(f"{idx}. KEEP {keep}")
                    _write(f"    DEL  {dup}")

                if delete_enabled and plans:
                    deleted_count, freed_bytes, errors = apply_delete_plan(plans)
                    _write("")
                    _write(f"Deleted: {deleted_count} file(s)")
                    _write(f"Freed: {freed_bytes / (1024 * 1024):.2f} MB")
                    for err in errors:
                        _write(err)
        else:
            _write("Scope: each folder separately")
            buckets = [(folder, files) for folder, files in bucket_by_folder(paths).items() if len(files) >= 2]
            total_folders = len(buckets)
            total_pairs = 0
            total_plan_entries = 0
            total_deleted = 0
            total_freed = 0
            total_delete_errors = 0

            _write(f"Folders to scan: {total_folders}")
            if total_folders == 0:
                _write("No folders with 2+ images found.")

            for i, (folder, folder_paths) in enumerate(buckets, 1):
                _folder_progress(i, total_folders, folder)
                print()

                if args.visual:
                    groups = find_visual_duplicates(folder_paths, args.threshold)
                else:
                    groups = find_exact_duplicates(folder_paths)

                pairs = groups_to_pairs(groups)
                if not pairs:
                    continue

                total_pairs += len(pairs)
                _write("")
                _write(f"=== Folder: {folder} ===")
                _write(f"Pairs: {len(pairs)}")
                for idx, (a, b) in enumerate(pairs, 1):
                    _write(f"{idx}. {a}  <=>  {b}")

                if args.delete_older:
                    plans = plan_delete_older(groups)
                    total_plan_entries += len(plans)
                    _write(f"Delete plan entries: {len(plans)}")
                    for idx, (keep, dup) in enumerate(plans, 1):
                        _write(f"D{idx}. KEEP {keep}")
                        _write(f"     DEL  {dup}")

                    if delete_enabled and plans:
                        deleted_count, freed_bytes, errors = apply_delete_plan(plans)
                        total_deleted += deleted_count
                        total_freed += freed_bytes
                        total_delete_errors += len(errors)
                        _write(f"Deleted in folder: {deleted_count} file(s)")
                        _write(f"Freed in folder: {freed_bytes / (1024 * 1024):.2f} MB")
                        for err in errors:
                            _write(err)

            _write("")
            _write(f"Total pairs: {total_pairs}")
            if args.delete_older:
                _write(f"Total delete plan entries: {total_plan_entries}")
                if delete_enabled:
                    _write(f"Total deleted: {total_deleted} file(s)")
                    _write(f"Total freed: {total_freed / (1024 * 1024):.2f} MB")
                    _write(f"Delete errors: {total_delete_errors}")

    finally:
        if writer is not None:
            writer.close()

    if out_path:
        print(f"Results written to: {out_path}")


if __name__ == "__main__":
    main()
