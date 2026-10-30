"""ロットごとの計算とグループ判定（旧【02】グループ分け）。

旧 VBA の手順をそのまま追う。変えたのは次の 2 点だけで、どちらも条件表の「判定方式」で
旧 VBA と同じ動きに戻せる（照合用。tests/test_golden.py が VBA と同じ方式で全件一致を確かめる）:

1. NOT 条件の材質を「材質」と比べる（旧 VBA は Val(調質) と比べていた。列ずれ）
2. NOT 条件に当たったら「その行だけ外して次の行へ」（旧 VBA は判定をやめ、未分類になっていた）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import columns as C
from .conditions import ConditionSet
from .vba_compat import (excel_value, is_number, like_contains, parse_datetime, v_eq, v_ge, v_le, v_lt, vba_round,
                         vba_val)

GENBA = "genba"   # 現場暦（BOX実績_作業終了_日付）
TAIYO = "taiyo"   # 太陽暦（最小の倉入完了_年月日）
CALENDARS = {GENBA: "現場暦", TAIYO: "太陽暦"}


@dataclass
class Lot:
    lot: str                         # ロット番号（キー。元の文字のまま）
    raw: Dict[str, Any]              # 元の値（表示・CSV 用）
    v: Dict[str, Any]                # Excel に貼った時と同じ型にした値（判定用）
    unit_w: float                    # 計算単重（小数 2 桁に四捨五入）
    count: Optional[int]             # 倉入枚数（単重 0 の時は空）
    weight: Optional[float]          # 実績_倉入重量（数でなければ空。SUMIFS と同じく数だけ足す）
    is_plate: bool
    shape10: bool                    # 形状 = 10（グラフ明細の対象）
    genba: Tuple[Optional[int], Optional[int], Optional[int]]   # 年・月・日
    taiyo: Tuple[Optional[int], Optional[int], Optional[int]]
    choku: int                       # 直（現場暦のときだけ使う）
    box_end: Optional[datetime]
    kura: Any

    def ymd(self, cal: str):
        return self.genba if cal == GENBA else self.taiyo

    def shift(self, cal: str) -> Optional[int]:
        return self.choku if cal == GENBA else None   # 太陽暦では直を計算しない（旧 VBA と同じ）


@dataclass
class Judge:
    group: Optional[int]             # 自動判定（1〜15。当たらなければ None）
    simul: Optional[str] = None      # 同時VC（L / F）
    trace: Optional[List[Dict[str, Any]]] = None


def _ymd_from(v: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """旧 VBA の Mid(値, 1, 4) / Mid(6, 2) / Mid(9, 2)。日時なら年月日。"""
    dt = v if isinstance(v, datetime) else parse_datetime(v)
    if dt is not None:
        return dt.year, dt.month, dt.day
    if v is None:
        return None, None, None
    s = str(v)

    def part(a: int, n: int) -> Optional[int]:
        t = s[a:a + n]
        return int(t) if t.isdigit() else None
    return part(0, 4), part(5, 2), part(8, 2)


def compute_lot(raw: Dict[str, Any]) -> Lot:
    v = {k: excel_value(x) for k, x in raw.items()}
    unit_w = vba_round(vba_val(v.get(C.M_T)) * vba_val(v.get(C.M_W)) * vba_val(v.get(C.M_L))
                       * vba_val(v.get(C.SG)) / 10 ** 6, 2)
    count = int(vba_round(vba_val(v.get(C.WEIGHT)) / unit_w, 0)) if unit_w != 0 else None
    w = v.get(C.WEIGHT)
    box_date = v.get(C.BOX_DATE)
    bd = box_date if isinstance(box_date, datetime) else parse_datetime(box_date)
    # 旧 VBA: Val(Format(値, "h"))。日時でなければ 0 時扱い（3 直）
    hour = bd.hour if bd is not None else 0
    choku = 1 if 7 <= hour < 15 else 2 if 15 <= hour < 23 else 3
    shape = v.get(C.SHAPE)
    box_end = v.get(C.BOX_END)
    return Lot(
        lot=str(raw.get(C.LOT) if raw.get(C.LOT) is not None else ""),
        raw=raw, v=v, unit_w=unit_w, count=count,
        weight=float(w) if is_number(w) else None,
        is_plate=(not v_eq(shape, 30)) or vba_val(v.get(C.M_L)) > 0,
        shape10=vba_val(shape) == 10,
        genba=_ymd_from(box_date), taiyo=_ymd_from(v.get(C.KURA_DATE)), choku=choku,
        box_end=box_end if isinstance(box_end, datetime) else None,
        kura=v.get(C.KURA_DATE),
    )


def _star(x: Any) -> bool:
    return x == "*"


def _checks(row: Dict[str, Any], prefix: str, lot: Lot, cols: Dict[str, str], mat_value: Any,
            mat_by_val: bool) -> List[Tuple[str, bool]]:
    """1 行ぶんの条件（材質・板厚・板幅・板丈・単重）を個別に評価する（旧 VBA の入れ子の If と同じ比較）。"""
    g = lambda k: row.get(prefix + k)  # noqa: E731
    t = vba_val(lot.v.get(cols["t"]))
    w = vba_val(lot.v.get(cols["w"]))
    ln = vba_val(lot.v.get(cols["l"]))
    if mat_by_val:
        mat_ok = _star(g("mat")) or v_eq(g("mat"), vba_val(mat_value))
    else:
        mat_ok = _star(g("mat")) or v_eq(g("mat"), mat_value)
    return [
        ("材質", mat_ok),
        ("板厚", (_star(g("t_min")) or v_le(g("t_min"), t)) and (_star(g("t_max")) or v_ge(g("t_max"), t))),
        ("板幅", (_star(g("w_min")) or v_le(g("w_min"), w)) and (_star(g("w_max")) or v_ge(g("w_max"), w))),
        ("板丈", (_star(g("l_min")) or v_le(g("l_min"), ln)) and (_star(g("l_gt")) or v_lt(g("l_gt"), ln))
         and (_star(g("l_max")) or v_ge(g("l_max"), ln))),
        ("単重", _star(g("wt_max")) or v_ge(g("wt_max"), lot.unit_w)),
    ]


def classify(lot: Lot, cs: ConditionSet, want_trace: bool = False) -> Judge:
    main_cols = C.BASIS_COLUMNS["ｵｰﾀﾞｰ" if cs.basis_main == "ｵｰﾀﾞｰ" else "製造"]
    not_cols = C.BASIS_COLUMNS["ｵｰﾀﾞｰ" if cs.basis_not == "ｵｰﾀﾞｰ" else "製造"]
    legacy_mat = cs.rule_not_material == "temper_val"
    stop_on_not = cs.rule_not_hit == "stop"
    # 旧 VBA は NOT の材質を Val(DB1(a, 12 - n)) すなわち調質の列と比べていた
    not_mat_value = lot.v.get(not_cols["tmp"]) if legacy_mat else lot.v.get(not_cols["mat"])

    group: Optional[int] = None
    first = True
    flag_v = False
    simul: Optional[str] = None
    trace: Optional[List[Dict[str, Any]]] = [] if want_trace else None
    vc = lot.v.get(C.VC)
    vc_on = vba_val(vc) == 1 or v_eq(vc, "01")

    for idx, row in enumerate(cs.rows, start=1):
        step: Dict[str, Any] = {"row": idx, "name": row.get("name") or ""}
        if row.get("not_use") == "*":
            nc = _checks(row, "not_", lot, not_cols, not_mat_value, legacy_mat)
            hit = all(ok for _, ok in nc)
            step["not"] = [{"field": f, "ok": ok} for f, ok in nc]
            step["not_hit"] = hit
            if hit:
                if stop_on_not:
                    step["result"] = "NOT 条件に当たったため判定を終了"
                    if trace is not None:
                        trace.append(step)
                    break
                step["result"] = "NOT 条件に当たったためこの行を外す"
                if trace is not None:
                    trace.append(step)
                continue
        if lot.is_plate:
            mc = _checks(row, "", lot, main_cols, lot.v.get(main_cols["mat"]), False)
            step["main"] = [{"field": f, "ok": ok} for f, ok in mc]
            if all(ok for _, ok in mc):
                if first:
                    group = idx
                    first = False
                    step["result"] = "最初に当たった行"
                else:
                    step["result"] = "当たり（先の行で決定済み）"
                course = lot.v.get(C.COURSE)
                face, back = lot.v.get(C.FACE_VC), lot.v.get(C.BACK_VC)
                if like_contains(course, "LS4 VC"):
                    if v_eq(face, "B535") and v_eq(back, ""):
                        flag_v, simul = True, "L"
                elif like_contains(course, "FS4 VC"):
                    if (v_eq(face, "SPV2001") or v_eq(face, "AL-200")) and v_eq(back, ""):
                        flag_v, simul = True, "F"
                if not flag_v and row.get("vc") == "*" and vc_on:
                    group = idx
                    step["result"] = "VC 行に当たったため確定"
                    if trace is not None:
                        trace.append(step)
                    break
            else:
                step["result"] = "外れ"
        else:
            step["result"] = "板ではないため対象外"
        if trace is not None:
            trace.append(step)
    return Judge(group=group, simul=simul, trace=trace)


# 旧 VBA の並べ替え（BOX実績終了日時 降順、倉入日 昇順）は .Apply が無く実行されていなかった。
# 並びは取り込んだ順のまま使う（aggregate.detail_rows）。
