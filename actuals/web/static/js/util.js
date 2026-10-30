/* util.js — 描くための小道具（文字の逃がし・数の表示）。判断はしない。 */

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const NF1 = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 1 });
const NF0 = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 0 });

/** 重量（kg）。小数 1 桁まで。0 は 0。 */
export const kg = (v) => (v === null || v === undefined || v === "") ? "" : NF1.format(v);
/** 枚数・件数。 */
export const n0 = (v) => (v === null || v === undefined || v === "") ? "" : NF0.format(v);

export function ymLabel(y, m) { return `${y}年${m}月`; }

/** 曜日（日曜は表で薄く塗る）。 */
export function isSunday(y, m, d) { return new Date(y, m - 1, d).getDay() === 0; }

export function setHtml(el, html) { el.innerHTML = html; return el; }

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }
