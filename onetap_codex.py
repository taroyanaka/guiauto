# Google Docs (公開ページ)の「段落ブロック」を1つクリップボードにコピーして、
# Codex のプロンプト入力欄へ貼り付け→実行(Enter)するワンショット自動化。
#
# 使い方:
#   python .\onetap_codex.py
#
# exe化例:
#   python -m PyInstaller --onefile --noconsole --name "OneTapCodex" --clean .\onetap_codex.py

import time
import ctypes
from ctypes import wintypes

import pyautogui
import pygetwindow as gw
from selenium import webdriver
from selenium.webdriver.common.by import By


DOC_URL = "https://docs.google.com/document/u/2/d/e/2PACX-1vQ5mkmAv_UuB3b_qeB5w5N7nmY_SCPItPshVCgEgoy3IFX69Wp7JBkLeMf7MCkRbT7a1EuDBUueHP7W/pub"

# Codex デスクトップアプリのウィンドウタイトル(部分一致)を想定
TARGET_TITLE = "Codex"
# もし exe 名で絞り込みたい場合の候補 (分からなければ空のままでOK)
TARGET_EXE_CANDIDATES = {"codex.exe", "Codex.exe"}


def _get_window_exe_basename(hwnd):
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
        buf_len = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(buf_len.value)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(buf_len)) == 0:
            return None
        return buf.value.split("\\")[-1]
    finally:
        kernel32.CloseHandle(hproc)


def _find_windows_by_exe(exe_candidates):
    user32 = ctypes.windll.user32
    results = []
    candidates_lc = {x.lower() for x in exe_candidates}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            exe = _get_window_exe_basename(hwnd)
            if not exe:
                return True
            if exe.lower() in candidates_lc:
                try:
                    results.append(gw.Win32Window(hwnd))
                except Exception:
                    pass
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc, 0)
    return results


def _force_foreground(win, timeout_sec=2.0):
    user32 = ctypes.windll.user32
    hwnd = getattr(win, "_hWnd", None)
    if not hwnd:
        return False

    SW_RESTORE = 9

    try:
        if getattr(win, "isMinimized", False):
            win.restore()
    except Exception:
        pass

    try:
        win.activate()
    except Exception:
        pass

    try:
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        user32.BringWindowToTop(wintypes.HWND(hwnd))
    except Exception:
        pass

    deadline = time.time() + float(timeout_sec)
    while time.time() < deadline:
        try:
            active = gw.getActiveWindow()
            if active is not None and getattr(active, "_hWnd", None) == hwnd:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def _try_focus_input_by_click(win):
    # 入力欄の位置が一定ではないので、下部あたりを数点クリックしてフォーカスを当てに行く
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
            pyautogui.click(left + int(width * rx), top + int(height * ry))
            time.sleep(0.05)
        except Exception:
            pass


def _pick_target_window(wins):
    if not wins:
        return None
    try:
        active = gw.getActiveWindow()
        active_hwnd = getattr(active, "_hWnd", None) if active else None
    except Exception:
        active_hwnd = None

    for w in wins:
        if getattr(w, "_hWnd", None) == active_hwnd:
            return w
    return wins[0]


def _copy_one_block_from_google_docs(driver):
    # 公開Google Docsの段落(<p>)を、空行区切りで「ブロック」扱いにして1ブロックだけコピーできるボタンを挿入。
    js_code = r"""
    (async()=>{
      const c=document.querySelector('.doc-content');
      if(!c) return;
      let block=[];
      const flush=(els)=>{
        if(!els.length) return;
        const t=els.map(x=>x.innerText.trim()).join('\n').trim();
        if(!t) return;
        const b=document.createElement('button');
        b.className='auto-btn';
        b.innerText='コピーして実行';
        Object.assign(b.style,{
          display:'block',margin:'12px 0',padding:'10px 12px',
          background:'#1a73e8',color:'#fff',border:'none',borderRadius:'6px',
          fontSize:'14px',cursor:'pointer'
        });
        b.onclick=async()=>{ await navigator.clipboard.writeText(t); b.remove(); };
        els[els.length-1].after(b);
      };
      Array.from(c.querySelectorAll('p')).forEach(p=>{
        if(p.innerText.trim()===""){ flush(block); block=[]; }
        else { block.push(p); }
      });
      flush(block);
    })();
    """
    driver.execute_script(js_code)
    time.sleep(0.5)

    button = driver.find_element(By.CLASS_NAME, "auto-btn")
    button.click()
    time.sleep(0.3)


def main():
    try:
        driver = webdriver.Chrome()
    except Exception as e:
        print(f"Chrome起動に失敗しました: {e}")
        return

    try:
        driver.get(DOC_URL)
        time.sleep(3)

        try:
            _copy_one_block_from_google_docs(driver)
        except Exception as e:
            print(f"Google Docs からコピーできませんでした: {e}")
            return

        # Codexウィンドウへ貼り付け→Enter
        wins = []
        if TARGET_EXE_CANDIDATES:
            wins = _find_windows_by_exe(TARGET_EXE_CANDIDATES)
        if not wins:
            wins = gw.getWindowsWithTitle(TARGET_TITLE)

        win = _pick_target_window(wins)
        if not win:
            print(f"ウィンドウ '{TARGET_TITLE}' が見つかりませんでした。Codexを開いてから実行してください。")
            return

        if not _force_foreground(win, timeout_sec=2.0):
            print("Codexを前面化できませんでした。手動でCodexを前面にしてからもう一度実行してください。")
            return

        _try_focus_input_by_click(win)
        time.sleep(0.2)

        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)
        pyautogui.press("enter")
        print("Codexへ貼り付けて実行しました。")
    finally:
        time.sleep(0.5)
        driver.quit()


if __name__ == "__main__":
    main()

