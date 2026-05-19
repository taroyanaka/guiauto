# antigravityのチャットのフォームにフォーカスが当たってる状態にしとかないと動作しないから注意(後で直せたら直したいけど画面キャプチャ以外の解決策が思いつかないから当分これ)

# tldr スマホでソフト改造するためのプログラム

# tl 利用方法: スマホで音声入力でプロンプトをgoogle docsに吹き込み→スマホのteamviewer開いて自宅のPCを開く&このコードのexe版を実行→
#              exe版の動作: google docsの自動更新webページからantigravityのチャット欄にコピペ&実行→
#                         : gitcommitpush.pyのexe版を実行してデプロイ完了
#              スマホでデプロイされたアプリの動作確認(確認後docsの実装済みの先頭行の仕様の文を削除)

# exe化のコマンド
#   python -m PyInstaller --onefile --noconsole --name "AutoPasteApp" --clean .\onetap.py                               

import time
import pyautogui
import pygetwindow as gw
from selenium import webdriver
from selenium.webdriver.common.by import By

# --- 設定項目 ---
DOC_URL = "https://docs.google.com/document/d/e/2PACX-1vTQSYmAoW4hL6JcL0Z7rkG5nN2koCGJeTt3qCoyKB_S-RgFk0LkZfQMu7-g89M50A3ewiR9-FBlnqCb/pub"
APP_TITLE = "Antigravity"

def main():
    # 1. ブラウザ起動と準備
    try:
        driver = webdriver.Chrome()
    except Exception as e:
        print(f"ブラウザの起動に失敗しました: {e}")
        return

    try:
        driver.get(DOC_URL)
        time.sleep(3) # 読み込み待ち

        # ボタン生成JS実行
        js_code = """
        (async()=>{const c=document.querySelector('.doc-content');if(!c)return;let e=[];const p=el=>{if(!el.length)return;const t=el.map(x=>x.innerText.trim()).join('\\n').trim();if(!t)return;const b=document.createElement('button');b.className='auto-btn';b.innerText='📋 コピー';Object.assign(b.style,{display:'block',margin:'10px 0',padding:'8px',background:'#1a73e8',color:'#fff',border:'none',borderRadius:'4px',cursor:'pointer'});b.onclick=async()=>{await navigator.clipboard.writeText(t);b.remove();};el[el.length-1].after(b)};Array.from(c.querySelectorAll('p')).forEach(x=>{if(x.innerText.trim()===""){p(e);e=[];}else{e.push(x)}});p(e);})();
        """
        driver.execute_script(js_code)
        time.sleep(1)

        # 2. 最初のボタンをクリックしてコピー
        try:
            button = driver.find_element(By.CLASS_NAME, "auto-btn")
            button.click()
            time.sleep(0.5) # クリップボード反映待ち
        except:
            print("処理可能なボタンが見つかりませんでした。")
            return

        # 3. アプリへ切り替えて貼り付け実行
        wins = gw.getWindowsWithTitle(APP_TITLE)
        if wins:
            win = wins[0]
            if win.isMinimized: win.restore()
            win.activate()
            time.sleep(0.8) # ウィンドウが完全に前面に来るのを少し待つ
            
            # ペースト & エンター
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            pyautogui.press('enter')
            print("アプリ（Antigravity）への入力を完了しました。")
        else:
            print(f"ウィンドウ '{APP_TITLE}' が見つかりませんでした。")

    finally:
        # 終了
        time.sleep(1)
        driver.quit()

if __name__ == "__main__":
    main()