# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import ollama
from PIL import Image


APP_DIR = Path(__file__).resolve().parent
INPUT_DIR = APP_DIR / "input_dir_mask_text_in_image5"
OUTPUT_DIR = APP_DIR / "output_dir_mask_text_in_image5"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_MODEL = "qwen2.5vl:7b"
DEFAULT_ROWS = 2
DEFAULT_COLS = 2
DEFAULT_OUTPUT_IMAGE_MODE = "original"


def safe_filename(name: str, fallback: str = "image") -> str:
    stem = Path(name).stem or fallback
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        suffix = ".png"
    stem = "".join("_" if ch in '<>:"/\\|?*\x00-\x1f' else ch for ch in stem).strip(" ._")
    return f"{stem or fallback}{suffix}"


def normalize_text(text: str) -> str:
    return text.replace(" ", "").replace("\u3000", "").replace("\n", "").strip()


def is_red_pixel(r: int, g: int, b: int, r_min: int = 120, diff_min: int = 40, factor: float = 1.1) -> bool:
    return (r >= r_min) and ((r - max(g, b)) >= diff_min) and (r > g * factor) and (r > b * factor)


def parse_ollama_text(response: Any) -> str:
    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return str(message.get("content", ""))
    message = getattr(response, "message", None)
    if message is not None:
        return str(getattr(message, "content", ""))
    return str(response)


def get_ollama_mode() -> str:
    return "gpu"


def parse_bbox_list_from_text(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key in ("items", "results", "data"):
                value = parsed.get(key)
                if isinstance(value, list):
                    parsed = value
                    break
        if isinstance(parsed, list):
            items: list[dict[str, Any]] = []
            for obj in parsed:
                if not isinstance(obj, dict):
                    continue
                label = str(obj.get("text") or obj.get("label") or "").strip()
                try:
                    x = int(float(obj.get("x", 0)))
                    y = int(float(obj.get("y", 0)))
                    w = int(float(obj.get("width", obj.get("w", 0))))
                    h = int(float(obj.get("height", obj.get("h", 0))))
                except Exception:
                    continue
                items.append({"text": label, "x": x, "y": y, "width": w, "height": h})
            return items
    except Exception:
        pass

    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        cleaned = normalize_text(line)
        if cleaned:
            items.append({"text": cleaned})
    return items


def extract_red_texts_and_bboxes_from_ollama(
    image_path: Path,
    model_name: str,
    rows: int,
    cols: int,
) -> list[dict[str, Any]]:
    ollama_mode = get_ollama_mode()
    print(f"[INFO] Ollama is running in {ollama_mode} mode")
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    w_chunk = width // cols
    h_chunk = height // rows

    extracted: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in range(rows):
        for col in range(cols):
            left = col * w_chunk
            top = row * h_chunk
            right = (col + 1) * w_chunk if col < cols - 1 else width
            bottom = (row + 1) * h_chunk if row < rows - 1 else height
            chunk = image.crop((left, top, right, bottom))
            chunk_w, chunk_h = chunk.size

            red_binary = Image.new("L", (chunk_w, chunk_h), 255)
            in_px = chunk.load()
            out_px = red_binary.load()
            for y in range(chunk_h):
                for x in range(chunk_w):
                    pr, pg, pb = in_px[x, y]
                    out_px[x, y] = 0 if is_red_pixel(pr, pg, pb) else 255

            red_bytes = BytesIO()
            red_binary.save(red_bytes, format="PNG")
            red_data = red_bytes.getvalue()

            prompt = (
                "この画像に含まれている赤色の文字だけを、可能なら JSON 配列で返してください。"
                ' 各要素は {"text":"...","x":0,"y":0,"width":10,"height":5} の形式です。'
                " 読めない部分は [?] としてください。"
            )

            print(f"[INFO] Ollama request start: mode={ollama_mode}, chunk={row}-{col}, model={model_name}")
            response = ollama.chat(
                model=model_name,
                messages=[{"role": "user", "content": prompt, "images": [red_data]}],
            )
            print(f"[INFO] Ollama request done: mode={ollama_mode}, chunk={row}-{col}")

            raw_text = parse_ollama_text(response)
            print(raw_text)
            items = parse_bbox_list_from_text(raw_text)
            if items:
                print(f"[INFO] recognized texts for chunk {row}-{col}:")
                for item in items:
                    print(item.get("text", ""))
            else:
                print(f"[INFO] recognized texts for chunk {row}-{col}: <none>")

            for item in items:
                text = normalize_text(str(item.get("text", "")))
                if not text or text in seen:
                    continue
                seen.add(text)
                item["text"] = text
                item["x"] = left + int(item.get("x", 0))
                item["y"] = top + int(item.get("y", 0))
                item["width"] = max(4, int(item.get("width", 0)))
                item["height"] = max(4, int(item.get("height", 0)))
                item["area_row"] = row
                item["area_col"] = col
                extracted.append(item)

    return extracted


def mask_texts_from_ollama_bbox(image: np.ndarray, target_texts_with_bbox: list[dict[str, Any]]) -> np.ndarray:
    height, width = image.shape[:2]
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    for item in target_texts_with_bbox:
        x = int(item.get("x", 0))
        y = int(item.get("y", 0))
        w = int(item.get("width", 1))
        h = int(item.get("height", 1))
        padding = 2
        start_x = max(0, x - padding)
        end_x = min(width, x + w + padding)
        start_y = max(0, y - padding)
        end_y = min(height, y + h + padding)
        cv2.rectangle(mask, (start_x, start_y), (end_x, end_y), (0, 0, 0, 255), -1)
    return mask


def overlay_mask_on_image(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if image.shape[:2] != mask.shape[:2]:
        raise ValueError("image and mask size mismatch")
    result = image.copy()
    result[mask[:, :, 3] > 0] = (0, 0, 0)
    return result


def build_output_names(original_name: str) -> tuple[str, str, str]:
    safe_name = safe_filename(original_name)
    path = Path(safe_name)
    return path.name, f"{path.stem}-original{path.suffix}", f"{path.stem}-mask.png"


def is_supported_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES


def iter_input_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"input directory not found: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"not a directory: {input_dir}")
    return sorted([path for path in input_dir.iterdir() if is_supported_image(path)])


def process_image_file(
    image_path: Path,
    model_name: str,
    rows: int,
    cols: int,
    output_image_mode: str,
) -> tuple[Path, Path, Path]:
    image_bytes = image_path.read_bytes()
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {image_path}")

    output_original_name, output_masked_name, output_mask_name = build_output_names(image_path.name)
    output_original_path = OUTPUT_DIR / output_original_name
    output_masked_path = OUTPUT_DIR / output_masked_name
    output_mask_path = OUTPUT_DIR / output_mask_name

    target_texts = extract_red_texts_and_bboxes_from_ollama(image_path, model_name, rows, cols)
    mask = mask_texts_from_ollama_bbox(image, target_texts)
    masked = overlay_mask_on_image(image, mask)

    output_original_path.write_bytes(image_bytes)
    if output_image_mode == "grayscale":
        gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)
        if not cv2.imwrite(str(output_masked_path), gray):
            raise RuntimeError(f"failed to write grayscale masked image: {output_masked_path}")
    else:
        if not cv2.imwrite(str(output_masked_path), masked):
            raise RuntimeError(f"failed to write masked image: {output_masked_path}")

    if not cv2.imwrite(str(output_mask_path), mask):
        raise RuntimeError(f"failed to write mask image: {output_mask_path}")

    return output_original_path, output_masked_path, output_mask_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mask red text using Ollama-derived text and Ollama-derived bbox coordinates.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--input-dir", default=str(INPUT_DIR), help="Folder containing input images")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Folder to write outputs to")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama vision model name")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="Chunk rows for Ollama recognition")
    parser.add_argument("--cols", type=int, default=DEFAULT_COLS, help="Chunk cols for Ollama recognition")
    parser.add_argument(
        "--output-image-mode",
        default=DEFAULT_OUTPUT_IMAGE_MODE,
        choices=("original", "grayscale"),
        help="Save masked image as original color or grayscale variant",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
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
            original_path, masked_path, mask_path = process_image_file(
                image_path=image_path,
                model_name=args.model,
                rows=args.rows,
                cols=args.cols,
                output_image_mode=args.output_image_mode,
            )
            print(f"[OK] {image_path.name} -> {original_path.name}, {masked_path.name}, {mask_path.name}")
        except Exception as exc:
            failures += 1
            print(f"[ERROR] {image_path.name}: {exc}")

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
