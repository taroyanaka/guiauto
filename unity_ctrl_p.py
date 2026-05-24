import argparse
import time
import ctypes
from ctypes import wintypes

import pyautogui
import pygetwindow as gw


def _force_foreground(win, timeout_sec: float = 2.0) -> bool:
    """
    pygetwindowのactivate()だけで前面化できないケースがあるため、Win32 APIも併用して前面化する。
    """
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
        user32.BringWindowToTop(wintypes.HWND(hwnd))
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
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

        try:
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
        except Exception:
            pass
        time.sleep(0.05)

    return False


def _find_unity_windows(title_contains: str):
    # Unityはプロジェクト名などでタイトルが変わるためcontains検索にする
    title_contains_lower = title_contains.lower()
    wins = []
    for w in gw.getAllWindows():
        try:
            if not w.title:
                continue
            if title_contains_lower in w.title.lower():
                wins.append(w)
        except Exception:
            continue
    return wins


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unity Editorを前面化して Ctrl+P（Play/Stop）を送るだけのスクリプト"
    )
    parser.add_argument(
        "--title",
        default="Unity",
        help="Unityのウィンドウタイトルに含まれる文字列（デフォルト: Unity）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="前面化後にキー送信するまでの待機秒（デフォルト: 0.2）",
    )
    args = parser.parse_args()

    wins = _find_unity_windows(args.title)
    if not wins:
        print(f"Unityウィンドウが見つかりません: title contains '{args.title}'")
        return 1

    # いくつか候補がある場合は、最後に見つかったもの（だいたい直近/前面）を優先
    win = wins[-1]
    if not _force_foreground(win, timeout_sec=2.0):
        print("Unityの前面化に失敗しました（管理者権限/フォーカス制御の制限の可能性）")
        return 2

    time.sleep(max(0.0, float(args.delay)))
    pyautogui.hotkey("ctrl", "p")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

