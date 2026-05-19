import time
import pyautogui
import pygetwindow as gw
from selenium import webdriver
from selenium.webdriver.common.by import By

# --- 設定項目 ---
DOC_URL = "https://docs.google.com/document/d/1LTO3NFd4R8KE0J4mJ8q77lcPO6Wqi8xSaba02cp_hQA/edit?usp=sharing"
# ターゲットを本家VS Codeのウィンドウ名に変更
APP_TITLE = "Visual Studio Code"

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

        # 3. VS Codeへ切り替えてチャット欄に貼り付け・実行
        # ウィンドウ名に「Visual Studio Code」が含まれるものを探す
        wins = gw.getWindowsWithTitle(APP_TITLE)
        if wins:
            win = wins[0]
            if win.isMinimized: win.restore()
            win.activate()
            time.sleep(0.8) # ウィンドウ切り替えの安定待ち
            
            # 【重要】VS Codeのチャットビューを開き、入力欄にフォーカスするショートカット
            pyautogui.hotkey('ctrl', 'alt', 'i')
            time.sleep(0.4) # パネルが開くのを少し待つ
            
            # ペースト & エンター（送信）
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            pyautogui.press('enter')
            print("VS Codeのチャットへの入力を完了しました。")
        else:
            print(f"ウィンドウ '{APP_TITLE}' が見つかりませんでした。起動しているか確認してください。")

    finally:
        # 終了
        time.sleep(1)
        driver.quit()

if __name__ == "__main__":
    main()