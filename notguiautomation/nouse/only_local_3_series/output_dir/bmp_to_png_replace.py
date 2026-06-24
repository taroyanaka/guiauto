from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=".bmp ファイルを同名の .png に変換し、成功したら元の .bmp を削除します。"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="変換対象の .bmp ファイルまたはディレクトリ。未指定ならカレントディレクトリの .bmp を処理します。",
    )
    return parser.parse_args()


def iter_bmp_files(paths: list[str]) -> list[Path]:
    if not paths:
        return sorted(Path.cwd().glob("*.bmp"))

    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.glob("*.bmp")))
        elif path.is_file() and path.suffix.lower() == ".bmp":
            files.append(path)
    return files


def read_bmp_pixels(path: Path) -> tuple[int, int, int, list[bytes]]:
    data = path.read_bytes()
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError(f"BMP ではありません: {path}")

    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    header_size = struct.unpack_from("<I", data, 14)[0]
    if header_size < 40:
        raise ValueError(f"非対応の BMP ヘッダーです: {path}")

    width = struct.unpack_from("<i", data, 18)[0]
    height = struct.unpack_from("<i", data, 22)[0]
    planes = struct.unpack_from("<H", data, 26)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]

    if planes != 1 or compression != 0:
        raise ValueError(f"圧縮 BMP または異常な BMP です: {path}")
    if bpp not in {24, 32}:
        raise ValueError(f"24bit/32bit BMP のみ対応です: {path}")
    if width <= 0 or height == 0:
        raise ValueError(f"画像サイズが不正です: {path}")

    top_down = height < 0
    height = abs(height)
    bytes_per_pixel = bpp // 8
    row_size = ((width * bytes_per_pixel + 3) // 4) * 4

    rows: list[bytes] = []
    for y in range(height):
        src_y = y if top_down else (height - 1 - y)
        row_start = pixel_offset + src_y * row_size
        row = data[row_start : row_start + width * bytes_per_pixel]
        if len(row) < width * bytes_per_pixel:
            raise ValueError(f"BMP のピクセルデータが不完全です: {path}")
        rows.append(row)

    return width, height, bytes_per_pixel, rows


def bmp_to_png_bytes(path: Path) -> bytes:
    width, height, bytes_per_pixel, rows = read_bmp_pixels(path)
    raw = bytearray()

    for row in rows:
        raw.append(0)  # PNG filter: None
        for i in range(0, len(row), bytes_per_pixel):
            if bytes_per_pixel == 3:
                b, g, r = row[i : i + 3]
                raw.extend((r, g, b))
            else:
                b, g, r, a = row[i : i + 4]
                raw.extend((r, g, b, a))

    color_type = 2 if bytes_per_pixel == 3 else 6
    bit_depth = 8
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    compressed = zlib.compress(bytes(raw), level=9)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(chunk(b"IHDR", ihdr))
    png.extend(chunk(b"IDAT", compressed))
    png.extend(chunk(b"IEND", b""))
    return bytes(png)


def convert_file(path: Path) -> Path:
    png_path = path.with_suffix(".png")
    png_path.write_bytes(bmp_to_png_bytes(path))
    path.unlink()
    return png_path


def main() -> None:
    args = parse_args()
    files = iter_bmp_files(args.paths)
    if not files:
        print("変換対象の .bmp ファイルが見つかりませんでした。")
        return

    converted = 0
    for path in files:
        try:
            png_path = convert_file(path)
            print(f"{path.name} -> {png_path.name}")
            converted += 1
        except Exception as exc:
            print(f"SKIP {path}: {exc}")

    print(f"converted: {converted}")


if __name__ == "__main__":
    main()
