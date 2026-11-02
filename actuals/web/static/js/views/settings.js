/*
  設定（この PC の設定）

  「パスの設定」はデータの流れの順に並べる:
    ① データ更新   RNE ファイル → SymNavi（このツールが動かす）→ 取り出した倉入実績
    ② 集計データの置き場所   共有フォルダ（蓄積した実績・条件表・手動訂正）
    ③ 書き出しと記録   CSV・ログ
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
    <span class="flowline__arrow">→ SymNavi で実行<br>（ID・パスワード）→</span>
    <span class="flowline__box"><b>当月の倉入実績</b><small>そのまま取り込む</small></span>
    <span class="flowline__arrow">→</span>
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
      <td class="t"><input class="input paths__input" data-key="${f.key}" value="${esc(val(f.key))}" spellcheck="false"
           ${f.kind === "number" ? 'inputmode="numeric" style="max-width:8em"' : ""}
           ${f.key === "log_dir" ? `placeholder="空欄 = この PC の logs（例 \\\\サーバー\\共有\\logs）"` : ""}>
           <div class="note" data-res="${f.key}"></div></td>
      <td class="t nowrap">${isPath(f) ? `<button class="btn btn--sm" type="button" data-pick="${f.key}" data-kind="${esc(f.kind)}">選ぶ…</button>
          <button class="btn btn--sm" type="button" data-open="${f.key}" title="エクスプローラーで開く">開く</button>` : ""}</td>
      <td class="t paths__state" data-state="${f.key}">${f.kind === "number" ? "" : stateCell(null)}</td></tr>`;
  const section = (g) => {
    const fs = v.fields.filter((f) => f.group === g);
    let n = 0;
    const rows = fs.map((f) => row(f, `${{ extract: "①", store: "②", output: "③", advanced: "" }[g]}${fs.length > 1 && g !== "advanced" ? `-${++n}` : ""}`)).join("");
    const extra = g === "extract" ? `
      <tr data-key="credentials"><th class="t"><span class="paths__no">①-${fs.length + 1}</span>SymNavi のログイン
          <div class="note">SymNavi に入る ID とパスワード（旧ブックではフォームに書かれていたもの）。この PC にだけ保存し、画面やログには出しません。</div></th>
        <td class="t"><div class="formrow">
            <input class="input" id="credId" value="${esc(v.credentials.id)}" placeholder="ID" autocomplete="off" spellcheck="false">
            <input class="input" id="credPass" type="password" placeholder="${v.credentials.has_password ? "（登録済み。変える時だけ入力）" : "パスワード"}" autocomplete="new-password">
            <button class="btn btn--sm btn--commit" type="button" id="credSave">登録</button></div></td>
        <td></td><td class="t paths__state" data-state="credentials">${stateCell(null)}</td></tr>` : "";
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
    ${section("store")}
    ${section("output")}
    <details class="card"><summary class="pad"><b>${esc(v.groups.advanced)}</b></summary>
      <div class="pad pad--flush"><table class="table paths"><tbody>${v.fields.filter((f) => f.group === "advanced").map((f) => row(f, "")).join("")}</tbody></table></div></details>
    <section class="card"><header><h3>いまの状態</h3></header><div class="pad">
      <dl class="facts">
        <dt>いま使っている共有フォルダ</dt><dd>${esc(v.resolved.shared_dir)}${v.resolved.shared_configured ? "" : "（未設定のため、この PC の中）"}</dd>
        <dt>いま書いているログ</dt><dd>${esc(v.log.dir)}\\${esc(v.log.text_file)}<br>
          <span class="note">出来事（起動・終了・処理時間・読込・保存・警告・エラー・操作）: ${esc(v.log.event_file)}。
          3 秒以上かかった処理は WARNING、エラーはエラー番号つきで残ります。</span>
          ${v.log.problem ? `<div class="note" style="color:var(--act-danger)">${esc(v.log.problem)}</div>` : ""}</dd>
        ${v.log.legacy.length ? `<dt>前の形のログ</dt><dd>${v.log.legacy.map(esc).join("<br>")}
          <span class="note">（v0.1.0 まで。移さず・もう書きません。古いものの片付けだけ同じに行います）</span></dd>` : ""}
        <dt>設定の既定値</dt><dd>ツールのフォルダの config\\settings.json（ここで変えた項目だけ、データの場所の config\\settings.json に残ります。全 PC に同じ既定を配る時はツール側を書き換えて配ります）</dd>
      </dl></div></section>
  </div>`;

  const collect = () => {
    const values = {};
    for (const el of main.querySelectorAll("input[data-key]")) values[el.dataset.key] = el.value;
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
        if (cell) cell.innerHTML = stateCell(r);
      }
    } catch (e) { toastError(e); }
  };
  let timer = null;
  for (const el of main.querySelectorAll("input[data-key]")) {
    el.addEventListener("input", () => {
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
      const key = b.dataset.open;
      const input = main.querySelector(`input[data-key="${key}"]`);
      const path = key === "log_dir" ? input.value : (input.value.includes("{") ? v.resolved[key] : input.value);
      try {
        const r = await api.post("/api/paths/open", { path, key });
        if (!r.opened) toast("開けませんでした（場所が無いか、Windows 以外です）。", "warn");
      } catch (e) { toastError(e); }
    });
  }
  $("#recheck").addEventListener("click", check);
  $("#credSave").addEventListener("click", async () => {
    try {
      const r = await api.post("/api/credentials", { id: $("#credId").value, password: $("#credPass").value });
      $("#credPass").value = "";
      $("#credPass").placeholder = "（登録済み。変える時だけ入力）";
      toast(`SymNavi のログインを登録しました（ID ${r.id}）。`, "ok");
      await ctx.reloadState();
      check();
    } catch (e) { toastError(e); }
  });
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
