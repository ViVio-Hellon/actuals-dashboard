/*
  月の集計（旧 月集計 + ｸﾞﾗﾌ シート）

  左: グループ（月計・HIT数）。押すと右のグラフと下の明細がそのグループに切り替わる（旧「レ」）。
  右: 日別グラフ。「合計」は旧 ｸﾞﾗﾌの合計（月集計の行）、「直別」は 1〜3 直（明細から）。
  下: グループ × 日の表（旧 月集計）。セルを押すと、そのロットが引き出しに出る。
*/
import { api, qs } from "../api.js";
import { toast, toastError } from "../toast.js";
import { chartSvg, legendHtml } from "../chartplan.js";
import { $, esc, isSunday, kg, n0, ymLabel } from "../util.js";

let chartMode = "total";
let matrixMode = "weight";

export async function render(main, ctx, stale) {
  const { y, m, cal, sel } = ctx.nav;
  if (!y) { main.innerHTML = '<div class="view"><p class="empty">読み込んでいます…</p></div>'; return; }
  const v = await api.get(`/api/month?${qs({ y, m, cal, sel })}`, "集計しています…");
  if (stale()) return;
  const noData = v.lots_in_month === 0;
  main.innerHTML = `
  <div class="view">
    <div class="view__head">
      <h2>${ymLabel(v.year, v.month)}</h2>
      <span class="monthnav noprint">
        <button class="btn btn--sm" type="button" id="prev" title="前の月（旧 ▲）">◀</button>
        <button class="btn btn--sm" type="button" id="next" title="次の月（旧 ▼）">▶</button>
      </span>
      <div class="seg" role="group" aria-label="暦">
        <button class="seg__btn" type="button" data-cal="genba" aria-pressed="${cal === "genba"}">現場暦</button>
        <button class="seg__btn" type="button" data-cal="taiyo" aria-pressed="${cal === "taiyo"}">太陽暦</button>
      </div>
      <span class="note">${cal === "genba" ? "BOX実績_作業終了_日付で集計・直あり" : "最小の倉入完了_年月日で集計・直なし"}</span>
      <span class="grow"></span>
      <button class="btn btn--sm noprint" type="button" id="csvMonth">月集計を CSV に</button>
      <button class="btn btn--sm noprint" type="button" id="csvDetail">明細を CSV に</button>
      <button class="btn btn--sm btn--print noprint" type="button" id="print"><span class="gl">⎙</span>印刷</button>
    </div>
    ${noData ? `<div class="card"><div class="pad"><p class="empty">この月（${esc(v.cal_label)}）の実績はまだ取り込まれていません。「データ」画面か、上の「データ更新」から取り込んでください。</p></div></div>` : ""}
    <div class="month-top">
      <section class="card" aria-label="グループ">
        <header><h3>グループ（月計）</h3><span class="note">行を押すとグラフと明細が切り替わります</span></header>
        <div class="pad pad--flush"><div class="tablewrap"><table class="table table--dense grp-table" id="grpTable">
          <thead><tr><th>No</th><th>グループ</th><th>重量 kg</th><th>枚数</th><th title="グラフ明細の件数（形状10）">件</th></tr></thead>
          <tbody>${v.groups.filter((g) => g.named).map((g) => `
            <tr data-sel="${g.no}" aria-selected="${v.sel === g.no}" class="${g.weight_total ? "" : "off"}" tabindex="0">
              <td class="n">${g.no}</td><td>${esc(g.name)}</td><td class="n">${kg(g.weight_total)}</td>
              <td class="n">${n0(g.count_total)}</td><td class="n">${n0(g.hits)}</td></tr>`).join("")}
            <tr data-sel="all" aria-selected="${v.sel === "all"}" tabindex="0">
              <td class="n">—</td><td>全グループ（板形状のみ）</td><td class="n">${kg(v.weight_total)}</td>
              <td class="n">${n0(v.count_total)}</td><td class="n">${n0(v.hits_all)}</td></tr>
          </tbody></table></div></div>
        <div class="grp-chips">
          <button class="linkchip ${v.unclassified ? "linkchip--warn" : "linkchip--ok"}" type="button" id="toReview">未分類 ${n0(v.unclassified)} 件</button>
          <button class="linkchip linkchip--info" type="button" id="toReview2">手動訂正 ${n0(v.overridden)} 件</button>
          <span class="note">この月のロット ${n0(v.lots_in_month)} 件</span>
        </div>
      </section>
      <section class="card" aria-label="グラフ">
        <header><h3>${esc(v.sel_label)}</h3>
          <span class="legend" id="legend" aria-label="凡例"></span>
          <div class="seg" role="group" aria-label="グラフ">
            <button class="seg__btn" type="button" data-chart="total" aria-pressed="${chartMode === "total"}">合計</button>
            <button class="seg__btn" type="button" data-chart="shift" aria-pressed="${chartMode === "shift"}">直別</button>
          </div></header>
        <div class="pad"><div class="charts" id="charts"></div></div>
      </section>
    </div>
    <section class="card" aria-label="グループ×日">
      <header><h3>グループ × 日（旧 月集計）</h3>
        <div class="seg" role="group" aria-label="表">
          <button class="seg__btn" type="button" data-mx="weight" aria-pressed="${matrixMode === "weight"}">重量 kg</button>
          <button class="seg__btn" type="button" data-mx="count" aria-pressed="${matrixMode === "count"}">枚数</button>
        </div><span class="note">セルを押すとロットの一覧が出ます</span></header>
      <div class="pad pad--flush"><div class="tablewrap"><table class="table table--dense matrix" id="matrix"></table></div></div>
    </section>
    <section class="card" aria-label="明細">
      <header><h3>明細（旧 ｸﾞﾗﾌ 明細: 形状10・${esc(v.sel_label)}）</h3><span class="note">${n0(v.detail.length)} 件。行を押すと判定の経過</span></header>
      <div class="pad pad--flush"><div class="tablewrap tablewrap--mid"><table class="table table--dense" id="detail"></table></div></div>
    </section>
  </div>`;

  drawCharts(v);
  drawMatrix(v, ctx);
  drawDetail(v, ctx);

  $("#prev").addEventListener("click", () => { ctx.setNav({ y: v.prev[0], m: v.prev[1] }); ctx.refresh(); });
  $("#next").addEventListener("click", () => { ctx.setNav({ y: v.next[0], m: v.next[1] }); ctx.refresh(); });
  for (const b of main.querySelectorAll("[data-cal]")) {
    b.addEventListener("click", () => { ctx.setNav({ cal: b.dataset.cal }); ctx.refresh(); });
  }
  for (const b of main.querySelectorAll("[data-chart]")) {
    b.addEventListener("click", () => {
      chartMode = b.dataset.chart;
      for (const x of main.querySelectorAll("[data-chart]")) x.setAttribute("aria-pressed", String(x === b));
      drawCharts(v);
    });
  }
  for (const b of main.querySelectorAll("[data-mx]")) {
    b.addEventListener("click", () => {
      matrixMode = b.dataset.mx;
      for (const x of main.querySelectorAll("[data-mx]")) x.setAttribute("aria-pressed", String(x === b));
      drawMatrix(v, ctx);
    });
  }
  for (const tr of main.querySelectorAll("#grpTable tr[data-sel]")) {
    const go = () => { ctx.setNav({ sel: tr.dataset.sel === "all" ? "all" : Number(tr.dataset.sel) }); ctx.refresh(); };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }
  $("#toReview").addEventListener("click", () => { location.hash = "#review"; });
  $("#toReview2").addEventListener("click", () => { location.hash = "#review"; });
  $("#print").addEventListener("click", () => window.print());
  const exp = async (kind) => {
    try {
      const r = await api.post("/api/export", { kind, y, m, cal, sel: v.sel }, "書き出しています…");
      toast(`書き出しました: ${r.path}`, "ok", { label: "フォルダを開く", onClick: () => api.post("/api/export/open", {}) });
    } catch (e) { toastError(e); }
  };
  $("#csvMonth").addEventListener("click", () => exp("month"));
  $("#csvDetail").addEventListener("click", () => exp("detail"));
}

function drawCharts(v) {
  const box = $("#charts");
  box.innerHTML = "";
  const plans = chartMode === "total" ? [v.charts.total] : v.charts.shift;
  box.className = chartMode === "total" ? "charts" : "charts charts--shift";
  // 凡例: 合計は積んだ段（1〜3 直）+ 枚数、直別は 3 つの直の色 + 枚数（色の意味は画面のどこでも同じ）
  $("#legend").innerHTML = legendHtml(chartMode === "total" ? v.charts.total.legend
    : [...v.charts.shift.map((p) => ({ ...p.legend[0], label: p.title })), { label: "枚数", mark: "count", shape: "line" }]);
  for (const p of plans) {
    const t = document.createElement("div");
    t.className = "chart__title";
    const sw = chartMode === "shift" ? `<i class="lg__sw lg__sw--bar mark-${esc(p.legend[0].mark)}"></i>` : "";
    t.innerHTML = `${sw}${esc(p.title)}<small>重量 ${kg(p.weight_total)} kg・枚数 ${n0(p.count_total)} 枚</small>${p.note ? `<small>${esc(p.note)}</small>` : ""}`;
    box.appendChild(t);
    box.appendChild(chartSvg(p));
  }
}

function drawMatrix(v, ctx) {
  const key = matrixMode;
  const fmt = key === "weight" ? kg : n0;
  const days = Array.from({ length: v.days_in_month }, (_, i) => i + 1);
  const sun = (d) => isSunday(v.year, v.month, d) ? " sun" : "";
  const head = `<thead><tr><th>グループ</th><th>月計</th>${days.map((d) => `<th class="${sun(d)}">${d}</th>`).join("")}</tr></thead>`;
  const body = v.groups.filter((g) => g.named).map((g) => {
    const vals = g[key];
    return `<tr><td>${g.no} ${esc(g.name)}</td><td class="n click" data-g="${g.no}">${fmt(g[key + "_total"])}</td>${days.map((d) => {
      const x = vals[d - 1];
      return `<td class="n click${x ? "" : " z"}${sun(d)}" data-g="${g.no}" data-d="${d}">${x ? fmt(x) : "0"}</td>`;
    }).join("")}</tr>`;
  }).join("");
  const tot = key === "weight" ? v.day_weight : v.day_count;
  const foot = `<tr class="total"><td>総計</td><td class="n click" data-g="all">${fmt(key === "weight" ? v.weight_total : v.count_total)}</td>${days.map((d) =>
    `<td class="n click${tot[d - 1] ? "" : " z"}${sun(d)}" data-g="all" data-d="${d}">${tot[d - 1] ? fmt(tot[d - 1]) : "0"}</td>`).join("")}</tr>`;
  const t = $("#matrix");
  t.innerHTML = head + `<tbody>${body}${foot}</tbody>`;
  for (const td of t.querySelectorAll("td.click")) {
    td.addEventListener("click", () => {
      const g = td.dataset.g;
      const name = g === "all" ? "総計" : v.groups[Number(g) - 1].name;
      const label = `${name}・${td.dataset.d ? `${v.month}/${td.dataset.d}` : `${v.month}月`}`;
      ctx.openCell({ g, d: td.dataset.d || "", label });
    });
  }
}

function drawDetail(v, ctx) {
  const t = $("#detail");
  const numCols = new Set([1, 2, 3, 10, 11, 12, 13, 16, 17, 18, 23, 24]);
  t.innerHTML = `<thead><tr>${v.detail_headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${v.detail.map((r) => `<tr data-lot="${esc(r[4])}" tabindex="0">${r.map((c, i) =>
      `<td class="${numCols.has(i) ? "n" : ""}">${i === 23 ? kg(c) : i === 3 && [1, 2, 3].includes(Number(c))
        ? `<span class="shift-chip mark-shift-${Number(c)}">${esc(c)}</span>` : esc(c)}</td>`).join("")}</tr>`).join("")
      || `<tr><td colspan="${v.detail_headers.length}">ありません</td></tr>`}</tbody>`;
  for (const tr of t.querySelectorAll("tr[data-lot]")) {
    tr.addEventListener("click", () => ctx.openLot(tr.dataset.lot));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") ctx.openLot(tr.dataset.lot); });
  }
}
