# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

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
        description="Mask text in every image under input_dir using per-image target files under ./target/."
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help="Directory containing source images",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where masked images will be written",
    )
    parser.add_argument(
        "--target-dir",
        default=str(DEFAULT_TARGET_DIR),
        help="Directory containing per-image target text files",
    )
    parser.add_argument(
        "--color-ocr-strength",
        default="strong",
        choices=("weak", "medium", "strong"),
        help="OCR tuning for colored text",
    )
    parser.add_argument(
        "--grayscale-enabled",
        action="store_true",
        help="Convert input to grayscale before OCR",
    )
    parser.add_argument(
        "--output-image-mode",
        default="original",
        choices=("original", "grayscale"),
        help="Store original image or grayscale variant locally",
    )
    parser.add_argument(
        "--no-gpu",
        dest="gpu_enabled",
        action="store_false",
        default=True,
        help="Disable GPU for OCR (GPU is enabled by default)",
    )
    return parser.parse_args()


def load_target_words(target_file: Path) -> list[str]:
    if not target_file.exists():
        return []

    words: list[str] = []
    for line in target_file.read_text(encoding="utf-8").splitlines():
        word = line.strip()
        if word:
            words.append(word)
    return words


def iter_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"input dir not found: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"input path is not a directory: {input_dir}")

    images = sorted(
        path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    return images


def main() -> int:
    args = parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    target_dir = Path(args.target_dir)

    os.environ["MASK_TEXT_USE_GPU"] = "1" if args.gpu_enabled else "0"
    output_dir.mkdir(parents=True, exist_ok=True)
    mask_app.OUTPUT_DIR = output_dir

    images = iter_images(input_dir)
    if not images:
        print(f"[INFO] no images found in {input_dir}")
        return 0

    processed = 0
    for image_path in images:
        # per-image target file expected at target_dir/<stem>.txt
        tgt_file = target_dir / f"{image_path.stem}.txt"
        if not tgt_file.exists():
            print(f"[SKIP] no target file for {image_path.name}: expected {tgt_file}")
            continue

        target_words = load_target_words(tgt_file)
        if not target_words:
            print(f"[SKIP] empty target file for {image_path.name}: {tgt_file}")
            continue

        image_bytes = image_path.read_bytes()
        result = mask_app.process_image_bytes(
            image_bytes,
            image_path.name,
            target_words,
            color_ocr_strength=args.color_ocr_strength,
            grayscale_enabled=args.grayscale_enabled,
            output_image_mode=args.output_image_mode,
        )

        print(f"[OK] {image_path.name} -> {result.get('mask')} (masked: {result.get('masked')})")
        processed += 1

    print(f"[INFO] processed {processed} image(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
