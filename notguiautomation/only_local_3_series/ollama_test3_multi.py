#!/usr/bin/env python3
"""Process images like ollama_test3.py but save OCR per-image to ./target/<name>.txt

Usage:
  python ollama_test3_multi.py <image_or_dir>
"""
import os
import sys
from pathlib import Path

import ollama
from PIL import Image

model_name = "qwen2.5vl:7b"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
DEFAULT_NUM_GPU = int(os.environ.get("OLLAMA_NUM_GPU", "999"))


def is_red_pixel(r, g, b, r_min=120, diff_min=40, factor=1.1):
    return (r >= r_min) and ((r - max(g, b)) >= diff_min) and (r > g * factor) and (r > b * factor)


def iter_image_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(
            path for path in input_path.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
    raise FileNotFoundError(f"input path not found: {input_path}")


def process_image(img_path: Path, script_dir: Path) -> str:
    img = Image.open(img_path).convert("RGB")
    width, height = img.size

    rows = 2
    cols = 2
    w_chunk = width // cols
    h_chunk = height // rows

    full_text = ""

    for r in range(rows):
        for c in range(cols):
            left = c * w_chunk
            top = r * h_chunk
            right = (c + 1) * w_chunk if c < cols - 1 else width
            bottom = (r + 1) * h_chunk if r < rows - 1 else height

            chunk = img.crop((left, top, right, bottom))
            chunk_path = script_dir / "temp_chunks" / f"chunk_{r}_{c}.jpg"
            chunk.save(chunk_path)

            cw, ch = chunk.size
            out = Image.new("L", (cw, ch), 255)
            in_px = chunk.load()
            out_px = out.load()
            for y in range(ch):
                for x in range(cw):
                    pr, pg, pb = in_px[x, y]
                    out_px[x, y] = 0 if is_red_pixel(pr, pg, pb) else 255

            red_path = script_dir / "temp_red" / f"red_only_chunk_{r}_{c}.png"
            out.save(red_path)

            prompt = (
                "この画像に含まれている赤い文字のみを、行ごとにそのまま出力してください。"
                " 余計な説明は不要です。読めない部分は [?] としてください。"
            )

            response = ollama.chat(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [str(red_path)],
                    }
                ],
                options={"num_gpu": DEFAULT_NUM_GPU},
            )

            try:
                text = response["message"]["content"]
            except Exception:
                try:
                    text = response.message.content
                except Exception:
                    text = str(response)

            full_text += text + "\n"

    return full_text


def main():
    if len(sys.argv) < 2:
        print("Usage: python ollama_test3_multi.py <image_path_or_dir>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    script_dir = Path(__file__).resolve().parent
    target_dir = script_dir / "target"

    os.makedirs(script_dir / "temp_chunks", exist_ok=True)
    os.makedirs(script_dir / "temp_red", exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    image_paths = iter_image_paths(input_path)
    if not image_paths:
        print(f"No images found: {input_path}")
        return

    for image_path in image_paths:
        print(f"[INFO] processing {image_path.name}")
        text = process_image(image_path, script_dir)
        out_path = target_dir / f"{image_path.stem}.txt"
        out_path.write_text(text, encoding="utf-8")
        print(f"[SAVED] {out_path}")


if __name__ == "__main__":
    main()
