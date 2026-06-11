import ollama
from PIL import Image
import os

# 元画像を開く
img_path = r"masked_test/IMG20260612052509.jpg"
img = Image.open(img_path)
width, height = img.size

# 例として2x2の4つに分割
rows = 2
cols = 2
w_chunk = width // cols
h_chunk = height // rows

full_text = ""

# テンポラリフォルダの作成
os.makedirs("temp_chunks", exist_ok=True)

for r in range(rows):
    for c in range(cols):
        # 切り出し範囲の計算
        left = c * w_chunk
        top = r * h_chunk
        right = (c + 1) * w_chunk if c < cols - 1 else width
        bottom = (r + 1) * h_chunk if r < rows - 1 else height
        
        # 画像の切り出しと保存
        chunk = img.crop((left, top, right, bottom))
        chunk_path = f"temp_chunks/chunk_{r}_{c}.jpg"
        chunk.save(chunk_path)
        
        # 分割画像をOllamaに投げる
        response = ollama.chat(
            model="qwen2.5vl:7b",
            messages=[
                {
                    "role": "user",
                    "content": "この画像に写っている文字を全て正確に読み取り赤色の文字列を全て抽出してください。",
                    "images": [chunk_path],
                }
            ],
        )
        
        full_text += f"\n--- [分割エリア {r+1}-{c+1} の読み取り結果] ---\n"
        full_text += response['message']['content']

print(full_text)