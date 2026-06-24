# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from io import BytesIO
import json

import cv2
import easyocr
import numpy as np
import ollama
from PIL import Image


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "masked_outputs_ollama_v6"
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_MODEL = "qwen2.5vl:7b"
DEFAULT_ROWS = 2
DEFAULT_COLS = 2

_reader = None


def get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["ja", "en"], gpu=True)
    return _reader


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


def parse_bbox_list_from_text(text: str) -> list[dict[str, Any]]:
    """Try to parse Ollama response as JSON list of objects with keys: text,x,y,width,height.
    Fallback to line-based parsing. """
    text = text.strip()
    if not text:
        return []
    # Try JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # common wrapper
            for key in ("items", "results", "data"):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
        if isinstance(parsed, list):
            out = []
            for obj in parsed:
                if not isinstance(obj, dict):
                    continue
                textv = str(obj.get("text") or obj.get("label") or "").strip()
                try:
                    x = int(obj.get("x", 0))
                    y = int(obj.get("y", 0))
                    w = int(obj.get("width", obj.get("w", 0)))
                    h = int(obj.get("height", obj.get("h", 0)))
                except Exception:
                    continue
                out.append({"text": textv, "x": x, "y": y, "width": w, "height": h})
            return out
    except Exception:
        pass

    # Fallback: line based. Expect lines like: text	 x,y,w,h  OR text ||| x,y,w,h
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = None
        if "|||" in line:
            parts = [p.strip() for p in line.split("|||", 1)]
        elif "\t" in line:
            parts = [p.strip() for p in line.split("\t", 1)]
        elif "|" in line:
            parts = [p.strip() for p in line.split("|", 1)]
        else:
            # try to find trailing coordinates in parentheses
            if line.endswith(")") and "(" in line:
                idx = line.rfind("(")
                parts = [line[:idx].strip(), line[idx + 1:-1].strip()]
        if not parts:
            continue
        txt = parts[0]
        coord = parts[1]
        coord = coord.replace(" ", "")
        coord = coord.replace("[", "").replace("]", "")
        # split by comma
        nums = coord.split(",")
        if len(nums) >= 4:
            try:
                x = int(float(nums[0]))
                y = int(float(nums[1]))
                w = int(float(nums[2]))
                h = int(float(nums[3]))
                out.append({"text": txt, "x": x, "y": y, "width": w, "height": h})
            except Exception:
                continue
    return out


def extract_red_texts_and_bboxes_from_ollama(image_path: Path, model_name: str, rows: int, cols: int) -> list[dict[str, Any]]:
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
                    if is_red_pixel(pr, pg, pb):
                        out_px[x, y] = 0
                    else:
                        out_px[x, y] = 255

            red_bytes = BytesIO()
            red_binary.save(red_bytes, format='PNG')
            red_bytes.seek(0)
            red_data = red_bytes.getvalue()

            # Prompt: request JSON list of objects with local bbox coordinates
            prompt = (
                "以下は、送信した白背景上の黒色で赤文字のみを残した画像です。"
                " この画像に写っている赤色の文字列それぞれについて、"
                "ローカル座標（画像左上が(0,0)）でのバウンディングボックスを整数で出力してください。"
                " 出力はJSON配列で、各要素は {\"text\": " +
                '"...\", \"x\":0, \"y\":0, \"width\":10, \"height\":5} の形にしてください。'
                " 不要な説明は書かないでください。認識できない箇所は text を \"[?]\" としてください。"
            )

            response = ollama.chat(
                model=model_name,
                messages=[{"role": "user", "content": prompt, "images": [red_data]}],
            )
            text = parse_ollama_text(response)
            print(f"[DEBUG] area {row}-{col}: Ollama raw response (first150) = {text[:150]}")

            items = parse_bbox_list_from_text(text)
            if not items:
                # No structured response — fallback: treat each non-empty line as text without bbox
                for line in text.splitlines():
                    cleaned = normalize_text(line)
                    if cleaned and cleaned not in seen:
                        seen.add(cleaned)
                        extracted.append({
                            "text": cleaned,
                            "x": left,
                            "y": top,
                            "width": right - left,
                            "height": bottom - top,
                            "area_row": row,
                            "area_col": col,
                        })
            else:
                for obj in items:
                    cleaned = normalize_text(obj.get("text", ""))
                    if not cleaned or cleaned in seen:
                        continue
                    seen.add(cleaned)
                    # local -> global
                    x_local = int(obj.get("x", 0))
                    y_local = int(obj.get("y", 0))
                    w_local = int(obj.get("width", 0))
                    h_local = int(obj.get("height", 0))
                    extracted.append({
                        "text": cleaned,
                        "x": left + x_local,
                        "y": top + y_local,
                        "width": max(w_local, 4),
                        "height": max(h_local, 4),
                        "area_row": row,
                        "area_col": col,
                    })

    return extracted


def mask_texts_from_ollama_bbox(image: np.ndarray, target_texts_with_bbox: list[dict[str, Any]]) -> np.ndarray:
    height, width = image.shape[:2]
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    if not target_texts_with_bbox:
        return mask

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


def build_output_names(original_name: str) -> tuple[str, str]:
    safe_name = safe_filename(original_name)
    path = Path(safe_name)
    return f"{path.stem}-original{path.suffix}", f"{path.stem}-mask.png"


def process_image_file(image_path: Path) -> tuple[Path, Path]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {image_path}")

    extracted = extract_red_texts_and_bboxes_from_ollama(image_path, DEFAULT_MODEL, DEFAULT_ROWS, DEFAULT_COLS)
    print(f"[DEBUG] {image_path.name}: extracted items: {[d['text'] for d in extracted]}")

    mask = mask_texts_from_ollama_bbox(image, extracted)
    composited = overlay_mask_on_image(image, mask)

    original_output_name, mask_output_name = build_output_names(image_path.name)
    original_output_path = OUTPUT_DIR / original_output_name
    mask_output_path = OUTPUT_DIR / mask_output_name

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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
        description="Extract red text strings with Ollama (including bbox detection by Ollama), and output mask/composited images.",
        formatter_class=argparse.RawTextHelpFormatter,
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
