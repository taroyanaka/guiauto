import ollama
from PIL import Image
import os

# 設定
img_path = r"masked_test/IMG20260612052509.jpg"
model_name = "qwen2.5vl:7b"

def is_red_pixel(r, g, b, r_min=120, diff_min=40, factor=1.1):
    return (r >= r_min) and ((r - max(g, b)) >= diff_min) and (r > g * factor) and (r > b * factor)

os.makedirs("temp_chunks", exist_ok=True)
os.makedirs("temp_red", exist_ok=True)

img = Image.open(img_path).convert("RGB")
width, height = img.size

# 分割数はollama_test2.pyと同様に2x2（必要なら変更）
rows = 2
cols = 2
w_chunk = width // cols
h_chunk = height // rows

full_text = ""

for r in range(rows):
    for c in range(cols):
        left = c * w_chunk
        top = r * h_chunk
        right = (c + 1) * w_chunk if c < cols - 1 else width
        bottom = (r + 1) * h_chunk if r < rows - 1 else height

        chunk = img.crop((left, top, right, bottom))
        chunk_path = f"temp_chunks/chunk_{r}_{c}.jpg"
        chunk.save(chunk_path)

        # 赤色ピクセルのみを抽出して二値画像を作成（文字を黒、背景を白）
        cw, ch = chunk.size
        out = Image.new("L", (cw, ch), 255)
        in_px = chunk.load()
        out_px = out.load()
        for y in range(ch):
            for x in range(cw):
                pr, pg, pb = in_px[x, y]
                if is_red_pixel(pr, pg, pb):
                    out_px[x, y] = 0
                else:
                    out_px[x, y] = 255

        red_path = f"temp_red/red_only_chunk_{r}_{c}.png"
        out.save(red_path)

        # Ollamaに投げる（赤文字のみが残った画像を送る）
        prompt = (
            "この画像に写っている赤色の文字のみを、行ごとにそのまま出力してください。"
            " 不要な説明は加えないでください。認識できない箇所は [?] としてください。"
        )

        response = ollama.chat(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                    "images": [red_path],
                }
            ],
        )

        # レスポンスからテキストを取り出す（互換性を考慮）
        try:
            text = response['message']['content']
        except Exception:
            try:
                text = response.message.content
            except Exception:
                text = str(response)

        # 区切り行を出力しない：認識結果だけを連結する
        full_text += text + "\n"

print(full_text)
