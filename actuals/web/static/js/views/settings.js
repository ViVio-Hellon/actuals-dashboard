/*
  設定（この PC の設定）

  「パスの設定」: 置き場所ごとに、入力欄・選ぶ…・開く・状態。入力を変えると保存の前に確かめる
  （ある・書ける・中身が合っている）。RNEConverter の KURAIRE が動ける状態かも出す。
  {DATA} はデータの場所（%USERPROFILE%\ActualsDashboard）。
  別のタブで先に保存されていたら保存しない（上書きで消さない）。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { pick } from "../picker.js";
import { $, esc } from "../util.js";

const STATE_TEXT = { ok: "OK", warn: "確認", missing: "なし", readonly: "書けない", timeout: "応答なし" };
const STATE_CLS = { ok: "ok", warn: "warn", missing: "ng", readonly: "ng", timeout: "ng" };

function stateCell(r) {
  if (!r) return '<span class="muted">確認中…</span>';
  return `<span class="st-dot ${STATE_CLS[r.state] || "warn"}">${esc(STATE_TEXT[r.state] || r.state)}</span>
          <div class="note">${esc(r.message)}</div>`;
}

export async function render(main, ctx, stale) {
  const v = await api.get("/api/settings", "読み込んでいます…");
  if (stale()) return;
  const pathFields = v.fields.filter((f) => f.kind === "dir" || f.kind.startsWith("file"));
  const otherFields = v.fields.filter((f) => !pathFields.includes(f));
  const val = (k) => v.values[k] ?? "";
  main.innerHTML = `
  <div class="view">
    <div class="view__head"><h2>設定</h2>
      <span class="note">この PC の設定です。{DATA} はデータの場所（${esc(v.data_dir)}）</span>
      <span class="grow"></span>
      <button class="btn" type="button" id="recheck">確かめる</button>
      <button class="btn btn--commit" type="button" id="save">保存</button></div>
    <section class="card"><header><h3>パスの設定</h3><span class="note">「選ぶ…」でフォルダを辿って選べます。\\\\サーバー\\共有 のように直接入力もできます。</span></header>
      <div class="pad pad--flush"><table class="table paths">
        <thead><tr><th class="t">項目</th><th class="t">場所</th><th></th><th class="t">状態</th></tr></thead>
        <tbody>${pathFields.map((f) => `<tr data-key="${f.key}">
          <th class="t">${esc(f.label)}<div class="note">${esc(f.note)}</div></th>
          <td class="t"><input class="input paths__input" data-key="${f.key}" value="${esc(val(f.key))}" spellcheck="false">
            <div class="note paths__resolved" data-res="${f.key}"></div></td>
          <td class="t nowrap"><button class="btn btn--sm" type="button" data-pick="${f.key}" data-kind="${esc(f.kind)}">選ぶ…</button>
            <button class="btn btn--sm" type="button" data-open="${f.key}" title="エクスプローラーで開く">開く</button></td>
          <td class="t paths__state" data-state="${f.key}">${stateCell(null)}</td></tr>`).join("")}</tbody></table></div></section>
    <section class="card" id="rneCard"><header><h3>RNEConverter（倉入実績の抽出）</h3><span class="note" id="rneMsg"></span></header>
      <div class="pad pad--flush"><table class="table"><tbody id="rneRows"></tbody></table></div></section>
    <section class="card"><header><h3>そのほか</h3></header><div class="pad pad--flush"><table class="table">
      ${otherFields.map((f) => `<tr><th class="t" style="width:32%">${esc(f.label)}${f.note ? `<div class="note">${esc(f.note)}</div>` : ""}</th>
        <td class="t">${f.kind === "bool" ? `<input type="checkbox" data-key="${f.key}" ${val(f.key) ? "checked" : ""}>`
          : `<input class="input input--wide" data-key="${f.key}" value="${esc(val(f.key))}">`}</td></tr>`).join("")}
    </table></div></section>
    <section class="card"><header><h3>いまの状態</h3></header><div class="pad">
      <dl class="facts">
        <dt>共有フォルダ</dt><dd>${esc(v.resolved.shared_dir)}${v.resolved.shared_configured ? "" : "（未設定のため、この PC の中）"}</dd>
        <dt>いま書いているログ</dt><dd>${esc(v.log_dir)}</dd>
        <dt>データの場所</dt><dd>${esc(v.data_dir)}</dd>
        <dt>設定の既定値</dt><dd>ツールのフォルダの config\\settings.json（ここで変えた項目だけ、データの場所の config\\settings.json に残ります）</dd>
      </dl></div></section>
  </div>`;

  const collect = () => {
    const values = {};
    for (const el of main.querySelectorAll("[data-key]")) {
      if (el.tagName !== "INPUT") continue;
      values[el.dataset.key] = el.type === "checkbox" ? el.checked : el.value;
    }
    return values;
  };
  let seq = 0;
  const check = async () => {
    const my = ++seq;
    for (const f of pathFields) {
      const r = v.resolved[f.key];
      const raw = main.querySelector(`input[data-key="${f.key}"]`).value;
      main.querySelector(`[data-res="${f.key}"]`).textContent = raw.includes("{") && typeof r === "string" && raw === val(f.key) ? `→ ${r}` : "";
    }
    try {
      const res = await api.post("/api/paths/check", { values: collect() });
      if (my !== seq) return;
      for (const [k, r] of Object.entries(res)) {
        const cell = main.querySelector(`[data-state="${k}"]`);
        if (cell) cell.innerHTML = stateCell(r);
      }
      const rr = res.rneconverter_dir;
      $("#rneMsg").textContent = rr.message;
      $("#rneRows").innerHTML = (rr.rows || []).map((x) => `<tr><th class="t" style="width:22%">${esc(x.label)}</th>
        <td class="t">${esc(x.value)}${x.note ? `<div class="note">${esc(x.note)}</div>` : ""}</td>
        <td class="t" style="width:12%"><span class="st-dot ${STATE_CLS[x.state] || "warn"}">${esc(STATE_TEXT[x.state] || x.state)}</span></td>
        <td class="t" style="width:18%">${x.suggest_source ? `<button class="btn btn--sm" type="button" data-suggest="${esc(x.suggest_source)}">取り込み元をこれにする</button>` : ""}</td></tr>`).join("")
        || `<tr><td class="t">${esc(rr.message)}</td></tr>`;
      for (const b of main.querySelectorAll("[data-suggest]")) {
        b.addEventListener("click", () => {
          main.querySelector('input[data-key="source_path"]').value = b.dataset.suggest;
          ctx.dirty = true; check();
        });
      }
    } catch (e) { toastError(e); }
  };
  let timer = null;
  for (const el of main.querySelectorAll("input[data-key]")) {
    el.addEventListener(el.type === "checkbox" ? "change" : "input", () => {
      ctx.dirty = true;
      clearTimeout(timer);
      timer = setTimeout(check, 600);
    });
  }
  for (const b of main.querySelectorAll("[data-pick]")) {
    b.addEventListener("click", async () => {
      const input = main.querySelector(`input[data-key="${b.dataset.pick}"]`);
      const kind = b.dataset.kind;
      const cur = v.resolved[b.dataset.pick];
      const start = input.value && !input.value.includes("{") ? input.value : (typeof cur === "string" ? cur : "");
      const chosen = await pick({ kind: kind === "dir" ? "dir" : "file", ext: kind.startsWith("file:") ? kind.slice(5) : "",
                                  start, title: main.querySelector(`tr[data-key="${b.dataset.pick}"] th`).firstChild.textContent });
      if (chosen) { input.value = chosen; ctx.dirty = true; check(); }
    });
  }
  for (const b of main.querySelectorAll("[data-open]")) {
    b.addEventListener("click", async () => {
      const input = main.querySelector(`input[data-key="${b.dataset.open}"]`);
      const path = input.value.includes("{") ? v.resolved[b.dataset.open] : input.value;
      try {
        const r = await api.post("/api/paths/open", { path });
        if (!r.opened) toast("開けませんでした（場所が無いか、Windows 以外です）。", "warn");
      } catch (e) { toastError(e); }
    });
  }
  $("#recheck").addEventListener("click", check);
  $("#save").addEventListener("click", async () => {
    try {
      await api.post("/api/settings", { values: collect(), revision: v.revision }, "保存しています…");
      ctx.dirty = false;
      toast("設定を保存しました。", "ok");
      await ctx.reloadState();
      ctx.refresh();
    } catch (e) { toastError(e); if (e.status === 409) { ctx.dirty = false; ctx.refresh(); } }
  });
  check();
}
