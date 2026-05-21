# -*- coding: utf-8 -*-
# セットアップ方法:
#   1. Python をインストールしてください。
#   2. コマンドプロンプトまたは PowerShell で、このプロジェクトのフォルダへ移動します。
#      例: cd C:\Users\taroyanaka\Downloads\guiauto
#   3. 必要なライブラリをインストールします。
#      pip install easyocr opencv-python numpy
#
# 使い方:
#   1. 次のコマンドでアプリを起動します。
#      python .\notguiautomation\mask_text_in_image.py
#   2. ブラウザが自動で開きます。開かない場合は、表示された http://127.0.0.1:xxxx/ を開きます。
#   3. 黒塗り対象の文字列を textarea に改行区切りで入力します。
#      空白の場合は実行されません。
#   4. 画像をドラッグ&ドロップします。複数ファイルをまとめて処理できます。
#      ファイル未選択の場合は、このスクリプトと同じフォルダの input.png を処理します。
#   5. 赤文字などの色付き文字がOCRされにくい場合は、
#      「色付き文字の検出強度」を弱/中/強から選んで実行します。
#   6. 実行後、ページ下部でマスクあり/なしのプレビューを切り替えられます。
#   7. 出力は notguiautomation\masked_outputs に保存されます。
#      元画像: 元ファイル名-original[拡張子]
#      透過マスク: 元ファイル名-mask.png
#
# 出力仕様:
#   - mask.png は常に透過PNGです。
#   - 元画像と同じピクセル幅・高さで生成されます。
#   - 背景は完全透明で、黒塗り対象部分だけが真っ黒の不透明ピクセルになります。

"""
画像内の指定文字列を黒塗りするローカルブラウザアプリ。

必要なライブラリ:
    pip install easyocr opencv-python numpy

使い方:
    python mask_text_in_image.py
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import cv2
import easyocr
import numpy as np


APP_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = APP_DIR / "input.png"
OUTPUT_DIR = APP_DIR / "masked_outputs"
MAX_WORKERS = min(2, max(1, os.cpu_count() or 1))
TARGET_WORDS_DEFAULT = [
    "分離",
    "精製",
    "純物質",
    "混合物",
    "ろ過",
    "融点",
    "沸点",
    "蒸留",
    "分留",
    "昇華法",
    "再結晶",
    "抽出",
    "クロマトグラフィー",
    "元素",
    "元素記号",
    "単体",
    "化合物",
    "同素体",
    "炎色反応",
    "白色",
    "青色",
    "拡散",
    "熱運動",
    "状態の三態",
    "ふってん",
    "ゆうてん",
    "じゅんぶっしつ",
    "こんごうぶつ",
]

_thread_local = threading.local()


def get_reader() -> easyocr.Reader:
    """EasyOCR reader is heavy, so reuse one per worker thread."""
    reader = getattr(_thread_local, "reader", None)
    if reader is None:
        reader = easyocr.Reader(["ja", "en"], gpu=False)
        _thread_local.reader = reader
    return reader


def parse_targets(raw_text: str) -> list[str]:
    return [line.strip() for line in raw_text.splitlines() if line.strip()]


def safe_filename(name: str, fallback: str = "image") -> str:
    stem = Path(name).stem or fallback
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
        suffix = ".png"
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem).strip(" ._")
    return f"{stem or fallback}{suffix}"


def normalize_ocr_text(text: str) -> str:
    return text.replace(" ", "").replace("　", "")


def make_contrast_variant(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def get_color_thresholds(color_ocr_strength: str) -> dict[str, int]:
    thresholds = {
        "weak": {"red_s": 45, "red_v": 35, "vivid_s": 85, "vivid_v": 45, "lab_a": 150},
        "medium": {"red_s": 34, "red_v": 28, "vivid_s": 68, "vivid_v": 36, "lab_a": 142},
        "strong": {"red_s": 18, "red_v": 18, "vivid_s": 44, "vivid_v": 26, "lab_a": 134},
    }
    return thresholds.get(color_ocr_strength, thresholds["strong"])


def make_color_pixel_mask(image: np.ndarray, color_ocr_strength: str) -> np.ndarray:
    threshold = get_color_thresholds(color_ocr_strength)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    red_pixels = (
        ((hue <= 20) | (hue >= 160))
        & (saturation >= threshold["red_s"])
        & (value >= threshold["red_v"])
    )
    vivid_pixels = (saturation >= threshold["vivid_s"]) & (value >= threshold["vivid_v"])

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    a_channel = lab[:, :, 1]
    red_lab_pixels = (a_channel >= threshold["lab_a"]) & (value >= threshold["red_v"])

    color_pixels = (red_pixels | vivid_pixels | red_lab_pixels).astype(np.uint8) * 255
    close_kernel = np.ones((2, 2), np.uint8)
    color_pixels = cv2.morphologyEx(color_pixels, cv2.MORPH_CLOSE, close_kernel)

    if color_ocr_strength == "strong":
        color_pixels = cv2.dilate(color_pixels, np.ones((2, 2), np.uint8), iterations=1)

    return color_pixels


def make_colored_text_variant(image: np.ndarray, color_ocr_strength: str) -> np.ndarray:
    color_pixels = make_color_pixel_mask(image, color_ocr_strength)
    text_like = np.full(image.shape[:2], 255, dtype=np.uint8)
    text_like[color_pixels > 0] = 0
    return cv2.cvtColor(text_like, cv2.COLOR_GRAY2BGR)


def make_red_ink_context_variant(image: np.ndarray, color_ocr_strength: str) -> np.ndarray:
    color_pixels = make_color_pixel_mask(image, color_ocr_strength)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    light_gray = cv2.addWeighted(gray, 0.35, np.full_like(gray, 255), 0.65, 0)
    light_gray[color_pixels > 0] = 0
    return cv2.cvtColor(light_gray, cv2.COLOR_GRAY2BGR)


def upscale_variant(image: np.ndarray, scale: float) -> np.ndarray:
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def make_ocr_variants(image: np.ndarray, color_ocr_strength: str) -> list[tuple[np.ndarray, float]]:
    variants: list[tuple[np.ndarray, float]] = [
        (image, 1.0),
        (make_contrast_variant(image), 1.0),
    ]
    if color_ocr_strength != "off":
        colored_text = make_colored_text_variant(image, color_ocr_strength)
        red_context = make_red_ink_context_variant(image, color_ocr_strength)
        variants.append((colored_text, 1.0))
        variants.append((red_context, 1.0))

        if color_ocr_strength in {"medium", "strong"}:
            scale = 1.5 if color_ocr_strength == "medium" else 2.0
            variants.append((upscale_variant(colored_text, scale), scale))
            variants.append((upscale_variant(red_context, scale), scale))
    return variants


def scale_bbox_to_original(bbox: Any, scale: float) -> list[list[float]]:
    if scale == 1.0:
        return bbox
    return [[point[0] / scale, point[1] / scale] for point in bbox]


def bbox_key(bbox: list[list[float]], text: str) -> tuple[str, int, int, int, int]:
    xs = [point[0] for point in bbox]
    ys = [point[1] for point in bbox]
    return (
        normalize_ocr_text(text),
        round(min(xs) / 8),
        round(min(ys) / 8),
        round(max(xs) / 8),
        round(max(ys) / 8),
    )


def read_text_variants(image: np.ndarray, color_ocr_strength: str) -> list[tuple[Any, str, float]]:
    reader = get_reader()
    results: list[tuple[Any, str, float]] = []
    seen: set[tuple[str, int, int, int, int]] = set()

    for variant, scale in make_ocr_variants(image, color_ocr_strength):
        for bbox, text, prob in reader.readtext(variant):
            original_bbox = scale_bbox_to_original(bbox, scale)
            key = bbox_key(original_bbox, text)
            if key in seen:
                continue
            seen.add(key)
            results.append((original_bbox, text, prob))

    return results


def mask_specific_words(
    image: np.ndarray,
    target_list: list[str],
    color_ocr_strength: str = "strong",
) -> np.ndarray:
    height, width = image.shape[:2]
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    results = read_text_variants(image, color_ocr_strength)

    for bbox, text, _prob in results:
        cleaned_text = normalize_ocr_text(text)
        if not cleaned_text or not text:
            continue

        for target in target_list:
            start_idx = cleaned_text.find(target)
            while start_idx != -1:
                tl, tr, br, _bl = bbox
                raw_len = max(len(text), 1)
                full_width = tr[0] - tl[0]
                char_width = full_width / raw_len

                word_start_x = int(tl[0] + (char_width * start_idx))
                word_end_x = int(tl[0] + (char_width * (start_idx + len(target))))

                padding = 4
                start_x = max(0, word_start_x - padding)
                end_x = min(width, word_end_x + padding)

                box_height = br[1] - tl[1]
                start_y = max(0, int(tl[1] - box_height * 0.1))
                end_y = min(mask.shape[0], int(br[1] + box_height * 0.1))

                cv2.rectangle(mask, (start_x, start_y), (end_x, end_y), (0, 0, 0, 255), -1)
                start_idx = cleaned_text.find(target, start_idx + 1)

    return mask


def to_grayscale_bgr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def build_output_names(original_name: str) -> tuple[str, str]:
    safe_name = safe_filename(original_name)
    path = Path(safe_name)
    return f"{path.stem}-original{path.suffix}", f"{path.stem}-mask.png"


def process_image_bytes(
    data: bytes,
    original_name: str,
    target_list: list[str],
    color_ocr_strength: str = "strong",
    grayscale_enabled: bool = False,
    output_image_mode: str = "original",
) -> dict[str, str]:
    image_array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"{original_name} を画像として読み込めませんでした。")

    ocr_image = to_grayscale_bgr(image) if grayscale_enabled else image
    mask = mask_specific_words(ocr_image, target_list, color_ocr_strength)
    original_output_name, mask_output_name = build_output_names(original_name)
    original_output_path = OUTPUT_DIR / original_output_name
    mask_output_path = OUTPUT_DIR / mask_output_name

    if output_image_mode == "grayscale":
        ok, encoded = cv2.imencode(Path(original_output_name).suffix or ".png", ocr_image)
        if not ok:
            raise RuntimeError(f"{original_output_name} の書き出しに失敗しました。")
        original_output_path.write_bytes(encoded.tobytes())
    else:
        original_output_path.write_bytes(data)
    if not cv2.imwrite(str(mask_output_path), mask):
        raise RuntimeError(f"{mask_output_name} の保存に失敗しました。")

    return {
        "name": original_name,
        "original": original_output_name,
        "original_url": f"/outputs/{quote(original_output_name)}",
        "mask": mask_output_name,
        "mask_url": f"/outputs/{quote(mask_output_name)}",
    }


def process_default_input(
    target_list: list[str],
    color_ocr_strength: str = "strong",
    grayscale_enabled: bool = False,
    output_image_mode: str = "original",
) -> dict[str, str]:
    if not DEFAULT_INPUT.exists():
        raise FileNotFoundError(f"デフォルト画像が見つかりません: {DEFAULT_INPUT}")
    return process_image_bytes(
        DEFAULT_INPUT.read_bytes(),
        DEFAULT_INPUT.name,
        target_list,
        color_ocr_strength,
        grayscale_enabled,
        output_image_mode,
    )


def make_zip(results: list[dict[str, str]]) -> str | None:
    if not results:
        return None

    zip_name = f"masked_{int(time.time())}_{uuid.uuid4().hex[:8]}.zip"
    zip_path = OUTPUT_DIR / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for result in results:
            for key in ("original", "mask"):
                filename = result[key]
                zf.write(OUTPUT_DIR / filename, arcname=filename)
    return f"/outputs/{quote(zip_name)}"


def parse_multipart_form(headers: Any, body: bytes) -> tuple[str, str, bool, str, list[tuple[bytes, str]]]:
    content_type = headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        raise ValueError("multipart/form-data 形式で送信してください。")

    raw_message = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n"
        "\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=email_policy).parsebytes(raw_message)
    target_text = ""
    color_ocr_strength = "strong"
    grayscale_enabled = False
    output_image_mode = "original"
    uploads: list[tuple[bytes, str]] = []

    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue

        payload = part.get_payload(decode=True) or b""
        if name == "targets":
            charset = part.get_content_charset() or "utf-8"
            target_text = payload.decode(charset, errors="replace")
            continue

        if name == "color_ocr_strength":
            value = payload.decode("utf-8", errors="replace")
            color_ocr_strength = value if value in {"weak", "medium", "strong"} else "strong"
            continue

        if name == "images":
            filename = part.get_filename()
            if filename and payload:
                uploads.append((payload, filename))
            continue

        if name == "grayscale_enabled":
            value = payload.decode("utf-8", errors="replace").strip().lower()
            grayscale_enabled = value in {"1", "true", "on", "yes"}
            continue

        if name == "output_image_mode":
            value = payload.decode("utf-8", errors="replace").strip().lower()
            output_image_mode = value if value in {"original", "grayscale"} else "original"

    return target_text, color_ocr_strength, grayscale_enabled, output_image_mode, uploads


def build_index_html() -> str:
    default_targets = html.escape("\n".join(TARGET_WORDS_DEFAULT))
    default_note = "見つかりました" if DEFAULT_INPUT.exists() else "未配置"
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>文字列黒塗り</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: "Yu Gothic UI", "Meiryo", system-ui, sans-serif;
      background: #f7f5ef;
      color: #1e2528;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
    }}
    main {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    header {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 20px;
      border-bottom: 1px solid #d9d4c7;
      padding-bottom: 14px;
    }}
    h1 {{
      margin: 0;
      font-size: 24px;
      letter-spacing: 0;
    }}
    .status-chip {{
      border: 1px solid #b9afa0;
      border-radius: 999px;
      padding: 6px 10px;
      background: #fffdf8;
      font-size: 13px;
      white-space: nowrap;
    }}
    .workspace {{
      display: grid;
      grid-template-columns: 340px 1fr;
      gap: 18px;
      align-items: start;
    }}
    section {{
      min-width: 0;
    }}
    label {{
      display: block;
      font-weight: 700;
      font-size: 14px;
      margin-bottom: 8px;
    }}
    textarea {{
      width: 100%;
      min-height: 320px;
      resize: vertical;
      box-sizing: border-box;
      border: 1px solid #b9afa0;
      border-radius: 6px;
      padding: 12px;
      font: 14px/1.55 "Yu Gothic UI", "Meiryo", sans-serif;
      background: #fffdf8;
      color: #1e2528;
    }}
    .dropzone {{
      display: grid;
      place-items: center;
      min-height: 220px;
      border: 2px dashed #75836f;
      border-radius: 8px;
      background: #fbfaf5;
      text-align: center;
      padding: 22px;
      box-sizing: border-box;
      transition: border-color .15s ease, background .15s ease;
    }}
    .dropzone.dragover {{
      border-color: #0f766e;
      background: #eef8f4;
    }}
    .dropzone strong {{
      display: block;
      font-size: 18px;
      margin-bottom: 6px;
    }}
    .dropzone span {{
      color: #53605c;
      font-size: 14px;
    }}
    input[type=file] {{
      display: none;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-top: 14px;
    }}
    .option-line {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      color: #374151;
      font-size: 14px;
      line-height: 1.4;
    }}
    .option-line select {{
      min-width: 80px;
      border: 1px solid #b9afa0;
      border-radius: 6px;
      background: #fffdf8;
      color: #1e2528;
      padding: 7px 9px;
      font: inherit;
    }}
    .option-line input[type=checkbox] {{
      width: 16px;
      height: 16px;
      accent-color: #20302e;
    }}
    button {{
      appearance: none;
      border: 1px solid #20302e;
      border-radius: 6px;
      background: #20302e;
      color: white;
      padding: 10px 14px;
      font-weight: 700;
      cursor: pointer;
    }}
    button.secondary {{
      background: #fffdf8;
      color: #20302e;
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: .55;
    }}
    .file-list, .results {{
      margin-top: 14px;
      display: grid;
      gap: 8px;
    }}
    .preview-section {{
      margin-top: 24px;
      border-top: 1px solid #d9d4c7;
      padding-top: 18px;
      display: none;
    }}
    .preview-section.active {{
      display: block;
    }}
    .preview-section h2 {{
      margin: 0 0 12px;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .preview-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }}
    .preview-card {{
      border: 1px solid #d9d4c7;
      border-radius: 8px;
      background: #fffdf8;
      overflow: hidden;
    }}
    .preview-frame {{
      position: relative;
      width: 100%;
      aspect-ratio: 4 / 3;
      background:
        linear-gradient(45deg, #e6e0d4 25%, transparent 25%),
        linear-gradient(-45deg, #e6e0d4 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #e6e0d4 75%),
        linear-gradient(-45deg, transparent 75%, #e6e0d4 75%);
      background-size: 20px 20px;
      background-position: 0 0, 0 10px, 10px -10px, -10px 0;
    }}
    .preview-frame img {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }}
    .preview-mask {{
      opacity: 1;
    }}
    .preview-card.mask-off .preview-mask {{
      opacity: 0;
    }}
    .preview-meta {{
      display: grid;
      gap: 8px;
      padding: 10px 12px 12px;
    }}
    .preview-name {{
      font-size: 14px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .preview-actions {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }}
    .preview-actions button {{
      padding: 7px 10px;
      font-size: 13px;
    }}
    .preview-actions a {{
      color: #075985;
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
    }}
    .row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      border: 1px solid #d9d4c7;
      border-radius: 6px;
      background: #fffdf8;
      padding: 10px 12px;
      font-size: 14px;
    }}
    .row a {{
      color: #075985;
      font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
    }}
    .message {{
      margin-top: 14px;
      min-height: 22px;
      color: #374151;
      font-size: 14px;
    }}
    .message.error {{
      color: #b42318;
      font-weight: 700;
    }}
    @media (max-width: 780px) {{
      header, .workspace {{
        display: block;
      }}
      .status-chip {{
        display: inline-block;
        margin-top: 10px;
      }}
      textarea {{
        min-height: 210px;
      }}
      .dropzone {{
        margin-top: 18px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>文字列黒塗り</h1>
      <div class="status-chip">既定画像 input.png: {default_note}</div>
    </header>
    <div class="workspace">
      <section>
        <label for="targets">黒塗り対象文字列</label>
        <textarea id="targets" spellcheck="false">{default_targets}</textarea>
      </section>
      <section>
        <label>画像ファイル</label>
        <div id="dropzone" class="dropzone">
          <div>
            <strong>画像をドラッグ&ドロップ</strong>
            <span>複数ファイル対応。クリックでも選択できます。</span>
          </div>
        </div>
        <input id="fileInput" type="file" accept="image/*" multiple>
        <label class="option-line" for="colorOcrStrength">
          <span>色付き文字の検出強度</span>
          <select id="colorOcrStrength">
            <option value="weak">弱</option>
            <option value="medium">中</option>
            <option value="strong" selected>強</option>
          </select>
        </label>
        <label class="option-line" for="grayscaleEnabled">
          <input id="grayscaleEnabled" type="checkbox">
          <span>実行時にモノクロ変換してからOCRを行う</span>
        </label>
        <label class="option-line">
          <span>出力画像</span>
          <input type="radio" id="outputModeOriginal" name="outputImageMode" value="original" checked required>
          <span>元画像</span>
          <input type="radio" id="outputModeGrayscale" name="outputImageMode" value="grayscale" required>
          <span>モノクロ画像</span>
        </label>
        <div class="toolbar">
          <button id="runButton" type="button">実行</button>
          <button id="clearButton" class="secondary" type="button">選択解除</button>
        </div>
        <div id="message" class="message"></div>
        <div id="fileList" class="file-list"></div>
        <div id="results" class="results"></div>
      </section>
    </div>
    <section id="previewSection" class="preview-section">
      <h2>プレビュー</h2>
      <div id="previewGrid" class="preview-grid"></div>
    </section>
  </main>
  <script>
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');
    const fileList = document.getElementById('fileList');
    const results = document.getElementById('results');
    const previewSection = document.getElementById('previewSection');
    const previewGrid = document.getElementById('previewGrid');
    const message = document.getElementById('message');
    const targets = document.getElementById('targets');
    const colorOcrStrength = document.getElementById('colorOcrStrength');
    const grayscaleEnabled = document.getElementById('grayscaleEnabled');
    const runButton = document.getElementById('runButton');
    const clearButton = document.getElementById('clearButton');
    let selectedFiles = [];

    function setMessage(text, isError = false) {{
      message.textContent = text;
      message.className = isError ? 'message error' : 'message';
    }}

    function renderFiles() {{
      fileList.innerHTML = '';
      selectedFiles.forEach(file => {{
        const row = document.createElement('div');
        row.className = 'row';
        row.innerHTML = `<span>${{file.name}}</span><span>${{Math.ceil(file.size / 1024)}} KB</span>`;
        fileList.appendChild(row);
      }});
      if (!selectedFiles.length) {{
        setMessage('ファイル未選択の場合は、同じディレクトリの input.png を処理します。');
      }}
    }}

    async function addFiles(files) {{
      const imageFiles = Array.from(files).filter(file => file.type.startsWith('image/'));
      if (!imageFiles.length) {{
        renderFiles();
        return;
      }}
      selectedFiles = [...selectedFiles, ...imageFiles];
      renderFiles();
    }}

    dropzone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async event => {{
      try {{
        await addFiles(event.target.files);
      }} catch (error) {{
        setMessage(error.message || 'ファイル追加に失敗しました。', true);
      }}
    }});

    ['dragenter', 'dragover'].forEach(type => {{
      dropzone.addEventListener(type, event => {{
        event.preventDefault();
        dropzone.classList.add('dragover');
      }});
    }});

    ['dragleave', 'drop'].forEach(type => {{
      dropzone.addEventListener(type, event => {{
        event.preventDefault();
        dropzone.classList.remove('dragover');
      }});
    }});

    dropzone.addEventListener('drop', async event => {{
      try {{
        await addFiles(event.dataTransfer.files);
      }} catch (error) {{
        setMessage(error.message || 'ファイル追加に失敗しました。', true);
      }}
    }});

    clearButton.addEventListener('click', () => {{
      selectedFiles = [];
      fileInput.value = '';
      results.innerHTML = '';
      previewGrid.innerHTML = '';
      previewSection.classList.remove('active');
      renderFiles();
    }});

    function renderPreviews(items) {{
      previewGrid.innerHTML = '';
      previewSection.classList.toggle('active', Boolean(items.length));

      items.forEach(item => {{
        const card = document.createElement('article');
        card.className = 'preview-card';

        const frame = document.createElement('div');
        frame.className = 'preview-frame';

        const original = document.createElement('img');
        original.src = item.original_url;
        original.alt = item.name + ' original';
        original.loading = 'lazy';

        const mask = document.createElement('img');
        mask.className = 'preview-mask';
        mask.src = item.mask_url;
        mask.alt = item.name + ' mask';
        mask.loading = 'lazy';

        frame.appendChild(original);
        frame.appendChild(mask);

        const meta = document.createElement('div');
        meta.className = 'preview-meta';

        const name = document.createElement('div');
        name.className = 'preview-name';
        name.textContent = item.name;

        const actions = document.createElement('div');
        actions.className = 'preview-actions';

        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.textContent = 'マスクあり';
        toggle.addEventListener('click', () => {{
          const isOff = card.classList.toggle('mask-off');
          toggle.textContent = isOff ? 'マスクなし' : 'マスクあり';
        }});

        const links = document.createElement('span');
        links.innerHTML = `<a href="${{item.original_url}}" target="_blank" rel="noreferrer">original</a> / <a href="${{item.mask_url}}" target="_blank" rel="noreferrer">mask</a>`;

        actions.appendChild(toggle);
        actions.appendChild(links);
        meta.appendChild(name);
        meta.appendChild(actions);
        card.appendChild(frame);
        card.appendChild(meta);
        previewGrid.appendChild(card);
      }});
    }}

    runButton.addEventListener('click', async () => {{
      const targetText = targets.value.trim();
      if (!targetText) {{
        setMessage('黒塗り対象文字列が空白です。実行しません。', true);
        return;
      }}

      const formData = new FormData();
      formData.append('targets', targetText);
      formData.append('color_ocr_strength', colorOcrStrength.value);
      formData.append('grayscale_enabled', String(grayscaleEnabled.checked));
      const outputMode = document.querySelector('input[name="outputImageMode"]:checked');
      if (!outputMode) {{
        setMessage('出力画像の設定を選択してください。', true);
        return;
      }}
      formData.append('output_image_mode', outputMode.value);
      selectedFiles.forEach(file => formData.append('images', file, file.name));

      runButton.disabled = true;
      results.innerHTML = '';
      previewGrid.innerHTML = '';
      previewSection.classList.remove('active');
      setMessage('処理中です。OCRモデルの初回読み込みには時間がかかります。');

      try {{
        const response = await fetch('/process', {{ method: 'POST', body: formData }});
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || '処理に失敗しました。');
        }}

        const suffix = payload.errors && payload.errors.length ? ` 一部エラー: ${{payload.errors.join(' / ')}}` : '';
        setMessage(`${{payload.count}} 件の処理が完了しました。${{suffix}}`, Boolean(suffix));
        if (payload.zip_url) {{
          const row = document.createElement('div');
          row.className = 'row';
          row.innerHTML = '<span>まとめてダウンロード</span><a href="' + payload.zip_url + '">ZIP</a>';
          results.appendChild(row);
        }}
        payload.results.forEach(item => {{
          const row = document.createElement('div');
          row.className = 'row';
          row.innerHTML = `<span>${{item.name}}</span><span><a href="${{item.original_url}}" target="_blank" rel="noreferrer">original</a> / <a href="${{item.mask_url}}" target="_blank" rel="noreferrer">mask</a></span>`;
          results.appendChild(row);
        }});
        renderPreviews(payload.results || []);
      }} catch (error) {{
        setMessage(error.message, true);
      }} finally {{
        runButton.disabled = false;
      }}
    }});

    renderFiles();
  </script>
</body>
</html>"""


class MaskAppHandler(BaseHTTPRequestHandler):
    server_version = "MaskTextInImage/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_text(self, status: HTTPStatus, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self.send_text(HTTPStatus.OK, build_index_html(), "text/html; charset=utf-8")
            return

        if self.path.startswith("/outputs/"):
            filename = unquote(self.path.removeprefix("/outputs/").split("?", 1)[0])
            path = (OUTPUT_DIR / filename).resolve()
            if OUTPUT_DIR.resolve() not in path.parents or not path.exists():
                self.send_text(HTTPStatus.NOT_FOUND, "ファイルが見つかりません。")
                return

            data = path.read_bytes()
            content_types = {
                ".bmp": "image/bmp",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".tif": "image/tiff",
                ".tiff": "image/tiff",
                ".webp": "image/webp",
                ".zip": "application/zip",
            }
            content_type = content_types.get(path.suffix.lower(), "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if path.suffix.lower() == ".zip":
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_text(HTTPStatus.NOT_FOUND, "ページが見つかりません。")

    def do_POST(self) -> None:
        if self.path != "/process":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "ページが見つかりません。"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            raw_targets, color_ocr_strength, grayscale_enabled, output_image_mode, upload_tasks = parse_multipart_form(self.headers, body)
            targets = parse_targets(raw_targets)
            if not targets:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "黒塗り対象文字列が空白です。実行しません。"})
                return

            OUTPUT_DIR.mkdir(exist_ok=True)
            errors: list[str] = []
            if upload_tasks:
                results, errors = self.process_uploads(
                    upload_tasks,
                    targets,
                    color_ocr_strength,
                    grayscale_enabled,
                    output_image_mode,
                )
            else:
                results = [process_default_input(targets, color_ocr_strength, grayscale_enabled, output_image_mode)]

            self.send_json(
                HTTPStatus.OK,
                {
                    "count": len(results),
                    "results": results,
                    "zip_url": make_zip(results),
                    "errors": errors,
                },
            )
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def process_uploads(
        self,
        uploads: list[tuple[bytes, str]],
        targets: list[str],
        color_ocr_strength: str,
        grayscale_enabled: bool,
        output_image_mode: str,
    ) -> tuple[list[dict[str, str]], list[str]]:
        worker_count = min(MAX_WORKERS, len(uploads))
        results: list[dict[str, str]] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    process_image_bytes,
                    data,
                    filename,
                    targets,
                    color_ocr_strength,
                    grayscale_enabled,
                    output_image_mode,
                )
                for data, filename in uploads
            ]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    errors.append(str(exc))

        if errors and not results:
            raise RuntimeError(" / ".join(errors))
        return results, errors


def find_free_port(start: int = 8765) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("空きポートが見つかりません。")


def close_existing_app_servers(port_start: int = 8765, port_end: int = 8865) -> None:
    if os.name != "nt":
        return

    script_name = Path(__file__).name.lower()
    current_pid = os.getpid()
    ps_script = (
        f"$currentPid = {current_pid}; "
        f"$scriptName = '{script_name}'; "
        f"$ports = {port_start}..{port_end}; "
        "$owners = Get-NetTCPConnection -LocalAddress 127.0.0.1 -State Listen "
        "| Where-Object { $ports -contains $_.LocalPort } "
        "| Select-Object -ExpandProperty OwningProcess -Unique; "
        "foreach ($ownerPid in $owners) { "
        "  if ($ownerPid -eq $currentPid) { continue } "
        "  $proc = Get-CimInstance Win32_Process -Filter \"ProcessId = $ownerPid\"; "
        "  if ($proc.CommandLine -and $proc.CommandLine.ToLower().Contains($scriptName)) { "
        "    Stop-Process -Id $ownerPid -Force "
        "  } "
        "}"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="文字列黒塗りブラウザアプリ")
    parser.add_argument("--port-start", type=int, default=8765, help="探索を開始するポート番号")
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="既存の同一アプリサーバーを停止せず、別ポートで追加起動します",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(exist_ok=True)
    if not args.keep_existing:
        close_existing_app_servers()
    port = find_free_port(args.port_start)
    server = ThreadingHTTPServer(("127.0.0.1", port), MaskAppHandler)
    url = f"http://127.0.0.1:{port}/"
    print(f"ブラウザアプリを起動しました: {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
