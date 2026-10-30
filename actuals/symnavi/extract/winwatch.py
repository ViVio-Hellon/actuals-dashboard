"""SymNavi と Excel の画面（ウィンドウ）を見張る。Windows のみ（ctypes = 標準ライブラリ）。

目的は 2 つ:
- 後追い: 実行中に SymNavi / Excel が出した画面（ログイン・確認・エラーの小窓など）の題名をログに残す。
  「SymNavi は開いていたか」「何か画面が出て止まっていなかったか」を記録で確かめられるようにする。
- 待ち合わせ: SymNavi の画面が出る（起動・ログインが進む）まで、命令を送るのを待つ。

見るのは SymNavi.exe と同じフォルダの exe と EXCEL.EXE の画面だけ（他のアプリの画面は記録しない）。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

DIALOG_CLASS = "#32770"   # Windows 標準の小窓（確認・エラー・入力）


def available() -> bool:
    return sys.platform == "win32"


_API: Dict[str, object] = {}


def _apis():  # type: ignore[no-untyped-def]
    """型を明示した user32 / kernel32（他のモジュールの windll の設定に影響しないよう別に読む）。"""
    if not _API:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p,
                                               wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        user32.SendMessageTimeoutW.restype = ctypes.c_size_t
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        _API.update(user32=user32, k32=k32)
    return _API["user32"], _API["k32"]


def list_windows() -> List[Dict[str, object]]:
    """見えているトップレベルウィンドウの一覧（題名・クラス・PID・exe・最小化）。

    持ち主の画面が最小化されているために隠れている小窓（#32770）も含める（hidden=True）。
    Windows は画面を最小化すると、その画面が持つ小窓も隠す。SymNavi を最小化に戻しても
    「処理中」やエラーの小窓を見落とさないようにするため。"""
    if not available():
        return []
    import ctypes
    from ctypes import wintypes
    user32, k32 = _apis()
    out: List[Dict[str, object]] = []
    exe_cache: Dict[int, str] = {}

    def exe_of(pid: int) -> str:
        if pid in exe_cache:
            return exe_cache[pid]
        path = ""
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    path = buf.value
            finally:
                k32.CloseHandle(h)
        exe_cache[pid] = path
        return path

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):  # type: ignore[no-untyped-def]
        try:
            hidden = False
            if not user32.IsWindowVisible(hwnd):
                c0 = ctypes.create_unicode_buffer(16)
                user32.GetClassNameW(hwnd, c0, 16)
                owner = user32.GetWindow(hwnd, 4) if c0.value == DIALOG_CLASS else None   # GW_OWNER
                if not owner or not user32.IsIconic(owner):
                    return True
                hidden = True
            n = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if n > 0:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                title = b.value
            cb_ = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cb_, 256)
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append({"hwnd": int(hwnd or 0), "title": title, "class": cb_.value, "pid": int(pid.value),
                        "exe": exe_of(int(pid.value)), "iconic": bool(user32.IsIconic(hwnd)), "hidden": hidden,
                        "minbox": bool(user32.GetWindowLongW(hwnd, -16) & 0x00020000)})   # GWL_STYLE / WS_MINIMIZEBOX
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        user32.EnumWindows(cb, 0)
    except Exception:  # noqa: BLE001
        return []
    return out


def pid_of_hwnd(hwnd: int) -> int:
    if not available() or not hwnd:
        return 0
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD(0)
    try:
        user32, _k = _apis()
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    except Exception:  # noqa: BLE001
        return 0
    return int(pid.value)


def hidden_excel_pids() -> List[int]:
    """画面の無い（見えない）EXCEL.EXE の PID。以前の実行（Excel 経由）で残ったものなど。"""
    if not available():
        return []
    import csv
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/FO", "CSV", "/NH"], capture_output=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:  # noqa: BLE001
        return []
    pids = []
    for row in csv.reader(out.decode("cp932", errors="replace").splitlines()):
        if len(row) > 1 and row[0].lower() == "excel.exe" and row[1].isdigit():
            pids.append(int(row[1]))
    with_window = {int(w["pid"]) for w in list_windows()
                   if os.path.basename(str(w.get("exe") or "")).lower() == "excel.exe" and w.get("class") == "XLMAIN"}
    return [p for p in pids if p not in with_window]


def close_hidden_excel() -> Dict[str, object]:
    """見えない Excel を終了させる（画面からの操作で、確認のあとに呼ぶ）。"""
    import subprocess
    pids = hidden_excel_pids()
    closed, failed = [], []
    for pid in pids:
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (closed if r.returncode == 0 else failed).append(pid)
    if pids:
        log.warning("見えない Excel を終了させました: 終了 %s / 失敗 %s", closed, failed)
    return {"closed": len(closed), "failed": len(failed), "pids": closed}


def excel_with_window() -> Optional[int]:
    """画面（XLMAIN）のある Excel の数。tasklist の EXCEL.EXE 数より少なければ、見えない Excel が残っている。"""
    if not available():
        return None
    return len({w["pid"] for w in list_windows()
                if os.path.basename(str(w.get("exe") or "")).lower() == "excel.exe" and w.get("class") == "XLMAIN"})


# 小窓の中身として読む部品（文字の表示とボタン）。入力欄（Edit）はパスワードがあり得るので読まない
_TEXT_CLASSES = ("static", "button")


def child_texts(hwnd: int, limit: int = 12) -> List[str]:
    """小窓の中の文字（ラベル・ボタン）。応答しない窓で止まらないよう、1 部品 0.2 秒まで。"""
    if not available() or not hwnd:
        return []
    import ctypes
    from ctypes import wintypes
    user32, _k = _apis()
    found: List[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _lp):  # type: ignore[no-untyped-def]
        try:
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls, 64)
            if cls.value.lower() not in _TEXT_CLASSES or not user32.IsWindowVisible(h):
                return True
            buf = ctypes.create_unicode_buffer(256)
            res = ctypes.c_size_t(0)
            if user32.SendMessageTimeoutW(h, 0x000D, 256, ctypes.cast(buf, ctypes.c_void_p), 0x0002, 200,
                                          ctypes.byref(res)):   # WM_GETTEXT, SMTO_ABORTIFHUNG
                t = " ".join(buf.value.replace("&", "").split())[:80]
                if t and t not in found:
                    found.append(t)
        except Exception:  # noqa: BLE001
            pass
        return len(found) < limit

    try:
        user32.EnumChildWindows(wintypes.HWND(hwnd), cb, 0)
    except Exception:  # noqa: BLE001
        return found
    return found


def _children(hwnd: int) -> List[Dict[str, object]]:
    """小窓の部品（ラベル・ボタン）の hwnd・クラス・文字。"""
    if not available() or not hwnd:
        return []
    import ctypes
    from ctypes import wintypes
    user32, _k = _apis()
    found: List[Dict[str, object]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _lp):  # type: ignore[no-untyped-def]
        try:
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls, 64)
            if cls.value.lower() in _TEXT_CLASSES:
                buf = ctypes.create_unicode_buffer(256)
                res = ctypes.c_size_t(0)
                if user32.SendMessageTimeoutW(h, 0x000D, 256, ctypes.cast(buf, ctypes.c_void_p), 0x0002, 200,
                                              ctypes.byref(res)):
                    found.append({"hwnd": int(h or 0), "class": cls.value,
                                  "text": " ".join(buf.value.replace("&", "").split())})
        except Exception:  # noqa: BLE001
            pass
        return len(found) < 40

    try:
        user32.EnumChildWindows(wintypes.HWND(hwnd), cb, 0)
    except Exception:  # noqa: BLE001
        pass
    return found


def click_button(dialog_hwnd: int, label_prefix: str) -> bool:
    """小窓の中の、文字が label_prefix で始まるボタンを押す（BM_CLICK）。押したら True。"""
    if not available():
        return False
    user32, _k = _apis()
    for c in _children(dialog_hwnd):
        if str(c["class"]).lower() == "button" and str(c["text"]).startswith(label_prefix):
            return bool(user32.PostMessageW(int(c["hwnd"]), 0x00F5, 0, 0))   # BM_CLICK
    return False


def minimize_main(pid: int) -> int:
    """その PID の本体の画面（最小化ボタンのある画面。小窓は除く）が出ていたら、前に出さずに最小化する。
    最小化した数を返す。SymNavi は RNE を開くたびに画面を元の大きさに戻して前に出すため（MFC の動き）。"""
    if not available() or not pid:
        return 0
    user32, _k = _apis()
    n = 0
    for w in list_windows():
        if (int(w["pid"]) == pid and w.get("class") != DIALOG_CLASS and not w.get("hidden")
                and not w.get("iconic") and w.get("minbox")):
            user32.ShowWindow(int(w["hwnd"]), 7)   # SW_SHOWMINNOACTIVE（最小化。ほかの画面から前を奪わない）
            n += 1
    return n


def post_close(pid: int) -> int:
    """その PID の見えているトップレベル画面に「閉じる」（WM_CLOSE）を送る。送った数を返す。
    旧 VBA が Word の Tasks で SymNavi を閉じていたのと同じ「普通の閉じ方」。"""
    if not available() or not pid:
        return 0
    user32, _k = _apis()
    n = 0
    for w in list_windows():
        if int(w["pid"]) == pid:
            if user32.PostMessageW(int(w["hwnd"]), 0x0010, 0, 0):   # WM_CLOSE
                n += 1
    return n


def describe(w: Dict[str, object], mask: Optional[List[str]] = None) -> str:
    kind = "小窓" if w.get("class") == DIALOG_CLASS else "画面"
    state = "、最小化" if w.get("iconic") else ""
    exe = os.path.basename(str(w.get("exe") or "")) or "?"
    text = f"{kind}「{w.get('title') or '（題名なし）'}」({exe} pid={w.get('pid')} class={w.get('class')}{state})"
    texts = w.get("texts") or []
    if texts:
        text += " 中身: " + " / ".join(f"「{t}」" for t in texts)   # type: ignore[union-attr]
    for m in mask or []:
        if m and len(m) >= 2:
            text = text.replace(m, "<伏せ字>")
    return text


class WindowWatcher:
    """SymNavi / Excel の画面の出入りをログに書く。小窓が出たら on_dialog で知らせる。"""

    def __init__(self, symnavi_exe: str, interval: float = 1.0,
                 on_dialog: Optional[Callable[[Dict[str, object]], None]] = None,
                 mask: Optional[List[str]] = None) -> None:
        self.mask = [m for m in (mask or []) if m]
        self.sym_dir = os.path.normcase(os.path.dirname(os.path.abspath(symnavi_exe))) if symnavi_exe else ""
        self.interval = interval
        self.on_dialog = on_dialog
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self._seen: Dict[int, str] = {}
        self.current: List[Dict[str, object]] = []
        self.symnavi_seen = False
        self._dismissed: set = set()
        self._lock = threading.Lock()

    # SymNavi の「起動画面」（新規 / 開く / キャンセル と「次回の起動時にもこの画面を表示」）。
    # 出ている間は DDE の命令を NOTPROCESSED で断るので、キャンセル（メイン画面へ進む）を押す。
    # キャンセルは「何もせずメイン画面に移る」ボタンで、データにも設定にも影響しない（ヘルプ「起動画面」）。
    START_SCREEN_MARK = "次回の起動時にもこの画面を表示"

    def dismiss_start_screen(self) -> bool:
        """SymNavi の起動画面が出ていればキャンセルを押す。押したら True（同じ画面には 1 度だけ）。"""
        for w in list(self.current):
            hwnd = int(w["hwnd"])
            if (self.is_symnavi(w) and w.get("class") == DIALOG_CLASS and hwnd not in self._dismissed
                    and any(self.START_SCREEN_MARK in str(t) for t in (w.get("texts") or []))):   # type: ignore[union-attr]
                self._dismissed.add(hwnd)
                if click_button(hwnd, "キャンセル"):
                    log.warning("SymNavi の起動画面が出ていたため「キャンセル」を押しました（命令を受け付けない原因のため）。"
                                "SymNavi のオプション → 画面タブ →「起動画面 表示する」をオフにすると出なくなります")
                    return True
        return False

    def is_symnavi(self, w: Dict[str, object]) -> bool:
        exe = os.path.normcase(str(w.get("exe") or ""))
        return bool(self.sym_dir and exe and os.path.dirname(exe) == self.sym_dir)

    @staticmethod
    def is_excel(w: Dict[str, object]) -> bool:
        return os.path.basename(str(w.get("exe") or "")).lower() == "excel.exe"

    def snapshot(self) -> List[Dict[str, object]]:
        out = [w for w in list_windows() if self.is_symnavi(w) or self.is_excel(w)]
        for w in out:
            if w.get("class") == DIALOG_CLASS:   # 小窓が何を言っているか（ラベルとボタンを分けて持つ）
                parts = [c for c in _children(int(w["hwnd"])) if c["text"]]
                w["labels"] = [str(c["text"])[:200] for c in parts if str(c["class"]).lower() != "button"][:12]
                w["buttons"] = [str(c["text"])[:40] for c in parts if str(c["class"]).lower() == "button"][:8]
                w["texts"] = list(w["labels"]) + list(w["buttons"])   # type: ignore[arg-type]
        return out

    def poll(self) -> List[Dict[str, object]]:
        """1 回見る。変化をログに書き、今の対象画面を返す（見張りスレッドと処理側の両方から呼ばれる）。"""
        with self._lock:
            return self._poll()

    def _poll(self) -> List[Dict[str, object]]:
        now = self.snapshot()
        cur = {int(w["hwnd"]): describe(w, self.mask) for w in now}
        new_dialogs: List[Dict[str, object]] = []
        for hwnd, text in cur.items():
            if self._seen.get(hwnd) != text:
                who = "SymNavi" if any(self.is_symnavi(w) for w in now if int(w["hwnd"]) == hwnd) else "Excel"
                log.info("%s の%s: %s", who, "画面が変わりました" if hwnd in self._seen else "画面が出ました", text)
                w = next(x for x in now if int(x["hwnd"]) == hwnd)
                if w.get("class") == DIALOG_CLASS and self.on_dialog:
                    new_dialogs.append(w)
        for hwnd in set(self._seen) - set(cur):
            log.info("画面が閉じました: %s", self._seen[hwnd])
        self._seen = cur
        self.current = now
        for w in new_dialogs:
            try:
                self.on_dialog(w)   # type: ignore[misc]
            except Exception:  # noqa: BLE001
                log.exception("小窓の扱いで失敗")
        if any(self.is_symnavi(w) for w in now):
            self.symnavi_seen = True
        return now

    def wait_symnavi(self, timeout: float, cancel: Optional[Callable[[], bool]] = None,
                     tick: Optional[Callable[[float], None]] = None) -> bool:
        """SymNavi の画面が出るまで待つ。出たら True、timeout で False。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if any(self.is_symnavi(w) for w in self.poll()):
                return True
            if cancel and cancel():
                return False
            if tick:
                tick(time.time() - t0)
            time.sleep(0.5)
        return False

    def start(self) -> "WindowWatcher":
        if not available():
            return self

        def loop() -> None:
            while not self._stop.wait(self.interval):
                try:
                    self.poll()
                except Exception:  # noqa: BLE001  見張りの失敗で処理は止めない
                    pass

        self._th = threading.Thread(target=loop, name="win-watch", daemon=True)
        self._th.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._th:
            self._th.join(timeout=3)
        try:
            self.poll()   # 最後の状態を残す
        except Exception:  # noqa: BLE001
            pass
