# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
from typing import Any
from io import BytesIO

import cv2
import easyocr
import numpy as np
import ollama
from PIL import Image


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "masked_outputs_ollama"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_MODEL = "qwen2.5vl:7b"
DEFAULT_ROWS = 2
DEFAULT_COLS = 2

_thread_local = threading.local()


def get_reader() -> easyocr.Reader:
    reader = getattr(_thread_local, "reader", None)
    if reader is None:
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


def normalize_text(text: str) -> str:
    return text.replace(" ", "").replace("\u3000", "").replace("\n", "").strip()


def parse_ollama_text(response: Any) -> str:
    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return str(message.get("content", ""))
    message = getattr(response, "message", None)
    if message is not None:
        return str(getattr(message, "content", ""))
    return str(response)


def extract_json_block(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def extract_red_texts_from_image(image_path: Path, model_name: str, rows: int, cols: int) -> list[str]:
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    w_chunk = width // cols
    h_chunk = height // rows

    extracted: list[str] = []
    seen: set[str] = set()

    for row in range(rows):
        for col in range(cols):
            left = col * w_chunk
            top = row * h_chunk
            right = (col + 1) * w_chunk if col < cols - 1 else width
            bottom = (row + 1) * h_chunk if row < rows - 1 else height
            chunk = image.crop((left, top, right, bottom))

            prompt = (
                "この画像にある赤い文字列だけを抽出してください。"
                "余計な説明は不要です。"
                "複数ある場合は改行区切りで出してください。"
                "不明なら空行にしてください。"
            )

            # PIL Image をバイト列に変換
            chunk_bytes = BytesIO()
            chunk.save(chunk_bytes, format='PNG')
            chunk_bytes.seek(0)
            chunk_data = chunk_bytes.getvalue()

            response = ollama.chat(
                model=model_name,
                messages=[{"role": "user", "content": prompt, "images": [chunk_data]}],
            )
            text = parse_ollama_text(response)

            try:
                payload = json.loads(extract_json_block(text))
                candidates = payload.get("texts", []) if isinstance(payload, dict) else []
                if isinstance(candidates, list):
                    for candidate in candidates:
                        cleaned = normalize_text(str(candidate))
                        if cleaned and cleaned not in seen:
                            seen.add(cleaned)
                            extracted.append(cleaned)
                    continue
            except Exception:
                pass

            for line in text.splitlines():
                cleaned = normalize_text(line)
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    extracted.append(cleaned)

    return extracted


def make_ocr_variants(image: np.ndarray) -> list[np.ndarray]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR)
    return [image, enhanced]


def read_text_variants(image: np.ndarray) -> list[tuple[Any, str, float]]:
    reader = get_reader()
    results: list[tuple[Any, str, float]] = []
    seen: set[tuple[str, int, int, int, int]] = set()

    for variant in make_ocr_variants(image):
        for bbox, text, prob in reader.readtext(variant):
            xs = [point[0] for point in bbox]
            ys = [point[1] for point in bbox]
            key = (normalize_text(text), round(min(xs) / 8), round(min(ys) / 8), round(max(xs) / 8), round(max(ys) / 8))
            if key in seen:
                continue
            seen.add(key)
            results.append((bbox, text, prob))
    return results


def mask_texts_from_ocr(image: np.ndarray, target_texts: list[str]) -> np.ndarray:
    height, width = image.shape[:2]
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    if not target_texts:
        return mask

    targets = [normalize_text(text) for text in target_texts if normalize_text(text)]
    if not targets:
        return mask

    results = read_text_variants(image)
    for bbox, text, _prob in results:
        cleaned = normalize_text(text)
        if not cleaned:
            continue
        for target in targets:
            start_idx = cleaned.find(target)
            while start_idx != -1:
                tl, tr, br, _bl = bbox
                raw_len = max(len(cleaned), 1)
                full_width = tr[0] - tl[0]
                char_width = full_width / raw_len
                word_start_x = int(tl[0] + (char_width * start_idx))
                word_end_x = int(tl[0] + (char_width * (start_idx + len(target))))
                padding = 4
                start_x = max(0, word_start_x - padding)
                end_x = min(width, word_end_x + padding)
                box_height = br[1] - tl[1]
                start_y = max(0, int(tl[1] - box_height * 0.1))
                end_y = min(height, int(br[1] + box_height * 0.1))
                cv2.rectangle(mask, (start_x, start_y), (end_x, end_y), (0, 0, 0, 255), -1)
                start_idx = cleaned.find(target, start_idx + 1)

    return mask


def overlay_mask_on_image(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if image.shape[:2] != mask.shape[:2]:
        raise ValueError("image and mask size mismatch")
    result = image.copy()
    result[mask[:, :, 3] > 0] = (0, 0, 0)
    return result


def build_output_names(original_name: str) -> tuple[str, str]:
    safe_name = safe_filename(original_name)
    path = Path(safe_name)
    return f"{path.stem}-original{path.suffix}", f"{path.stem}-mask.png"


def process_image_file(image_path: Path) -> tuple[Path, Path]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {image_path}")

    extracted_texts = extract_red_texts_from_image(image_path, DEFAULT_MODEL, DEFAULT_ROWS, DEFAULT_COLS)
    mask = mask_texts_from_ocr(image, extracted_texts)
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
        description="Extract red text strings with Ollama, search them in images, and output mask/composited images.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python .\\notguiautomation\\mask_text_in_image4_only_redchar_local_ollama.py .\\masked_test\n"
            "  python .\\notguiautomation\\mask_text_in_image4_only_redchar_local_ollama.py .\\masked_test --output-dir .\\masked_outputs_ollama"
        ),
    )
    parser.add_argument("input_dir", nargs="?", help="Folder containing input images")
    parser.add_argument("--input-dir", dest="input_dir_option", help="Folder containing input images")
    parser.add_argument("--output-dir", "--output_dir", default=str(OUTPUT_DIR), help="Folder to write outputs to")
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
            original_output_path, mask_output_path = process_image_file(image_path)
            print(f"[OK] {image_path.name} -> {original_output_path.name}, {mask_output_path.name}")
        except Exception as exc:
            failures += 1
            print(f"[ERROR] {image_path.name}: {exc}")

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
