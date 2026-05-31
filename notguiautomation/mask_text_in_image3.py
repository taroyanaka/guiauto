# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import msvcrt
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mask_text_in_image import OUTPUT_DIR, process_image_bytes


API_FETCH_TASK = "/api/fetch-task"
API_UPLOAD_RESULT = "/api/upload-result"
DEFAULT_BASE_URL = "https://ez-server-d7h7.onrender.com"
DEFAULT_CREDENTIALS_FILE = Path(__file__).with_name("user_credentials.txt")
DEFAULT_TARGETS_DIR = Path(__file__).parent
LOCK_FILE = OUTPUT_DIR / "mask_text_in_image3.lock"


@dataclass
class TaskItem:
    task_id: str
    original_url: str


@dataclass
class UserAccount:
    user_id: str
    password: str

    @property
    def targets_file(self) -> Path:
        suffix = "".join(ch for ch in self.user_id if ch.isdigit())
        if not suffix:
            raise ValueError(f"user_id has no numeric suffix: {self.user_id}")
        return DEFAULT_TARGETS_DIR / f"targets_user{suffix}.txt"


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def single_instance_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            handle.close()
            raise RuntimeError(f"another instance is already running: {lock_path}") from exc
        handle.seek(0, os.SEEK_END)
        handle.truncate()
        handle.write(str(os.getpid()).encode("utf-8"))
        handle.flush()
        yield
    finally:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        handle.close()


def load_target_words(targets_file: Path) -> list[str]:
    if not targets_file.exists():
        raise FileNotFoundError(f"targets file not found: {targets_file}")

    words: list[str] = []
    for line in targets_file.read_text(encoding="utf-8").splitlines():
        word = line.strip()
        if word:
            words.append(word)

    return words


def load_accounts(credentials_file: Path) -> list[UserAccount]:
    if not credentials_file.exists():
        raise FileNotFoundError(f"credentials file not found: {credentials_file}")

    accounts: list[UserAccount] = []
    for line_number, line in enumerate(credentials_file.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = [part.strip() for part in raw.split(",", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"invalid credentials format at line {line_number}: {raw}")
        accounts.append(UserAccount(user_id=parts[0], password=parts[1]))

    if not accounts:
        raise ValueError(f"credentials file is empty: {credentials_file}")
    return accounts


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
    print(f"[INFO] fetching tasks for {user_id} from {url}")
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
    print(f"[INFO] downloading image: {url}")
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
    print(f"[INFO] uploading mask for task_id={task_id} as {user_id}")
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
    account: UserAccount,
    task: TaskItem,
    target_words: list[str],
    color_ocr_strength: str,
    grayscale_enabled: bool,
    output_image_mode: str,
) -> None:
    print(f"[INFO] user={account.user_id} task_id={task.task_id} start")
    image_bytes, content_type = download_image(task.original_url)
    extension = guess_extension(task.original_url, content_type)
    original_name = f"{account.user_id}_task_{task.task_id}{extension}"
    print(f"[INFO] user={account.user_id} task_id={task.task_id} generating mask as {original_name}")
    result = process_image_bytes(
        image_bytes,
        original_name,
        target_words,
        color_ocr_strength=color_ocr_strength,
        grayscale_enabled=grayscale_enabled,
        output_image_mode=output_image_mode,
    )

    mask_path = OUTPUT_DIR / result["mask"]
    response = upload_result(base_url, account.user_id, account.password, task.task_id, mask_path)
    print(f"[INFO] user={account.user_id} task_id={task.task_id} upload complete")
    print(
        f"[OK] user={account.user_id} task_id={task.task_id} original={task.original_url} "
        f"mask={response.get('item', {}).get('mask', result['mask'])}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process tasks per user and upload masks.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument(
        "--credentials-file",
        default=str(DEFAULT_CREDENTIALS_FILE),
        help="Path to comma-separated user_id/password credentials file",
    )
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


def process_user(base_url: str, account: UserAccount, args: argparse.Namespace) -> int:
    try:
        target_words = load_target_words(account.targets_file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] user={account.user_id} {exc}")
        return 1

    if not target_words:
        print(f"[INFO] user={account.user_id} targets file is empty, skipping user")
        return 0

    print(f"[INFO] user={account.user_id} using targets file {account.targets_file}")

    total_failures = 0
    while True:
        try:
            tasks = fetch_tasks(base_url, account.user_id, account.password)
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            print(f"[ERROR] user={account.user_id} failed to fetch tasks: {exc}")
            return total_failures + 1

        if not tasks:
            print(f"[INFO] user={account.user_id} no pending tasks")
            break

        print(f"[INFO] user={account.user_id} fetched {len(tasks)} task(s)")
        for index, task in enumerate(tasks, start=1):
            try:
                print(f"[INFO] user={account.user_id} processing {index}/{len(tasks)} task_id={task.task_id}")
                process_task(
                    base_url,
                    account,
                    task,
                    target_words,
                    args.color_ocr_strength,
                    args.grayscale_enabled,
                    args.output_image_mode,
                )
            except HTTPError as exc:
                total_failures += 1
                print(f"[ERROR] user={account.user_id} task_id={task.task_id} http_error={exc.code} {exc.reason}")
                if exc.fp is not None:
                    try:
                        error_body = exc.fp.read().decode("utf-8", errors="replace")
                        if error_body:
                            print(f"[ERROR] user={account.user_id} task_id={task.task_id} response={error_body}")
                    except Exception:
                        pass
            except URLError as exc:
                total_failures += 1
                print(f"[ERROR] user={account.user_id} task_id={task.task_id} url_error={exc.reason}")
            except Exception as exc:
                total_failures += 1
                print(f"[ERROR] user={account.user_id} task_id={task.task_id} {exc}")

    print(f"[INFO] user={account.user_id} completed with {total_failures} failure(s)")
    return total_failures


def main() -> int:
    args = parse_args()
    ensure_output_dir()

    try:
        with single_instance_lock(LOCK_FILE):
            try:
                accounts = load_accounts(Path(args.credentials_file))
            except (FileNotFoundError, ValueError) as exc:
                print(f"ERROR: {exc}")
                return 1

            print(f"[INFO] loaded {len(accounts)} user account(s)")
            total_failures = 0
            for account in accounts:
                total_failures += process_user(args.base_url, account, args)

            print(f"[INFO] completed with {total_failures} total failure(s)")
            return 0 if total_failures == 0 else 2
    except RuntimeError as exc:
        print(f"[INFO] {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
