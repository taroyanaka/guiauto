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
DEFAULT_OCR_CONFIDENCE = 0.20
DEFAULT_USE_OCR_HYBRID = True
DEFAULT_UPSCALE_FACTOR = 4

_thread_local = threading.local()


def get_reader() -> easyocr.Reader:
    reader = getattr(_thread_local, "reader", None)
    if reader is None:
        try:
            reader = easyocr.Reader(["ja", "en"], gpu=True)
        except Exception:
            reader = easyocr.Reader(["ja", "en"], gpu=False)
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


def apply_white_balance(image: np.ndarray) -> np.ndarray:
    if hasattr(cv2, "xphoto") and hasattr(cv2.xphoto, "createSimpleWB"):
        try:
            wb = cv2.xphoto.createSimpleWB()
            return wb.balanceWhite(image)
        except cv2.error:
            pass
    return image


def upscale_image(image: np.ndarray, scale: int = DEFAULT_UPSCALE_FACTOR) -> np.ndarray:
    if scale <= 1:
        return image
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def preprocess_for_color_detection(image: np.ndarray) -> np.ndarray:
    return enhance_contrast(apply_white_balance(image))


def get_dynamic_kernel(image: np.ndarray, divisor: int = 100, minimum: int = 3, maximum: int = 15) -> np.ndarray:
    short_side = min(image.shape[:2])
    size = max(minimum, min(maximum, short_side // divisor))
    if size % 2 == 0:
        size += 1
    return np.ones((size, size), np.uint8)


def mask_from_boxes(shape: tuple[int, int], boxes: list[list[list[float]]], padding_ratio: float = 0.08) -> np.ndarray:
    height, width = shape
    mask = np.zeros((height, width), dtype=np.uint8)
    for box in boxes:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        x1 = max(0, int(np.floor(min(xs))))
        y1 = max(0, int(np.floor(min(ys))))
        x2 = min(width, int(np.ceil(max(xs))))
        y2 = min(height, int(np.ceil(max(ys))))
        pad_x = max(1, int((x2 - x1) * padding_ratio))
        pad_y = max(1, int((y2 - y1) * padding_ratio))
        cv2.rectangle(mask, (max(0, x1 - pad_x), max(0, y1 - pad_y)), (min(width, x2 + pad_x), min(height, y2 + pad_y)), 255, -1)
    return mask


def get_text_regions(image: np.ndarray, min_confidence: float = DEFAULT_OCR_CONFIDENCE) -> np.ndarray | None:
    reader = get_reader()
    boxes: list[list[list[float]]] = []
    for result in reader.readtext(image, text_threshold=0.25, low_text=0.15, link_threshold=0.15):
        bbox, _text, confidence = result
        if confidence >= min_confidence:
            boxes.append(bbox)
    if not boxes:
        return None
    return mask_from_boxes(image.shape[:2], boxes)


def rgb_red_mask(image: np.ndarray) -> np.ndarray:
    b_channel, g_channel, r_channel = cv2.split(image)
    return ((r_channel.astype(np.int16) - g_channel.astype(np.int16) > 30) & (r_channel.astype(np.int16) - b_channel.astype(np.int16) > 30))


def hsv_red_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    return (((hue < 15) | (hue > 160)) & (saturation > 25) & (value > 20))


def lab_red_mask(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    return lab[:, :, 1] > 130


def make_color_pixel_mask(
    image: np.ndarray,
    color_ocr_strength: str,
    color_target: str,
    roi_mask: np.ndarray | None = None,
) -> np.ndarray:
    prepared = preprocess_for_color_detection(image)
    threshold = get_color_thresholds(color_ocr_strength, color_target)
    hsv = cv2.cvtColor(prepared, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    hsv_pixels = (
        ((hue < threshold["hue_max"]) | (hue > threshold["hue_wrap_min"]))
        & (saturation > max(20, threshold["red_s"]))
        & (value > max(20, threshold["red_v"]))
    )
    color_pixels = (
        rgb_red_mask(prepared)
        | hsv_pixels
        | lab_red_mask(prepared)
    ).astype(np.uint8) * 255
    if roi_mask is not None:
        color_pixels = cv2.bitwise_and(color_pixels, roi_mask)

    kernel = np.ones((2, 2), np.uint8)
    color_pixels = cv2.morphologyEx(color_pixels, cv2.MORPH_CLOSE, kernel)
    return color_pixels


def component_boxes(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    boxes: list[tuple[int, int, int, int]] = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if area < 2:
            continue
        boxes.append((int(x), int(y), int(w), int(h)))
    return boxes


def filter_boxes(boxes: list[tuple[int, int, int, int]], image_shape: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    height, width = image_shape
    kept: list[tuple[int, int, int, int]] = []
    for x, y, w, h in boxes:
        area = w * h
        if area < 2 or area > (height * width):
            continue
        if w <= 0 or h <= 0:
            continue
        aspect_ratio = max(w / h, h / w)
        extent = 1.0
        if aspect_ratio > 12.0:
            continue
        if extent < 0.08:
            continue
        kept.append((x, y, w, h))
    return kept


def expand_box(x: int, y: int, w: int, h: int, image: np.ndarray) -> tuple[int, int, int, int]:
    pad = max(2, int(min(w, h) * 0.5))
    height, width = image.shape[:2]
    return (
        max(0, x - pad),
        max(0, y - pad),
        min(width, x + w + pad),
        min(height, y + h + pad),
    )


def mask_red_characters(
    image: np.ndarray,
    color_ocr_strength: str = DEFAULT_COLOR_OCR_STRENGTH,
    color_target: str = DEFAULT_COLOR_TARGET,
    use_ocr_hybrid: bool = DEFAULT_USE_OCR_HYBRID,
) -> np.ndarray:
    upscaled = upscale_image(image, DEFAULT_UPSCALE_FACTOR)
    height, width = upscaled.shape[:2]
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    roi_mask = get_text_regions(upscaled) if use_ocr_hybrid else None
    color_pixels = make_color_pixel_mask(upscaled, color_ocr_strength, color_target, roi_mask=roi_mask)
    if not np.any(color_pixels):
        return mask

    kernel = np.ones((2, 2), np.uint8)
    color_pixels = cv2.morphologyEx(color_pixels, cv2.MORPH_CLOSE, kernel)
    boxes = filter_boxes(component_boxes(color_pixels), (height, width))

    ocr_boxes: list[tuple[int, int, int, int]] = []
    if use_ocr_hybrid:
        reader = get_reader()
        for bbox, _text, confidence in reader.readtext(upscaled, text_threshold=0.25, low_text=0.15, link_threshold=0.15):
            if confidence < DEFAULT_OCR_CONFIDENCE:
                continue
            xs = [point[0] for point in bbox]
            ys = [point[1] for point in bbox]
            x1 = max(0, int(np.floor(min(xs))))
            y1 = max(0, int(np.floor(min(ys))))
            x2 = min(width, int(np.ceil(max(xs))))
            y2 = min(height, int(np.ceil(max(ys))))
            ocr_boxes.append((x1, y1, max(1, x2 - x1), max(1, y2 - y1)))

    for x, y, w, h in boxes:
        start_x, start_y, end_x, end_y = expand_box(x, y, w, h, upscaled)
        candidate = color_pixels[start_y:end_y, start_x:end_x]
        if candidate.size == 0:
            continue
        overlap = float(candidate.mean()) / 255.0
        if use_ocr_hybrid and ocr_boxes:
            matched = False
            for ox, oy, ow, oh in ocr_boxes:
                ix1 = max(start_x, ox)
                iy1 = max(start_y, oy)
                ix2 = min(end_x, ox + ow)
                iy2 = min(end_y, oy + oh)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                matched = True
                if overlap > 0.15:
                    cv2.rectangle(mask, (start_x, start_y), (end_x, end_y), (0, 0, 0, 255), -1)
                    break
            if not matched and overlap > 0.25:
                cv2.rectangle(mask, (start_x, start_y), (end_x, end_y), (0, 0, 0, 255), -1)
        elif overlap > 0.15:
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

    upscaled_mask = mask_red_characters(image, color_ocr_strength, color_target)
    mask = cv2.resize(upscaled_mask, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)
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
