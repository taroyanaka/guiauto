# tldr スマホで自宅PCのコードをcommitとpushをするコード

# tl 利用方法: AutoPasteApp(https://gist.github.com/taroyanaka/0ed4b21805dbe2b88090c4ef9c6d0111)(onetap.pyのexe版)の次に使うために作った

# exe化のコマンド
#   python -m PyInstaller --onefile --noconsole --name "AutoGitCommitPush" --clean .\gitcommitpush.py

import time
import pyautogui
import pygetwindow as gw
import sys

# --- 設定項目 ---
APP_TITLE = "GitHub Desktop"
COMMIT_MESSAGE = "Quick commit"

def main():
    print("[GUI] GitHub Desktop の自動操作を開始します...")

    # 1. GitHub Desktop のウィンドウを探してアクティブにする
    wins = gw.getWindowsWithTitle(APP_TITLE)
    if not wins:
        print(f"❌ エラー: '{APP_TITLE}' のウィンドウが見つかりません。起動しているか確認してください。")
        sys.exit(1)
        
    win = wins[0]
    if win.isMinimized: 
        win.restore()
    win.activate()
    time.sleep(0.8) # ウィンドウが確実に前面に来るのを待つ

    # 2. 「Changes」画面を表示 (Ctrl + 1)
    pyautogui.hotkey('ctrl', '1')
    time.sleep(0.3)

    # 3. 正式なショートカットで Commit Summary 欄へ移動 (Ctrl + G)
    pyautogui.hotkey('ctrl', 'g')
    time.sleep(0.3)

    # 4. コミットメッセージを入力
    # 万が一、前の文字が残っていた場合のために全選択して削除してから入力
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.press('delete')
    pyautogui.write(COMMIT_MESSAGE)
    time.sleep(0.3)

    # 5. コミットを実行 (Ctrl + Enter)
    # Summary欄がアクティブな状態で実行し、変更を確定させます
    pyautogui.hotkey('ctrl', 'enter')
    print("📝 画面操作: コミットを実行しました。")
    time.sleep(2.0) # コミットの確定処理を少し待つ

    # 6. 最新のコミットをPush (Ctrl + P)
    print("🚀 画面操作: Push origin を実行します...")
    pyautogui.hotkey('ctrl', 'p')
    
    print("📦 全てのGUI操作が完了しました。")

if __name__ == "__main__":
    main()