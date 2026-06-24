# -*- coding: utf-8 -*-
"""
GPU対応版のマスク処理アプリ。

動作は [mask_text_in_image.py](./mask_text_in_image.py) と同じで、
EasyOCR の reader を GPU 利用で初期化する点だけが違います。
"""

from __future__ import annotations

import mask_text_in_image as base


def use_gpu() -> bool:
    return True


# 元実装の GPU 判定だけ差し替える
base.use_gpu = use_gpu


if __name__ == "__main__":
    base.main()
