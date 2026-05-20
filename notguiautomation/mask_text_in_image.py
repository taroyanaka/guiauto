# -*- coding: utf-8 -*-
"""
画像内の指定文字列を黒塗りするローカルブラウザアプリ。

必要なライブラリ:
    pip install easyocr opencv-python numpy

使い方:
    python mask_text_in_image.py
"""

from __future__ import annotations

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


def mask_specific_words(image: np.ndarray, target_list: list[str]) -> np.ndarray:
    reader = get_reader()
    height, width = image.shape[:2]
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    results = reader.readtext(image)

    for bbox, text, _prob in results:
        cleaned_text = text.replace(" ", "").replace("　", "")
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


def build_output_names(original_name: str) -> tuple[str, str]:
    safe_name = safe_filename(original_name)
    path = Path(safe_name)
    return f"{path.stem}-original{path.suffix}", f"{path.stem}-mask.png"


def process_image_bytes(data: bytes, original_name: str, target_list: list[str]) -> dict[str, str]:
    image_array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"{original_name} を画像として読み込めませんでした。")

    mask = mask_specific_words(image, target_list)
    original_output_name, mask_output_name = build_output_names(original_name)
    original_output_path = OUTPUT_DIR / original_output_name
    mask_output_path = OUTPUT_DIR / mask_output_name

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


def process_default_input(target_list: list[str]) -> dict[str, str]:
    if not DEFAULT_INPUT.exists():
        raise FileNotFoundError(f"デフォルト画像が見つかりません: {DEFAULT_INPUT}")
    return process_image_bytes(DEFAULT_INPUT.read_bytes(), DEFAULT_INPUT.name, target_list)


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


def parse_multipart_form(headers: Any, body: bytes) -> tuple[str, list[tuple[bytes, str]]]:
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

        if name == "images":
            filename = part.get_filename()
            if filename and payload:
                uploads.append((payload, filename))

    return target_text, uploads


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

    function addFiles(files) {{
      selectedFiles = [...selectedFiles, ...Array.from(files).filter(file => file.type.startsWith('image/'))];
      renderFiles();
    }}

    dropzone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', event => addFiles(event.target.files));

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

    dropzone.addEventListener('drop', event => addFiles(event.dataTransfer.files));

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
            raw_targets, upload_tasks = parse_multipart_form(self.headers, body)
            targets = parse_targets(raw_targets)
            if not targets:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "黒塗り対象文字列が空白です。実行しません。"})
                return

            OUTPUT_DIR.mkdir(exist_ok=True)
            errors: list[str] = []
            if upload_tasks:
                results, errors = self.process_uploads(upload_tasks, targets)
            else:
                results = [process_default_input(targets)]

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
    ) -> tuple[list[dict[str, str]], list[str]]:
        worker_count = min(MAX_WORKERS, len(uploads))
        results: list[dict[str, str]] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(process_image_bytes, data, filename, targets)
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


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    close_existing_app_servers()
    port = find_free_port()
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
