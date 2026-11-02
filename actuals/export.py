"""CSV の書き出し（ダウンロードはしない。設定したフォルダに書く）。

python-web-tools（order_export.py）と同じ書き方: utf-8-sig（Excel でそのまま開ける）、
先頭が 0 の番号は ="0123" にして Excel に消されないようにする。
"""
from __future__ import annotations

import csv
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from . import logsetup

_LEAD0 = re.compile(r"0\d+")


def excel_text(v: Any) -> Any:
    if isinstance(v, str) and _LEAD0.fullmatch(v):
        return f'="{v}"'
    return v


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")


def write_csv(folder: Path, name: str, header: Sequence[str], rows: Iterable[Sequence[Any]],
              what: str = "CSV") -> Path:
    t0 = time.monotonic()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_name(name)}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    logsetup.using(path)
    tmp = path.with_suffix(".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([excel_text(v) for v in r])
            n += 1
    os.replace(tmp, path)
    logsetup.saved(what, path, n, time.monotonic() - t0)
    return path


def open_folder(path: Path) -> bool:
    """エクスプローラーで開く（Windows のみ。旧 UserForm03「同一フォルダを開く」）。"""
    if os.name != "nt":
        return False
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
        return True
    except OSError:
        return False
