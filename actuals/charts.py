"""グラフの描画計画（旧 ｸﾞﾗﾌ シートの 4 つのグラフ: 重量は棒、枚数は線、横軸は 1〜31 日）。

座標と色の印（mark）はここで決める。画面（static/js/chartplan.js）は計画を SVG の要素に置き換えるだけ
（python-web-tools の svgplan.js・all-tools 日報の chart.js と同じ分け方。JS は「1 直が何色か」を知らない）。

見た目は all-tools（日報複合ツール）のグラフに合わせる:
  - 直の色は Excel のグラフと同じ並び（1 直 青・2 直 赤・3 直 緑）。表の直の札も同じ色
  - 合計のグラフは、棒を直ごとに積む（下から 1→2→3 直）。段のあいだに地の色の細い境
  - 枚数の線は直のどの色とも違う紫。地の色の縁取り（halo）を下に敷き、棒の上でも消えない
  - 目盛りの横線、左右の軸の見出し（重量／枚数）、凡例は形でも分ける（棒は四角・線は横棒）
  - 日曜は表と同じ淡い地。横軸は 1〜末日をすべて出す
直の無い太陽暦・積んだ段が合計と合わない時は、1 色の棒（旧ツールと同じ形）にする。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

W, H = 960.0, 250.0
LEFT, RIGHT, TOP, BOTTOM = 62.0, 62.0, 30.0, 30.0
WEEKDAYS = "月火水木金土日"
SHIFT_MARKS = {1: "shift-1", 2: "shift-2", 3: "shift-3"}


def nice_max(v: float, ticks: int = 4) -> float:
    if v <= 0:
        return 1.0
    raw = v / ticks
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag * ticks
    return 10 * mag * ticks


def _fmt_kg(v: float) -> str:
    if v >= 1000:
        t = v / 1000
        return f"{t:g}t"
    return f"{v:g}kg"


def _day_label(y: int, m: int, d: int) -> str:
    try:
        return f"{m}/{d}({WEEKDAYS[date(y, m, d).weekday()]})"
    except ValueError:
        return f"{m}/{d}"


def _weekday(y: int, m: int, d: int) -> Optional[int]:
    try:
        return date(y, m, d).weekday()
    except ValueError:
        return None


def stacks_match(total: Sequence[float], stacks: Sequence[Tuple[str, str, Sequence[float]]]) -> bool:
    """直ごとの段を積んだ高さが、日ごとの合計と同じか（違えば積まない。見た目で数字を変えない）。"""
    if not stacks:
        return False
    for i, t in enumerate(total):
        s = sum((st[2][i] if i < len(st[2]) else 0) or 0 for st in stacks)
        if abs(s - (t or 0)) > 1e-6 * max(1.0, abs(t or 0)):
            return False
    return True


def bar_line(title: str, weights: Sequence[float], counts: Sequence[int], days_in_month: int,
             note: str = "", height: float = H, *, year: int = 0, month: int = 0, mark: str = "total",
             stacks: Optional[Sequence[Tuple[str, str, Sequence[float]]]] = None,
             wmax: Optional[float] = None, cmax: Optional[float] = None) -> Dict[str, Any]:
    """重量の棒 + 枚数の線。stacks = [(名前, 印, 日ごとの重量), …] を渡すと棒を下から積む。

    wmax / cmax を渡すと目盛りの頭をそろえる（直別の 3 枚を同じ物差しで比べるため）。
    """
    pw, ph = W - LEFT - RIGHT, height - TOP - BOTTOM
    n = 31
    step = pw / n
    base_y = TOP + ph
    wmax = wmax or nice_max(max(weights) if weights else 0)
    cmax = cmax or nice_max(max(counts) if counts else 0)
    use_stacks = list(stacks) if stacks and stacks_match(weights, stacks) else []
    items: List[Dict[str, Any]] = []

    # 日曜の淡い地（表の日曜と同じ）。目盛りより先に敷く
    if year and month:
        for i in range(days_in_month):
            if _weekday(year, month, i + 1) == 6:
                items.append({"t": "rect", "x": LEFT + i * step, "y": TOP, "w": step, "h": ph, "cls": "sun"})
    # 目盛り（横線）と左右の数字
    for k in range(5):
        y = base_y - ph * k / 4
        items.append({"t": "line", "x1": LEFT, "y1": y, "x2": LEFT + pw, "y2": y, "cls": "grid"})
        items.append({"t": "text", "x": LEFT - 6, "y": y + 4, "anchor": "end", "text": _fmt_kg(wmax * k / 4),
                      "cls": "axis"})
        items.append({"t": "text", "x": LEFT + pw + 6, "y": y + 4, "anchor": "start",
                      "text": f"{cmax * k / 4:,.0f}枚", "cls": "axis axis--count"})
    items.append({"t": "text", "x": LEFT - 6, "y": TOP - 14, "anchor": "end", "text": "重量", "cls": "axis axis--head"})
    items.append({"t": "text", "x": LEFT + pw + 6, "y": TOP - 14, "anchor": "start", "text": "枚数",
                  "cls": "axis axis--head axis--count"})

    # 棒（積む時は下から 1→2→3 直）
    bw = step * 0.64
    for i in range(n):
        x = LEFT + i * step + step * 0.18
        if use_stacks:
            acc = 0.0
            for _, smark, vals in use_stacks:
                v = (vals[i] if i < len(vals) else 0) or 0
                if v <= 0:
                    continue
                y0, y1 = base_y - ph * acc / wmax, base_y - ph * (acc + v) / wmax
                items.append({"t": "rect", "x": x, "y": y1, "w": bw, "h": y0 - y1, "cls": f"bar bar--seg mark-{smark}"})
                acc += v
        else:
            v = (weights[i] if i < len(weights) else 0) or 0
            if v > 0:
                h = ph * v / wmax
                items.append({"t": "rect", "x": x, "y": base_y - h, "w": bw, "h": h, "cls": f"bar mark-{mark}"})

    # 横軸（1〜末日すべて。日曜は字も色を変える）
    for i in range(days_in_month):
        sun = year and month and _weekday(year, month, i + 1) == 6
        items.append({"t": "text", "x": LEFT + i * step + step / 2, "y": height - 12, "anchor": "middle",
                      "text": f"{i + 1}", "cls": "axis axis--day" + (" axis--sun" if sun else "")})
    items.append({"t": "line", "x1": LEFT, "y1": base_y, "x2": LEFT + pw, "y2": base_y, "cls": "base"})

    # 枚数の線（縁取り → 本体 → 点）
    pts = []
    for i in range(min(days_in_month, n)):
        cv = counts[i] if i < len(counts) else 0
        pts.append((round(LEFT + i * step + step / 2, 1), round(base_y - ph * (cv or 0) / cmax, 1)))
    if pts:
        items.append({"t": "polyline", "points": [list(p) for p in pts], "cls": "line line--halo"})
        items.append({"t": "polyline", "points": [list(p) for p in pts], "cls": "line mark-count"})
        for x, y in pts:
            items.append({"t": "dot", "x": x, "y": y, "r": 2.6, "cls": "dot mark-count"})

    # 日ごとの当たり（透明）。指した日の重量（直ごと）と枚数をまとめて出す
    for i in range(days_in_month):
        wv = (weights[i] if i < len(weights) else 0) or 0
        cv = (counts[i] if i < len(counts) else 0) or 0
        lab = _day_label(year, month, i + 1) if year and month else f"{i + 1}日"
        parts = [f"{lab}  重量 {wv:,.1f} kg・枚数 {cv:,} 枚"]
        if use_stacks:
            parts.append("・".join(f"{name} {(vals[i] if i < len(vals) else 0) or 0:,.1f} kg"
                                  for name, _, vals in use_stacks))
        items.append({"t": "rect", "x": LEFT + i * step, "y": TOP, "w": step, "h": ph, "cls": "hit",
                      "tip": "\n".join(parts)})

    legend = ([{"label": name, "mark": smark, "shape": "bar"} for name, smark, _ in use_stacks]
              or [{"label": "重量", "mark": mark, "shape": "bar"}])
    legend.append({"label": "枚数", "mark": "count", "shape": "line"})
    return {"title": title, "note": note, "viewBox": [0, 0, W, height], "items": items, "legend": legend,
            "stacked": bool(use_stacks), "weight_total": round(sum(weights), 6), "count_total": sum(counts)}


def month_charts(title: str, total_w: Sequence[float], total_c: Sequence[int],
                 shift: Dict[int, Dict[str, List[float]]], days_in_month: int, year: int, month: int,
                 with_shift: bool, shift_note: str = "") -> Dict[str, Any]:
    """月の集計のグラフ一式: 合計（直で積む）と直別 3 枚（同じ物差し）。"""
    stacks = [(f"{k}直", SHIFT_MARKS[k], shift[k]["weight"]) for k in (1, 2, 3)] if with_shift else None
    total = bar_line(title, total_w, total_c, days_in_month, height=320, year=year, month=month, stacks=stacks)
    wmax = nice_max(max([max(shift[k]["weight"] or [0]) for k in (1, 2, 3)] + [0]))
    cmax = nice_max(max([max(shift[k]["count"] or [0]) for k in (1, 2, 3)] + [0]))
    per = [bar_line(f"{k}直", shift[k]["weight"], shift[k]["count"], days_in_month, note=shift_note, height=170,
                    year=year, month=month, mark=SHIFT_MARKS[k], wmax=wmax, cmax=cmax) for k in (1, 2, 3)]
    return {"total": total, "shift": per}
