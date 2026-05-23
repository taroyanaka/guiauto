from faster_whisper import WhisperModel

# =========================
# Whisper
# =========================

model = WhisperModel(
    "large-v3",
    device="cpu",
    compute_type="int8"
)

# 【機能追加】
# vad_filter=True を設定し、オススメのパラメータを指定
# これにより、無音部分のノイズによるハルシネーション（誤作動）を防ぎつつ、
# 字幕が不自然に1つなぎになったり細切れになったりするのを防ぎます。
segments, info = model.transcribe(
    "input.mp4",
    language="ja",
    vad_filter=True,
    vad_parameters=dict(
        threshold=0.5,              # 音声とみなす確率のしきい値（デフォルト: 0.5）
        min_silence_duration_ms=500 # 0.5秒（500ms）以上の無音区間を適切に処理
    )
)

# generator を全部読み切る
segments = list(segments)

print(f"検出言語: {info.language}")

for segment in segments:

    print(
        f"[{segment.start:.2f}s - "
        f"{segment.end:.2f}s] "
        f"{segment.text}"
    )

print("文字起こし完了")


# =========================
# SRT 時間変換
# =========================

def format_srt_time(seconds):

    hours = int(seconds // 3600)

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = int(seconds % 60)

    milliseconds = int(
        (seconds - int(seconds)) * 1000
    )

    return (
        f"{hours:02}:"
        f"{minutes:02}:"
        f"{secs:02},"
        f"{milliseconds:03}"
    )


# =========================
# SRT 出力
# =========================

output_srt = "subtitle.srt"

with open(
    output_srt,
    "w",
    encoding="utf-8"
) as f:

    index = 1

    for segment in segments:

        text = segment.text.strip()

        if not text:
            continue

        start_time = format_srt_time(
            segment.start
        )

        end_time = format_srt_time(
            segment.end
        )

        f.write(f"{index}\n")

        f.write(
            f"{start_time} --> "
            f"{end_time}\n"
        )

        f.write(f"{text}\n\n")

        index += 1

print(
    f"SRTファイル出力完了: "
    f"{output_srt}"
)