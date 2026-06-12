#!/usr/bin/env python3
"""Copy all files ending with '-original.png' into an 'only_original' directory.

Usage:
  python copy_originals_to_only_original.py [root_dir]

If no root_dir is provided, the current working directory is used.
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def find_originals(root: Path):
    for dirpath, dirs, files in os.walk(root):
        for fname in files:
            if fname.endswith("-original.png"):
                yield Path(dirpath) / fname


def unique_dest(dest_dir: Path, name: str) -> Path:
    dest = dest_dir / name
    if not dest.exists():
        return dest
    stem, dot, ext = name.rpartition('.')
    if not dot:
        stem = name
        ext = ""
    i = 1
    while True:
        new_name = f"{stem}_{i}.{ext}" if ext else f"{stem}_{i}"
        new_dest = dest_dir / new_name
        if not new_dest.exists():
            return new_dest
        i += 1


def main():
    parser = argparse.ArgumentParser(description="Collect -original.png files into only_original")
    parser.add_argument("root", nargs="?", default=".", help="Root directory to search")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    dest_dir = root / "only_original"
    dest_dir.mkdir(parents=True, exist_ok=True)

    found = list(find_originals(root))
    if not found:
        print("[INFO] no -original.png files found")
        return 0

    copied = 0
    for src in found:
        name = src.name
        dest = unique_dest(dest_dir, name)
        try:
            shutil.copy2(src, dest)
            print(f"[COPIED] {src} -> {dest}")
            copied += 1
        except Exception as e:
            print(f"[ERROR] failed to copy {src}: {e}")

    print(f"[INFO] copied {copied} file(s) to {dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
