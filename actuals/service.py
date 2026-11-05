"""画面から呼ぶ処理（ビューモデルを組み立てる）。業務の判断はすべてここから下で行い、画面は描くだけ。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import closing
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import aggregate as A
from . import charts
from . import columns as C
from . import source as S
from .conditions import (FIELDS, GROUP_COUNT, RULE_NOT_HIT, RULE_NOT_MATERIAL, ConditionError, ConditionSet,
                         blank_warnings, parse_cell, validate)
from .engine import CALENDARS, GENBA, TAIYO, classify, compute_lot
from .export import open_folder, write_csv
from . import credentials, logsetup
from .rne_runner import UpdateRunner
from .settings import Paths, Settings, app_info
from .sharedfile import SharedBusy, SharedUnreachable, who
from .stores import Conflict, HistoryStore, ImportError_, MasterStore

log = logging.getLogger("actuals.service")


class UserError(ValueError):
    """画面にそのまま出す断り（422）。"""


def _ym(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def _shift_month(y: int, m: int, k: int) -> Tuple[int, int]:
    i = y * 12 + (m - 1) + k
    return i // 12, i % 12 + 1


def _days_in_month(y: int, m: int) -> int:
    ny, nm = _shift_month(y, m, 1)
    return (date(ny, nm, 1) - date(y, m, 1)).days


def fiscal_year_of(y: int, m: int) -> int:
    return y if m >= 4 else y - 1


class Service:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.settings = Settings(paths)
        self.rne = UpdateRunner()
        self._cache: "OrderedDict[tuple, List[A.Row]]" = OrderedDict()
        self._import_mu = threading.Lock()
        self._stores_key: Optional[tuple] = None
        self.master: MasterStore
        self.history: HistoryStore
        self._open_stores()

    # ---------- 置き場所 ----------
    def _open_stores(self) -> None:
        s = self.settings.resolved()
        key = (s["shared_dir"], s.get("lock_wait_sec"))
        if key == self._stores_key:
            return
        kw = {"lock_wait": float(s.get("lock_wait_sec") or 10)}
        # 写しは置き場所ごとに分ける（② を変えた直後に、前の置き場所の写しを読まない）
        import hashlib
        cache = self.paths.cache / "shared" / hashlib.sha1(
            str(Path(s["shared_dir"]).resolve()).lower().encode("utf-8")).hexdigest()[:12]
        self.master = MasterStore(Path(s["shared_dir"]), cache, **kw)
        self.history = HistoryStore(Path(s["shared_dir"]), cache, **kw)
        self._stores_key = key
        self._cache.clear()
        try:
            self.master.ensure()   # 共有に条件表がまだ無ければ初期値（受領ブックの設定シート）で作る
        except (SharedBusy, SharedUnreachable) as e:
            log.warning("条件表を用意できません（読むときは初期値を使って続けます）: %s", e)

    def conf(self) -> Dict[str, Any]:
        self._open_stores()
        return self.settings.resolved()

    # ---------- 判定済みの行（キャッシュ） ----------
    def _rows(self, cal: str, months: Sequence[str]) -> Tuple[List[A.Row], ConditionSet]:
        cs = self.master.conditions()                      # 共有の変化はここと次の行で手元に写る
        self.history.file.refresh()
        key = (self.history.file.fingerprint(), self.master.file.fingerprint(), cs.rev, cal, tuple(months))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key], cs
        t0 = time.monotonic()
        raws = self.history.lots_in_months(cal, months)
        ov = {k: v["grp"] for k, v in self.master.overrides().items()}
        logsetup.loaded(f"集計に使うロット（{CALENDARS.get(cal, cal)} {','.join(months)}）", self.history.file.shared,
                        len(raws), time.monotonic() - t0, overrides=len(ov), conditions_rev=cs.rev)
        rows = A.make_rows(raws, cs, ov)
        self._cache[key] = rows
        while len(self._cache) > 12:
            self._cache.popitem(last=False)
        return rows, cs

    # ---------- 状態 ----------
    def default_month(self, summary: Dict[str, Any]) -> Tuple[int, int]:
        """旧 VBA: DATA 先頭行の BOX実績_作業終了_年月日時分秒 の年月。"""
        last = summary.get("last") or {}
        from .vba_compat import parse_datetime
        dt = parse_datetime(last.get("first_box_end") or "")
        if dt:
            return dt.year, dt.month
        ms = summary.get("months") or []
        if ms:
            y, m = ms[-1]["ym"].split("-")
            return int(y), int(m)
        t = date.today()
        return t.year, t.month

    def state(self) -> Dict[str, Any]:
        s = self.conf()
        info = app_info()
        summary = self.history.summary()
        y, m = self.default_month(summary)
        cs = self.master.conditions()
        shared = {"dir": s["shared_dir"], "configured": s["shared_configured"],
                  "stale": self.master.file.stale or self.history.file.stale,
                  "error": self.master.file.last_error or self.history.file.last_error}
        return {"app": {"name": info.get("display_name"), "version": info.get("version"), **who()},
                "shared": shared,
                "history": {"lots": summary["lots"], "months": summary["months"], "last": summary["last"]},
                "default": {"year": y, "month": m, "fy": fiscal_year_of(y, m)},
                "conditions": {"rev": cs.rev, "updated_at": cs.updated_at, "updated_by": cs.updated_by,
                               "rules": [RULE_NOT_MATERIAL[cs.rule_not_material], RULE_NOT_HIT[cs.rule_not_hit]]},
                "overrides": len(self.master.overrides()),
                "rne": {**self.rne.status(), "configured": bool(s.get("rne_path")) and credentials.status(self.paths)["ready"],
                        "rne_name": Path(s.get("rne_path") or "").name},
                "calendars": CALENDARS}

    # ---------- 月の集計（旧 月集計 + ｸﾞﾗﾌ） ----------
    def month(self, y: int, m: int, cal: str, sel: Any) -> Dict[str, Any]:
        if cal not in CALENDARS:
            raise UserError("暦の指定が不正です。")
        rows, cs = self._rows(cal, [_ym(y, m)])
        names = cs.names()
        if sel != A.ALL:
            sel = int(sel)
            if not (1 <= sel <= GROUP_COUNT):
                raise UserError("グループの指定が不正です。")
        mat = A.month_matrix(rows, names, cal, y, m)
        hits = A.hit_counts(rows, cal, y, m)
        det = A.detail_rows(rows, cal, y, m, sel)
        shift = A.shift_daily(det, cal)
        tw, tc, tname = A.selected_total(mat, names, sel)
        dim = _days_in_month(y, m)
        in_month = [r for r in rows if r.lot.ymd(cal)[:2] == (y, m)]
        unclassified = [r for r in in_month if r.override is None and r.auto is None]
        overridden = [r for r in in_month if r.override is not None]
        sel_label = "全グループ（板形状のみ）" if sel == A.ALL else f"{sel} {names[sel - 1] or '（名前なし）'}"
        charts_ = charts.month_charts(f"{tname}　日別", tw, tc, shift, dim, y, m, with_shift=cal == GENBA,
                                      shift_note="太陽暦では直を出しません（旧ツールと同じ）" if cal == TAIYO else "")
        prev, nxt = _shift_month(y, m, -1), _shift_month(y, m, 1)
        return {
            "year": y, "month": m, "cal": cal, "cal_label": CALENDARS[cal], "days_in_month": dim,
            "sel": sel, "sel_label": sel_label, "prev": list(prev), "next": list(nxt),
            "groups": [{**g, "hits": hits.get(g["no"], 0)} for g in mat["groups"]],
            "hits_all": hits[A.ALL], "day_weight": mat["day_weight"], "day_count": mat["day_count"],
            "weight_total": mat["weight_total"], "count_total": mat["count_total"],
            "lots_in_month": len(in_month), "unclassified": len(unclassified), "overridden": len(overridden),
            "charts": charts_,
            "detail_headers": A.DETAIL_HEADERS,
            "detail": [A.detail_record(r, cal) for r in det],
        }

    def cell_lots(self, y: int, m: int, cal: str, grp: Any, day: Optional[int], shift: Optional[int]) -> Dict[str, Any]:
        """月集計のセル（グループ×日）に入っているロット。"""
        rows, cs = self._rows(cal, [_ym(y, m)])
        out = []
        for r in rows:
            ry, rm, rd = r.lot.ymd(cal)
            if (ry, rm) != (y, m) or (day and rd != day):
                continue
            if grp == "unclassified":
                if not (r.override is None and r.auto is None):
                    continue
            elif grp != A.ALL and r.final != int(grp):
                continue
            elif grp == A.ALL and not (1 <= r.final <= 15 and cs.names()[r.final - 1]):
                continue
            if shift and r.lot.shift(cal) != shift:
                continue
            out.append(self._lot_brief(r, cal))
        return {"lots": out, "count": len(out), "weight": round(sum(x["weight"] or 0 for x in out), 6)}

    def _lot_brief(self, r: A.Row, cal: str) -> Dict[str, Any]:
        v = r.lot.v
        _, _, d = r.lot.ymd(cal)
        return {"lot": r.lot.lot, "day": d, "shift": r.lot.shift(cal), "auto": r.auto, "override": r.override,
                "final": r.final, "o_mat": _t(v.get(C.O_MAT)), "o_tmp": _t(v.get(C.O_TMP)),
                "t": _t(v.get(C.O_T)), "w": _t(v.get(C.O_W)), "l": _t(v.get(C.O_L)),
                "m_mat": _t(v.get(C.M_MAT)), "unit_w": r.lot.unit_w, "vc": _t(v.get(C.VC)),
                "shape": _t(v.get(C.SHAPE)), "weight": r.lot.weight, "count": r.lot.count}

    # ---------- 年の集計（旧 年集計） ----------
    def year(self, fy: int, cal: str) -> Dict[str, Any]:
        months = [_ym(*_shift_month(fy, 4, k)) for k in range(12)]
        rows, cs = self._rows(cal, months)
        out = A.year_matrix(rows, cs.names(), cal, fy)
        out.update({"cal": cal, "cal_label": CALENDARS[cal], "prev": fy - 1, "next": fy + 1,
                    "charts": charts.year_charts(fy, out["months"], out["groups"])})
        return out

    # ---------- ロット 1 件（判定の経過） ----------
    def lot_detail(self, lot: str, cal: str) -> Dict[str, Any]:
        rec = self.history.lot(lot)
        if rec is None:
            raise UserError(f"ロット {lot} は取り込んだ実績にありません。")
        cs = self.master.conditions()
        computed = compute_lot(rec["data"])
        j = classify(computed, cs, want_trace=True)
        old = classify(computed, cs.vba_compatible())
        ov = self.master.overrides().get(computed.lot)
        names = cs.names()
        y, m, d = computed.ymd(cal)
        return {
            "lot": computed.lot, "cal": cal, "year": y, "month": m, "day": d, "shift": computed.shift(cal),
            "auto": j.group, "auto_name": names[j.group - 1] if j.group else "", "simul": j.simul or "",
            "vba_group": old.group, "vba_name": names[old.group - 1] if old.group else "",
            "override": ov, "final": ov["grp"] if ov else (j.group or 0),
            "unit_w": computed.unit_w, "count": computed.count, "weight": computed.weight,
            "is_plate": computed.is_plate, "basis_main": cs.basis_main, "basis_not": cs.basis_not,
            "rules": [RULE_NOT_MATERIAL[cs.rule_not_material], RULE_NOT_HIT[cs.rule_not_hit]],
            "trace": j.trace, "names": names,
            "fields": [{"name": k, "value": _t(rec["data"].get(k))} for k in self.history.columns()
                       if k in rec["data"]],
            "imported": {"import_id": rec["import_id"], "updated_at": rec["updated_at"]},
        }

    def find_lots(self, text: str) -> List[str]:
        return self.history.find_lots(text.strip()) if text.strip() else []

    # ---------- 振り分け確認（未分類・訂正） ----------
    def review(self, y: int, m: int, cal: str) -> Dict[str, Any]:
        rows, cs = self._rows(cal, [_ym(y, m)])
        names = cs.names()
        un, ov = [], []
        overrides = self.master.overrides()
        for r in rows:
            if r.lot.ymd(cal)[:2] != (y, m):
                continue
            if r.override is None and r.auto is None:
                b = self._lot_brief(r, cal)
                b["reason"] = _reason(classify(r.lot, cs, want_trace=True).trace or [])
                un.append(b)
            elif r.override is not None:
                b = self._lot_brief(r, cal)
                o = overrides.get(r.lot.lot) or {}
                b.update({"reason": o.get("reason", ""), "by": f"{o.get('by_pc', '')} {o.get('by_user', '')}".strip(),
                          "at": o.get("at", "")})
                ov.append(b)
        return {"year": y, "month": m, "cal": cal, "names": names, "unclassified": un, "overridden": ov,
                "unclassified_weight": round(sum(x["weight"] or 0 for x in un), 6)}

    def set_override(self, lot: str, grp: Optional[int], reason: str, seen: Optional[int]) -> Dict[str, Any]:
        if self.history.lot(lot) is None:
            raise UserError(f"ロット {lot} は取り込んだ実績にありません。")
        res = self.master.set_override(lot, grp, reason, seen)
        self._cache.clear()
        log.info("手動訂正: %s → %s（%s）", lot, grp if grp is not None else "外す", reason)
        return {"override": res}

    # ---------- 条件表（旧 設定シート） ----------
    def conditions(self) -> Dict[str, Any]:
        cs = self.master.conditions()
        return {"conditions": cs.to_dict(),
                "fields": [{"key": k, "col": c, "label": lb, "kind": kd} for k, c, lb, kd in FIELDS],
                "rule_not_material": RULE_NOT_MATERIAL, "rule_not_hit": RULE_NOT_HIT,
                "warnings": blank_warnings(cs),
                "audit": [_audit_brief(a) for a in self.master.audit(30, "conditions")]}

    def _parse_conditions(self, payload: Dict[str, Any]) -> ConditionSet:
        rows = []
        for r in payload.get("rows") or []:
            rows.append({k: parse_cell(kind, r.get(k)) for k, _, _, kind in FIELDS})
        return ConditionSet.from_dict({**payload, "rows": rows})

    def preview_conditions(self, payload: Dict[str, Any], y: int, m: int, cal: str) -> Dict[str, Any]:
        """保存する前に、表示中の月で振り分けがどう変わるかを見せる。"""
        new = self._parse_conditions(payload)
        problems = validate(new)
        if problems:
            return {"problems": problems, "changes": []}
        rows, cs = self._rows(cal, [_ym(y, m)])
        names_new = new.names()
        changes = []
        for r in rows:
            if r.lot.ymd(cal)[:2] != (y, m):
                continue
            g2 = classify(r.lot, new).group
            if g2 != r.auto:
                changes.append({"lot": r.lot.lot, "before": r.auto, "after": g2,
                                "before_name": cs.names()[r.auto - 1] if r.auto else "未分類",
                                "after_name": names_new[g2 - 1] if g2 else "未分類",
                                "weight": r.lot.weight, "override": r.override})
        return {"problems": [], "warnings": blank_warnings(new), "changes": changes, "year": y, "month": m,
                "cal": cal}

    def save_conditions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cs = self._parse_conditions(payload)
        try:
            new = self.master.save_conditions(cs, int(payload.get("rev") or 0), str(payload.get("note") or ""))
        except ConditionError as e:
            raise UserError("\n".join(e.problems))
        self._cache.clear()
        log.info("条件表を保存しました（rev %d）", new.rev)
        return self.conditions()

    # ---------- 取り込み ----------
    def import_sqlite(self, path: str) -> Dict[str, Any]:
        """RNEConverter などが作った SQLite（.sqlite3）から取り込む（予備）。"""
        p = Path((path or "").strip().strip('"'))
        if not p.is_file():
            raise UserError(f"ファイルがありません: {p}")
        t0 = time.monotonic()
        src = S.read_sqlite(p, self.paths.cache / "source")
        logsetup.loaded("取り込むファイル（.sqlite3）", p, len(src["rows"]), time.monotonic() - t0)
        with self._import_mu:
            res = self.history.merge(src["columns"], src["rows"], kind="sqlite", source=str(p),
                                     fingerprint=src["fingerprint"], generated_at=src["meta"].get("generated_at", ""),
                                     run_id=src["meta"].get("run_id", ""))
        self._cache.clear()
        log.info("取り込みました（%s）: %s", p, res)
        return res

    def import_rows(self, columns: List[str], rows: List[Dict[str, Any]], kind: str, source: str) -> Dict[str, Any]:
        with self._import_mu, logsetup.timed("蓄積へ取り込み", files=[self.history.file.shared], kind_of=kind,
                                             source=source, rows=len(rows)) as fr:
            res = self.history.merge(columns, rows, kind=kind, source=source)
            fr.note(added=res["added"], changed=res["changed"], same=res["same"])
        self._cache.clear()
        log.info("取り込みました（%s %s）: %s", kind, source, res)
        return res

    def import_paste(self, text: str) -> Dict[str, Any]:
        t0 = time.monotonic()
        cols, rows = S.parse_pasted(text)
        logsetup.loaded("貼り付けた表", "Excel から貼付け", len(rows), time.monotonic() - t0)
        return self.import_rows(cols, rows, "paste", "Excel から貼付け")

    def import_xls(self, data: bytes, name: str) -> Dict[str, Any]:
        t0 = time.monotonic()
        cols, rows = S.read_xls_bytes(data, self.paths.cache / "upload")
        logsetup.loaded("選んだ xls", name, len(rows), time.monotonic() - t0, bytes=len(data))
        return self.import_rows(cols, rows, "xls", name)

    def import_workbook(self, path: str, with_conditions: bool) -> Dict[str, Any]:
        """旧ブック（.xlsm）から DATA・修正シート A 列（・設定シート）を取り込む。"""
        from . import xlsm
        p = Path(path.strip().strip('"'))
        if not p.is_file():
            raise UserError(f"ファイルがありません: {p}")
        logsetup.using(p)
        t0 = time.monotonic()
        with xlsm.Workbook(p) as wb:
            header, rows = xlsm.read_data_rows(wb)
            overrides = xlsm.read_overrides(wb)
            cs = xlsm.read_conditions(wb) if with_conditions else None
        logsetup.loaded("旧ブックの DATA・修正シート", p, len(rows), time.monotonic() - t0, overrides=len(overrides),
                        conditions=cs is not None)
        res = self.import_rows(header, rows, "xlsm", p.name)
        res["overrides"] = self.master.import_overrides(overrides, f"旧ブック {p.name} の修正シートから")
        if cs is not None:
            cur = self.master.conditions()
            cs.rule_not_material, cs.rule_not_hit = cur.rule_not_material, cur.rule_not_hit
            self.master.save_conditions(cs, cur.rev, f"旧ブック {p.name} の設定シートから")
            res["conditions"] = True
        self._cache.clear()
        return res

    def history_summary(self) -> Dict[str, Any]:
        self.conf()                            # ② を変えた直後でも、いまの置き場所を読む
        return {**self.history.summary(), "leftover": self.local_leftover()}

    # ---------- 共有フォルダを決める前に、この PC に貯めた分 ----------
    def _local_dir(self) -> Path:
        return self.paths.base / "shared"      # ② が空欄の時の置き場所（settings.resolved と同じ）

    def _read_local(self) -> Optional[Dict[str, Any]]:
        """この PC の中の蓄積・手動訂正・条件表を読む（読むだけ）。共有フォルダを決めていない時・何も無い時は None。"""
        import sqlite3
        s = self.conf()
        local = self._local_dir()
        if not s["shared_configured"] or Path(s["shared_dir"]).resolve() == local.resolve():
            return None
        hist, mast = local / "actuals_history.sqlite3", local / "actuals_master.sqlite3"
        out: Dict[str, Any] = {"dir": str(local), "lots": [], "columns": [], "overrides": [], "conditions": None}

        def ro(p: Path):
            c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
            c.row_factory = sqlite3.Row
            return c
        try:
            if hist.is_file():
                with closing(ro(hist)) as c:
                    out["lots"] = [dict(r) for r in c.execute(
                        "SELECT lot, data, genba_ym, taiyo_ym, updated_at FROM lots ORDER BY import_id, pos")]
                    out["columns"] = [r[0] for r in c.execute("SELECT name FROM columns ORDER BY pos")]
            if mast.is_file():
                with closing(ro(mast)) as c:
                    out["overrides"] = [dict(r) for r in c.execute("SELECT * FROM overrides ORDER BY at")]
                    row = c.execute("SELECT data FROM conditions WHERE id = 1").fetchone()
                    if row:
                        cs = ConditionSet.from_dict(json.loads(row[0]))
                        if not str(cs.updated_by).startswith("初期値"):   # この PC で保存した条件表だけ
                            out["conditions"] = cs
        except sqlite3.Error as e:
            log.warning("この PC の中の蓄積を読めません（移せません）: %s（%s）", local, e)
            return None
        if not (out["lots"] or out["overrides"] or out["conditions"]):
            return None
        return out

    def local_leftover(self) -> Optional[Dict[str, Any]]:
        """画面に出す: この PC に貯めた分の件数・年月（共有へ移せるもの）。"""
        d = self._read_local()
        if d is None:
            return None
        months = sorted({m for r in d["lots"] for m in (r["genba_ym"],) if m})
        shared = set()
        with self.history.file.read() as conn:
            if conn is not None and d["lots"]:
                shared = {r[0] for r in conn.execute("SELECT lot FROM lots")}
        new = sum(1 for r in d["lots"] if r["lot"] not in shared)
        return {"dir": d["dir"], "lots": len(d["lots"]), "new_lots": new, "months": months,
                "overrides": len(d["overrides"]), "conditions": d["conditions"] is not None,
                "conditions_by": d["conditions"].updated_by if d["conditions"] else ""}

    def move_local_to_shared(self) -> Dict[str, Any]:
        """この PC に貯めた分を共有フォルダへ移す。共有にある物は上書きしない。移したらこの PC の物は名前を変えて残す。"""
        d = self._read_local()
        if d is None:
            raise UserError("移すものがありません（共有フォルダが未設定か、この PC の中に貯めた分がありません）。")
        note = f"この PC（{who()['pc']}）に貯めた分を移した"
        with self._import_mu:
            res: Dict[str, Any] = {"lots": {"rows": 0, "added": 0, "kept": 0, "months": []}}
            if d["lots"]:
                res["lots"] = self.history.absorb(d["lots"], d["columns"], d["dir"])
            res.update(self.master.absorb(d["overrides"], d["conditions"], note))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        kept = []
        for name in ("actuals_history.sqlite3", "actuals_master.sqlite3"):
            p = Path(d["dir"]) / name
            if p.is_file():
                dst = p.with_name(f"{p.stem}_移した_{stamp}{p.suffix}")
                try:
                    os.replace(p, dst)
                    kept.append(str(dst))
                except OSError as e:
                    log.warning("移した後に名前を変えられません（次回もう一度出ますが、二重には入りません）: %s（%s）", p, e)
        res["kept_as"] = kept
        self._cache.clear()
        log.info("この PC に貯めた分を共有へ移しました: %s", res)
        return res

    # ---------- データ更新（SymNavi） ----------
    def run_update(self) -> Dict[str, Any]:
        """SymNavi で RNE を実行して当月の倉入実績を取り出し、共有の蓄積に取り込む（旧 VBA のデータ作成）。"""
        s = self.conf()
        cred, _ = credentials.load(self.paths)
        problems = []
        rne = Path(s.get("rne_path") or "")
        if not s.get("rne_path") or not rne.is_file():
            problems.append(f"RNE ファイルが見つかりません: {s.get('rne_path') or '（未設定）'}")
        if not s.get("symnavi_exe") or not Path(s["symnavi_exe"]).is_file():
            problems.append(f"SymNavi.exe が見つかりません: {s.get('symnavi_exe') or '（未設定）'}")
        if not (cred["id"] and cred["pass"]):
            problems.append("SymNavi の ID・パスワードが未登録です")
        if problems:
            raise UserError("データ更新できません。「パスの設定」を確認してください: " + "／".join(problems))
        work = Path(s.get("work_dir") or (self.paths.base / "work"))

        def job(report):
            from . import rne_runner
            with logsetup.timed("SymNavi で RNE を実行して取り出す", files=[rne, s["symnavi_exe"], work],
                                expected=lambda e: self.rne.cancelled, rne=rne.name,
                                server=s.get("symnavi_server") or "NlmFaNgyNavd") as fr:
                got = rne_runner.extract_rows(rne, work, s["symnavi_exe"], s.get("symnavi_server") or "NlmFaNgyNavd",
                                              cred, int(s.get("startup_wait_sec") or 20),
                                              int(s.get("result_wait_sec") or 120), s.get("cscript_path") or "", report)
                fr.note(rows=len(got["rows"]))
            report(0.95, f"取り込んでいます（{len(got['rows']):,} 行）")
            res = self.import_rows(got["columns"], got["rows"], "symnavi", rne.name)
            report(1.0, f"{res['rows']:,} 件を取り込みました（追加 {res['added']}・更新 {res['changed']}）")
            return res
        try:
            self.rne.start(job)
        except RuntimeError as e:
            raise UserError(str(e))
        return self.rne.status()

    def cancel_update(self) -> Dict[str, Any]:
        return {"cancelled": self.rne.cancel()}

    # ---------- 書き出し ----------
    def export(self, kind: str, y: int, m: int, cal: str, sel: Any, fy: int) -> Dict[str, Any]:
        s = self.conf()
        folder = Path(s["export_dir"])
        label = CALENDARS.get(cal, "")
        if kind == "month":
            v = self.month(y, m, cal, A.ALL)
            header = ["No", "グループ", "区分", "月計"] + [f"{d}日" for d in range(1, 32)]
            lines = []
            for g in v["groups"]:
                if not g["named"]:
                    continue
                lines.append([g["no"], g["name"], "重量(kg)", g["weight_total"], *g["weight"]])
                lines.append([g["no"], g["name"], "枚数(枚)", g["count_total"], *g["count"]])
            lines.append(["", "総計", "重量(kg)", v["weight_total"], *v["day_weight"]])
            lines.append(["", "総計", "枚数(枚)", v["count_total"], *v["day_count"]])
            path = write_csv(folder, f"月集計_{label}_{y}-{m:02d}", header, lines, what="CSV（月の集計）")
        elif kind == "detail":
            v = self.month(y, m, cal, sel)
            path = write_csv(folder, f"明細_{label}_{y}-{m:02d}_{v['sel_label']}", v["detail_headers"], v["detail"],
                             what="CSV（明細）")
        elif kind == "year":
            v = self.year(fy, cal)
            gn = [g["name"] for g in v["groups"]]
            header = ["日付", "重量総計", *[f"重量_{n}" for n in gn], "枚数総計", *[f"枚数_{n}" for n in gn]]
            lines = [[d["date"], d["weight_total"], *d["weight"], d["count_total"], *d["count"]] for d in v["days"]]
            path = write_csv(folder, f"年集計_{label}_{fy}年度", header, lines, what="CSV（年の集計）")
        else:
            raise UserError("書き出す種類が不正です。")
        log.info("CSV を書き出しました: %s", path)
        return {"path": str(path), "folder": str(folder)}

    def open_export_folder(self) -> Dict[str, Any]:
        folder = Path(self.conf()["export_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        return {"opened": open_folder(folder), "folder": str(folder)}


def _t(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.strftime("%Y/%m/%d %H:%M:%S")
    return v


def _audit_brief(a: Dict[str, Any]) -> Dict[str, Any]:
    return {"at": a.get("at"), "by": f"{a.get('by_pc', '')} {a.get('by_user', '')}".strip(), "target": a.get("target"),
            "note": a.get("note") or ""}


def _reason(trace: List[Dict[str, Any]]) -> str:
    """未分類の理由を短く。NOT で外れた行と、1 項目だけ外れた行を挙げる。"""
    parts = []
    for st in trace:
        if st.get("not_hit"):
            parts.append(f"{st['row']}行目 {st['name']} の NOT 条件")
    near = []
    for st in trace:
        main = st.get("main")
        if not main or not st.get("name"):
            continue
        bad = [x["field"] for x in main if not x["ok"]]
        if len(bad) == 1:
            near.append(f"{st['row']}行目 {st['name']} は{bad[0]}で外れ")
    if trace and trace[-1].get("result") == "板ではないため対象外":
        return "板ではない（形状 30 で板丈 0）"
    text = "、".join(parts + near[:3])
    return text or "どの行の条件にも当たらない"
