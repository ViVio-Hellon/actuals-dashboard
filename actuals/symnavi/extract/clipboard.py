"""Windows のクリップボードを読む（標準ライブラリの ctypes だけ）。

SymNavi の [Copy()]（富士通の Excel / Word 連携のマクロも使っている命令）で出した問い合わせ表を受け取るため。
ファイル（Excel 97-2003）に保存させるより速い見込み。実機で「比べる」モードで確かめてから使う。

クリップボードは PC の利用者と共有なので:
- 読む前後の通し番号（GetClipboardSequenceNumber）と持ち主（SymNavi の画面か）を確かめ、
  途中で利用者が別の物をコピーしていたら使わない（ファイルでの受け取りに戻る）。
- 利用者が入れていた文字は、読み終わったら戻す（文字以外は戻せない）。
"""
from __future__ import annotations

import logging
import sys
import time
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

CF_TEXT = 1
CF_UNICODETEXT = 13

_API: dict = {}


class ClipboardError(Exception):
    pass


def available() -> bool:
    return sys.platform == "win32"


def _apis():  # type: ignore[no-untyped-def]
    if not _API:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        u.OpenClipboard.argtypes = [wintypes.HWND]
        u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.restype = wintypes.BOOL
        u.GetClipboardSequenceNumber.restype = wintypes.DWORD
        u.GetClipboardOwner.restype = wintypes.HWND
        u.EnumClipboardFormats.argtypes = [wintypes.UINT]
        u.EnumClipboardFormats.restype = wintypes.UINT
        u.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
        u.GetClipboardFormatNameW.restype = ctypes.c_int
        u.GetClipboardData.argtypes = [wintypes.UINT]
        u.GetClipboardData.restype = wintypes.HANDLE
        u.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u.SetClipboardData.restype = wintypes.HANDLE
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k.GlobalSize.argtypes = [wintypes.HGLOBAL]
        k.GlobalSize.restype = ctypes.c_size_t
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalFree.argtypes = [wintypes.HGLOBAL]
        _API.update(u=u, k=k, ctypes=ctypes, wintypes=wintypes)
    return _API["u"], _API["k"]


def sequence() -> int:
    """クリップボードの通し番号（中身が変わるたびに増える）。"""
    if not available():
        return 0
    u, _k = _apis()
    return int(u.GetClipboardSequenceNumber())


class _Open:
    """OpenClipboard をほかのアプリが開いている間は少し待って取り直す。"""

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def __enter__(self):  # type: ignore[no-untyped-def]
        u, _k = _apis()
        t0 = time.time()
        while not u.OpenClipboard(None):
            if time.time() - t0 > self.timeout:
                raise ClipboardError("クリップボードを開けません（ほかのアプリが使用中）")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        u, _k = _apis()
        u.CloseClipboard()


def _formats() -> List[Tuple[int, str]]:
    u, _k = _apis()
    ctypes = _API["ctypes"]
    out = []
    f = 0
    while True:
        f = int(u.EnumClipboardFormats(f))
        if not f:
            break
        buf = ctypes.create_unicode_buffer(128)
        n = u.GetClipboardFormatNameW(f, buf, 128)
        out.append((f, buf.value if n > 0 else {CF_TEXT: "CF_TEXT", CF_UNICODETEXT: "CF_UNICODETEXT"}.get(f, str(f))))
    return out


def _bytes_of(fmt: int) -> Optional[bytes]:
    u, k = _apis()
    ctypes = _API["ctypes"]
    h = u.GetClipboardData(fmt)
    if not h:
        return None
    p = k.GlobalLock(h)
    if not p:
        return None
    try:
        return ctypes.string_at(p, int(k.GlobalSize(h)))
    finally:
        k.GlobalUnlock(h)


def owner_pid() -> int:
    u, _k = _apis()
    wintypes = _API["wintypes"]
    ctypes = _API["ctypes"]
    hwnd = u.GetClipboardOwner()
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def read_table(prefer: Tuple[str, ...] = ("Csv",)) -> Tuple[str, bytes, List[str]]:
    """クリップボードの表を読む。戻り値: (形式, 中身, 載っていた形式の一覧)。

    形式 "Csv" = SymNavi が登録した CSV（Shift-JIS の bytes）、"CF_UNICODETEXT" = タブ区切りの文字（UTF-16 の bytes）。
    """
    with _Open():
        fmts = _formats()
        names = [n for _f, n in fmts]
        for want in prefer:
            for f, n in fmts:
                if n.lower() == want.lower():
                    data = _bytes_of(f)
                    if data is not None:
                        return n, data.split(b"\x00", 1)[0], names
        data = _bytes_of(CF_UNICODETEXT)
        if data is not None:
            # UTF-16 の終端（2 バイトの 0）まで
            end = next((i for i in range(0, len(data) - 1, 2) if data[i] == 0 and data[i + 1] == 0), len(data))
            return "CF_UNICODETEXT", data[:end], names
    raise ClipboardError(f"クリップボードに表がありません（載っていた形式: {', '.join(names) or 'なし'}）")


def get_text() -> Optional[str]:
    """利用者がクリップボードに入れていた文字（無ければ None）。読んだら戻すため。"""
    if not available():
        return None
    try:
        with _Open(timeout=1.0):
            data = _bytes_of(CF_UNICODETEXT)
    except ClipboardError:
        return None
    if data is None:
        return None
    return data.decode("utf-16-le", errors="replace").split("\x00", 1)[0]


def put_text(text: Optional[str]) -> None:
    """クリップボードを text にする（None なら空にする。SymNavi の表を置いたままにしない）。"""
    if not available():
        return
    u, k = _apis()
    ctypes = _API["ctypes"]
    with _Open(timeout=1.0):
        u.EmptyClipboard()
        if text is None:
            return
        raw = text.encode("utf-16-le") + b"\x00\x00"
        h = k.GlobalAlloc(0x0002, len(raw))   # GMEM_MOVEABLE
        if not h:
            return
        p = k.GlobalLock(h)
        ctypes.memmove(p, raw, len(raw))
        k.GlobalUnlock(h)
        if not u.SetClipboardData(CF_UNICODETEXT, h):
            k.GlobalFree(h)
