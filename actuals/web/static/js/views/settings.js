/*
  設定（この PC の設定）

  「パスの設定」はデータの流れの順に並べる:
    ① データ更新   RNE ファイル → RNEConverter（SymNavi）→ 置くフォルダ\名前.sqlite3 … ここから取り込む
    ② 集計データの置き場所   共有フォルダ（蓄積した実績・条件表・手動訂正）
    ③ 書き出しと記録   CSV・ログ
  取り込むファイルは入力しない（①の置くフォルダ + 名前 から決まる）。
  入力を変えると、保存の前に確かめる（ある・書ける・中身が合っている）。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { pick } from "../picker.js";
import { $, esc } from "../util.js";

const STATE_TEXT = { ok: "OK", warn: "確認", missing: "要設定", readonly: "書けない", timeout: "応答なし" };
const STATE_CLS = { ok: "ok", warn: "warn", missing: "ng", readonly: "ng", timeout: "ng" };

function stateCell(r) {
  if (!r) return '<span class="muted">確認中…</span>';
  return `<span class="st-dot ${STATE_CLS[r.state] || "warn"}">${esc(STATE_TEXT[r.state] || r.state)}</span>
          <div class="note">${esc(r.message)}</div>`;
}

const FLOW = `
  <div class="flowline" aria-label="データの流れ">
    <span class="flowline__box"><b>RNE ファイル</b><small>梱包実績(当月_倉入れ日付順).RNE</small></span>
    <span class="flowline__arrow">→ SymNavi<br>（RNEConverter）→</span>
    <span class="flowline__box"><b>作ったファイル</b><small>置くフォルダ \\ 名前.sqlite3</small></span>
    <span class="flowline__arrow">→ 取り込み →</span>
    <span class="flowline__box"><b>共有フォルダ</b><small>月ごとに蓄積・条件表・手動訂正</small></span>
    <span class="flowline__arrow">→</span>
    <span class="flowline__box"><b>集計画面・CSV</b><small>CSV は書き出し先へ</small></span>
  </div>`;

export async function render(main, ctx, stale) {
  const v = await api.get("/api/settings", "読み込んでいます…");
  if (stale()) return;
  const isPath = (f) => f.kind === "dir" || f.kind.startsWith("file");
  const val = (k) => v.values[k] ?? "";
  const row = (f, i) => `<tr data-key="${f.key}">
      <th class="t"><span class="paths__no">${i}</span>${esc(f.label)}<div class="note">${esc(f.note)}</div></th>
      <td class="t">${f.kind === "bool"
        ? `<label><input type="checkbox" data-key="${f.key}" ${val(f.key) ? "checked" : ""}> する</label>`
        : `<input class="input paths__input" data-key="${f.key}" value="${esc(val(f.key))}" spellcheck="false">
           <div class="note" data-res="${f.key}"></div>`}</td>
      <td class="t nowrap">${isPath(f) ? `<button class="btn btn--sm" type="button" data-pick="${f.key}" data-kind="${esc(f.kind)}">選ぶ…</button>
          <button class="btn btn--sm" type="button" data-open="${f.key}" title="エクスプローラーで開く">開く</button>` : ""}</td>
      <td class="t paths__state" data-state="${f.key}">${f.kind === "bool" ? "" : stateCell(null)}</td></tr>`;
  const section = (g) => {
    const fs = v.fields.filter((f) => f.group === g);
    let n = 0;
    const rows = fs.map((f) => row(f, `${{ extract: "①", store: "②", output: "③", advanced: "" }[g]}${fs.length > 1 && g !== "advanced" ? `-${++n}` : ""}`)).join("");
    const extra = g === "extract" ? `
      <tr class="paths__derived"><th class="t">→ 取り込むファイル<div class="note">①-2 と ①-3 から決まります。このツールはこのファイルを取り込みます。</div></th>
        <td class="t mono" id="srcPath"></td><td></td><td class="t paths__state" data-state="source">${stateCell(null)}</td></tr>` : "";
    return `<section class="card"><header><h3>${esc(v.groups[g])}</h3></header>
      <div class="pad pad--flush"><table class="table paths">
        <thead><tr><th class="t">何に使うか</th><th class="t">場所</th><th></th><th class="t">状態</th></tr></thead>
        <tbody>${rows}${extra}</tbody></table></div></section>`;
  };
  main.innerHTML = `
  <div class="view">
    <div class="view__head"><h2>パスの設定</h2>
      <span class="note">この PC の設定です。{DATA} はデータの場所（${esc(v.data_dir)}）</span>
      <span class="grow"></span>
      <button class="btn" type="button" id="recheck">確かめる</button>
      <button class="btn btn--commit" type="button" id="save">保存</button></div>
    <section class="card"><div class="pad">${FLOW}
      <p class="note">「選ぶ…」でフォルダを辿って選べます（\\\\サーバー\\共有 のように直接入力もできます）。入力を変えると右の「状態」がすぐに変わります。保存するまで反映されません。</p></div></section>
    ${section("extract")}
    <section class="card" id="rneCard"><header><h3>RNEConverter の状態</h3><span class="note" id="rneMsg"></span></header>
      <div class="pad pad--flush"><table class="table"><tbody id="rneRows"></tbody></table></div></section>
    ${section("store")}
    ${section("output")}
    <details class="card"><summary class="pad"><b>${esc(v.groups.advanced)}</b></summary>
      <div class="pad pad--flush"><table class="table paths"><tbody>${v.fields.filter((f) => f.group === "advanced").map((f) => row(f, "")).join("")}</tbody></table></div></details>
    <section class="card"><header><h3>いまの状態</h3></header><div class="pad">
      <dl class="facts">
        <dt>いま使っている共有フォルダ</dt><dd>${esc(v.resolved.shared_dir)}${v.resolved.shared_configured ? "" : "（未設定のため、この PC の中）"}</dd>
        <dt>いま取り込んでいるファイル</dt><dd>${esc(v.resolved.source_path || "（未設定）")}</dd>
        <dt>いま書いているログ</dt><dd>${esc(v.log_dir)}</dd>
        <dt>設定の既定値</dt><dd>ツールのフォルダの config\\settings.json（ここで変えた項目だけ、データの場所の config\\settings.json に残ります。全 PC に同じ既定を配る時はツール側を書き換えて配ります）</dd>
      </dl></div></section>
  </div>`;

  const collect = () => {
    const values = {};
    for (const el of main.querySelectorAll("input[data-key]")) values[el.dataset.key] = el.type === "checkbox" ? el.checked : el.value;
    return values;
  };
  let seq = 0;
  const check = async () => {
    const my = ++seq;
    for (const f of v.fields.filter(isPath)) {
      const raw = main.querySelector(`input[data-key="${f.key}"]`).value;
      const r = v.resolved[f.key];
      main.querySelector(`[data-res="${f.key}"]`).textContent = raw.includes("{") && raw === val(f.key) && typeof r === "string" ? `→ ${r}` : "";
    }
    try {
      const res = await api.post("/api/paths/check", { values: collect() });
      if (my !== seq) return;
      for (const [k, r] of Object.entries(res)) {
        const cell = main.querySelector(`[data-state="${k}"]`);
        if (cell && k !== "rneconverter_dir") cell.innerHTML = stateCell(r);
      }
      main.querySelector('[data-state="rneconverter_dir"]').innerHTML = stateCell(res.rneconverter_dir);
      $("#srcPath").textContent = res.source.path || "（未定）";
      const rr = res.rneconverter_dir;
      $("#rneMsg").textContent = rr.message;
      $("#rneRows").innerHTML = (rr.rows || []).map((x) => `<tr><th class="t" style="width:24%">${esc(x.label)}</th>
        <td class="t">${esc(x.value)}${x.note ? `<div class="note">${esc(x.note)}</div>` : ""}</td>
        <td class="t" style="width:14%"><span class="st-dot ${STATE_CLS[x.state] || "warn"}">${esc(STATE_TEXT[x.state] || x.state)}</span></td></tr>`).join("")
        || `<tr><td class="t">${esc(rr.message)}</td></tr>`;
    } catch (e) { toastError(e); }
  };
  let timer = null;
  for (const el of main.querySelectorAll("input[data-key]")) {
    el.addEventListener(el.type === "checkbox" ? "change" : "input", () => {
      ctx.dirty = true;
      clearTimeout(timer);
      timer = setTimeout(check, 500);
    });
  }
  for (const b of main.querySelectorAll("[data-pick]")) {
    b.addEventListener("click", async () => {
      const key = b.dataset.pick;
      const input = main.querySelector(`input[data-key="${key}"]`);
      const kind = b.dataset.kind;
      const cur = v.resolved[key];
      const start = input.value && !input.value.includes("{") ? input.value : (typeof cur === "string" ? cur : "");
      const label = v.fields.find((f) => f.key === key).label;
      const chosen = await pick({ kind: kind === "dir" ? "dir" : "file", ext: kind.startsWith("file:") ? kind.slice(5) : "",
                                  start, title: label });
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
