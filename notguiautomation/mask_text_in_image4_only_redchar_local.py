# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import threading

import cv2
import easyocr
import numpy as np


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "masked_outputs_local"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_COLOR_OCR_STRENGTH = "strong"
DEFAULT_COLOR_TARGET = "red_orange"

_thread_local = threading.local()


def get_reader() -> easyocr.Reader:
    reader = getattr(_thread_local, "reader", None)
    if reader is None:
        reader = easyocr.Reader(["ja", "en"], gpu=True)
        _thread_local.reader = reader
    return reader


def safe_filename(name: str, fallback: str = "image") -> str:
    stem = Path(name).stem or fallback
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        suffix = ".png"
    stem = "".join("_" if ch in '<>:"/\\|?*\x00-\x1f' else ch for ch in stem).strip(" ._")
    return f"{stem or fallback}{suffix}"


def get_color_thresholds(color_ocr_strength: str, color_target: str) -> dict[str, int]:
    strength_thresholds = {
        "weak": {"red_s": 45, "red_v": 35, "vivid_s": 85, "vivid_v": 45, "lab_a": 150},
        "medium": {"red_s": 34, "red_v": 28, "vivid_s": 68, "vivid_v": 36, "lab_a": 142},
        "strong": {"red_s": 18, "red_v": 18, "vivid_s": 44, "vivid_v": 26, "lab_a": 134},
    }
    target_thresholds = {
        "red": {"hue_min": 0, "hue_max": 15, "hue_wrap_min": 165, "hue_wrap_max": 180, "use_vivid": False, "use_lab": True},
        "red_orange": {"hue_min": 0, "hue_max": 25, "hue_wrap_min": 160, "hue_wrap_max": 180, "use_vivid": True, "use_lab": True},
        "warm": {"hue_min": 0, "hue_max": 35, "hue_wrap_min": 150, "hue_wrap_max": 180, "use_vivid": True, "use_lab": True},
    }
    thresholds = dict(strength_thresholds.get(color_ocr_strength, strength_thresholds["strong"]))
    thresholds.update(target_thresholds.get(color_target, target_thresholds["red_orange"]))
    return thresholds


def make_color_pixel_mask(image: np.ndarray, color_ocr_strength: str, color_target: str) -> np.ndarray:
    threshold = get_color_thresholds(color_ocr_strength, color_target)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    red_pixels = ((hue <= 20) | (hue >= 160)) & (saturation >= threshold["red_s"]) & (value >= threshold["red_v"])
    warm_pixels = (
        ((hue >= threshold["hue_min"]) & (hue <= threshold["hue_max"]))
        | ((hue >= threshold["hue_wrap_min"]) & (hue <= threshold["hue_wrap_max"]))
    ) & (saturation >= threshold["red_s"]) & (value >= threshold["red_v"])
    vivid_pixels = (
        (saturation >= threshold["vivid_s"]) & (value >= threshold["vivid_v"])
        if threshold["use_vivid"]
        else np.zeros_like(saturation, dtype=bool)
    )

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    a_channel = lab[:, :, 1]
    red_lab_pixels = (
        (a_channel >= threshold["lab_a"]) & (value >= threshold["red_v"])
        if threshold["use_lab"]
        else np.zeros_like(a_channel, dtype=bool)
    )

    color_pixels = (red_pixels | warm_pixels | vivid_pixels | red_lab_pixels).astype(np.uint8) * 255
    color_pixels = cv2.morphologyEx(color_pixels, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    if color_ocr_strength == "strong":
        color_pixels = cv2.dilate(color_pixels, np.ones((2, 2), np.uint8), iterations=1)
    return color_pixels


def mask_red_characters(
    image: np.ndarray,
    color_ocr_strength: str = DEFAULT_COLOR_OCR_STRENGTH,
    color_target: str = DEFAULT_COLOR_TARGET,
) -> np.ndarray:
    height, width = image.shape[:2]
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    color_pixels = make_color_pixel_mask(image, color_ocr_strength, color_target)
    if not np.any(color_pixels):
        return mask

    expanded = cv2.dilate(color_pixels, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(expanded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        if cv2.contourArea(contour) < 3:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        padding_x = max(1, w // 10)
        padding_y = max(1, h // 10)
        start_x = max(0, x - padding_x)
        start_y = max(0, y - padding_y)
        end_x = min(width, x + w + padding_x)
        end_y = min(height, y + h + padding_y)
        cv2.rectangle(mask, (start_x, start_y), (end_x, end_y), (0, 0, 0, 255), -1)

    return mask


def overlay_mask_on_image(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if image.shape[:2] != mask.shape[:2]:
        raise ValueError("image and mask size mismatch")
    result = image.copy()
    masked_area = mask[:, :, 3] > 0
    result[masked_area] = (0, 0, 0)
    return result


def build_output_names(original_name: str) -> tuple[str, str]:
    safe_name = safe_filename(original_name)
    path = Path(safe_name)
    return f"{path.stem}-original{path.suffix}", f"{path.stem}-mask.png"


def process_image_file(image_path: Path, color_ocr_strength: str, color_target: str) -> tuple[Path, Path]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {image_path}")

    mask = mask_red_characters(image, color_ocr_strength, color_target)
    composited = overlay_mask_on_image(image, mask)

    original_output_name, mask_output_name = build_output_names(image_path.name)
    original_output_path = OUTPUT_DIR / original_output_name
    mask_output_path = OUTPUT_DIR / mask_output_name

    if not cv2.imwrite(str(mask_output_path), mask):
        raise RuntimeError(f"failed to write mask output: {mask_output_path}")
    if not cv2.imwrite(str(original_output_path), composited):
        raise RuntimeError(f"failed to write original output: {original_output_path}")

    return original_output_path, mask_output_path


def iter_input_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"input directory not found: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"not a directory: {input_dir}")
    return sorted([path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mask red characters in all images under a folder.",
        epilog=(
            "Examples:\n"
            "  python .\\notguiautomation\\mask_text_in_image4_only_redchar_local.py .\\input_images\n"
            "  python .\\notguiautomation\\mask_text_in_image4_only_redchar_local.py .\\input_images --color-ocr-strength medium\n"
            "  python .\\notguiautomation\\mask_text_in_image4_only_redchar_local.py .\\input_images --color-target red\n"
            "  python .\\notguiautomation\\mask_text_in_image4_only_redchar_local.py .\\input_images --color-target warm --color-ocr-strength weak"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("input_dir", nargs="?", help="Folder containing input images")
    parser.add_argument(
        "--input-dir",
        dest="input_dir_option",
        help="Folder containing input images",
    )
    parser.add_argument(
        "--output-dir",
        "--output_dir",
        default=str(OUTPUT_DIR),
        help="Folder to write outputs to",
    )
    parser.add_argument(
        "--color-ocr-strength",
        default=DEFAULT_COLOR_OCR_STRENGTH,
        choices=("weak", "medium", "strong"),
        help="Threshold strength for red-ish color detection",
    )
    parser.add_argument(
        "--color-target",
        default=DEFAULT_COLOR_TARGET,
        choices=("red", "red_orange", "warm"),
        help="Color range to target: red only, red+orange, or broader warm colors",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir_value = args.input_dir_option or args.input_dir
    if not input_dir_value:
        raise SystemExit("input directory is required: use positional input_dir or --input-dir")
    input_dir = Path(input_dir_value)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    global OUTPUT_DIR
    OUTPUT_DIR = output_dir

    images = iter_input_images(input_dir)
    if not images:
        print(f"[INFO] no supported images found in {input_dir}")
        return 0

    print(f"[INFO] processing {len(images)} image(s) from {input_dir}")
    failures = 0
    for image_path in images:
        try:
            original_output_path, mask_output_path = process_image_file(
                image_path,
                args.color_ocr_strength,
                args.color_target,
            )
            print(f"[OK] {image_path.name} -> {original_output_path.name}, {mask_output_path.name}")
        except Exception as exc:
            failures += 1
            print(f"[ERROR] {image_path.name}: {exc}")

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
