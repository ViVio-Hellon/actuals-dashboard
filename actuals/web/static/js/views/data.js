/*
  データ（取り込みと蓄積）

  通常: 「データ更新」で SymNavi が RNE を実行し、当月の倉入実績を取り出してそのまま取り込む（旧 データ作成）。
  予備: SymNavi から出した xls／Excel からコピーした表の貼付け（旧 手作業の手順 UserForm02〜10）／
        RNEConverter などが作った .sqlite3。
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
  const rne = st.rne;
  const kinds = { symnavi: "データ更新（SymNavi）", sqlite: "SQLite", source: "RNEConverter", paste: "貼付け", xls: "xls", xlsm: "旧ブック", move: "この PC から移した" };
  const runState = rne.running ? `<span class="st-dot warn">実行中 ${rne.progress}%（${rne.elapsed} 秒）</span>`
    : rne.ok === true ? `<span class="st-dot ok">前回は成功（${esc(rne.step)}）</span>`
    : rne.ok === false ? `<span class="st-dot ng">前回は失敗: ${esc(rne.message)}</span>` : '<span class="muted">まだ実行していません</span>';
  main.innerHTML = `
  <div class="view">
    <div class="view__head"><h2>データ</h2><span class="note">蓄積しているロット ${n0(h.lots)} 件</span></div>
    ${h.leftover ? `
    <section class="card card--warn" aria-label="この PC に貯めた分"><header><h3>この PC に貯めた分を共有フォルダへ移す</h3></header><div class="pad stack">
      <p>共有フォルダを決める前に、この PC の中（${esc(h.leftover.dir)}）に貯めた分があります。共有フォルダへ移すと、ほかの PC でも見えます。</p>
      <dl class="facts">
        <dt>実績</dt><dd>${n0(h.leftover.lots)} ロット（うち共有に無いもの ${n0(h.leftover.new_lots)}）${h.leftover.months.length ? `・${esc(h.leftover.months[0])}〜${esc(h.leftover.months[h.leftover.months.length - 1])}` : ""}</dd>
        <dt>手動訂正</dt><dd>${n0(h.leftover.overrides)} 件</dd>
        <dt>条件表</dt><dd>${h.leftover.conditions ? `この PC で保存した条件表あり（${esc(h.leftover.conditions_by)}）` : "この PC では変えていない"}</dd>
      </dl>
      <p class="note">共有にある物は上書きしません（同じロット・同じロットの手動訂正は共有のまま。条件表は共有がまだ初期値の時だけ写します）。
        移した後、この PC の中の物は「…_移した_日時」の名前で残します（消しません）。</p>
      <div class="btn-row"><button class="btn btn--run" type="button" id="moveLocal">共有フォルダへ移す</button></div>
    </div></section>` : ""}
    <div class="month-top">
      <section class="card"><header><h3>データ更新（SymNavi から取り出す）</h3></header><div class="pad stack">
        <dl class="facts">
          <dt>RNE ファイル</dt><dd>${esc(rne.rne_name || "（未設定。パスの設定 ①で選んでください）")}</dd>
          <dt>状態</dt><dd>${runState}</dd>
          ${rne.running ? `<dt>いま</dt><dd>${esc(rne.step)}</dd>` : ""}
        </dl>
        <div class="btn-row">
          <button class="btn btn--run" type="button" id="rerun" ${rne.running ? "disabled" : ""}>データ更新</button>
          ${rne.running ? '<button class="btn btn--danger" type="button" id="cancelRun">中断</button>' : ""}
        </div>
        <p class="note">SymNavi が起動し、RNE を実行して当月の倉入実績を取り出します。終わると共有の蓄積に取り込みます（ほかの PC にもすぐ反映）。</p>
        ${rne.lines && rne.lines.length ? `<div class="loglines">${esc(rne.lines.join("\n"))}</div>` : ""}
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
    <section class="card"><header><h3>データ更新が使えない時（手作業の取り込み）</h3></header><div class="pad stack">
      <p class="note">SymNavi で RNE を開いて保存した xls を選ぶか、SymNavi の結果を Excel に出して表全体（見出しの行ごと）をコピーし、下に貼り付けてください。</p>
      <div class="btn-row"><input type="file" id="xlsFile" accept=".xls,.XLS"><button class="btn btn--run" type="button" id="xlsGo">xls を取り込む</button></div>
      <textarea class="input" id="paste" placeholder="ここに貼り付け（ﾛｯﾄ番号 … BOX実績_作業終了_日付 の見出しを含めて）"></textarea>
      <div class="btn-row"><button class="btn btn--run" type="button" id="pasteGo">貼り付けた表を取り込む</button></div>
      <p class="note">RNEConverter などで作った倉入実績の .sqlite3 がある時は、その場所を入れて取り込めます。</p>
      <div class="btn-row"><input class="input input--wide" id="sqlPath" placeholder="例: \\\\サーバー\\共有\\KURAIRE.sqlite3">
        <button class="btn btn--run" type="button" id="sqlGo">.sqlite3 を取り込む</button></div>
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
  const cancel = document.getElementById("cancelRun");
  if (cancel) cancel.addEventListener("click", async () => {
    if (!(await confirmBox("データ更新を中断します（SymNavi を閉じます）。", { ok: "中断", danger: true }))) return;
    try { await api.post("/api/rne/cancel", {}); toast("中断しました。", "warn"); ctx.refresh(); } catch (e) { toastError(e); }
  });
  if (rne.running) setTimeout(() => { if (location.hash === "#data") ctx.refresh(); }, 2000);
  $("#sqlGo").addEventListener("click", async () => {
    const path = $("#sqlPath").value.trim();
    if (!path) { toast("ファイルの場所を入れてください。", "warn"); return; }
    try { await after(await api.post("/api/import/sqlite", { path }, "取り込んでいます…")); } catch (e) { toastError(e); }
  });
  if (h.leftover) {
    $("#moveLocal").addEventListener("click", async () => {
      const ok = await confirmBox(`この PC に貯めた実績 ${n0(h.leftover.lots)} ロット・手動訂正 ${n0(h.leftover.overrides)} 件を、共有フォルダへ移します。共有にある物は上書きしません。よろしいですか？`,
                                  { ok: "移す" });
      if (!ok) return;
      try {
        const r = await api.post("/api/local/move", {}, "移しています…");
        const cond = { copied: "条件表も写しました", kept: "条件表は共有のまま（共有で保存済みのため）", same: "条件表は同じでした", none: "" }[r.conditions] || "";
        toast(`移しました: 実績 追加 ${n0(r.lots.added)}・共有のまま ${n0(r.lots.kept)}、手動訂正 追加 ${n0(r.overrides_added)}・共有のまま ${n0(r.overrides_kept)}${cond ? `。${cond}` : ""}`, "ok");
        await ctx.reloadState();
        ctx.refresh();
      } catch (e) { toastError(e); }
    });
  }
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
