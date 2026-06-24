# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import html
import json
import mimetypes
import os
import re
import msvcrt
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import cv2
import easyocr
import numpy as np


APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "masked_outputs"
API_FETCH_TASK = "/api/fetch-task"
API_UPLOAD_RESULT = "/api/upload-result"
BACKUP_CSV_URL = "/backup/csv"
DEFAULT_BASE_URL = "https://ez-server-d7h7.onrender.com"
DEFAULT_CREDENTIALS_FILE = Path(__file__).with_name("user_credentials.txt")
DEFAULT_TARGETS_DIR = Path(__file__).parent
DEFAULT_BACKUP_CSV_FILE = Path(__file__).with_name("backup.csv")
DEFAULT_INPUT = APP_DIR / "input.png"
TARGET_WORDS_DEFAULT = [
    "蛻・屬",
    "邊ｾ陬ｽ",
    "邏皮黄雉ｪ",
    "豺ｷ蜷育黄",
    "繧埼℃",
    "陞咲せ",
    "豐ｸ轤ｹ",
    "闥ｸ逡・",
    "蛻・蕗",
    "譏・庄豕・",
    "蜀咲ｵ先匕",
    "謚ｽ蜃ｺ",
    "繧ｯ繝ｭ繝槭ヨ繧ｰ繝ｩ繝輔ぅ繝ｼ",
    "蜈・ｴ",
    "蜈・ｴ險伜捷",
    "蜊倅ｽ・",
    "蛹門粋迚ｩ",
    "蜷檎ｴ菴・",
    "轤手牡蜿榊ｿ・",
    "逋ｽ濶ｲ",
    "髱定牡",
    "諡｡謨｣",
    "辭ｱ驕句虚",
    "迥ｶ諷九・荳画・",
    "縺ｵ縺｣縺ｦ繧・",
    "繧・≧縺ｦ繧・",
    "縺倥ｅ繧薙・縺｣縺励▽",
    "縺薙ｓ縺斐≧縺ｶ縺､",
]
LOCK_FILE = OUTPUT_DIR / "mask_text_in_image4.lock"


_thread_local = threading.local()


@dataclass
class TaskItem:
    task_id: str
    original_url: str
    target: str | None


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


@dataclass
class BackupUserRecord:
    row_id: str
    user_id: str
    password: str


def get_reader() -> easyocr.Reader:
    reader = getattr(_thread_local, "reader", None)
    if reader is None:
        reader = easyocr.Reader(["ja", "en"], gpu=False)
        _thread_local.reader = reader
    return reader


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


def normalize_ocr_text(text: str) -> str:
    return text.replace(" ", "").replace("縲", "")


def safe_filename(name: str, fallback: str = "image") -> str:
    stem = Path(name).stem or fallback
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
        suffix = ".png"
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem).strip(" ._")
    return f"{stem or fallback}{suffix}"


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
    variants: list[tuple[np.ndarray, float]] = [(image, 1.0), (make_contrast_variant(image), 1.0)]
    colored_text = make_colored_text_variant(image, color_ocr_strength)
    red_context = make_red_ink_context_variant(image, color_ocr_strength)
    variants.extend([(colored_text, 1.0), (red_context, 1.0)])
    if color_ocr_strength in {"medium", "strong"}:
        scale = 1.5 if color_ocr_strength == "medium" else 2.0
        variants.extend([(upscale_variant(colored_text, scale), scale), (upscale_variant(red_context, scale), scale)])
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
            raise RuntimeError(f"{original_output_name} の保存に失敗しました。")
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
        raise FileNotFoundError(f"default input not found: {DEFAULT_INPUT}")
    return process_image_bytes(
        DEFAULT_INPUT.read_bytes(),
        DEFAULT_INPUT.name,
        target_list,
        color_ocr_strength,
        grayscale_enabled,
        output_image_mode,
    )


def build_headers(user_id: str, password: str) -> dict[str, str]:
    return {
        "user_id": user_id,
        "password": password,
        "Accept": "application/json",
    }


def parse_json_response(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


def download_backup_csv(base_url: str, backup_csv_url: str, destination: Path) -> Path:
    url = f"{base_url.rstrip('/')}{backup_csv_url}"
    print(f"[INFO] downloading backup csv from {url}")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
    with urlopen(request, timeout=120) as response:
        data = response.read()
    destination.write_bytes(data)
    return destination


def load_backup_users(backup_csv: Path) -> list[BackupUserRecord]:
    if not backup_csv.exists():
        raise FileNotFoundError(f"backup csv not found: {backup_csv}")

    rows: list[BackupUserRecord] = []
    with backup_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required_fields = {"id", "user_id", "password"}
        if not reader.fieldnames or not required_fields.issubset(set(reader.fieldnames)):
            raise ValueError(f"backup csv must include columns: id,user_id,password ({backup_csv})")

        for line_number, row in enumerate(reader, start=2):
            row_id = (row.get("id") or "").strip()
            user_id = (row.get("user_id") or "").strip()
            password = (row.get("password") or "").strip()
            if not user_id or not password:
                raise ValueError(f"invalid backup csv row at line {line_number}: {row}")
            rows.append(BackupUserRecord(row_id=row_id, user_id=user_id, password=password))

    if not rows:
        raise ValueError(f"backup csv is empty: {backup_csv}")
    return rows


def write_credentials_file(credentials_file: Path, users: list[BackupUserRecord]) -> None:
    lines = [f"{user.user_id},{user.password}" for user in users]
    credentials_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[INFO] updated credentials file: {credentials_file}")


def load_target_words(targets_file: Path) -> list[str]:
    if not targets_file.exists():
        raise FileNotFoundError(f"targets file not found: {targets_file}")

    words: list[str] = []
    for line in targets_file.read_text(encoding="utf-8").splitlines():
        word = line.strip()
        if word:
            words.append(word)
    return words


def write_target_words(targets_file: Path, target_text: str) -> None:
    targets_file.write_text(target_text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")
    print(f"[INFO] updated targets file: {targets_file}")


def sync_user_files(backup_csv: Path, credentials_file: Path) -> list[UserAccount]:
    users = load_backup_users(backup_csv)
    write_credentials_file(credentials_file, users)
    return [UserAccount(user_id=user.user_id, password=user.password) for user in users]


def ensure_targets_file(targets_file: Path) -> bool:
    if targets_file.exists():
        return False
    targets_file.write_text("", encoding="utf-8")
    print(f"[INFO] created empty targets file: {targets_file}")
    return True


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
        target = item.get("target")
        if task_id is None or not original_url:
            continue
        tasks.append(
            TaskItem(
                task_id=str(task_id),
                original_url=str(original_url),
                target=None if target is None else str(target),
            )
        )
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


def prepare_targets_for_task(task: TaskItem, account: UserAccount, target_path: Path) -> list[str]:
    if task.target is not None:
        write_target_words(target_path, task.target)

    target_words = load_target_words(target_path)
    if not target_words:
        print(f"[INFO] user={account.user_id} task_id={task.task_id} targets file is empty, skipping task")
    return target_words


def process_user(base_url: str, account: UserAccount, args: argparse.Namespace) -> int:
    ensure_targets_file(account.targets_file)
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
                target_words = prepare_targets_for_task(task, account, account.targets_file)
                if not target_words:
                    continue
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
    from email.parser import BytesParser
    from email.policy import default as email_policy

    content_type = headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        raise ValueError("multipart/form-data で送信してください。")

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
</head>
<body>
  <main>
    <h1>文字列黒塗り</h1>
    <p>input.png: {default_note}</p>
    <textarea id="targets">{default_targets}</textarea>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process tasks per user and upload masks.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument(
        "--backup-csv-url",
        default=BACKUP_CSV_URL,
        help="Relative path for the backup CSV endpoint",
    )
    parser.add_argument(
        "--credentials-file",
        default=str(DEFAULT_CREDENTIALS_FILE),
        help="Path to comma-separated user_id/password credentials file",
    )
    parser.add_argument(
        "--backup-csv-file",
        default=str(DEFAULT_BACKUP_CSV_FILE),
        help="Local path to save the downloaded backup CSV",
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


def main() -> int:
    args = parse_args()
    ensure_output_dir()

    try:
        with single_instance_lock(LOCK_FILE):
            try:
                backup_csv_path = download_backup_csv(
                    args.base_url,
                    args.backup_csv_url,
                    Path(args.backup_csv_file),
                )
            except (HTTPError, URLError) as exc:
                print(f"ERROR: failed to download backup csv: {exc}")
                return 1

            try:
                accounts = sync_user_files(
                    backup_csv_path,
                    Path(args.credentials_file),
                )
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
