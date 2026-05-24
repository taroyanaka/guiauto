# 指定テキストファイルを読み込んで Codex のフォームに貼り付ける。
# `EXECUTE_ENTER` が True のとき、貼り付け後に Enter で実行する。
#
# 使い方:
#   python .\paste_file_to_codex.py
#
# exe化例:
#   python -m PyInstaller --onefile --noconsole --name "PasteFileToCodex" --clean .\paste_file_to_codex.py

import time
import ctypes
from ctypes import wintypes
from pathlib import Path

import pyautogui
import pygetwindow as gw


TEXT_FILE_PATH = r"C:\Users\taroyanaka\Downloads\navmeshPractice20260524\navmeshPractice\Assets\Scripts\Test\Log.txt"

# デフォルト: 貼り付け後に Enter で実行する
EXECUTE_ENTER = False

# Codex デスクトップアプリのウィンドウタイトル(部分一致)を想定
TARGET_TITLE = "Codex"
# もし exe 名で絞り込みたい場合の候補 (分からなければ空のままでOK)
TARGET_EXE_CANDIDATES = {"codex.exe", "Codex.exe"}


def _read_text_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    # ありがちな順に試す
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    # 最後の手段: 置換
    return p.read_text(encoding="utf-8", errors="replace")


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


def main():
    try:
        text = _read_text_file(TEXT_FILE_PATH)
    except Exception as e:
        print(f"テキスト読み込みに失敗しました: {e}")
        return

    # クリップボードに入れる (Ctrl+V で貼れるようにする)
    try:
        _set_clipboard_text_windows(text)
    except Exception as e:
        print(f"クリップボード設定に失敗しました: {e}")
        return

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
    time.sleep(0.15)

    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.1)
    if EXECUTE_ENTER:
        pyautogui.press("enter")
    print("Codexへ貼り付けました。" + ("(Enter実行)" if EXECUTE_ENTER else "(Enterなし)"))


def _set_clipboard_text_windows(text: str) -> None:
    # Windows Clipboard: Unicode text (CF_UNICODETEXT)
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    GHND = GMEM_MOVEABLE

    # 64-bit 環境でのポインタ/ハンドル破損を避けるため、シグネチャを明示
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL

    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID

    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL

    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL

    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL

    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL

    # たまに他プロセスがクリップボードを掴んでいるので少しリトライ
    deadline = time.time() + 2.0
    while True:
        if user32.OpenClipboard(None):
            break
        if time.time() >= deadline:
            raise RuntimeError("OpenClipboard failed")
        time.sleep(0.05)
    try:
        if not user32.EmptyClipboard():
            raise RuntimeError("EmptyClipboard failed")

        # 末尾NUL込み
        data = (text + "\x00").encode("utf-16-le")
        hmem = kernel32.GlobalAlloc(GHND, len(data))
        if not hmem:
            raise MemoryError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(hmem)
        if not ptr:
            kernel32.GlobalFree(hmem)
            raise MemoryError("GlobalLock failed")
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            kernel32.GlobalUnlock(hmem)

        if not user32.SetClipboardData(CF_UNICODETEXT, hmem):
            kernel32.GlobalFree(hmem)
            raise RuntimeError("SetClipboardData failed")
        # 成功時、hmem の所有権はシステムへ移るので GlobalFree しない
    finally:
        user32.CloseClipboard()


if __name__ == "__main__":
    main()
