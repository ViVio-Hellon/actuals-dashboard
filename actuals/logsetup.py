"""ログ（日報複合ツール・python-web-tools と同じ決まり）。

<ログの保存先>\\actuals-dashboard\\actuals-dashboard_<PC名>_<YYYYMMDD>.log
各行に [操作番号]（画面からの要求ごとの 16 進 6 桁）。エラーは画面にエラー番号（E yymmdd-HHMMSS-xx）を出し、
同じ番号をログに残す。保存先に届かない時は、この PC のデータの場所の logs に書く（ログで止めない）。
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

TOOL_ID = "actuals-dashboard"
_local = threading.local()
_state = {"dir": "", "fallback": ""}


def new_operation_id() -> str:
    oid = secrets.token_hex(3)
    _local.op = oid
    return oid


def clear_operation() -> None:
    _local.op = "-"


def new_error_ref() -> str:
    return f"E {datetime.now():%y%m%d-%H%M%S}-{secrets.token_hex(1)}"


class _OpFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.op = getattr(_local, "op", "-")
        return True


class DailyFileHandler(logging.Handler):
    """日付が変わったら次のファイルへ。書けなければ控えの場所へ。"""

    def __init__(self, primary: Path, fallback: Path):
        super().__init__()
        self.primary, self.fallback = primary, fallback
        self.pc = os.environ.get("COMPUTERNAME") or socket.gethostname()
        self._lock = threading.Lock()

    def _path(self, base: Path) -> Path:
        d = base / TOOL_ID
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{TOOL_ID}_{self.pc}_{datetime.now():%Y%m%d}.log"

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        with self._lock:
            for base in (self.primary, self.fallback):
                try:
                    with open(self._path(base), "a", encoding="utf-8") as f:
                        f.write(msg)
                    _state["active"] = str(base / TOOL_ID)
                    return
                except OSError:
                    continue


def setup(log_dir: str, fallback: Path, console: bool = False, keep_days: int = 90) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_actuals", False):
            root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(op)s] %(levelname)s %(name)s: %(message)s")
    primary = Path(log_dir) if log_dir else fallback
    h = DailyFileHandler(primary, fallback)
    h._actuals = True  # type: ignore[attr-defined]
    h.setFormatter(fmt)
    h.addFilter(_OpFilter())
    root.addHandler(h)
    if console:
        c = logging.StreamHandler()
        c._actuals = True  # type: ignore[attr-defined]
        c.setFormatter(fmt)
        c.addFilter(_OpFilter())
        root.addHandler(c)
    root.setLevel(logging.INFO)
    _state["dir"] = str(primary / TOOL_ID)
    _cleanup(primary, keep_days, h.pc)


def _cleanup(base: Path, keep_days: int, pc: str) -> None:
    """このツールの、この PC の分だけ古いログを消す。"""
    try:
        limit = datetime.now() - timedelta(days=keep_days)
        for p in (base / TOOL_ID).glob(f"{TOOL_ID}_{pc}_*.log"):
            if datetime.fromtimestamp(p.stat().st_mtime) < limit:
                p.unlink()
    except OSError:
        pass


def active_dir() -> str:
    return _state.get("active") or _state.get("dir", "")
