/*
  設定（この PC の設定。置き場所・取り込み元・RNEConverter・書き出し先・ログ）

  共有フォルダは全 PC で同じ場所を指定する（条件表・手動訂正・蓄積した実績は全員共通）。
  別のタブで先に保存されていたら保存しない（上書きで消さない）。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { $, esc } from "../util.js";

export async function render(main, ctx, stale) {
  const v = await api.get("/api/settings", "読み込んでいます…");
  if (stale()) return;
  const rows = v.fields.map((f) => {
    const val = v.values[f.key];
    const input = f.key === "auto_import"
      ? `<input type="checkbox" data-key="${f.key}" ${val ? "checked" : ""}>`
      : `<input class="input input--wide" data-key="${f.key}" value="${esc(val ?? "")}">`;
    const res = v.resolved[f.key];
    const showRes = typeof res === "string" && res !== (val ?? "") ? `<div class="note">→ ${esc(res)}</div>` : "";
    return `<tr><th style="text-align:left;white-space:normal;width:32%">${esc(f.label)}${f.note ? `<div class="note">${esc(f.note)}</div>` : ""}</th>
      <td style="text-align:left">${input}${showRes}</td></tr>`;
  }).join("");
  main.innerHTML = `
  <div class="view">
    <div class="view__head"><h2>設定</h2><span class="note">この PC の設定です。{DATA} はデータの場所（${esc(v.data_dir)}）</span>
      <span class="grow"></span><button class="btn btn--commit" type="button" id="save">保存</button></div>
    <section class="card"><div class="pad pad--flush"><table class="table">${rows}</table></div></section>
    <section class="card"><header><h3>いまの状態</h3></header><div class="pad">
      <dl class="facts">
        <dt>共有フォルダ</dt><dd>${esc(v.resolved.shared_dir)}${v.resolved.shared_configured ? "" : "（未設定のため、この PC の中）"}</dd>
        <dt>ログ</dt><dd>${esc(v.log_dir)}</dd>
        <dt>データの場所</dt><dd>${esc(v.data_dir)}</dd>
      </dl></div></section>
  </div>`;
  $("#save").addEventListener("click", async () => {
    const values = {};
    for (const el of main.querySelectorAll("[data-key]")) values[el.dataset.key] = el.type === "checkbox" ? el.checked : el.value;
    try {
      await api.post("/api/settings", { values, revision: v.revision }, "保存しています…");
      toast("設定を保存しました。", "ok");
      await ctx.reloadState();
      ctx.refresh();
    } catch (e) { toastError(e); if (e.status === 409) ctx.refresh(); }
  });
}
