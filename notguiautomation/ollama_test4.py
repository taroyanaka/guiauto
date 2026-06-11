import ollama
from PIL import Image
import os
import json
import re

# 設定
img_path = r"masked_test/IMG20260612052509.jpg"
model_name = "qwen2.5vl:7b"
target_string = "確定"  # 👈 ここに探したい特定の文字列を入力してください

def is_red_pixel(r, g, b, r_min=120, diff_min=40, factor=1.1):
    return (r >= r_min) and ((r - max(g, b)) >= diff_min) and (r > g * factor) and (r > b * factor)

os.makedirs("temp_chunks", exist_ok=True)
os.makedirs("temp_red", exist_ok=True)

img = Image.open(img_path).convert("RGB")
width, height = img.size

rows = 2
cols = 2
w_chunk = width // cols
h_chunk = height // rows

print(f"探す文字列: '{target_string}'\n位置特定を開始します...\n" + "-"*50)

for r in range(rows):
    for c in range(cols):
        # チャンクの元画像における開始ピクセル座標
        left = c * w_chunk
        top = r * h_chunk
        right = (c + 1) * w_chunk if c < cols - 1 else width
        bottom = (r + 1) * h_chunk if r < rows - 1 else height
        
        chunk_w = right - left
        chunk_h = bottom - top

        chunk = img.crop((left, top, right, bottom))
        chunk_path = f"temp_chunks/chunk_{r}_{c}.jpg"
        chunk.save(chunk_path)

        # 赤色ピクセルのみを抽出
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

        # プロンプトの改造：位置情報を [ymin, xmin, ymax, xmax] (0-1000規格) で出力させる
        prompt = (
            f"画像内の赤色の文字を認識してください。もし '{target_string}' という文字列が含まれている場合は、"
            "その文字列と、文字列を囲むボックスの座標を以下のJSONフォーマットのみで返してください。余計な解説文は一切出力しないでください。\n"
            "座標は画像の左上を(0,0)、右下を(1000,1000)としたときの [ymin, xmin, ymax, xmax] の形式（0から1000の整数）とします。\n\n"
            "【出力フォーマット】\n"
            "{\n"
            f'  "found": true,\n'
            f'  "text": "{target_string}",\n'
            '  "box_1000": [ymin, xmin, ymax, xmax]\n'
            "}\n"
            f"もし '{target_string}' が見つからない場合は、以下のJSONのみを返してください。\n"
            '{"found": false}'
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

        try:
            res_text = response['message']['content']
        except Exception:
            try: res_text = response.message.content
            except Exception: res_text = str(response)

        # AIの返答からJSON部分のみを抽出してパース
        try:
            json_match = re.search(r'\{.*\}', res_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                
                if data.get("found") is True and "box_1000" in data:
                    box = data["box_1000"] # [ymin, xmin, ymax, xmax]
                    
                    # チャンク内の実際のピクセル座標に逆算
                    y_min_px = int((box[0] / 1000) * chunk_h)
                    x_min_px = int((box[1] / 1000) * chunk_w)
                    y_max_px = int((box[2] / 1000) * chunk_h)
                    x_max_px = int((box[3] / 1000) * chunk_w)
                    
                    # 元の画像（全体）における絶対ピクセル座標に変換
                    abs_left = left + x_min_px
                    abs_top = top + y_min_px
                    abs_width = x_max_px - x_min_px
                    abs_height = y_max_px - y_min_px
                    
                    print(f"🎯 発見！ グリッド [{r}, {c}] 内に存在します。")
                    print(f"元画像内での位置（ピクセル座標）:")
                    print(f"  - 開始位置 (X, Y): ({abs_left}, {abs_top})")
                    print(f"  - 文字のサイズ (幅, 高さ): ({abs_width}, {abs_height})")
                    print(f"  - バウンディングボックス (左, 上, 右, 下): ({abs_left}, {abs_top}, {abs_left + abs_width}, {abs_top + abs_height})")
                    print("-" * 50)
        except Exception as e:
            # パースエラーが起きた場合はAIの生の出力をデバッグ用に表示
            pass