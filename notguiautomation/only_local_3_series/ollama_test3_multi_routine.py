#!/usr/bin/env python3
"""Routine runner for ollama_test3_multi: create per-image ./target/<name>.txt for images
that don't yet have a corresponding target file.

Usage:
  python ollama_test3_multi_routine.py --input-dir ./input_dir --target-dir ./target
"""
import argparse
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import ollama_test3_multi as worker


def parse_args():
    parser = argparse.ArgumentParser(description="Routine: generate missing target/<name>.txt using ollama_test3_multi")
    parser.add_argument("--input-dir", default=str(APP_DIR / "input_dir"))
    parser.add_argument("--target-dir", default=str(APP_DIR / "target"))
    parser.add_argument("--no-gpu", dest="gpu_enabled", action="store_false", default=True, help="Disable GPU (enabled by default)")
    return parser.parse_args()


def main():
    args = parse_args()
    input_dir = Path(args.input_dir)
    target_dir = Path(args.target_dir)
    script_dir = APP_DIR

    os.environ["OLLAMA_NUM_GPU"] = os.environ.get("OLLAMA_NUM_GPU", "999")
    os.environ["MASK_TEXT_USE_GPU"] = "1" if args.gpu_enabled else "0"

    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        images = worker.iter_image_paths(Path(input_dir))
    except Exception as e:
        print(f"[ERROR] failed to list images in {input_dir}: {e}")
        return 1

    to_process = []
    for img in images:
        tgt = target_dir / f"{img.stem}.txt"
        if tgt.exists():
            continue
        to_process.append(img)

    if not to_process:
        print("[INFO] no unprocessed images found")
        return 0

    for img in to_process:
        try:
            print(f"[PROCESS] {img.name}")
            text = worker.process_image(img, script_dir)
            out = target_dir / f"{img.stem}.txt"
            # atomic write: write to temp then replace
            tmp = out.with_suffix(out.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(str(tmp), str(out))
            print(f"[SAVED] {out}")
        except Exception as e:
            print(f"[ERROR] processing {img.name}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
