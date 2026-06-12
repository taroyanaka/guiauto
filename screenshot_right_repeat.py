from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import time


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
VK_RIGHT = 0x27
KEYEVENTF_KEYUP = 0x0002


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="フルスクリーンスクリーンショットを保存し、右キーを押す操作を1秒ごとに繰り返します。"
    )
    parser.add_argument("count", type=int, help="実行回数")
    parser.add_argument("--delay", type=float, default=2.0, help="開始前の待機時間（秒）")
    parser.add_argument("--interval", type=float, default=1.0, help="各回の間隔（秒）")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "screenshots"),
        help="スクリーンショット保存先",
    )
    return parser.parse_args()


def save_fullscreen_bmp(path: Path) -> None:
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    h_old = gdi32.SelectObject(hdc_mem, hbm)

    try:
        gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, 0, 0, SRCCOPY)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 24
        bmi.bmiHeader.biCompression = 0

        row_size = ((width * 3 + 3) // 4) * 4
        image_size = row_size * height
        buffer = (ctypes.c_ubyte * image_size)()

        if gdi32.GetDIBits(hdc_mem, hbm, 0, height, buffer, ctypes.byref(bmi), 0) == 0:
            raise ctypes.WinError()

        file_header_size = 14
        info_header_size = ctypes.sizeof(BITMAPINFOHEADER)
        file_size = file_header_size + info_header_size + image_size

        with path.open("wb") as f:
            f.write(b"BM")
            f.write(int(file_size).to_bytes(4, "little"))
            f.write((0).to_bytes(2, "little"))
            f.write((0).to_bytes(2, "little"))
            f.write((file_header_size + info_header_size).to_bytes(4, "little"))

            header = BITMAPINFOHEADER()
            header.biSize = info_header_size
            header.biWidth = width
            header.biHeight = height
            header.biPlanes = 1
            header.biBitCount = 24
            header.biCompression = 0
            header.biSizeImage = image_size
            header.biXPelsPerMeter = 0
            header.biYPelsPerMeter = 0
            header.biClrUsed = 0
            header.biClrImportant = 0
            f.write(bytes(header))

            # GDI の DIB は下から上の並びなので、そのまま書き出す
            f.write(buffer)
    finally:
        gdi32.SelectObject(hdc_mem, h_old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)


def press_right_key() -> None:
    user32.keybd_event(VK_RIGHT, 0, 0, 0)
    user32.keybd_event(VK_RIGHT, 0, KEYEVENTF_KEYUP, 0)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise SystemExit("count は 1 以上を指定してください")
    if args.delay < 0:
        raise SystemExit("delay は 0 以上を指定してください")
    if args.interval < 0:
        raise SystemExit("interval は 0 以上を指定してください")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.delay > 0:
        time.sleep(args.delay)

    for i in range(1, args.count + 1):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        screenshot_path = output_dir / f"screenshot_{timestamp}_{i:03}.bmp"
        save_fullscreen_bmp(screenshot_path)
        press_right_key()
        if i < args.count:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
