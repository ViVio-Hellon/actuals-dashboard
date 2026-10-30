"""グラフの描画計画（旧 ｸﾞﾗﾌ シートの 4 つのグラフ: 重量は棒、枚数は線、横軸は 1〜31 日）。

座標はここで決める。画面（static/js/chartplan.js）は計画を SVG の要素に置き換えるだけ
（python-web-tools の svgplan.js と同じ分け方）。色は tokens の名前で渡す。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

W, H = 960.0, 250.0
LEFT, RIGHT, TOP, BOTTOM = 62.0, 56.0, 16.0, 30.0


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


def bar_line(title: str, weights: Sequence[float], counts: Sequence[int], days_in_month: int,
             note: str = "", height: float = H) -> Dict[str, Any]:
    pw, ph = W - LEFT - RIGHT, height - TOP - BOTTOM
    n = 31
    step = pw / n
    wmax = nice_max(max(weights) if weights else 0)
    cmax = nice_max(max(counts) if counts else 0)
    items: List[Dict[str, Any]] = []
    for k in range(5):
        v = wmax * k / 4
        y = TOP + ph - ph * k / 4
        items.append({"t": "line", "x1": LEFT, "y1": y, "x2": LEFT + pw, "y2": y, "cls": "grid"})
        items.append({"t": "text", "x": LEFT - 6, "y": y + 4, "anchor": "end", "text": _fmt_kg(v), "cls": "axis"})
        items.append({"t": "text", "x": LEFT + pw + 6, "y": y + 4, "anchor": "start",
                      "text": f"{cmax * k / 4:g}枚", "cls": "axis"})
    pts = []
    for i in range(n):
        x = LEFT + i * step
        wv = weights[i] if i < len(weights) else 0
        if wv:
            h = ph * wv / wmax
            items.append({"t": "rect", "x": x + step * 0.18, "y": TOP + ph - h, "w": step * 0.64, "h": h,
                          "fill": "--chart-weight", "tip": f"{i + 1}日 重量 {wv:,.1f} kg"})
        if i < days_in_month:
            cv = counts[i] if i < len(counts) else 0
            pts.append((x + step / 2, TOP + ph - ph * cv / cmax, i + 1, cv))
        if (i + 1) in (1, 5, 10, 15, 20, 25, 30) or i + 1 == days_in_month:
            items.append({"t": "text", "x": x + step / 2, "y": height - 10, "anchor": "middle", "text": f"{i + 1}",
                          "cls": "axis"})
    if pts:
        items.append({"t": "polyline", "points": [[round(x, 1), round(y, 1)] for x, y, _, _ in pts],
                      "stroke": "--chart-count"})
        for x, y, d, cv in pts:
            items.append({"t": "dot", "x": x, "y": y, "r": 2.2, "fill": "--chart-count", "tip": f"{d}日 枚数 {cv:,} 枚"})
    items.append({"t": "line", "x1": LEFT, "y1": TOP + ph, "x2": LEFT + pw, "y2": TOP + ph, "cls": "base"})
    return {"title": title, "note": note, "viewBox": [0, 0, W, height], "items": items,
            "weight_total": round(sum(weights), 6), "count_total": sum(counts)}
