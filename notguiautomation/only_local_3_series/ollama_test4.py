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

    # Create a single red-only mask for the whole image
    out = Image.new("L", (width, height), 255)
    in_px = img.load()
    out_px = out.load()
    for y in range(height):
        for x in range(width):
            pr, pg, pb = in_px[x, y]
            out_px[x, y] = 0 if is_red_pixel(pr, pg, pb) else 255

    red_path = script_dir / "temp_red" / f"red_only_{img_path.name}.png"
    out.save(red_path)

    prompt = "全ての文字出力"

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

    return text + "\n"


def main():
    if len(sys.argv) < 2:
        print("Usage: python ollama_test4.py <image_path_or_dir>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    script_dir = Path(__file__).resolve().parent
    target_path = script_dir / "all.txt"

    os.makedirs(script_dir / "temp_red", exist_ok=True)

    image_paths = iter_image_paths(input_path)
    if not image_paths:
        print(f"No images found: {input_path}")
        target_path.write_text("", encoding="utf-8")
        return

    all_text = ""
    for image_path in image_paths:
        print(f"[INFO] processing {image_path.name}")
        all_text += process_image(image_path, script_dir)

    print(all_text, end="")
    target_path.write_text(all_text, encoding="utf-8")


if __name__ == "__main__":
    main()
