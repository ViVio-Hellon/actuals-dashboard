"""多重起動防止（基盤仕様書 2.4）。

- LocalLock : 同じ PC で 2 つ目のコンバータが走らないようにする（runtime フォルダのロックファイル + PID）。
- ShareLock : 別 PC が同じ共有フォルダへ同時に配布しないようにする（共有フォルダのロックファイル + 時刻）。

どちらも O_EXCL でファイルを作ることで原子的に取得する。
残骸（前回の異常終了）は、LocalLock は PID の生死、ShareLock は経過時間で判定して回収する。
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class LockHeld(Exception):
    pass


def pid_alive(pid: int) -> bool:
    """PID が生きているか。Windows では os.kill(pid, 0) が使えない（TerminateProcess される）ので API を使う。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _write_lock(path: Path, payload: dict) -> None:
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _read_lock(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


class LocalLock:
    def __init__(self, runtime_dir: Path, name: str = "app.lock") -> None:
        self.path = runtime_dir / name
        self.acquired = False

    def __enter__(self) -> "LocalLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "host": socket.gethostname(), "time": datetime.now().isoformat(timespec="seconds")}
        for _ in range(2):
            try:
                _write_lock(self.path, payload)
                self.acquired = True
                return self
            except FileExistsError:
                other = _read_lock(self.path) or {}
                pid = int(other.get("pid", 0) or 0)
                if pid and pid_alive(pid):
                    raise LockHeld(f"同じアプリが既に実行中です (pid={pid}, {other.get('time')})。終了を待ってから再実行してください。")
                log.warning("残っていたロックを回収します: %s (%s)", self.path, other)
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        raise LockHeld(f"ロックを取得できません: {self.path}")

    def __exit__(self, *exc) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


class ShareLock:
    """共有フォルダ上のロック。無効化（enabled=False）すると何もしない。"""

    def __init__(self, share_dir: Path, enabled: bool = True, stale_minutes: int = 120, name: str = ".rneconverter.lock") -> None:
        self.path = share_dir / name
        self.enabled = enabled
        self.stale_sec = stale_minutes * 60
        self.acquired = False

    def __enter__(self) -> "ShareLock":
        if not self.enabled:
            return self
        payload = {"pid": os.getpid(), "host": socket.gethostname(), "time": datetime.now().isoformat(timespec="seconds"), "epoch": time.time()}
        for _ in range(2):
            try:
                _write_lock(self.path, payload)
                self.acquired = True
                return self
            except FileExistsError:
                other = _read_lock(self.path) or {}
                age = time.time() - float(other.get("epoch", 0) or 0)
                if age < self.stale_sec:
                    raise LockHeld(f"別の PC が配布中です: {other.get('host')} pid={other.get('pid')} {other.get('time')}（{int(age)} 秒前）。")
                log.warning("古い共有ロックを回収します: %s (%s)", self.path, other)
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        raise LockHeld(f"共有ロックを取得できません: {self.path}")

    def __exit__(self, *exc) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
