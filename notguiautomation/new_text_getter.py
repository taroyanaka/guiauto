# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import threading

import cv2
import easyocr
import numpy as np


APP_DIR = Path(__file__).resolve().parent
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_OUTPUT_DIR = APP_DIR / "text_outputs"
DEFAULT_UPSCALE_FACTOR = 4
DEFAULT_CONFIDENCE = 0.20
DEFAULT_TILE_ROWS = 4
DEFAULT_TILE_COLS = 4

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


def upscale_image(image: np.ndarray, scale: int = DEFAULT_UPSCALE_FACTOR) -> np.ndarray:
    if scale <= 1:
        return image
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def preprocess_image(image: np.ndarray) -> np.ndarray:
    return upscale_image(enhance_contrast(image))


def split_image_grid(image: np.ndarray, rows: int = DEFAULT_TILE_ROWS, cols: int = DEFAULT_TILE_COLS) -> list[tuple[int, int, np.ndarray]]:
    if rows <= 0 or cols <= 0:
        raise ValueError("rows and cols must be positive")

    height, width = image.shape[:2]
    y_edges = np.linspace(0, height, rows + 1, dtype=int)
    x_edges = np.linspace(0, width, cols + 1, dtype=int)

    tiles: list[tuple[int, int, np.ndarray]] = []
    for row in range(rows):
        y0, y1 = y_edges[row], y_edges[row + 1]
        for col in range(cols):
            x0, x1 = x_edges[col], x_edges[col + 1]
            tile = image[y0:y1, x0:x1]
            if tile.size == 0:
                continue
            tiles.append((row, col, tile))
    return tiles


def iter_input_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"input directory not found: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"not a directory: {input_dir}")
    return sorted(
        path for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def decode_image(path: Path) -> np.ndarray:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to decode image: {path}")
    return image


def normalize_text(text: str) -> str:
    return text.replace("\n", " ").replace("\r", " ").strip()


def extract_text_from_image(image: np.ndarray, min_confidence: float = DEFAULT_CONFIDENCE) -> list[str]:
    reader = get_reader()
    preprocessed = preprocess_image(image)
    lines: list[str] = []
    seen: set[str] = set()

    for _bbox, text, confidence in reader.readtext(
        preprocessed,
        text_threshold=0.25,
        low_text=0.15,
        link_threshold=0.15,
    ):
        if confidence < min_confidence:
            continue
        cleaned = normalize_text(text)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        lines.append(cleaned)

    return lines


def extract_text_from_image_grid(
    image: np.ndarray,
    rows: int = DEFAULT_TILE_ROWS,
    cols: int = DEFAULT_TILE_COLS,
    min_confidence: float = DEFAULT_CONFIDENCE,
) -> list[str]:
    lines: list[str] = []
    for row, col, tile in split_image_grid(image, rows=rows, cols=cols):
        tile_lines = extract_text_from_image(tile, min_confidence=min_confidence)
        lines.append(f"[tile r{row + 1} c{col + 1}]")
        if tile_lines:
            lines.extend(tile_lines)
        else:
            lines.append("")
        lines.append("")
    return lines


def build_output_path(output_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return output_dir / f"result_{timestamp}.txt"


def process_folder(input_dir: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    images = iter_input_images(input_dir)
    if not images:
        raise FileNotFoundError(f"no supported images found in: {input_dir}")

    output_path = build_output_path(output_dir)
    chunks: list[str] = []

    for image_path in images:
        image = decode_image(image_path)
        lines = extract_text_from_image_grid(image)
        chunks.append(f"[{image_path.name}]")
        if lines:
            chunks.extend(lines)
        else:
            chunks.append("")
        chunks.append("")

    output_path.write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract text from all images in a folder and write it to a timestamped txt file.",
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
        default=str(DEFAULT_OUTPUT_DIR),
        help="Folder to write the result txt file to",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir_value = args.input_dir_option or args.input_dir
    if not input_dir_value:
        raise SystemExit("input directory is required: use positional input_dir or --input-dir")

    input_dir = Path(input_dir_value)
    output_dir = Path(args.output_dir)
    output_path = process_folder(input_dir, output_dir)
    print(f"[OK] wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
