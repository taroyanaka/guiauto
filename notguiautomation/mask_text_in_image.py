# 必要なライブラリは pip install easyocr opencv-python numpy でインストールしてください。
# 利用法はINPUT_IMG,OUTPUT_IMG, TARGET_WORDSを適宜変更して実行してください。

import easyocr
import cv2
import numpy as np

def mask_specific_words_improved(input_path, output_path, target_list):
    # OCRリーダーの初期化
    reader = easyocr.Reader(['ja', 'en'], gpu=False)

    # 1. 画像の読み込み
    image = cv2.imread(input_path)
    if image is None:
        print(f"エラー: '{input_path}' が読み込めませんでした。")
        print("プログラムと同じフォルダに画像があるか、ファイル名が正しいか確認してください。")
        return

    # 2. 文字列の検出
    results = reader.readtext(input_path)

    for (bbox, text, prob) in results:
        # 空白を削除してマッチング精度を上げる
        cleaned_text = text.replace(" ", "").replace("　", "")
        
        for target in target_list:
            if target in cleaned_text:
                # 文字列内での開始位置を探す
                start_idx = cleaned_text.find(target)
                while start_idx != -1:
                    tl, tr, br, bl = bbox
                    
                    # 1文字あたりの横幅を概算
                    raw_len = len(text)
                    full_width = tr[0] - tl[0]
                    char_width = full_width / raw_len
                    
                    # ターゲット単語の開始・終了X座標を計算
                    word_start_x = int(tl[0] + (char_width * start_idx))
                    word_end_x = int(tl[0] + (char_width * (start_idx + len(target))))
                    
                    # ルビや文字のハネを完全に隠すためのパディング調整
                    padding = 4
                    start_x = max(int(tl[0]), word_start_x - padding)
                    end_x = min(int(tr[0]), word_end_x + padding)
                    
                    # 縦幅（Y軸）もルビごと消し去るために少し上下に広げる（上下に10%分拡張）
                    height = br[1] - tl[1]
                    start_y = max(0, int(tl[1] - height * 0.1))
                    end_y = min(image.shape[0], int(br[1] + height * 0.1))

                    # 3. 黒塗りの実行
                    cv2.rectangle(
                        image, 
                        (start_x, start_y), 
                        (end_x, end_y), 
                        (0, 0, 0), 
                        -1
                    )
                    
                    # 同じ行に複数ある場合に対応
                    start_idx = cleaned_text.find(target, start_idx + 1)

    # 4. 保存
    cv2.imwrite(output_path, image)
    print(f"処理が完了しました。保存先: {output_path}")

# --- 設定 ---
INPUT_IMG = "input2.png"          # 元画像
OUTPUT_IMG = "output_fixed2.png"  # 出力先
TARGET_WORDS = ["分離", "精製", "純物質", "混合物", "ろ過", "融点", "沸点", "ふってん", "ゆうてん", "じゅんぶっしつ", "こんごうぶつ"]

mask_specific_words_improved(INPUT_IMG, OUTPUT_IMG, TARGET_WORDS)