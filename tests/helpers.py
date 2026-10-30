"""試験用の小道具。RNEConverter の出力と同じ形の SQLite を作る（混在列は TEXT、日時は文字列、_meta 付き）。"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "golden_202206.json"


def golden():
    return json.loads(FIX.read_text(encoding="utf-8"))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def write_source(path: Path, header, rows, table="倉入実績"):
    """列ごとに、全部が数なら数のまま、1 つでも文字があれば全部を文字にする（RNEConverter の型推定と同じ考え）。"""
    path = Path(path)
    if path.exists():
        path.unlink()
    text_cols = {h for h in header if any(r.get(h) is not None and not _num(r.get(h)) for r in rows)}

    def conv(h, v):
        if v is None:
            return ""
        if h in text_cols:
            return str(v) if not (isinstance(v, float) and v.is_integer()) else str(int(v))
        return v
    conn = sqlite3.connect(str(path))
    cols = ", ".join(f'"{h}"' for h in header)
    conn.execute(f'CREATE TABLE "{table}" ({cols})')
    conn.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in header)})',
                     [[conv(h, r.get(h)) for h in header] for r in rows])
    conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO _meta VALUES (?, ?)", [("generated_at", datetime.now().isoformat(timespec="seconds")),
                                                         ("table", table), ("run_id", "test")])
    conn.commit()
    conn.close()
    return path
