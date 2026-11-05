"""共有に置く 2 つのファイル。

actuals_master.sqlite3   条件表（旧 設定シート）・手動訂正（旧 修正シート A 列、ロット番号で保持）・変更履歴
actuals_history.sqlite3  取り込んだ倉入実績（ロット番号ごとに蓄積）・取り込みの記録

どちらも sharedfile.SharedFile で読み書きする（全 PC 共通）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import columns as C
from .conditions import ConditionError, ConditionSet, default_conditions, validate
from .engine import GENBA, TAIYO, compute_lot
from .sharedfile import SharedFile, now_text, who

SCHEMA_VERSION = "1"


class Conflict(RuntimeError):
    """画面が見ていた内容のあとに、別の PC（タブ）が保存していた。"""

    def __init__(self, message: str, current: Any = None):
        super().__init__(message)
        self.current = current


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ======================================================================
# 条件表・手動訂正
# ======================================================================
def _master_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS conditions (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS overrides (
        lot TEXT PRIMARY KEY, grp INTEGER NOT NULL, reason TEXT, by_user TEXT, by_pc TEXT, at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, by_user TEXT, by_pc TEXT, kind TEXT, target TEXT,
        before TEXT, after TEXT, note TEXT)""")
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))
    if conn.execute("SELECT 1 FROM conditions WHERE id = 1").fetchone() is None:
        cs = default_conditions()
        cs.rev, cs.updated_at, cs.updated_by = 1, now_text(), "初期値（受領ブックの設定シート）"
        conn.execute("INSERT INTO conditions (id, data) VALUES (1, ?)", (_dumps(cs.to_dict()),))


def _body(cs: ConditionSet) -> Dict[str, Any]:
    """条件表の中身だけ（版番号・保存した人と日時を除く）。"""
    d = dict(cs.to_dict())
    for k in ("rev", "updated_at", "updated_by"):
        d.pop(k, None)
    return d


def _audit(conn: sqlite3.Connection, kind: str, target: str, before: Any, after: Any, note: str = "") -> None:
    w = who()
    conn.execute("INSERT INTO audit (at, by_user, by_pc, kind, target, before, after, note) VALUES (?,?,?,?,?,?,?,?)",
                 (now_text(), w["user"], w["pc"], kind, target, _dumps(before), _dumps(after), note))


class MasterStore:
    def __init__(self, shared_dir: Path, cache_dir: Path, **kw):
        self.file = SharedFile(Path(shared_dir) / "actuals_master.sqlite3", cache_dir, _master_schema, **kw)

    # ----- 読む -----
    def conditions(self) -> ConditionSet:
        with self.file.read() as conn:
            if conn is None:
                return default_conditions()
            row = conn.execute("SELECT data FROM conditions WHERE id = 1").fetchone()
            return ConditionSet.from_dict(json.loads(row["data"])) if row else default_conditions()

    def overrides(self) -> Dict[str, Dict[str, Any]]:
        with self.file.read() as conn:
            if conn is None:
                return {}
            return {r["lot"]: dict(r) for r in conn.execute("SELECT * FROM overrides ORDER BY at DESC")}

    def audit(self, limit: int = 100, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.file.read() as conn:
            if conn is None:
                return []
            sql = "SELECT * FROM audit" + (" WHERE kind = ?" if kind else "") + " ORDER BY id DESC LIMIT ?"
            args = (kind, limit) if kind else (limit,)
            return [dict(r) for r in conn.execute(sql, args)]

    def ensure(self) -> None:
        """共有のファイルがまだ無ければ初期値で作る。"""
        if not self.file.shared.exists():
            self.file.write(lambda conn: None, what="条件表の初期値", count=0)

    # ----- 書く -----
    def absorb(self, overrides: Sequence[Dict[str, Any]], conditions: Optional[ConditionSet], note: str) -> Dict[str, Any]:
        """この PC に貯めた手動訂正・条件表を共有へ移す。**共有にある物は上書きしない。**

        手動訂正: 共有に無いロットだけ足す（理由・PC・利用者・日時はそのまま）。
        条件表: 共有の条件表がまだ初期値のまま（だれも保存していない）時だけ、この PC の条件表を写す。
        """
        def mutate(conn):
            added = kept = 0
            for o in overrides:
                if conn.execute("SELECT 1 FROM overrides WHERE lot = ?", (o["lot"],)).fetchone():
                    kept += 1
                    continue
                rec = {k: o.get(k) for k in ("lot", "grp", "reason", "by_user", "by_pc", "at")}
                conn.execute("INSERT INTO overrides (lot, grp, reason, by_user, by_pc, at) "
                             "VALUES (:lot, :grp, :reason, :by_user, :by_pc, :at)", rec)
                _audit(conn, "override", o["lot"], None, rec, note)
                added += 1
            cond = "none"
            if conditions is not None:
                cur = ConditionSet.from_dict(json.loads(conn.execute("SELECT data FROM conditions WHERE id=1").fetchone()[0]))
                if _body(cur) == _body(conditions):
                    cond = "same"
                elif str(cur.updated_by).startswith("初期値"):
                    w = who()
                    new = ConditionSet.from_dict(conditions.to_dict())
                    new.rev, new.updated_at = cur.rev + 1, now_text()
                    new.updated_by = f"{w['pc']} {w['user']}（{note}）".strip()
                    conn.execute("UPDATE conditions SET data = ? WHERE id = 1", (_dumps(new.to_dict()),))
                    _audit(conn, "conditions", f"rev {new.rev}", cur.to_dict(), new.to_dict(), note)
                    cond = "copied"
                else:
                    cond = "kept"
            return {"overrides_added": added, "overrides_kept": kept, "conditions": cond}
        return self.file.write(mutate, what="この PC の手動訂正・条件表を共有へ", count=lambda r: r["overrides_added"])

    def save_conditions(self, cs: ConditionSet, base_rev: int, note: str = "") -> ConditionSet:
        problems = validate(cs)
        if problems:
            raise ConditionError(problems)

        def mutate(conn):
            cur = ConditionSet.from_dict(json.loads(conn.execute("SELECT data FROM conditions WHERE id=1").fetchone()[0]))
            if cur.rev != base_rev:
                raise Conflict(f"条件表は {cur.updated_at} に {cur.updated_by} が保存しています。"
                               "最新を読み込んでから、もう一度変更してください。", cur.to_dict())
            w = who()
            new = ConditionSet.from_dict(cs.to_dict())
            new.rev, new.updated_at, new.updated_by = cur.rev + 1, now_text(), f"{w['pc']} {w['user']}".strip()
            conn.execute("UPDATE conditions SET data = ? WHERE id = 1", (_dumps(new.to_dict()),))
            _audit(conn, "conditions", f"rev {new.rev}", cur.to_dict(), new.to_dict(), note)
            return new
        return self.file.write(mutate, what="条件表", count=len(cs.rows))

    def set_override(self, lot: str, grp: Optional[int], reason: str, seen: Optional[int]) -> Optional[Dict[str, Any]]:
        """手動訂正を付ける（grp=None で外す）。seen は画面が見ていた値（別の人の変更を上書きしない）。"""
        lot = str(lot).strip()
        if not lot:
            raise ValueError("ロット番号がありません。")
        if grp is not None and not (0 <= int(grp) <= 15):
            raise ValueError("グループは 1〜15、または 0（集計しない）です。")

        def mutate(conn):
            row = conn.execute("SELECT * FROM overrides WHERE lot = ?", (lot,)).fetchone()
            cur = row["grp"] if row else None
            if cur != seen:
                raise Conflict(f"このロットの訂正は、別の PC で変更されています（いま: {cur if cur is not None else 'なし'}）。"
                               "表示を読み直してから、もう一度操作してください。", cur)
            w = who()
            if grp is None:
                conn.execute("DELETE FROM overrides WHERE lot = ?", (lot,))
                after = None
            else:
                after = {"lot": lot, "grp": int(grp), "reason": reason or "", "by_user": w["user"], "by_pc": w["pc"],
                         "at": now_text()}
                conn.execute("INSERT OR REPLACE INTO overrides (lot, grp, reason, by_user, by_pc, at) "
                             "VALUES (:lot, :grp, :reason, :by_user, :by_pc, :at)", after)
            _audit(conn, "override", lot, dict(row) if row else None, after)
            return after
        return self.file.write(mutate, what=f"手動訂正（{lot}）", count=1)

    def import_overrides(self, items: Dict[str, Any], note: str) -> int:
        """旧ブックの修正シート A 列を取り込む（既にある訂正は上書きしない）。"""
        def mutate(conn):
            n = 0
            w = who()
            for lot, g in items.items():
                try:
                    gi = int(float(g))
                except (TypeError, ValueError):
                    continue
                if conn.execute("SELECT 1 FROM overrides WHERE lot = ?", (lot,)).fetchone():
                    continue
                rec = {"lot": lot, "grp": gi, "reason": note, "by_user": w["user"], "by_pc": w["pc"], "at": now_text()}
                conn.execute("INSERT INTO overrides (lot, grp, reason, by_user, by_pc, at) "
                             "VALUES (:lot, :grp, :reason, :by_user, :by_pc, :at)", rec)
                _audit(conn, "override", lot, None, rec, note)
                n += 1
            return n
        return self.file.write(mutate, what="手動訂正（旧ブックから）", count=lambda n: n)


# ======================================================================
# 倉入実績の蓄積
# ======================================================================
def _history_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS lots (
        lot TEXT PRIMARY KEY, data TEXT NOT NULL, genba_ym TEXT, taiyo_ym TEXT,
        import_id INTEGER, pos INTEGER, first_import INTEGER, updated_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_lots_genba ON lots (genba_ym)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_lots_taiyo ON lots (taiyo_ym)")
    conn.execute("""CREATE TABLE IF NOT EXISTS imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, by_user TEXT, by_pc TEXT, kind TEXT, source TEXT,
        fingerprint TEXT, generated_at TEXT, run_id TEXT, rows INTEGER, added INTEGER, changed INTEGER,
        same INTEGER, months TEXT, first_box_end TEXT, note TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS columns (name TEXT PRIMARY KEY, pos INTEGER)")
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))


def _ym(t: Tuple[Optional[int], Optional[int], Optional[int]]) -> Optional[str]:
    y, m, _ = t
    return f"{y:04d}-{m:02d}" if y and m else None


class ImportError_(ValueError):
    pass


class HistoryStore:
    def __init__(self, shared_dir: Path, cache_dir: Path, **kw):
        self.file = SharedFile(Path(shared_dir) / "actuals_history.sqlite3", cache_dir, _history_schema, **kw)

    def merge(self, columns: Sequence[str], rows: Sequence[Dict[str, Any]], *, kind: str, source: str,
              fingerprint: str = "", generated_at: str = "", run_id: str = "", note: str = "") -> Dict[str, Any]:
        """取り込んだ行をロット番号で蓄積する（同じロットは新しい内容に置き換える。消しはしない）。"""
        missing = C.missing_required(columns)
        if missing:
            raise ImportError_("必要な列がありません: " + "、".join(missing) +
                               "。RNE の出力列が変わっていないか確認してください。")
        seen: Dict[str, int] = {}
        dups = 0
        clean: List[Tuple[str, Dict[str, Any]]] = []
        for r in rows:
            lot = r.get(C.LOT)
            lot = "" if lot is None else str(lot).strip()
            if not lot:
                continue
            if lot in seen:
                dups += 1
                clean[seen[lot]] = (lot, r)
                continue
            seen[lot] = len(clean)
            clean.append((lot, r))

        def mutate(conn):
            w = who()
            cur = conn.execute(
                "INSERT INTO imports (at, by_user, by_pc, kind, source, fingerprint, generated_at, run_id, rows, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now_text(), w["user"], w["pc"], kind, source, fingerprint, generated_at, run_id, len(clean), note))
            imp = cur.lastrowid
            for i, name in enumerate(columns):
                conn.execute("INSERT OR IGNORE INTO columns (name, pos) VALUES (?, ?)", (name, i))
            added = changed = same = 0
            months = set()
            first_box_end = ""
            for pos, (lot, r) in enumerate(clean):
                data = _dumps({k: r.get(k) for k in columns})
                computed = compute_lot(r)
                gy, ty = _ym(computed.ymd(GENBA)), _ym(computed.ymd(TAIYO))
                if pos == 0:
                    first_box_end = str(r.get(C.BOX_END) or "")
                months.update(x for x in (gy, ty) if x)
                old = conn.execute("SELECT data FROM lots WHERE lot = ?", (lot,)).fetchone()
                if old is None:
                    added += 1
                    conn.execute("INSERT INTO lots (lot, data, genba_ym, taiyo_ym, import_id, pos, first_import, "
                                 "updated_at) VALUES (?,?,?,?,?,?,?,?)", (lot, data, gy, ty, imp, pos, imp, now_text()))
                else:
                    # 並び（旧 DATA シートの順）は最新の取り込みに合わせる
                    if old["data"] == data:
                        same += 1
                        conn.execute("UPDATE lots SET import_id=?, pos=? WHERE lot=?", (imp, pos, lot))
                    else:
                        changed += 1
                        conn.execute("UPDATE lots SET data=?, genba_ym=?, taiyo_ym=?, import_id=?, pos=?, "
                                     "updated_at=? WHERE lot=?", (data, gy, ty, imp, pos, now_text(), lot))
            conn.execute("UPDATE imports SET added=?, changed=?, same=?, months=?, first_box_end=? WHERE id=?",
                         (added, changed, same, ",".join(sorted(months)), first_box_end, imp))
            return {"import_id": imp, "rows": len(clean), "added": added, "changed": changed, "same": same,
                    "duplicates": dups, "months": sorted(months)}
        return self.file.write(mutate, what="蓄積した実績", count=lambda r: r["rows"])

    def absorb(self, lots: Sequence[Dict[str, Any]], columns: Sequence[str], source: str) -> Dict[str, Any]:
        """この PC に貯めた実績を共有へ移す。**共有にあるロットは共有のまま**（上書きしない）。並びはこの PC の順。"""
        def mutate(conn):
            w = who()
            cur = conn.execute("INSERT INTO imports (at, by_user, by_pc, kind, source, rows, note) VALUES (?,?,?,?,?,?,?)",
                               (now_text(), w["user"], w["pc"], "move", source, len(lots),
                                "共有フォルダを決める前にこの PC に貯めた分"))
            imp = cur.lastrowid
            for i, name in enumerate(columns):
                conn.execute("INSERT OR IGNORE INTO columns (name, pos) VALUES (?, ?)", (name, i))
            added = kept = 0
            months = set()
            for pos, r in enumerate(lots):
                if conn.execute("SELECT 1 FROM lots WHERE lot = ?", (r["lot"],)).fetchone():
                    kept += 1
                    continue
                conn.execute("INSERT INTO lots (lot, data, genba_ym, taiyo_ym, import_id, pos, first_import, updated_at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (r["lot"], r["data"], r["genba_ym"], r["taiyo_ym"], imp, pos,
                                                         imp, r.get("updated_at") or now_text()))
                months.update(x for x in (r["genba_ym"], r["taiyo_ym"]) if x)
                added += 1
            conn.execute("UPDATE imports SET added=?, changed=0, same=?, months=? WHERE id=?",
                         (added, kept, ",".join(sorted(months)), imp))
            return {"rows": len(lots), "added": added, "kept": kept, "months": sorted(months)}
        return self.file.write(mutate, what="この PC の実績を共有へ", count=lambda r: r["added"])

    # ----- 読む -----
    def lots_in_months(self, cal: str, months: Sequence[str]) -> List[Dict[str, Any]]:
        """集計日の年月が months に入るロット（取り込んだ順）。"""
        col = "genba_ym" if cal == GENBA else "taiyo_ym"
        with self.file.read() as conn:
            if conn is None or not months:
                return []
            q = ",".join("?" for _ in months)
            rows = conn.execute(f"SELECT data FROM lots WHERE {col} IN ({q}) ORDER BY import_id, pos", list(months))
            return [json.loads(r["data"]) for r in rows]

    def lot(self, lot: str) -> Optional[Dict[str, Any]]:
        with self.file.read() as conn:
            if conn is None:
                return None
            r = conn.execute("SELECT * FROM lots WHERE lot = ?", (lot,)).fetchone()
            if r is None:
                return None
            d = dict(r)
            d["data"] = json.loads(d["data"])
            return d

    def find_lots(self, text: str, limit: int = 50) -> List[str]:
        with self.file.read() as conn:
            if conn is None:
                return []
            return [r[0] for r in conn.execute("SELECT lot FROM lots WHERE lot LIKE ? ORDER BY lot LIMIT ?",
                                               (f"%{text}%", limit))]

    def columns(self) -> List[str]:
        with self.file.read() as conn:
            if conn is None:
                return list(C.DATA_COLUMNS)
            got = [r[0] for r in conn.execute("SELECT name FROM columns ORDER BY pos, name")]
            return got or list(C.DATA_COLUMNS)

    def summary(self) -> Dict[str, Any]:
        with self.file.read() as conn:
            if conn is None:
                return {"lots": 0, "months": [], "imports": [], "last": None}
            n = conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
            months = {}
            for col, key in (("genba_ym", "genba"), ("taiyo_ym", "taiyo")):
                for ym, cnt in conn.execute(f"SELECT {col}, COUNT(*) FROM lots WHERE {col} IS NOT NULL "
                                            f"GROUP BY {col} ORDER BY {col}"):
                    months.setdefault(ym, {"genba": 0, "taiyo": 0})[key] = cnt
            imps = [dict(r) for r in conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 50")]
            return {"lots": n, "months": [{"ym": k, **v} for k, v in sorted(months.items())],
                    "imports": imps, "last": imps[0] if imps else None}

    def last_source_fingerprint(self, source: str) -> Optional[str]:
        with self.file.read() as conn:
            if conn is None:
                return None
            r = conn.execute("SELECT fingerprint FROM imports WHERE kind = 'source' AND source = ? "
                             "ORDER BY id DESC LIMIT 1", (source,)).fetchone()
            return r[0] if r else None
