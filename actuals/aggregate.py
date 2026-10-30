"""集計（旧 月集計・年集計・ｸﾞﾗﾌ シートの式と クリック動作）。

旧シートの SUMIFS と同じ条件で数える:
  月集計  集計用グループ = 行、年 = 集計月の年、月 = 集計月の月、日 = 列
  年集計  集計用グループ = 列、集計日 = 行の日付
  明細    集計用グループ ≠ 0、選んだグループ（または全部）、形状 = 10、集計月
          （旧 VBA は月だけで絞っていた。月ごとに蓄積するので年も見る）
グループ名が空の行は、旧シートと同じく表示も合計もしない。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import columns as C
from .engine import GENBA, Lot
from .vba_compat import format_fixed

GROUPS = range(1, 16)
ALL = "all"


@dataclass
class Row:
    lot: Lot
    auto: Optional[int]               # 自動判定（None = 未分類）
    override: Optional[int] = None    # 手動訂正（None = なし、0 = 集計しない）
    simul: Optional[str] = None

    @property
    def final(self) -> int:
        """旧 修正!C = IF(A="", B, A)。未分類は 0。"""
        if self.override is not None:
            return self.override
        return self.auto or 0


def _add(a: float, b: Optional[float]) -> float:
    return a + b if b is not None else a


def month_matrix(rows: Sequence[Row], names: Sequence[str], cal: str, year: int, month: int) -> Dict[str, Any]:
    days = list(range(1, 32))
    w = {g: [0.0] * 31 for g in GROUPS}
    c = {g: [0] * 31 for g in GROUPS}
    for r in rows:
        y, m, d = r.lot.ymd(cal)
        if y != year or m != month or not d or not (1 <= d <= 31):
            continue
        g = r.final
        if g in w:
            w[g][d - 1] = _add(w[g][d - 1], r.lot.weight)
            if r.lot.count is not None:
                c[g][d - 1] += r.lot.count
    groups = []
    for g in GROUPS:
        name = names[g - 1] if g - 1 < len(names) else ""
        named = bool(name)
        groups.append({"no": g, "name": name, "named": named,
                       "weight": w[g] if named else None, "count": c[g] if named else None,
                       "weight_total": round(sum(w[g]), 6) if named else None,
                       "count_total": sum(c[g]) if named else None})
    day_w = [round(sum(gr["weight"][i] for gr in groups if gr["named"]), 6) for i in range(31)]
    day_c = [sum(gr["count"][i] for gr in groups if gr["named"]) for i in range(31)]
    return {"days": days, "groups": groups, "day_weight": day_w, "day_count": day_c,
            "weight_total": round(sum(day_w), 6), "count_total": sum(day_c)}


def detail_rows(rows: Sequence[Row], cal: str, year: int, month: int, sel: Any) -> List[Row]:
    """旧 クリック動作。並びは取り込んだ順（旧 DATA シートの並びと同じ）。

    旧 VBA は BOX実績終了日時の降順に並べ替える設定を書いているが .Apply が無く、実際には並べ替えて
    いなかった（受領ブックの DATA は SymNavi の出力順）。結果を変えないため元の順のままにする。
    """
    out = []
    for r in rows:
        g = r.final
        if g == 0:
            continue
        if sel != ALL and g != sel:
            continue
        if not r.lot.shape10:
            continue
        y, m, d = r.lot.ymd(cal)
        if m != month or y != year:
            continue
        out.append(r)
    return out


def shift_daily(detail: Sequence[Row], cal: str) -> Dict[int, Dict[str, List[float]]]:
    """旧 ｸﾞﾗﾌ!J〜O。明細を日・直で足す。太陽暦では直が無いので 0。"""
    out = {k: {"weight": [0.0] * 31, "count": [0] * 31} for k in (1, 2, 3)}
    for r in detail:
        s = r.lot.shift(cal)
        _, _, d = r.lot.ymd(cal)
        if s not in out or not d or not (1 <= d <= 31):
            continue
        out[s]["weight"][d - 1] = _add(out[s]["weight"][d - 1], r.lot.weight)
        if r.lot.count is not None:
            out[s]["count"][d - 1] += r.lot.count
    return out


def selected_total(matrix: Dict[str, Any], names: Sequence[str], sel: Any) -> Tuple[List[float], List[int], str]:
    """旧 ｸﾞﾗﾌ!H/I。選んだグループの名前で月集計の行を引く。全部・名前が空・見つからない時は合計の行。"""
    if sel != ALL:
        name = names[sel - 1] if 0 < sel <= len(names) else ""
        if name:
            for gr in matrix["groups"]:
                if gr["named"] and gr["name"] == name:
                    return gr["weight"], gr["count"], name
    return matrix["day_weight"], matrix["day_count"], "全グループ合計"


def hit_counts(rows: Sequence[Row], cal: str, year: int, month: int) -> Dict[Any, int]:
    """グループごとの明細の件数（旧 ｸﾞﾗﾌ!E 列。旧シートは選んだグループだけ数えていた）。"""
    counts: Dict[Any, int] = {g: 0 for g in GROUPS}
    total = 0
    for r in rows:
        g = r.final
        if g == 0 or not r.lot.shape10:
            continue
        y, m, _ = r.lot.ymd(cal)
        if y != year or m != month:
            continue
        total += 1
        if g in counts:
            counts[g] += 1
    counts[ALL] = total
    return counts


DETAIL_HEADERS = ["集計用", "月", "日", "直", "ﾛｯﾄ番号", "検査番号", "ｵｰﾀﾞｰ番号", "用途ｺｰﾄﾞ",
                  "製造材質", "製造調質", "製造板厚", "製造板幅", "製造板丈", "製品単重",
                  "ｵｰﾀﾞｰ材質", "ｵｰﾀﾞｰ調質", "ｵｰﾀﾞｰ板厚", "ｵｰﾀﾞｰ板幅", "ｵｰﾀﾞｰ板丈",
                  "VC", "表VC", "裏VC", "同時VC", "重量", "枚数"]


def _disp(v: Any) -> Any:
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y/%m/%d %H:%M:%S")
    return v


def detail_record(r: Row, cal: str) -> List[Any]:
    """旧 ｸﾞﾗﾌ!W〜AU の 1 行（寸法は旧 VBA と同じ桁で表示）。"""
    v = r.lot.v
    _, m, d = r.lot.ymd(cal)
    return [r.final, m, d, r.lot.shift(cal) or "", r.lot.lot, _disp(v.get(C.INSP)), _disp(v.get(C.ORDER)),
            _disp(v.get(C.USE)),
            _disp(v.get(C.M_MAT)), _disp(v.get(C.M_TMP)), format_fixed(v.get(C.M_T), 3),
            format_fixed(v.get(C.M_W), 2), format_fixed(v.get(C.M_L), 2), format_fixed(r.lot.unit_w, 2),
            _disp(v.get(C.O_MAT)), _disp(v.get(C.O_TMP)), format_fixed(v.get(C.O_T), 3),
            format_fixed(v.get(C.O_W), 2), format_fixed(v.get(C.O_L), 2),
            _disp(v.get(C.VC)), _disp(v.get(C.FACE_VC)), _disp(v.get(C.BACK_VC)), r.simul or "",
            _disp(v.get(C.WEIGHT)), r.lot.count if r.lot.count is not None else ""]


def fiscal_days(fy: int) -> List[date]:
    """年度（4/1〜翌 3/31）の日付。旧 年集計の B 列。"""
    d0 = date(fy, 4, 1)
    d1 = date(fy + 1, 3, 31)
    return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]


def year_matrix(rows: Sequence[Row], names: Sequence[str], cal: str, fy: int) -> Dict[str, Any]:
    days = fiscal_days(fy)
    index = {(d.year, d.month, d.day): i for i, d in enumerate(days)}
    w = {g: [0.0] * len(days) for g in GROUPS}
    c = {g: [0] * len(days) for g in GROUPS}
    for r in rows:
        i = index.get(r.lot.ymd(cal))
        g = r.final
        if i is None or g not in w:
            continue
        w[g][i] = _add(w[g][i], r.lot.weight)
        if r.lot.count is not None:
            c[g][i] += r.lot.count
    named = [g for g in GROUPS if g - 1 < len(names) and names[g - 1]]
    lines = []
    for i, d in enumerate(days):
        lines.append({"date": d.isoformat(), "weight": [w[g][i] for g in named], "count": [c[g][i] for g in named],
                      "weight_total": round(sum(w[g][i] for g in named), 6),
                      "count_total": sum(c[g][i] for g in named)})
    months = []
    for k in range(12):
        y, m = (fy, 4 + k) if k < 9 else (fy + 1, k - 8)
        idx = [i for i, d in enumerate(days) if d.year == y and d.month == m]
        months.append({"year": y, "month": m,
                       "weight": [round(sum(w[g][i] for i in idx), 6) for g in named],
                       "count": [sum(c[g][i] for i in idx) for g in named],
                       "weight_total": round(sum(w[g][i] for g in named for i in idx), 6),
                       "count_total": sum(c[g][i] for g in named for i in idx)})
    return {"fy": fy, "groups": [{"no": g, "name": names[g - 1]} for g in named], "days": lines, "months": months,
            "weight_total": round(sum(x["weight_total"] for x in lines), 6),
            "count_total": sum(x["count_total"] for x in lines)}


def make_rows(raw_rows: Iterable[Dict[str, Any]], cs, overrides: Optional[Dict[str, int]] = None) -> List[Row]:
    """元の行 → 計算・判定・手動訂正を当てた行。"""
    from .engine import classify, compute_lot
    overrides = overrides or {}
    out = []
    for raw in raw_rows:
        lot = compute_lot(raw)
        j = classify(lot, cs)
        out.append(Row(lot=lot, auto=j.group, override=overrides.get(lot.lot), simul=j.simul))
    return out
