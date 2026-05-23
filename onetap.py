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
import ctypes
from ctypes import wintypes
from selenium import webdriver
from selenium.webdriver.common.by import By

# --- 設定項目 ---
DOC_URL = "https://docs.google.com/document/d/e/2PACX-1vTQSYmAoW4hL6JcL0Z7rkG5nN2koCGJeTt3qCoyKB_S-RgFk0LkZfQMu7-g89M50A3ewiR9-FBlnqCb/pub"
APP_TITLE = "Antigravity"
ANTIGRAVITY_EXE_CANDIDATES = {"antigravity.exe", "antigravity", "Antigravity.exe", "Antigravity"}

def _try_focus_input_by_click(win):
    """
    Antigravityがアクティブでも入力欄にフォーカスが入らないことがあるため、
    ウィンドウ内の「入力欄っぽい位置」を複数試してクリックする。
    成否判定は難しいので、誤爆しない前提(=前面がAntigravityであること)でベストエフォート。
    """
    # 下寄り中心から始めて、少しずつ位置を変えて試す
    ratios = [
        (0.50, 0.88),
        (0.50, 0.82),
        (0.50, 0.76),
        (0.40, 0.88),
        (0.60, 0.88),
    ]

    try:
        left, top, width, height = win.left, win.top, win.width, win.height
    except Exception:
        return

    for (rx, ry) in ratios:
        try:
            x = left + int(width * rx)
            y = top + int(height * ry)
            pyautogui.click(x, y)
            time.sleep(0.05)
        except Exception:
            pass

def _get_window_exe_basename(hwnd):
    """
    hwnd -> 実行ファイル名(例: 'chrome.exe')を取得。取れない場合はNone。
    pywin32無しでctypesだけでやる。
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    if not pid.value:
        return None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not hproc:
        return None
    try:
        # QueryFullProcessImageNameW
        buf_len = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(buf_len.value)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(buf_len)) == 0:
            return None
        path = buf.value
        # basenameだけ返す
        return path.split("\\")[-1]
    finally:
        kernel32.CloseHandle(hproc)

def _find_antigravity_windows():
    """
    タイトル一致は誤検出（ブラウザのタブ名など）し得るので、
    まずはプロセス名(EXE)でAntigravityのトップレベルウィンドウを探す。
    """
    user32 = ctypes.windll.user32

    results = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            exe = _get_window_exe_basename(hwnd)
            if not exe:
                return True
            if exe.lower() in {x.lower() for x in ANTIGRAVITY_EXE_CANDIDATES}:
                try:
                    w = gw.Win32Window(hwnd)
                    results.append(w)
                except Exception:
                    pass
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc, 0)
    return results

def _pick_antigravity_window(wins):
    # できるだけ「いま使える」ウィンドウを選ぶ（複数マッチするケース対策）
    if not wins:
        return None
    for w in wins:
        try:
            if getattr(w, "isActive", False):
                return w
        except Exception:
            pass
    for w in wins:
        try:
            if not getattr(w, "isMinimized", False):
                return w
        except Exception:
            pass
    return wins[0]

def _force_foreground(win, timeout_sec=2.0):
    """
    pygetwindow.activate()だけだと前面化できないケースがあるため、User32経由で前面化を強制する。
    それでも失敗する場合があるので、最後はウィンドウ内クリックでフォーカスを取りに行く。
    """
    hwnd = getattr(win, "_hWnd", None)
    if not hwnd:
        return False

    user32 = ctypes.windll.user32
    SW_RESTORE = 9

    try:
        if getattr(win, "isMinimized", False):
            win.restore()
    except Exception:
        pass

    # まずは通常のactivateを試す
    try:
        win.activate()
    except Exception:
        pass

    # 追加で前面化を強制
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        user32.BringWindowToTop(wintypes.HWND(hwnd))
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception:
        pass

    # 2秒程度リトライ（SetForegroundWindowが拒否されることがある）
    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        try:
            active = gw.getActiveWindow()
            if active is not None and getattr(active, "_hWnd", None) == hwnd:
                return True
        except Exception:
            pass

        try:
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
        except Exception:
            pass
        time.sleep(0.05)

    return False

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
        # まずEXE名ベースで正しいウィンドウを取る（ブラウザ誤爆を防ぐ）
        wins = _find_antigravity_windows()
        if not wins:
            # フォールバック: タイトル一致（ただし誤検出リスクあり）
            wins = gw.getWindowsWithTitle(APP_TITLE)
        if wins:
            win = _pick_antigravity_window(wins)
            ok = _force_foreground(win, timeout_sec=2.0)
            time.sleep(0.2)

            # 安全策: 前面がAntigravityになっていなければ、貼り付け/Enterを中止する
            try:
                active = gw.getActiveWindow()
                active_hwnd = getattr(active, "_hWnd", None) if active else None
            except Exception:
                active_hwnd = None

            if not ok or active_hwnd != getattr(win, "_hWnd", None):
                print("エラー: Antigravityをアクティブ化できませんでした。誤爆防止のため貼り付け/Enterを中止します。")
                return

            # 入力欄のフォーカスを取りに行く（複数クリックで保険）
            _try_focus_input_by_click(win)
            time.sleep(0.15)

            time.sleep(0.4) # ウィンドウが完全に前面に来るのを少し待つ
            
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
