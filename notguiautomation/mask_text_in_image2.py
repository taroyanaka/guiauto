# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mask_text_in_image import OUTPUT_DIR, TARGET_WORDS_DEFAULT, process_image_bytes


API_FETCH_TASK = "/api/fetch-task"
API_UPLOAD_RESULT = "/api/upload-result"
DEFAULT_BASE_URL = "http://127.0.0.1:3000"


@dataclass
class TaskItem:
    task_id: str
    original_url: str


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def build_headers(user_id: str, password: str) -> dict[str, str]:
    return {
        "user_id": user_id,
        "password": password,
        "Accept": "application/json",
    }


def parse_json_response(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


def fetch_tasks(base_url: str, user_id: str, password: str) -> list[TaskItem]:
    query = urlencode({"user_id": user_id, "password": password})
    url = f"{base_url.rstrip('/')}{API_FETCH_TASK}?{query}"
    request = Request(url, headers=build_headers(user_id, password), method="GET")
    with urlopen(request, timeout=60) as response:
        payload = parse_json_response(response.read())

    tasks: list[TaskItem] = []
    for item in payload.get("tasks", []):
        task_id = item.get("task_id")
        original_url = item.get("original_url")
        if task_id is None or not original_url:
            continue
        tasks.append(TaskItem(task_id=str(task_id), original_url=str(original_url)))
    return tasks


def download_image(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
    with urlopen(request, timeout=120) as response:
        data = response.read()
        content_type = response.headers.get_content_type()
        return data, content_type


def guess_extension(url: str, content_type: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
        return suffix
    guessed = mimetypes.guess_extension(content_type or "") or ".png"
    if guessed == ".jpe":
        return ".jpg"
    return guessed


def upload_result(
    base_url: str,
    user_id: str,
    password: str,
    task_id: str,
    mask_path: Path,
) -> dict[str, Any]:
    boundary = f"----MaskTextBoundary{os.urandom(12).hex()}"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    def add_file(name: str, filename: str, data: bytes, content_type: str) -> None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(data)
        body.extend(b"\r\n")

    add_field("task_id", task_id)
    mask_bytes = mask_path.read_bytes()
    add_file("mask", mask_path.name, mask_bytes, "image/png")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = f"{base_url.rstrip('/')}{API_UPLOAD_RESULT}"
    headers = build_headers(user_id, password)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    headers["Content-Length"] = str(len(body))

    request = Request(url, data=bytes(body), headers=headers, method="POST")
    with urlopen(request, timeout=120) as response:
        return parse_json_response(response.read())


def process_task(
    base_url: str,
    user_id: str,
    password: str,
    task: TaskItem,
    target_words: list[str],
    color_ocr_strength: str,
    grayscale_enabled: bool,
    output_image_mode: str,
) -> None:
    image_bytes, content_type = download_image(task.original_url)
    extension = guess_extension(task.original_url, content_type)
    original_name = f"task_{task.task_id}{extension}"
    result = process_image_bytes(
        image_bytes,
        original_name,
        target_words,
        color_ocr_strength=color_ocr_strength,
        grayscale_enabled=grayscale_enabled,
        output_image_mode=output_image_mode,
    )

    mask_path = OUTPUT_DIR / result["mask"]
    response = upload_result(base_url, user_id, password, task.task_id, mask_path)
    print(
        f"[OK] task_id={task.task_id} original={task.original_url} "
        f"mask={response.get('item', {}).get('mask', result['mask'])}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch tasks, generate mask images, and upload results.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument("--user-id", default=os.getenv("MASK_USER_ID", ""), help="API user_id")
    parser.add_argument("--password", default=os.getenv("MASK_PASSWORD", ""), help="API password")
    parser.add_argument(
        "--color-ocr-strength",
        default="strong",
        choices=("weak", "medium", "strong"),
        help="OCR tuning for colored text",
    )
    parser.add_argument(
        "--grayscale-enabled",
        action="store_true",
        help="Convert input to grayscale before OCR",
    )
    parser.add_argument(
        "--output-image-mode",
        default="original",
        choices=("original", "grayscale"),
        help="Store original image or grayscale variant locally",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.user_id or not args.password:
        print("ERROR: --user-id and --password are required, or set MASK_USER_ID / MASK_PASSWORD.")
        return 1

    ensure_output_dir()
    try:
        tasks = fetch_tasks(args.base_url, args.user_id, args.password)
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to fetch tasks: {exc}")
        return 1

    if not tasks:
        print("[INFO] no pending tasks")
        return 0

    print(f"[INFO] fetched {len(tasks)} task(s)")
    failures = 0
    for task in tasks:
        try:
            process_task(
                args.base_url,
                args.user_id,
                args.password,
                task,
                TARGET_WORDS_DEFAULT,
                args.color_ocr_strength,
                args.grayscale_enabled,
                args.output_image_mode,
            )
        except HTTPError as exc:
            failures += 1
            print(f"[ERROR] task_id={task.task_id} http_error={exc.code} {exc.reason}")
        except URLError as exc:
            failures += 1
            print(f"[ERROR] task_id={task.task_id} url_error={exc.reason}")
        except Exception as exc:
            failures += 1
            print(f"[ERROR] task_id={task.task_id} {exc}")

    print(f"[INFO] completed with {failures} failure(s)")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
