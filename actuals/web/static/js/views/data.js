/*
  データ（取り込みと蓄積）

  通常: RNEConverter が出した倉入実績の SQLite を取り込む（新しいものが出ていれば自動）。
        「データ更新」は RNEConverter に SymNavi の抽出を頼み、終わったら取り込む。
  予備: SymNavi から出した xls／Excel からコピーした表の貼付け（旧 手作業の手順 UserForm02〜10）。
  移行: 旧ブック（.xlsm）の DATA・修正シート（・設定シート）。
  取り込んだ実績はロット番号ごとに蓄積する（同じロットは新しい内容に置き換え、消しはしない）。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";
import { $, esc, n0 } from "../util.js";

function resultText(r) {
  return `${n0(r.rows)} 件を取り込みました（追加 ${n0(r.added)}・更新 ${n0(r.changed)}・変化なし ${n0(r.same)}${r.duplicates ? `・重複 ${r.duplicates}` : ""}）`;
}

export async function render(main, ctx, stale) {
  const [h, st] = await Promise.all([api.get("/api/history", "読み込んでいます…"), api.get("/api/state")]);
  if (stale()) return;
  const src = st.source;
  const rne = st.rne;
  const kinds = { source: "RNEConverter", paste: "貼付け", xls: "xls", xlsm: "旧ブック" };
  main.innerHTML = `
  <div class="view">
    <div class="view__head"><h2>データ</h2><span class="note">蓄積しているロット ${n0(h.lots)} 件</span></div>
    <div class="month-top">
      <section class="card"><header><h3>取り込み元（RNEConverter の出力）</h3></header><div class="pad stack">
        <dl class="facts">
          <dt>ファイル</dt><dd>${esc(src.path)}</dd>
          <dt>状態</dt><dd>${src.exists ? `<span class="st-dot ${src.pending ? "warn" : "ok"}">${src.pending ? "まだ取り込んでいない新しいデータがあります" : "取り込み済み"}</span>`
            : `<span class="st-dot ng">${esc(src.error)}</span>`}</dd>
          ${src.modified ? `<dt>更新日時</dt><dd>${esc(src.modified.replace("T", " "))}</dd>` : ""}
          <dt>自動の取り込み</dt><dd>${st.auto_import ? "する（新しいデータが出ていたら、画面を開いている間に取り込みます）" : "しない"}</dd>
        </dl>
        <div class="btn-row">
          <button class="btn btn--run" type="button" id="imp" ${src.exists ? "" : "disabled"}>いま取り込む</button>
          <button class="btn" type="button" id="rerun" ${rne.running ? "disabled" : ""}>データ更新（RNEConverter で抽出）</button>
        </div>
        ${rne.lines && rne.lines.length ? `<div class="loglines">${esc(rne.lines.join("\n"))}</div>` : ""}
        ${rne.rc !== null && rne.rc !== undefined ? `<p class="note">前回のデータ更新: ${esc(rne.rc_text)}（${rne.elapsed} 秒）</p>` : ""}
      </div></section>
      <section class="card"><header><h3>月ごとの件数（集計日）</h3></header><div class="pad pad--flush">
        <div class="tablewrap tablewrap--mid"><table class="table table--dense">
          <thead><tr><th>年月</th><th>現場暦</th><th>太陽暦</th></tr></thead>
          <tbody>${h.months.slice().reverse().map((x) => `<tr><td>${esc(x.ym)}</td><td class="n">${n0(x.genba)}</td><td class="n">${n0(x.taiyo)}</td></tr>`).join("")
            || '<tr><td colspan="3">まだありません</td></tr>'}</tbody></table></div></div></section>
    </div>
    <section class="card"><header><h3>取り込みの記録</h3></header><div class="pad pad--flush"><div class="tablewrap tablewrap--mid">
      <table class="table table--dense"><thead><tr><th>日時</th><th class="t">方法</th><th class="t">取り込み元</th><th>件数</th><th>追加</th><th>更新</th><th>変化なし</th><th>集計日の年月</th><th>PC・利用者</th></tr></thead>
      <tbody>${h.imports.map((i) => `<tr><td>${esc(i.at)}</td><td>${esc(kinds[i.kind] || i.kind)}</td><td class="t">${esc(i.source)}</td>
        <td class="n">${n0(i.rows)}</td><td class="n">${n0(i.added)}</td><td class="n">${n0(i.changed)}</td><td class="n">${n0(i.same)}</td>
        <td>${esc((i.months || "").split(",").join(" "))}</td><td>${esc(i.by_pc)} ${esc(i.by_user)}</td></tr>`).join("")
        || '<tr><td colspan="9">まだありません</td></tr>'}</tbody></table></div></div></section>
    <section class="card"><header><h3>RNEConverter が使えない時（手作業の取り込み）</h3></header><div class="pad stack">
      <p class="note">SymNavi で RNE を開いて保存した xls を選ぶか、SymNavi の結果を Excel に出して表全体（見出しの行ごと）をコピーし、下に貼り付けてください。</p>
      <div class="btn-row"><input type="file" id="xlsFile" accept=".xls,.XLS"><button class="btn btn--run" type="button" id="xlsGo">xls を取り込む</button></div>
      <textarea class="input" id="paste" placeholder="ここに貼り付け（ﾛｯﾄ番号 … BOX実績_作業終了_日付 の見出しを含めて）"></textarea>
      <div class="btn-row"><button class="btn btn--run" type="button" id="pasteGo">貼り付けた表を取り込む</button></div>
    </div></section>
    <section class="card"><header><h3>旧ブックからの移行</h3></header><div class="pad stack">
      <p class="note">旧ツール（.xlsm）の DATA シートの実績と、修正シート A 列の手動訂正（同じ行のロット番号に結び付けて）を取り込みます。
        すでにある手動訂正は上書きしません。</p>
      <div class="btn-row"><input class="input input--wide" id="wbPath" placeholder="例: C:\\Users\\…\\旧ブック.xlsm">
        <label class="note"><input type="checkbox" id="wbCond"> 設定シートの条件表も取り込む</label>
        <button class="btn btn--run" type="button" id="wbGo">取り込む</button></div>
    </div></section>
  </div>`;

  const after = async (r) => { toast(resultText(r), "ok"); await ctx.reloadState(); ctx.refresh(); };
  $("#imp").addEventListener("click", async () => {
    try { const r = await api.post("/api/import/source", { force: true }, "取り込んでいます…"); r.skipped ? toast(r.message, "info") : await after(r); }
    catch (e) { toastError(e); }
  });
  $("#rerun").addEventListener("click", () => document.getElementById("rneRun").click());
  $("#xlsGo").addEventListener("click", async () => {
    const f = $("#xlsFile").files[0];
    if (!f) { toast("xls を選んでください。", "warn"); return; }
    try { await after(await api.postBytes(`/api/import/xls?name=${encodeURIComponent(f.name)}`, await f.arrayBuffer(), "取り込んでいます…")); }
    catch (e) { toastError(e); }
  });
  $("#pasteGo").addEventListener("click", async () => {
    const text = $("#paste").value;
    if (!text.trim()) { toast("貼り付けてください。", "warn"); return; }
    try { await after(await api.post("/api/import/paste", { text }, "取り込んでいます…")); $("#paste").value = ""; }
    catch (e) { toastError(e); }
  });
  $("#wbGo").addEventListener("click", async () => {
    const path = $("#wbPath").value.trim();
    if (!path) { toast("旧ブックの場所を入れてください。", "warn"); return; }
    const withCond = $("#wbCond").checked;
    if (withCond && !(await confirmBox("旧ブックの設定シートで、いまの条件表を置き換えます（判定方式はいまのまま）。", { ok: "置き換える", danger: true }))) return;
    try {
      const r = await api.post("/api/import/workbook", { path, with_conditions: withCond }, "旧ブックを読んでいます…");
      toast(`${resultText(r)}。手動訂正 ${r.overrides} 件${r.conditions ? "・条件表" : ""}`, "ok");
      await ctx.reloadState(); ctx.refresh();
    } catch (e) { toastError(e); }
  });
}
