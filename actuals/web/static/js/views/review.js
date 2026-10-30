/*
  振り分け確認（旧 修正シート A 列の運用）

  どのグループにも当たらなかったロット（未分類）と、手動訂正したロットを月ごとに一覧する。
  訂正はロット番号に結び付けて共有に保存する（データを取り直してもずれない・全 PC 共通）。
*/
import { api, qs } from "../api.js";
import { toast, toastError } from "../toast.js";
import { $, esc, kg, n0, ymLabel } from "../util.js";

function groupOptions(names, cur) {
  return ['<option value="">グループを選ぶ</option>']
    .concat(names.map((nm, i) => nm ? `<option value="${i + 1}" ${cur === i + 1 ? "selected" : ""}>${i + 1} ${esc(nm)}</option>` : ""))
    .concat([`<option value="0" ${cur === 0 ? "selected" : ""}>0 集計しない</option>`]).join("");
}

export async function render(main, ctx, stale) {
  const { y, m, cal } = ctx.nav;
  const v = await api.get(`/api/review?${qs({ y, m, cal })}`, "読み込んでいます…");
  if (stale()) return;
  const names = v.names;
  main.innerHTML = `
  <div class="view">
    <div class="view__head">
      <h2>振り分け確認　${ymLabel(y, m)}</h2>
      <span class="monthnav"><button class="btn btn--sm" type="button" id="prev">◀</button>
        <button class="btn btn--sm" type="button" id="next">▶</button></span>
      <div class="seg" role="group" aria-label="暦">
        <button class="seg__btn" type="button" data-cal="genba" aria-pressed="${cal === "genba"}">現場暦</button>
        <button class="seg__btn" type="button" data-cal="taiyo" aria-pressed="${cal === "taiyo"}">太陽暦</button></div>
      <span class="grow"></span>
      <input class="input" id="findLot" placeholder="ロット番号で探す" list="findList" autocomplete="off">
      <datalist id="findList"></datalist>
      <button class="btn btn--sm btn--find" type="button" id="findGo">開く</button>
    </div>
    <section class="card"><header><h3>未分類（どのグループにも当たらない）</h3>
      <span class="note">${n0(v.unclassified.length)} 件・${kg(v.unclassified_weight)} kg。どの集計にも入っていません。</span></header>
      <div class="pad pad--flush"><div class="tablewrap tablewrap--mid"><table class="table table--dense">
        <thead><tr><th>ﾛｯﾄ番号</th><th>日</th><th>ｵｰﾀﾞｰ材質</th><th>板厚</th><th>板幅</th><th>板丈</th><th>計算単重</th><th>VC</th>
          <th>重量kg</th><th>枚数</th><th>外れた理由</th><th>訂正</th><th>理由</th><th></th></tr></thead>
        <tbody>${v.unclassified.map((l) => `<tr class="unclassified-row" data-lot="${esc(l.lot)}">
          <td><a href="#" data-open="${esc(l.lot)}">${esc(l.lot)}</a></td><td class="n">${esc(l.day ?? "")}</td><td>${esc(l.o_mat ?? "")}</td>
          <td class="n">${esc(l.t ?? "")}</td><td class="n">${esc(l.w ?? "")}</td><td class="n">${esc(l.l ?? "")}</td>
          <td class="n">${esc(l.unit_w)}</td><td class="n">${esc(l.vc ?? "")}</td><td class="n">${kg(l.weight)}</td><td class="n">${n0(l.count)}</td>
          <td style="text-align:left;white-space:normal">${esc(l.reason)}</td>
          <td><select class="input" data-grp>${groupOptions(names, null)}</select></td>
          <td><input class="input" data-reason placeholder="理由"></td>
          <td><button class="btn btn--sm btn--commit" type="button" data-save>保存</button></td></tr>`).join("")
          || '<tr><td colspan="14">ありません</td></tr>'}</tbody></table></div></div></section>
    <section class="card"><header><h3>手動訂正したロット</h3><span class="note">${n0(v.overridden.length)} 件</span></header>
      <div class="pad pad--flush"><div class="tablewrap tablewrap--mid"><table class="table table--dense">
        <thead><tr><th>ﾛｯﾄ番号</th><th>日</th><th>自動判定</th><th>訂正後</th><th>理由</th><th>入力</th><th>日時</th><th></th></tr></thead>
        <tbody>${v.overridden.map((l) => `<tr data-lot="${esc(l.lot)}" data-seen="${l.override}">
          <td><a href="#" data-open="${esc(l.lot)}">${esc(l.lot)}</a></td><td class="n">${esc(l.day ?? "")}</td>
          <td>${l.auto ? `${l.auto} ${esc(names[l.auto - 1])}` : "未分類"}</td>
          <td><b>${l.override === 0 ? "0 集計しない" : `${l.override} ${esc(names[l.override - 1] || "")}`}</b></td>
          <td style="text-align:left">${esc(l.reason)}</td><td>${esc(l.by)}</td><td>${esc(l.at)}</td>
          <td><button class="btn btn--sm btn--danger" type="button" data-clear>外す</button></td></tr>`).join("")
          || '<tr><td colspan="8">ありません</td></tr>'}</tbody></table></div></div></section>
  </div>`;

  $("#prev").addEventListener("click", () => { const d = new Date(y, m - 2, 1); ctx.setNav({ y: d.getFullYear(), m: d.getMonth() + 1 }); ctx.refresh(); });
  $("#next").addEventListener("click", () => { const d = new Date(y, m, 1); ctx.setNav({ y: d.getFullYear(), m: d.getMonth() + 1 }); ctx.refresh(); });
  for (const b of main.querySelectorAll("[data-cal]")) b.addEventListener("click", () => { ctx.setNav({ cal: b.dataset.cal }); ctx.refresh(); });
  for (const a of main.querySelectorAll("[data-open]")) a.addEventListener("click", (e) => { e.preventDefault(); ctx.openLot(a.dataset.open); });
  for (const tr of main.querySelectorAll("tr.unclassified-row")) {
    tr.querySelector("[data-save]").addEventListener("click", async () => {
      const g = tr.querySelector("[data-grp]").value;
      if (g === "") { toast("グループを選んでください。", "warn"); return; }
      try {
        await api.post("/api/override", { lot: tr.dataset.lot, grp: Number(g), reason: tr.querySelector("[data-reason]").value, seen: null });
        toast(`${tr.dataset.lot} を訂正しました。`, "ok");
        ctx.refresh();
      } catch (e) { toastError(e); }
    });
  }
  for (const tr of main.querySelectorAll("tr[data-seen]")) {
    tr.querySelector("[data-clear]").addEventListener("click", async () => {
      try {
        await api.post("/api/override", { lot: tr.dataset.lot, grp: null, reason: "", seen: Number(tr.dataset.seen) });
        toast(`${tr.dataset.lot} の訂正を外しました。`, "ok");
        ctx.refresh();
      } catch (e) { toastError(e); ctx.refresh(); }
    });
  }
  const find = $("#findLot");
  find.addEventListener("input", async () => {
    if (find.value.trim().length < 3) return;
    try {
      const r = await api.get(`/api/find?${qs({ q: find.value })}`);
      $("#findList").innerHTML = r.lots.map((l) => `<option value="${esc(l)}">`).join("");
    } catch { /* 候補が出ないだけ */ }
  });
  const go = () => { if (find.value.trim()) ctx.openLot(find.value.trim()); };
  $("#findGo").addEventListener("click", go);
  find.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
}
