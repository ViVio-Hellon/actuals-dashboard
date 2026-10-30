"""倉入実績の取り込み元。

通常  RNEConverter が共有に出す <出力名>.sqlite3（表は rne_list.json の table_name、既定「仕掛」）。
      共有の上では開かない。手元に写してから読み取り専用で開き、すぐ閉じる（配布側の差し替えを妨げない）。
予備  SymNavi が作った .xls（旧手順: SymNavi から Excel に出す）／ Excel からコピーした表の貼付け（旧 UserForm02〜10）。
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sharedfile import SharedUnreachable, _TimedOut, within


class SourceError(RuntimeError):
    pass


def probe(path: Path, timeout: float = 8.0) -> Optional[Tuple[int, int]]:
    """取り込み元の (大きさ, 更新時刻)。無ければ None。届かなければ SharedUnreachable。"""
    try:
        st = within(lambda: Path(path).stat(), timeout)
    except FileNotFoundError:
        return None
    except (_TimedOut, OSError) as e:
        raise SharedUnreachable(f"取り込み元に届きません: {path}（{type(e).__name__}）") from e
    return st.st_size, st.st_mtime_ns


def read_sqlite(path: Path, cache_dir: Path, table: str = "") -> Dict[str, Any]:
    """{columns, rows, meta, fingerprint}。"""
    path = Path(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = probe(path)
    if fp is None:
        raise SourceError(f"取り込み元のファイルがありません: {path}")
    local = cache_dir / ("source_" + path.name)
    tmp = local.with_suffix(".copying")
    try:
        within(lambda: shutil.copyfile(path, tmp), 60)
        os.replace(tmp, local)
    except (_TimedOut, OSError) as e:
        raise SharedUnreachable(f"取り込み元を写せません: {path}（{e}）") from e
    conn = sqlite3.connect(f"file:{local.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        meta: Dict[str, str] = {}
        if "_meta" in names:
            meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM _meta")}
        data_tables = [n for n in names if n != "_meta"]
        t = table or meta.get("table") or (data_tables[0] if len(data_tables) == 1 else "")
        if t not in data_tables:
            raise SourceError(f"取り込み元に表「{table or '（自動）'}」がありません。ある表: {', '.join(data_tables) or 'なし'}")
        cur = conn.execute(f'SELECT * FROM "{t}"')
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    except sqlite3.DatabaseError as e:
        raise SourceError(f"取り込み元を読めません（壊れているか SQLite ではありません）: {e}") from e
    finally:
        conn.close()
    fingerprint = f"{fp[0]}-{fp[1]}"   # 写す直前の（大きさ・更新時刻）
    return {"columns": columns, "rows": rows, "meta": meta, "fingerprint": fingerprint, "table": t}


def parse_pasted(text: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Excel からコピーした表（タブ区切り、1 行目が見出し）。"""
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    lines = [r for r in reader if any(c.strip() for c in r)]
    if not lines:
        raise SourceError("貼り付けた内容が空です。")
    header = [h.strip() for h in lines[0]]
    rows = []
    for r in lines[1:]:
        rows.append({h: (r[i] if i < len(r) else "") for i, h in enumerate(header) if h})
    return [h for h in header if h], rows


def read_xls_bytes(data: bytes, cache_dir: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    """SymNavi が保存した .xls（Excel 97-2003）。Page1（無ければ先頭のシート）を読む。"""
    from .vendor.xls_reader import XlsFormatError, read_xls
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / "upload.xls"
    p.write_bytes(data)
    try:
        _, lines = read_xls(p)
    except (XlsFormatError, Exception) as e:  # noqa: BLE001
        raise SourceError(f"xls を読めません: {e}") from e
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    if not lines:
        raise SourceError("xls にデータがありません。")
    header = [h.strip() for h in lines[0]]
    rows = [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header) if h} for r in lines[1:]
            if any(str(c).strip() for c in r)]
    return [h for h in header if h], rows
