"""DDE クライアント（Windows の DDEML を ctypes で直接呼ぶ。標準ライブラリのみ・Excel 不要）。

旧 VBA は Excel の DDEInitiate / DDEExecute を使っていた。Excel 経由だと
- 見えない Excel を 1 つ起動する必要がある（残ると邪魔をする）
- 命令が「処理された」のか「受け流された」のか分からない（旧 VBA のコメントにもある）
ので、Windows の DDEML（user32.dll）を直接使う。命令ごとに
  受け付けた（DDE_FACK）/ 忙しい（DMLERR_BUSY）/ 処理しなかった（DMLERR_NOTPROCESSED）/ 時間切れ
が分かる。

DDEML は「初期化したスレッド」で全部の呼び出しを行い、待つ間もそのスレッドのメッセージを流す必要がある
（流さないと、他のアプリの DDE 一斉呼びかけを止めてしまう）。待ちには pump() を使う。
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable, Optional, Tuple

APPCMD_CLIENTONLY = 0x00000010
CP_WINANSI = 1004
XTYP_EXECUTE = 0x4050
DDE_FACK = 0x8000
DDE_FBUSY = 0x4000

DMLERR = {
    0x0000: "NO_ERROR", 0x4000: "ADVACKTIMEOUT", 0x4001: "BUSY", 0x4002: "DATAACKTIMEOUT",
    0x4003: "DLL_NOT_INITIALIZED", 0x4004: "DLL_USAGE", 0x4005: "EXECACKTIMEOUT", 0x4006: "INVALIDPARAMETER",
    0x4007: "LOW_MEMORY", 0x4008: "MEMORY_ERROR", 0x4009: "NOTPROCESSED", 0x400A: "NO_CONV_ESTABLISHED",
    0x400B: "POKEACKTIMEOUT", 0x400C: "POSTMSG_FAILED", 0x400D: "REENTRANCY", 0x400E: "SERVER_DIED",
    0x400F: "SYS_ERROR", 0x4010: "UNADVACKTIMEOUT", 0x4011: "UNFOUND_QUEUE_ID",
}
BUSY = 0x4001
NOTPROCESSED = 0x4009
EXECACKTIMEOUT = 0x4005
SERVER_DIED = 0x400E


def err_name(code: int) -> str:
    return DMLERR.get(code, f"0x{code:04X}")


class DdeError(Exception):
    def __init__(self, message: str, err: int = 0) -> None:
        super().__init__(message)
        self.err = err


class Win32Dde:
    """DDEML の薄い包み。ANSI 版（SymNavi のような古いアプリと同じ文字コードで送る）。"""

    def __init__(self, encoding: str = "mbcs") -> None:
        if sys.platform != "win32":
            raise DdeError("DDE は Windows でのみ使えます")
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.encoding = encoding
        u = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        H = ctypes.c_void_p
        self.CALLBACK = ctypes.WINFUNCTYPE(H, wintypes.UINT, wintypes.UINT, H, H, H, H, ctypes.c_size_t, ctypes.c_size_t)
        u.DdeInitializeA.argtypes = [ctypes.POINTER(wintypes.DWORD), self.CALLBACK, wintypes.DWORD, wintypes.DWORD]
        u.DdeInitializeA.restype = wintypes.UINT
        u.DdeCreateStringHandleA.argtypes = [wintypes.DWORD, ctypes.c_char_p, ctypes.c_int]
        u.DdeCreateStringHandleA.restype = H
        u.DdeFreeStringHandle.argtypes = [wintypes.DWORD, H]
        u.DdeConnect.argtypes = [wintypes.DWORD, H, H, ctypes.c_void_p]
        u.DdeConnect.restype = H
        u.DdeClientTransaction.argtypes = [ctypes.c_char_p, wintypes.DWORD, H, H, wintypes.UINT, wintypes.UINT,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        u.DdeClientTransaction.restype = H
        u.DdeGetLastError.argtypes = [wintypes.DWORD]
        u.DdeGetLastError.restype = wintypes.UINT
        u.DdeDisconnect.argtypes = [H]
        u.DdeUninitialize.argtypes = [wintypes.DWORD]
        u.DdeFreeDataHandle.argtypes = [H]
        LPMSG = ctypes.POINTER(wintypes.MSG)
        u.PeekMessageW.argtypes = [LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        u.TranslateMessage.argtypes = [LPMSG]
        u.DispatchMessageW.argtypes = [LPMSG]
        self.u = u
        self.inst = wintypes.DWORD(0)
        self._cb = self.CALLBACK(lambda *a: None)   # クライアントだけなので何もしない。参照を持ち続ける
        rc = u.DdeInitializeA(ctypes.byref(self.inst), self._cb, APPCMD_CLIENTONLY, 0)
        if rc != 0:
            raise DdeError(f"DDE を初期化できません（{err_name(rc)}）", rc)
        self.conv: Optional[int] = None
        self._msg = wintypes.MSG()

    def last_error(self) -> int:
        return int(self.u.DdeGetLastError(self.inst))

    def connect(self, service: str, topic: str) -> bool:
        u, enc = self.u, self.encoding
        hs = u.DdeCreateStringHandleA(self.inst, service.encode(enc), CP_WINANSI)
        ht = u.DdeCreateStringHandleA(self.inst, topic.encode(enc), CP_WINANSI)
        try:
            conv = u.DdeConnect(self.inst, hs, ht, None)
        finally:
            u.DdeFreeStringHandle(self.inst, hs)
            u.DdeFreeStringHandle(self.inst, ht)
        self.conv = conv or None
        return bool(conv)

    def execute(self, command: str, timeout_ms: int) -> Tuple[bool, int, int]:
        """戻り値: (受け付けたか, 結果フラグ, DMLERR)。"""
        data = command.encode(self.encoding) + b"\0"
        from ctypes import wintypes
        res = wintypes.DWORD(0)
        h = self.u.DdeClientTransaction(data, len(data), self.conv, None, 0, XTYP_EXECUTE, int(timeout_ms),
                                        self.ctypes.byref(res))
        ok = bool(h)
        err = 0 if ok else self.last_error()
        if h and h != 1:   # 同期の実行は TRUE(1) を返す。データハンドルが来たら解放する
            try:
                self.u.DdeFreeDataHandle(h)
            except Exception:  # noqa: BLE001
                pass
        return ok, int(res.value), err

    def pump(self, seconds: float) -> None:
        """待つ間もこのスレッドのメッセージを流す。"""
        end = time.time() + seconds
        while True:
            m = self.ctypes.byref(self._msg)
            while self.u.PeekMessageW(m, None, 0, 0, 1):   # PM_REMOVE
                self.u.TranslateMessage(m)
                self.u.DispatchMessageW(m)
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def close(self) -> None:
        try:
            if self.conv:
                self.u.DdeDisconnect(self.conv)
        finally:
            self.conv = None
            self.u.DdeUninitialize(self.inst)


RETRY_ERRORS = (BUSY, NOTPROCESSED)
RETRY_INTERVAL_SEC = 0.5   # 断られた命令を送り直す間隔


def execute_with_retry(api: Any, command: str, timeout_ms: int, retry_sec: float,
                       log_line: Callable[[str], None], name: str,
                       cancel: Optional[Callable[[], bool]] = None,
                       on_retry: Optional[Callable[[int, float, str], None]] = None) -> Tuple[bool, int, int]:
    """命令を送る。「忙しい」「処理しない」と返されたら retry_sec まで 1 秒おきに送り直す。

    実機の SymNavi は、起動して「Navigator クライアント」の小窓を出した直後（ログイン中とみられる）は
    命令を NOTPROCESSED で断る。旧 VBA（Excel 経由）ではこの返事が見えず、受け付けられたように見えていた。
    """
    t0 = time.time()
    tries = 0
    while True:
        tries += 1
        ts = time.time()
        ok, res, err = api.execute(command, timeout_ms)
        took = time.time() - ts
        waited = time.time() - t0
        if ok:
            log_line(f"cmd {name} ok sec={took:.1f} tries={tries} waited={waited:.1f} flags=0x{res:04X}")
            return ok, res, err
        if err in RETRY_ERRORS and waited < retry_sec and not (cancel and cancel()):
            log_line(f"cmd {name} {err_name(err).lower()} tries={tries} waited={waited:.1f} (retry)")
            if on_retry:
                on_retry(tries, waited, err_name(err))
            api.pump(RETRY_INTERVAL_SEC)
            continue
        log_line(f"cmd {name} error={err_name(err)} sec={took:.1f} tries={tries} waited={waited:.1f}")
        return ok, res, err
