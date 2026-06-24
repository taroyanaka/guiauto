# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import socket
import sys
import threading
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2
import easyocr
import numpy as np


APP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8765
MAX_WORKERS = min(4, max(1, os.cpu_count() or 1))
_thread_local = threading.local()


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class ImagePair:
    key: str
    mask_path: Path
    original_path: Path


@dataclass
class RegionResult:
    index: int
    bbox: tuple[int, int, int, int]
    candidates: list[str]


def get_reader() -> easyocr.Reader:
    reader = getattr(_thread_local, "reader", None)
    if reader is None:
        log("[INFO] initializing EasyOCR reader")
        reader = easyocr.Reader(["ja", "en"], gpu=False)
        _thread_local.reader = reader
    return reader


def resolve_folder_input(raw_folder: str) -> Path:
    folder = Path(raw_folder).expanduser()
    if folder.is_absolute():
        return folder

    for candidate in (Path.cwd() / folder, APP_DIR / folder):
        if candidate.exists():
            return candidate
    return Path.cwd() / folder


def safe_key_from_path(path: Path) -> str | None:
    stem = path.stem
    if stem.endswith("-mask"):
        return stem[:-5]
    if stem.endswith("-original"):
        return stem[:-9]
    return None


def collect_pairs(folder: Path) -> list[ImagePair]:
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"folder not found: {folder}")

    log(f"[INFO] scanning folder: {folder}")
    grouped: dict[str, dict[str, Path]] = {}
    for path in folder.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        key = safe_key_from_path(path)
        if key is None:
            continue
        entry = grouped.setdefault(key, {})
        if path.stem.endswith("-mask"):
            entry["mask"] = path
        else:
            entry["original"] = path

    pairs: list[ImagePair] = []
    for key in sorted(grouped):
        entry = grouped[key]
        if "mask" in entry and "original" in entry:
            pairs.append(ImagePair(key=key, mask_path=entry["mask"], original_path=entry["original"]))

    log(f"[INFO] found {len(pairs)} pair(s)")
    return pairs


def normalize_text(text: str) -> str:
    return text.replace(" ", "").replace("　", "").replace("縲", "")


def decode_image(path: Path, flags: int) -> np.ndarray:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, flags)
    if image is None:
        raise ValueError(f"failed to decode image: {path}")
    return image


def detect_regions(mask_image: np.ndarray, strict_black: bool) -> list[dict[str, Any]]:
    if mask_image.ndim == 3 and mask_image.shape[2] == 4:
        alpha = mask_image[:, :, 3]
        if np.count_nonzero(alpha > 0) > 0:
            black = alpha > 0
        else:
            gray = cv2.cvtColor(mask_image[:, :, :3], cv2.COLOR_BGR2GRAY)
            black = gray == 0 if strict_black else gray <= 32
    else:
        gray = cv2.cvtColor(mask_image, cv2.COLOR_BGR2GRAY)
        black = gray == 0 if strict_black else gray <= 32

    mask = black.astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.dilate(mask, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions: list[dict[str, Any]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < 16:
            continue
        contour_mask = np.zeros(mask.shape, dtype=np.uint8)
        cv2.drawContours(contour_mask, [contour], -1, 255, thickness=-1)
        regions.append(
            {
                "bbox": (x, y, w, h),
                "mask": contour_mask,
                "contour": contour,
            }
        )

    regions.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    log(f"[INFO] detected black regions: {len(regions)}")
    return regions


def run_ocr(image: np.ndarray, bbox: tuple[int, int, int, int], region_mask: np.ndarray) -> list[str]:
    x, y, w, h = bbox
    pad_x = max(2, int(w * 0.08))
    pad_y = max(2, int(h * 0.15))
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(image.shape[1], x + w + pad_x)
    y2 = min(image.shape[0], y + h + pad_y)
    crop = image[y1:y2, x1:x2]
    crop_mask = region_mask[y1:y2, x1:x2]
    if crop.size == 0:
        return []

    masked_crop = crop.copy()
    masked_crop[crop_mask == 0] = 255
    reader = get_reader()
    candidates: list[str] = []
    seen: set[str] = set()
    for _bbox, text, prob in reader.readtext(masked_crop):
        cleaned = normalize_text(text)
        if not cleaned:
            continue
        if prob < 0.12:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        candidates.append(cleaned)
    return candidates


def build_overlay_image(original: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> np.ndarray:
    output = original.copy()
    for x, y, w, h in boxes:
        cv2.rectangle(output, (x, y), (x + w, y + h), (0, 0, 0), -1)
    return output


def encode_png_data_url(image: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("failed to encode preview image")
    return "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def process_pair(pair: ImagePair, strict_black: bool) -> dict[str, Any]:
    log(f"[INFO] processing pair: {pair.key}")
    mask = decode_image(pair.mask_path, cv2.IMREAD_UNCHANGED)
    original = decode_image(pair.original_path, cv2.IMREAD_COLOR)

    if mask.shape[:2] != original.shape[:2]:
        raise ValueError(f"image size mismatch: {pair.mask_path.name} and {pair.original_path.name}")

    regions = detect_regions(mask, strict_black)
    overlay = build_overlay_image(original, [region["bbox"] for region in regions])

    region_results: list[RegionResult] = []
    flat_candidates: list[str] = []
    for index, region in enumerate(regions, start=1):
        bbox = region["bbox"]
        candidates = run_ocr(original, bbox, region["mask"])
        flat_candidates.extend(candidates)
        region_results.append(RegionResult(index=index, bbox=bbox, candidates=candidates))
        log(f"[INFO] pair={pair.key} region={index} box={bbox} candidates={candidates}")

    return {
        "name": pair.key,
        "overlay_data_url": encode_png_data_url(overlay),
        "mask_data_url": encode_png_data_url(mask[:, :, :3] if mask.ndim == 3 and mask.shape[2] == 4 else mask),
        "original_url": f"/files/{pair.original_path.name}",
        "mask_url": f"/files/{pair.mask_path.name}",
        "single_line": " / ".join(flat_candidates),
        "candidate_list": flat_candidates,
        "regions": [
            {
                "index": item.index,
                "bbox": item.bbox,
                "candidates": item.candidates,
            }
            for item in region_results
        ],
    }


def build_index_html(default_folder: str = "") -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>text_from_mask_test</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f2e9;
      --panel: #fffdf8;
      --line: #d9ccb6;
      --text: #1d2429;
      --muted: #66707a;
      --accent: #2b6f67;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Yu Gothic UI", "Meiryo", system-ui, sans-serif;
    }}
    main {{
      width: min(1280px, calc(100% - 32px));
      margin: 0 auto;
      padding: 22px 0 36px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 12px;
      margin-bottom: 18px;
    }}
    h1 {{ margin: 0; font-size: 26px; }}
    .note {{ font-size: 13px; color: var(--muted); }}
    .controls {{
      display: grid;
      grid-template-columns: 1.7fr 1fr 1fr auto;
      gap: 12px;
      align-items: end;
      margin-bottom: 14px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 10px 30px rgba(25, 20, 10, 0.06);
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    input[type="text"] {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      font: inherit;
      background: #fff;
    }}
    .radio-group {{
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
    }}
    .radio-group label {{
      margin: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 600;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 10px 18px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    button:disabled {{ opacity: 0.5; cursor: progress; }}
    .actions {{ display: flex; gap: 10px; justify-content: end; }}
    .status {{
      margin: 8px 0 16px;
      color: var(--muted);
      font-size: 14px;
    }}
    .results {{
      display: grid;
      gap: 16px;
    }}
    .result {{
      padding: 12px;
    }}
    .result-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
    }}
    .result-head h2 {{
      margin: 0;
      font-size: 18px;
    }}
    .tag {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      background: #2e4a3c;
      color: white;
      padding: 5px 10px;
      font-size: 12px;
      font-weight: 700;
    }}
    .preview-wrap {{
      display: grid;
      grid-template-columns: minmax(420px, 1.2fr) minmax(300px, 0.8fr);
      gap: 14px;
      align-items: start;
    }}
    .preview {{
      position: relative;
      overflow: hidden;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: white;
    }}
    .preview img {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .preview-actions {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-top: 10px;
      font-size: 13px;
    }}
    .preview-actions a {{
      color: #165c86;
      text-decoration: none;
      font-weight: 600;
    }}
    .outputs {{
      display: grid;
      gap: 10px;
    }}
    .block {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      padding: 12px;
    }}
    .block h3 {{
      margin: 0 0 8px;
      font-size: 14px;
    }}
    .text {{
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.7;
      font-size: 14px;
    }}
    .regions {{
      margin-top: 10px;
      display: grid;
      gap: 8px;
    }}
    .region-item {{
      border-top: 1px dashed #e3dac8;
      padding-top: 8px;
      font-size: 13px;
      color: #2a3237;
    }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 980px) {{
      .controls, .preview-wrap {{ grid-template-columns: 1fr; }}
      .actions {{ justify-content: start; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>text_from_mask_test</h1>
        <div class="note">`-mask` と `-original` のペアを読み込み、マスク位置の文字列を抽出します。</div>
      </div>
      <div id="pairCount" class="note">未実行</div>
    </header>

    <div class="controls">
      <div class="card">
        <label for="folder">フォルダ</label>
        <input id="folder" type="text" placeholder="例: masked_test" value="{html.escape(default_folder)}">
      </div>
      <div class="card">
        <label>黒塗り検出</label>
        <div class="radio-group">
          <label><input type="radio" name="blackMode" value="strict" checked> 完全黒のみ</label>
          <label><input type="radio" name="blackMode" value="loose"> ほぼ黒も含む</label>
        </div>
      </div>
      <div class="card">
        <label>入力</label>
        <div class="note">手入力またはフォルダ選択で指定できます</div>
        <input id="folderPicker" type="file" webkitdirectory directory multiple style="display:none">
      </div>
      <div class="actions">
        <button id="pickButton" type="button">フォルダ選択</button>
        <button id="runButton" type="button">実行</button>
      </div>
    </div>

    <div id="status" class="status">フォルダを指定して実行してください。</div>
    <div id="results" class="results"></div>
  </main>

  <script>
    const folderInput = document.getElementById('folder');
    const folderPicker = document.getElementById('folderPicker');
    const pickButton = document.getElementById('pickButton');
    const runButton = document.getElementById('runButton');
    const status = document.getElementById('status');
    const results = document.getElementById('results');
    const pairCount = document.getElementById('pairCount');

    pickButton.addEventListener('click', () => folderPicker.click());
    folderPicker.addEventListener('change', () => {{
      if (!folderPicker.files.length) return;
      const first = folderPicker.files[0].webkitRelativePath || folderPicker.files[0].name;
      folderInput.value = first.split('/')[0];
    }});

    function selectedBlackMode() {{
      return document.querySelector('input[name="blackMode"]:checked').value;
    }}

    function renderResults(items) {{
      results.innerHTML = '';
      for (const item of items) {{
        const article = document.createElement('article');
        article.className = 'card result';

        const head = document.createElement('div');
        head.className = 'result-head';
        head.innerHTML = `<h2>${{item.name}}</h2><span class="tag">マスクあり</span>`;

        const previewWrap = document.createElement('div');
        previewWrap.className = 'preview-wrap';
        previewWrap.innerHTML = `
          <div>
            <div class="preview">
              <img src="${{item.overlay_data_url}}" alt="${{item.name}} preview">
            </div>
            <div class="preview-actions">
              <span>${{item.original_url.split('/').pop()}}</span>
              <span><a href="${{item.original_url}}" target="_blank" rel="noreferrer">original</a> / <a href="${{item.mask_url}}" target="_blank" rel="noreferrer">mask</a></span>
            </div>
          </div>
        `;

        const outputs = document.createElement('div');
        outputs.className = 'outputs';
        const single = item.single_line ? item.single_line : '<span class="muted">抽出なし</span>';
        const listText = item.candidate_list.length
          ? item.candidate_list.map(text => `- ${{text}}`).join('\\n')
          : '<span class="muted">抽出なし</span>';
        const regions = item.regions.length
          ? item.regions.map(region => `#${{region.index}} [${{region.bbox.join(', ')}}] => ${{region.candidates.length ? region.candidates.join(' / ') : '抽出なし'}}`).join('\\n')
          : '抽出なし';
        outputs.innerHTML = `
          <div class="block">
            <h3>1領域1行</h3>
            <div class="text">${{single}}</div>
          </div>
          <div class="block">
            <h3>領域ごとの候補リスト</h3>
            <div class="text">${{listText}}</div>
          </div>
          <div class="block">
            <h3>領域詳細</h3>
            <div class="text">${{regions}}</div>
          </div>
        `;

        article.appendChild(head);
        article.appendChild(previewWrap);
        article.appendChild(outputs);
        results.appendChild(article);
      }}
    }}

    runButton.addEventListener('click', async () => {{
      const folder = folderInput.value.trim();
      if (!folder) {{
        status.textContent = 'フォルダを指定してください。';
        return;
      }}

      runButton.disabled = true;
      results.innerHTML = '';
      status.textContent = '処理中です...';

      try {{
        const response = await fetch('/process', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ folder, black_mode: selectedBlackMode() }}),
        }});
        const payload = await response.json();
        if (!response.ok) {{
          const details = payload.traceback ? `\\n\\n${{payload.traceback}}` : '';
          throw new Error((payload.error || 'processing failed') + details);
        }}

        pairCount.textContent = `${{payload.count}} ペア`;
        status.textContent = `完了: ${{payload.count}} ペアを処理しました。`;
        renderResults(payload.results);
      }} catch (error) {{
        status.textContent = error.message || '処理に失敗しました。';
      }} finally {{
        runButton.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "text-from-mask-test"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_text(self, status: HTTPStatus, body: str, content_type: str = "text/html; charset=utf-8") -> None:
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
            self.send_text(HTTPStatus.OK, build_index_html())
            return
        if self.path.startswith("/files/"):
            filename = self.path.removeprefix("/files/").split("?", 1)[0]
            path = (APP_DIR / filename).resolve()
            if not path.exists() or APP_DIR.resolve() not in path.parents:
                self.send_text(HTTPStatus.NOT_FOUND, "not found", "text/plain; charset=utf-8")
                return
            data = path.read_bytes()
            suffix = path.suffix.lower()
            content_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }.get(suffix, "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_text(HTTPStatus.NOT_FOUND, "not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path != "/process":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            log("[INFO] POST /process")
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            log(f"[INFO] request bytes={len(body)}")
            payload = json.loads(body.decode("utf-8"))
            folder = resolve_folder_input(str(payload.get("folder", "")))
            black_mode = str(payload.get("black_mode", "strict"))
            strict_black = black_mode != "loose"
            log(f"[INFO] payload folder={folder} black_mode={black_mode}")

            pairs = collect_pairs(folder)
            if not pairs:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "paired images not found"})
                return

            results: list[dict[str, Any]] = []
            errors: list[str] = []

            def worker(pair: ImagePair) -> dict[str, Any]:
                return process_pair(pair, strict_black)

            if len(pairs) == 1:
                results.append(worker(pairs[0]))
            else:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = [executor.submit(worker, pair) for pair in pairs]
                    for future in as_completed(futures):
                        try:
                            results.append(future.result())
                        except Exception as exc:
                            errors.append(str(exc))
                            log(f"[ERROR] pair failed: {exc}")

            results.sort(key=lambda item: item["name"])
            log(f"[INFO] completed: results={len(results)} errors={len(errors)}")
            self.send_json(
                HTTPStatus.OK,
                {
                    "count": len(results),
                    "results": results,
                    "errors": errors,
                },
            )
        except Exception as exc:
            error_text = traceback.format_exc()
            log(f"[ERROR] process failed: {exc}")
            log(error_text)
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "error": str(exc),
                    "traceback": error_text,
                },
            )


def find_free_port(start_port: int) -> int:
    for port in range(start_port, start_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port available")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local mask/original pair OCR viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    port = find_free_port(args.port)
    server = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://{args.host}:{port}/"
    log(f"[INFO] server starting: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
