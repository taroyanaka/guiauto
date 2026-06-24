# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import mask_text_in_image as mask_app

DEFAULT_INPUT_DIR = APP_DIR / "input_dir"
DEFAULT_OUTPUT_DIR = APP_DIR / "output_dir"
DEFAULT_TARGET_DIR = APP_DIR / "target"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Routine runner: process unprocessed images when per-image target exists."
    )
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Directory containing source images")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory where masked images will be written")
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR), help="Directory containing per-image target text files")
    parser.add_argument("--color-ocr-strength", default="strong", choices=("weak", "medium", "strong"))
    parser.add_argument("--grayscale-enabled", action="store_true")
    parser.add_argument("--output-image-mode", default="original", choices=("original", "grayscale"))
    parser.add_argument("--no-gpu", dest="gpu_enabled", action="store_false", default=True, help="Disable GPU (GPU enabled by default)")
    return parser.parse_args()


def iter_images(input_dir: Path) -> Iterable[Path]:
    if not input_dir.exists() or not input_dir.is_dir():
        return []
    return sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def is_processed(output_dir: Path, image_path: Path) -> bool:
    stem = image_path.stem
    suffix = image_path.suffix
    candidates = [f"{stem}-mask.png", f"{stem}-masked{suffix}", f"{stem}-original{suffix}"]
    return any((output_dir / name).exists() for name in candidates)


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    target_dir = Path(args.target_dir)

    os.environ["MASK_TEXT_USE_GPU"] = "1" if args.gpu_enabled else "0"
    output_dir.mkdir(parents=True, exist_ok=True)
    mask_app.OUTPUT_DIR = output_dir

    images = list(iter_images(input_dir))
    if not images:
        print(f"[INFO] no images found in {input_dir}")
        return 0

    to_process = []
    for img in images:
        if is_processed(output_dir, img):
            # already processed
            continue
        tgt = target_dir / f"{img.stem}.txt"
        if not tgt.exists():
            # skip if no per-image target
            continue
        # skip empty target file
        if not tgt.read_text(encoding="utf-8").strip():
            continue
        to_process.append((img, tgt))

    if not to_process:
        print("[INFO] no unprocessed images with target found")
        return 0

    processed = 0
    for img, tgt in to_process:
        try:
            print(f"[PROCESS] {img.name} using {tgt.name}")
            image_bytes = img.read_bytes()
            target_words = [line.strip() for line in tgt.read_text(encoding="utf-8").splitlines() if line.strip()]
            result = mask_app.process_image_bytes(
                image_bytes,
                img.name,
                target_words,
                color_ocr_strength=args.color_ocr_strength,
                grayscale_enabled=args.grayscale_enabled,
                output_image_mode=args.output_image_mode,
            )
            print(f"[OK] {img.name} -> {result.get('mask')} (masked: {result.get('masked')})")
            processed += 1
        except Exception as e:
            print(f"[ERROR] {img.name}: {e}")

    print(f"[INFO] processed {processed} image(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
