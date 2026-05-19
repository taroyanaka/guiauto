import time
import pyautogui
import pygetwindow as gw
import sys

# --- 設定項目 ---
# Roblox Studioのウィンドウタイトルに含まれる共通の文字列
APP_TITLE = "Roblox Studio"

def main():
    print("[GUI] Roblox Studio の自動公開操作を開始します...")

    # 1. Roblox Studio のウィンドウを探してアクティブにする
    wins = gw.getWindowsWithTitle(APP_TITLE)
    if not wins:
        print(f"❌ エラー: '{APP_TITLE}' が見つかりません。プロジェクトが開いているか確認してください。")
        sys.exit(1)
        
    win = wins[0]
    if win.isMinimized: 
        win.restore()
    win.activate()
    time.sleep(0.8) # ウィンドウが確実に前面に来るのを少し待つ

    # 2. 「Robloxへ公開」のショートカットキーを実行 (Alt + P)
    print("🚀 画面操作: Robloxへ公開 (Alt + P) を実行します...")
    pyautogui.hotkey('alt', 'p')
    
    # 3. 確定処理の待機
    # 公開処理の通信やダイアログの完了を待つため、少し長めに待機します
    time.sleep(3.0) 
    
    print("📦 全てのGUI操作が完了しました。")

if __name__ == "__main__":
    main()