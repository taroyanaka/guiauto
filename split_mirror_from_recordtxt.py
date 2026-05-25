import argparse
import os
import re
import sys

from moviepy import VideoFileClip, clips_array, CompositeVideoClip, vfx

from split_mirror_from_srt import build_alternating_clip, choose_codec


def record_time_to_seconds(time_str: str) -> float:
    """
    00:00:01,488 -> 1.488
    00:00:01.488 -> 1.488 (念のため対応)
    """
    s = time_str.strip()
    if not s:
        raise ValueError("empty time string")

    # HH:MM:SS,mmm or HH:MM:SS.mmm
    m = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})([,.])(\d{1,3})", s)
    if not m:
        raise ValueError(f"invalid time format: {time_str!r}")

    h = int(m.group(1))
    minutes = int(m.group(2))
    sec = int(m.group(3))
    ms = int(m.group(5).ljust(3, "0"))

    return h * 3600 + minutes * 60 + sec + ms / 1000.0


def parse_record_txt(path: str) -> list[float]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    times: list[float] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        times.append(record_time_to_seconds(s))

    # 0は除外してソート/重複除去
    times = sorted(set(t for t in times if t > 0))
    return times


def main() -> int:
    parser = argparse.ArgumentParser(
        description="record.txt のタイミングで split_mirror（左右交互フリーズ）を生成"
    )
    parser.add_argument("-i", "--input", required=True, help="入力動画ファイル (mp4 等)")
    parser.add_argument("-o", "--output", required=True, help="出力動画ファイル (mp4 等)")
    parser.add_argument(
        "-r",
        "--record",
        default="record.txt",
        help="タイミングファイル (例: 00:00:01,488 を改行区切り)",
    )
    parser.add_argument(
        "--side",
        choices=["left", "right"],
        default="right",
        help="どちら側を元映像として扱うか（もう片方はミラー）",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print("入力動画ファイルが見つかりません")
        return 1

    if not os.path.exists(args.record):
        print("record.txt が見つかりません")
        return 1

    stops = parse_record_txt(args.record)
    if not stops:
        print("record.txt に有効な時間がありません")
        return 1

    clip = None
    final_clip = None
    try:
        clip = VideoFileClip(args.input)
        w, h = clip.size
        half_w = w // 2

        if args.side == "left":
            left_base = clip.with_effects([vfx.Crop(x1=0, y1=0, x2=half_w, y2=h)])
            try:
                right_base = left_base.with_effects([vfx.MirrorX()])
            except Exception:
                right_base = left_base.with_effects([vfx.MirrorHorizontal()])
        else:
            right_base = clip.with_effects(
                [vfx.Crop(x1=w - half_w, y1=0, x2=w, y2=h)]
            )
            try:
                left_base = right_base.with_effects([vfx.MirrorX()])
            except Exception:
                left_base = right_base.with_effects([vfx.MirrorHorizontal()])

        left_final = build_alternating_clip(left_base, stops, freeze_on_even=False)
        right_final = build_alternating_clip(right_base, stops, freeze_on_even=True)

        final_clip = clips_array([[left_final, right_final]])
        if clip.audio:
            final_clip = final_clip.with_audio(clip.audio)

        # (SRT字幕は無し)
        final_clip = CompositeVideoClip([final_clip])

        codec = choose_codec()
        final_clip.write_videofile(
            args.output,
            codec=codec,
            audio_codec="aac",
            bitrate="5000k",
            threads=os.cpu_count(),
            fps=clip.fps if hasattr(clip, "fps") else 30,
            preset="medium",
        )
        return 0
    except Exception as e:
        print("エラー:")
        print(e)
        import traceback

        traceback.print_exc()
        return 2
    finally:
        try:
            if final_clip:
                final_clip.close()
            if clip:
                clip.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

