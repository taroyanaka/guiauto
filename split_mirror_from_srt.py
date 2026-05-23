import argparse
import os
import sys
import subprocess
import re

from moviepy import (
    VideoFileClip,
    ImageClip,
    TextClip,
    CompositeVideoClip,
    clips_array,
    concatenate_videoclips,
    vfx
)

# ---------------------------------------
# ffmpeg encoder 自動判定
# ---------------------------------------

def get_available_encoders():

    try:

        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True,
            text=True
        )

        return result.stdout

    except Exception:

        return ""


def choose_codec():

    encoders = get_available_encoders()

    if "h264_nvenc" in encoders:

        print("GPU Encoder: h264_nvenc")

        return "h264_nvenc"

    elif "h264_qsv" in encoders:

        print("GPU Encoder: h264_qsv")

        return "h264_qsv"

    elif "h264_amf" in encoders:

        print("GPU Encoder: h264_amf")

        return "h264_amf"

    print("CPU Encoder: libx264")

    return "libx264"


# ---------------------------------------
# moviepy compatibility
# ---------------------------------------

def safe_subclip(clip, start, end):

    if hasattr(clip, "subclipped"):

        return clip.subclipped(start, end)

    return clip.subclip(start, end)


# ---------------------------------------
# freeze section
# ---------------------------------------

def freeze_segment(clip, start, end):

    """
    start-end 区間を静止画化
    """

    frame = clip.get_frame(start)

    frozen = ImageClip(frame).with_duration(
        end - start
    )

    if hasattr(clip, "fps"):

        frozen = frozen.with_fps(
            clip.fps
        )

    return frozen


# ---------------------------------------
# build alternating freeze clip
# ---------------------------------------

def build_alternating_clip(
    original_clip,
    stop_times,
    freeze_on_even
):

    duration = original_clip.duration

    points = [0]

    points.extend(stop_times)

    points.append(duration)

    # 重複除去
    points = sorted(list(set(points)))

    segments = []

    for i in range(len(points) - 1):

        start = points[i]
        end = points[i + 1]

        should_freeze = (
            i % 2 == 0
            if freeze_on_even
            else i % 2 == 1
        )

        if should_freeze:

            print(
                f"FREEZE {start:.2f} - {end:.2f}"
            )

            seg = freeze_segment(
                original_clip,
                start,
                end
            )

        else:

            print(
                f"PLAY {start:.2f} - {end:.2f}"
            )

            seg = safe_subclip(
                original_clip,
                start,
                end
            )

        segments.append(seg)

    return concatenate_videoclips(
        segments
    )


# ---------------------------------------
# SRT parsing
# ---------------------------------------

def srt_time_to_seconds(time_str):

    """
    00:00:01,500 -> 1.5
    """

    h, m, s_ms = time_str.split(":")
    s, ms = s_ms.split(",")

    return (
        int(h) * 3600
        + int(m) * 60
        + int(s)
        + int(ms) / 1000
    )


def parse_srt(srt_path):

    with open(
        srt_path,
        "r",
        encoding="utf-8"
    ) as f:

        content = f.read()

    blocks = re.split(
        r"\n\s*\n",
        content.strip()
    )

    subtitles = []

    for block in blocks:

        lines = block.strip().splitlines()

        if len(lines) < 3:
            continue

        time_line = lines[1]

        text = "\n".join(
            lines[2:]
        ).strip()

        start_str, end_str = (
            time_line.split(" --> ")
        )

        start = srt_time_to_seconds(
            start_str.strip()
        )

        end = srt_time_to_seconds(
            end_str.strip()
        )

        subtitles.append({
            "start": start,
            "end": end,
            "text": text
        })

    return subtitles


# ---------------------------------------
# subtitle generation from srt
# ---------------------------------------

def generate_subtitle_clips_and_stops(
    srt_path,
    video_clip,
    extend_to_next=False
):

    print("----- SRT読み込み開始 -----")

    subtitles = parse_srt(
        srt_path
    )

    subtitle_clips = []

    stop_times = []

    for i, sub in enumerate(subtitles):

        text = sub["text"]

        # 下余白用空白行
        text = text + "\n "

        start = sub["start"]

        end = sub["end"]

        # ---------------------------------------
        # 次字幕まで延長
        # ---------------------------------------

        if extend_to_next:

            if i < len(subtitles) - 1:

                next_start = subtitles[i + 1]["start"]

                if next_start > end:

                    end = next_start

        print(
            f"[{start:.2f}s - "
            f"{end:.2f}s] "
            f"{text}"
        )

        # 字幕切替位置をstopへ
        stop_times.append(start)

        subtitle = TextClip(

            text=text,

            font="C:/Windows/Fonts/meiryo.ttc",

            font_size=48,

            color="white",

            stroke_color="black",
            stroke_width=3,

            method="caption",

            size=(
                int(video_clip.w * 0.9),
                None
            ),

            text_align="center"
        )

        subtitle = (
            subtitle
            .with_start(start)
            .with_end(end)
            .with_position(
                (
                    "center",
                    "center"
                )
            )
        )

        subtitle_clips.append(
            subtitle
        )

    # 0除去
    stop_times = [
        s for s in stop_times
        if s > 0
    ]

    print("----- 字幕生成完了 -----")

    return subtitle_clips, stop_times


# ---------------------------------------
# main
# ---------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="左右交互フリーズミラー動画 + SRT字幕"
    )

    parser.add_argument(
        "-i",
        "--input",
        required=True
    )

    parser.add_argument(
        "--srt",
        required=True
    )

    parser.add_argument(
        "-o",
        "--output",
        default="output.mp4"
    )

    parser.add_argument(
        "--side",
        choices=["left", "right"],
        default="right"
    )

    parser.add_argument(
        "--extend-to-next",
        action="store_true",
        help="次の字幕開始まで表示を延長"
    )

    args = parser.parse_args()

    if not os.path.exists(args.input):

        print("入力動画が見つかりません")

        sys.exit(1)

    if not os.path.exists(args.srt):

        print("SRTファイルが見つかりません")

        sys.exit(1)

    clip = None
    final_clip = None

    try:

        print("----- 処理開始 -----")

        clip = VideoFileClip(args.input)

        w, h = clip.size
        duration = clip.duration
        half_w = w // 2

        print(f"解像度: {w}x{h}")
        print(f"長さ: {duration:.2f}秒")

        # ---------------------------------------
        # SRT先行処理
        # ---------------------------------------

        subtitle_clips, stops = (
            generate_subtitle_clips_and_stops(
                args.srt,
                clip,
                extend_to_next=args.extend_to_next
            )
        )

        print("字幕切替stop:")
        print(stops)

        # ---------------------------------------
        # 左右ベース生成
        # ---------------------------------------

        if args.side == "left":

            print("左側基準")

            left_base = clip.with_effects([
                vfx.Crop(
                    x1=0,
                    y1=0,
                    x2=half_w,
                    y2=h
                )
            ])

            try:

                right_base = left_base.with_effects([
                    vfx.MirrorX()
                ])

            except Exception:

                right_base = left_base.with_effects([
                    vfx.MirrorHorizontal()
                ])

        else:

            print("右側基準")

            right_base = clip.with_effects([
                vfx.Crop(
                    x1=w-half_w,
                    y1=0,
                    x2=w,
                    y2=h
                )
            ])

            try:

                left_base = right_base.with_effects([
                    vfx.MirrorX()
                ])

            except Exception:

                left_base = right_base.with_effects([
                    vfx.MirrorHorizontal()
                ])

        # ---------------------------------------
        # 左右交互フリーズ
        # ---------------------------------------

        left_final = build_alternating_clip(
            left_base,
            stops,
            freeze_on_even=False
        )

        right_final = build_alternating_clip(
            right_base,
            stops,
            freeze_on_even=True
        )

        # ---------------------------------------
        # 左右結合
        # ---------------------------------------

        final_clip = clips_array([
            [left_final, right_final]
        ])

        # ---------------------------------------
        # 音声
        # ---------------------------------------

        if clip.audio:

            final_clip = final_clip.with_audio(
                clip.audio
            )

        # ---------------------------------------
        # 字幕合成
        # ---------------------------------------

        final_clip = CompositeVideoClip(
            [final_clip] + subtitle_clips
        )

        # ---------------------------------------
        # codec
        # ---------------------------------------

        codec = choose_codec()

        print(f"使用codec: {codec}")

        # ---------------------------------------
        # 書き出し
        # ---------------------------------------

        print("----- レンダリング開始 -----")

        final_clip.write_videofile(

            args.output,

            codec=codec,

            audio_codec="aac",

            bitrate="5000k",

            threads=os.cpu_count(),

            fps=clip.fps if hasattr(
                clip,
                "fps"
            ) else 30,

            preset="medium"
        )

        print("----- 完了 -----")

    except Exception as e:

        print("実行エラー:")
        print(e)

        import traceback

        traceback.print_exc()

    finally:

        try:

            if final_clip:
                final_clip.close()

            if clip:
                clip.close()

        except Exception:
            pass


if __name__ == "__main__":
    main()