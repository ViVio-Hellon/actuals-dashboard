/*
  年の集計（旧 年集計シート: 年度の日別 × グループ）

  旧シートは日付が 2015 年度の固定値・見出しが旧グループ名のままで機能していなかった。
  年度を選び、見出しは条件表のグループ名を使う。月ごとに蓄積した実績から数える。
  上に月別のまとめ（追加）、下に日別（旧シートと同じ並び）。
*/
import { api, qs } from "../api.js";
import { toast, toastError } from "../toast.js";
import { $, esc, kg, n0 } from "../util.js";

let mode = "weight";

export async function render(main, ctx, stale) {
  const { cal } = ctx.nav;
  const fy = ctx.nav.fy || (ctx.nav.m >= 4 ? ctx.nav.y : ctx.nav.y - 1);
  const v = await api.get(`/api/year?${qs({ fy, cal })}`, "集計しています…");
  if (stale()) return;
  const fmt = mode === "weight" ? kg : n0;
  const gs = v.groups;
  const monthRows = v.months.map((mo) => `<tr><td>${mo.year}/${String(mo.month).padStart(2, "0")}</td>
      <td class="n"><b>${fmt(mo[mode + "_total"])}</b></td>${mo[mode].map((x) => `<td class="n${x ? "" : " z"}">${x ? fmt(x) : "0"}</td>`).join("")}</tr>`).join("");
  const dayRows = v.days.map((d) => `<tr><td>${d.date.replaceAll("-", "/")}</td><td class="n"><b>${fmt(d[mode + "_total"])}</b></td>
      ${d[mode].map((x) => `<td class="n${x ? "" : " z"}">${x ? fmt(x) : "0"}</td>`).join("")}</tr>`).join("");
  const head = `<thead><tr><th>${"日付"}</th><th>${mode === "weight" ? "重量総計" : "枚数総計"}</th>${gs.map((g) => `<th>${esc(g.name)}</th>`).join("")}</tr></thead>`;
  main.innerHTML = `
  <div class="view">
    <div class="view__head">
      <h2>${v.fy}年度</h2><span class="note">${v.fy}/4/1〜${v.fy + 1}/3/31</span>
      <span class="monthnav noprint">
        <button class="btn btn--sm" type="button" id="prev">◀</button>
        <button class="btn btn--sm" type="button" id="next">▶</button></span>
      <div class="seg" role="group" aria-label="暦">
        <button class="seg__btn" type="button" data-cal="genba" aria-pressed="${cal === "genba"}">現場暦</button>
        <button class="seg__btn" type="button" data-cal="taiyo" aria-pressed="${cal === "taiyo"}">太陽暦</button></div>
      <div class="seg" role="group" aria-label="値">
        <button class="seg__btn" type="button" data-mode="weight" aria-pressed="${mode === "weight"}">重量 kg</button>
        <button class="seg__btn" type="button" data-mode="count" aria-pressed="${mode === "count"}">枚数</button></div>
      <span class="grow"></span>
      <button class="btn btn--sm noprint" type="button" id="csv">年集計を CSV に</button>
    </div>
    <section class="card"><header><h3>月別</h3><span class="note">年度の合計 ${kg(v.weight_total)} kg・${n0(v.count_total)} 枚</span></header>
      <div class="pad pad--flush"><div class="tablewrap"><table class="table table--dense matrix">
        ${head.replace("日付", "年月")}<tbody>${monthRows}</tbody></table></div></div></section>
    <section class="card"><header><h3>日別（旧 年集計）</h3></header>
      <div class="pad pad--flush"><div class="tablewrap tablewrap--tall"><table class="table table--dense matrix">
        ${head}<tbody>${dayRows}</tbody></table></div></div></section>
  </div>`;
  $("#prev").addEventListener("click", () => { ctx.setNav({ fy: v.fy - 1 }); ctx.refresh(); });
  $("#next").addEventListener("click", () => { ctx.setNav({ fy: v.fy + 1 }); ctx.refresh(); });
  for (const b of main.querySelectorAll("[data-cal]")) b.addEventListener("click", () => { ctx.setNav({ cal: b.dataset.cal }); ctx.refresh(); });
  for (const b of main.querySelectorAll("[data-mode]")) b.addEventListener("click", () => { mode = b.dataset.mode; ctx.refresh(); });
  $("#csv").addEventListener("click", async () => {
    try {
      const r = await api.post("/api/export", { kind: "year", fy: v.fy, cal }, "書き出しています…");
      toast(`書き出しました: ${r.path}`, "ok", { label: "フォルダを開く", onClick: () => api.post("/api/export/open", {}) });
    } catch (e) { toastError(e); }
  });
}
