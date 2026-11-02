/*
  chartplan.js — 描画計画を SVG にする（python-web-tools の svgplan.js・all-tools 日報の chart.js と同じ分け方）

  **計算はしない。色も決めない。** 座標・目盛り・文字・色の印（mark-shift-1 など）はサーバ（actuals/charts.py）が
  決める。ここは計画の class 名をそのまま書き、色は CSS（tokens.actuals.css）が付ける。
*/

import { esc } from "./util.js";

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}, text = null) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, v);
  if (text !== null) node.textContent = text;
  return node;
}

function tip(node, text) {
  if (!text) return node;
  node.appendChild(el("title", {}, text));
  return node;
}

export function chartSvg(plan) {
  const [x, y, w, h] = plan.viewBox;
  const svg = el("svg", { viewBox: `${x} ${y} ${w} ${h}`, class: "chart", role: "img",
                          "aria-label": `${plan.title}: 重量 ${plan.weight_total} kg、枚数 ${plan.count_total} 枚` });
  for (const it of plan.items) {
    if (it.t === "line") {
      svg.appendChild(el("line", { x1: it.x1, y1: it.y1, x2: it.x2, y2: it.y2, class: it.cls }));
    } else if (it.t === "rect") {
      svg.appendChild(tip(el("rect", { x: it.x, y: it.y, width: it.w, height: it.h, class: it.cls }), it.tip));
    } else if (it.t === "polyline") {
      svg.appendChild(el("polyline", { points: it.points.map((p) => p.join(",")).join(" "), class: it.cls }));
    } else if (it.t === "dot") {
      svg.appendChild(tip(el("circle", { cx: it.x, cy: it.y, r: it.r, class: it.cls }), it.tip));
    } else if (it.t === "text") {
      svg.appendChild(el("text", { x: it.x, y: it.y, "text-anchor": it.anchor, class: it.cls }, it.text));
    }
  }
  return svg;
}

/** 凡例。**形でも分ける**（棒は四角、線は横棒）── 色が見分けにくくても何の印か分かる。 */
export function legendHtml(items) {
  return (items || []).map((g) =>
    `<span class="lg"><i class="lg__sw lg__sw--${esc(g.shape)} mark-${esc(g.mark)}"></i>${esc(g.label)}</span>`).join("");
}
