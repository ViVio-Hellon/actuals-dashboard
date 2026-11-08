/*
  picker.js — 「選ぶ…」の窓（フォルダ・ファイル）

  ブラウザはファイル選択でフルパスを渡せないので、サーバ（/api/fs）がフォルダの中を見せ、
  利用者が辿って選ぶ（RNEConverter の「選ぶ…」と同じ作り）。ネイティブのダイアログは使わない。
  上の欄に \\サーバー\共有 のようなパスを直接打って「開く」こともできる。
*/
import { api, qs } from "./api.js";
import { esc } from "./util.js";

let dlg = null;

function ensure() {
  if (dlg && document.body.contains(dlg)) return dlg;
  dlg = document.createElement("dialog");
  dlg.className = "askbox picker";
  dlg.innerHTML = `
    <div class="askbox__body picker__body">
      <h3 class="picker__title"></h3>
      <div class="picker__bar">
        <button class="btn btn--sm" type="button" data-up title="ひとつ上のフォルダ">↑ 上へ</button>
        <input class="input picker__path" spellcheck="false" autocomplete="off" aria-label="場所">
        <button class="btn btn--sm" type="button" data-go>開く</button>
      </div>
      <p class="note picker__hint"></p>
      <ul class="picker__list" role="listbox"></ul>
    </div>
    <div class="askbox__foot">
      <button class="btn" type="button" data-cancel>やめる</button>
      <button class="btn btn--commit" type="button" data-choose>このフォルダを選ぶ</button>
    </div>`;
  document.body.appendChild(dlg);
  return dlg;
}

/**
 * @param {{kind:"dir"|"file", ext?:string, start?:string, title?:string}} opt
 * @returns {Promise<string|null>} 選んだフルパス。やめたら null
 */
/** デスクトップ版（Tauri の窓の中）か。 */
export function isDesktop() {
  return Boolean(window.__TAURI__ && window.__TAURI__.core);
}

export function pick(opt) {
  // デスクトップ版は Windows の「フォルダーの選択」「ファイルを開く」の窓を使う（外枠 src-tauri の pick_path）
  if (isDesktop()) {
    return window.__TAURI__.core.invoke("pick_path", {
      kind: opt.kind === "dir" ? "dir" : "file", ext: opt.ext || "", start: opt.start || "", title: opt.title || "",
    }).then((p) => p || null).catch(() => pickInPage(opt));
  }
  return pickInPage(opt);
}

function pickInPage(opt) {
  const d = ensure();
  const st = { path: "", parent: null };
  const $ = (s) => d.querySelector(s);
  $(".picker__title").textContent = opt.title || (opt.kind === "dir" ? "フォルダを選ぶ" : "ファイルを選ぶ");
  $("[data-choose]").hidden = opt.kind !== "dir";
  $(".picker__hint").textContent = opt.kind === "dir"
    ? "フォルダを押すと中に入ります。目的のフォルダを開いたら「このフォルダを選ぶ」。"
    : `${opt.ext || ""} のファイルを押して選びます。`;

  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (done) return; done = true; if (d.open) d.close(); resolve(v); };
    const browse = async (path) => {
      const ul = $(".picker__list");
      ul.innerHTML = '<li class="muted">読み込み中…</li>';
      try {
        const r = await api.get(`/api/fs?${qs({ path, ext: opt.kind === "file" ? (opt.ext || "") : "" })}`);
        st.path = r.path; st.parent = r.parent;
        $(".picker__path").value = r.path;
        $("[data-up]").disabled = r.parent === null || r.parent === undefined;
        const join = (name) => r.path ? (r.path.endsWith("\\") || r.path.endsWith("/") ? r.path + name
                                         : r.path + (r.path.includes("\\") ? "\\" : "/") + name) : name;
        ul.innerHTML = r.dirs.map((n) => `<li><button type="button" class="picker__item" data-dir="${esc(join(n))}">📁 ${esc(n)}</button></li>`).join("")
          + r.files.map((n) => `<li><button type="button" class="picker__item picker__file" data-file="${esc(join(n))}">📄 ${esc(n)}</button></li>`).join("")
          || '<li class="muted">（空です）</li>';
        for (const b of ul.querySelectorAll("[data-dir]")) b.addEventListener("click", () => browse(b.dataset.dir));
        for (const b of ul.querySelectorAll("[data-file]")) b.addEventListener("click", () => finish(b.dataset.file));
      } catch (e) {
        ul.innerHTML = `<li class="problems">${esc(e.message)}</li>`;
        $(".picker__path").value = path;
      }
    };
    $("[data-up]").onclick = () => browse(st.parent || "");
    $("[data-go]").onclick = () => browse($(".picker__path").value);
    $(".picker__path").onkeydown = (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); browse($(".picker__path").value); } };
    $("[data-cancel]").onclick = () => finish(null);
    $("[data-choose]").onclick = () => { if (st.path) finish(st.path); };
    d.onclose = () => finish(null);
    d.showModal();
    browse(opt.start || "");
  });
}
