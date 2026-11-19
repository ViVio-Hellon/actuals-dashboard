/*
  条件表（旧 設定シート 5〜19 行）── 左にグループの一覧（見る順番）、右に選んだグループの条件

  決まり方（旧ツールと同じ。結果も同じ）:
    1. 一覧の上から順に見て、最初に当てはまったグループに入る
    2. VC 付きのロットは、「VC 付きを優先」のグループに当てはまれば、そちらに入る（下にあっても）
    3. 「除外する範囲」に入るロットは、そのグループには入らない（次のグループへ）
  入力: 空欄は「条件なし」。範囲は下限〜上限（両端を含む。板丈の下限だけ「より大きい」も選べる）。
  見る順番はグループの番号（手動訂正・集計表の番号）とは別。並べ替えても番号は変わらない。

  保存の形は旧シートと同じ（* = 条件なし、空欄 = 0）。画面で触った所だけを書き換え、触らない所は元の値のまま送る。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";
import { $, esc, kg, n0, ymLabel } from "../util.js";

let draft = null;
let selected = 1;

// lo/hi は旧シートの列（以上・以下）、gt/lt は足した比べ方（より大きい・未満）
const RANGES = [
  { label: "板厚", unit: "mm", lo: "t_min", hi: "t_max", lt: "t_lt" },
  { label: "板幅", unit: "mm", lo: "w_min", hi: "w_max", lt: "w_lt" },
  { label: "板丈", unit: "mm", lo: "l_min", gt: "l_gt", hi: "l_max", lt: "l_lt" },
  { label: "単重", unit: "kg", lo: "wt_min", hi: "wt_max", lt: "wt_lt" },
];
const NEW_KEYS = new Set(["mmat", "wt_min", "t_lt", "w_lt", "l_lt", "wt_lt"]);

const isStar = (v) => v === "*";
const isBlank = (v) => v === null || v === undefined || v === "";
const named = (r) => !isBlank(r.name);

/** 保存の値 → 入力欄。* は空欄（条件なし）。旧シートの空欄（0 として比べる）は 0 と見せる（結果を変えない）。 */
function toInput(v, blankAsZero = true) {
  if (isStar(v)) return "";
  if (isBlank(v)) return blankAsZero ? "0" : "";
  return String(v);
}

/** 入力欄 → 保存の値。空欄は * （条件なし）。 */
function fromInput(s) {
  const t = String(s).trim();
  return t === "" ? "*" : t;
}

function num(v) {
  if (isStar(v) || isBlank(v)) return isBlank(v) ? 0 : null;
  const n = Number(String(v).replace(/[０-９．]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)));
  return Number.isFinite(n) ? n : null;
}

/** 下限の値と比べ方（板丈は「以上」と「より大きい」の 2 つの欄を 1 つに見せる） */
function lowerOf(r, rg, p = "") {
  if (rg.gt && !isStar(r[p + rg.gt]) && !isBlank(r[p + rg.gt]) && (isStar(r[p + rg.lo]) || isBlank(r[p + rg.lo]))) {
    return { v: r[p + rg.gt], op: "gt" };
  }
  return { v: rg.lo ? r[p + rg.lo] : "*", op: "ge" };
}

/** 上限の値と比べ方（以下 / 未満） */
function upperOf(r, rg, p = "") {
  const lt = r[p + rg.lt];
  if (rg.lt && !isStar(lt) && !isBlank(lt) && (isStar(r[p + rg.hi]) || isBlank(r[p + rg.hi]))) return { v: lt, op: "lt" };
  return { v: r[p + rg.hi], op: "le" };
}

/** 1 グループの条件を文で（一覧とカードの見出し） */
function sentence(r, p = "") {
  const parts = [];
  const mat = r[p + "mat"];
  parts.push(isStar(mat) ? "材質どれでも" : `材質 ${isBlank(mat) ? "（空欄）" : mat}`);
  const mm = r[p + "mmat"];
  if (!isStar(mm) && !isBlank(mm)) parts.push(`製造材質 ${mm}`);
  for (const rg of RANGES) {
    const lo = lowerOf(r, rg, p);
    const up = upperOf(r, rg, p);
    const zl = !(rg.lo && NEW_KEYS.has(rg.lo));
    const l = isStar(lo.v) || (isBlank(lo.v) && !zl) ? "" : `${toInput(lo.v)}${lo.op === "gt" ? "超" : ""}`;
    const h = isStar(up.v) ? "" : `${toInput(up.v)}${up.op === "lt" ? "未満" : ""}`;
    if (l || h) parts.push(`${rg.label} ${l}〜${h}`);
  }
  return parts.join("・");
}

/** その場で分かる入れ方の誤り（保存は止めない。旧シートでも入れられた形なので） */
function rowWarnings(r, p = "") {
  const out = [];
  for (const rg of RANGES) {
    const lo = lowerOf(r, rg, p);
    const up = upperOf(r, rg, p);
    const a = num(lo.v), b = num(up.v);
    if (a !== null && b !== null && !isStar(lo.v) && !isStar(up.v) && !isBlank(lo.v) && (a > b || (a === b && up.op === "lt"))) {
      out.push(`${p ? "除外の" : ""}${rg.label}の下限（${a}）が上限（${b}）より大きいので、${p ? "何も除外しません" : "このグループには入りません"}。`);
    }
  }
  if (!p) {
    for (const rg of RANGES) {
      if (isBlank(r[rg.hi]) && upperOf(r, rg).op === "le" && named(r)) out.push(`${rg.label}の上限が 0（旧シートの空欄）です。このグループにはほぼ入りません。条件なしにするなら空欄にしてください。`);
    }
  }
  return out;
}

export async function render(main, ctx, stale) {
  const { y, m, cal } = ctx.nav;
  const v = await api.get(`/api/conditions?y=${y || 0}&m=${m || 0}&cal=${cal || "genba"}`, "読み込んでいます…");
  if (stale()) return;
  const cs = v.conditions;
  draft = JSON.parse(JSON.stringify(cs));
  let after = null;              // 影響を確認した後の件数
  if (!draft.rows[selected - 1] || !named(draft.rows[selected - 1])) selected = draft.order.find((g) => named(draft.rows[g - 1])) || 1;

  const radio = (name, opts, cur) => Object.entries(opts).map(([k, label]) =>
    `<label><input type="radio" name="${name}" value="${k}" ${k === cur ? "checked" : ""}>${esc(label)}</label>`).join("");
  const sel = (id, cur) => `<select class="input" id="${id}">${["製造", "ｵｰﾀﾞｰ"].map((b) => `<option ${b === cur ? "selected" : ""}>${b}</option>`).join("")}</select>`;

  main.innerHTML = `
  <div class="view">
    <div class="view__head">
      <h2>条件表</h2>
      <span class="note">版 ${cs.rev}・${esc(cs.updated_at)} ${esc(cs.updated_by)}（全 PC 共通）</span>
      <span class="grow"></span>
      <button class="btn" type="button" id="preview">影響を確認（${ymLabel(y, m)}）</button>
      <button class="btn" type="button" id="exportCond" title="条件表を「パスの設定」の出力先に .sqlite3 で書く（保存するたびにも書く）">出力（.sqlite3）</button>
      <button class="btn btn--commit" type="button" id="saveCond">保存</button>
    </div>
    <section class="card rules-card"><div class="pad">
      <b>グループの決まり方</b>
      <ol class="rules">
        <li>左の一覧の<b>上から順に</b>見て、<b>最初に当てはまったグループ</b>に入ります。順番は ▲▼ で変えられます（グループの番号は変わりません）。</li>
        <li><b>VC 付きのロット</b>は、「VC 付きを優先」のグループに当てはまれば、下にあってもそちらに入ります。</li>
        <li>「除外する範囲」に入るロットは、そのグループには入らず、次のグループを見ます。</li>
      </ol>
      <p class="note">入力欄が空欄なら「条件なし」です。範囲は下限・上限の値も含みます。</p>
    </div></section>
    <div class="cond-layout">
      <section class="card" aria-label="グループの一覧">
        <header><h3>グループ（見る順番）</h3><span class="note">${ymLabel(y, m)} の件数</span></header>
        <div class="pad pad--flush"><ol class="cond-list" id="condList"></ol></div>
      </section>
      <section class="card" aria-label="条件"><div id="editor"></div></section>
    </div>
    <section class="card"><header><h3>ロットで試す</h3><span class="note">保存する前の条件表で、そのロットがどこに入るか・なぜかを見ます</span></header>
      <div class="pad stack"><div class="formrow"><input class="input" id="tryLot" placeholder="ロット番号" spellcheck="false" style="max-width:16em">
        <button class="btn btn--sm" type="button" id="tryGo">試す</button></div><div id="tryOut"></div></div></section>
    <div id="previewOut"></div>
    <details class="card"><summary class="pad"><b>詳しい設定（ふつうは変えない）</b></summary><div class="pad stack">
      <div class="formrow">
        <label>条件の寸法・材質 ${sel("basisMain", cs.basis_main)}</label>
        <label>除外の寸法・材質 ${sel("basisNot", cs.basis_not)}</label>
        <span class="note">単重はどちらの場合も計算単重（製造寸法から計算）で比べます。</span>
      </div>
      <div class="formrow"><b>除外の材質</b>${radio("ruleMat", v.rule_not_material, cs.rule_not_material)}</div>
      <div class="formrow"><b>除外に当たったら</b>${radio("ruleHit", v.rule_not_hit, cs.rule_not_hit)}</div>
      <p class="note">旧 VBA と同じ判定に戻すのは、旧ツールと突き合わせる時だけにしてください（旧 VBA の誤りまで戻ります）。</p>
      <div class="formrow"><button class="btn btn--sm" type="button" id="useSpec">設備仕様の条件を入れる（${Object.keys(v.equipment || {}).map((g) => esc(v.equipment[g].name)).join("・")}）</button>
        <span class="note">梱包のデータベースの「設備仕様」表の値を、その番号のグループに入れます（保存はまだしません。「影響を確認」してから保存）。</span></div>
    </div></details>
    <section class="card"><header><h3>保存の記録</h3></header><div class="pad pad--flush"><div class="tablewrap tablewrap--mid">
      <table class="table table--dense"><thead><tr><th>日時</th><th>保存した人</th><th>版</th><th>メモ</th></tr></thead>
      <tbody>${v.audit.map((a) => `<tr><td>${esc(a.at)}</td><td>${esc(a.by)}</td><td>${esc(a.target)}</td><td style="text-align:left">${esc(a.note)}</td></tr>`).join("")
        || '<tr><td colspan="4">ありません</td></tr>'}</tbody></table></div></div></section>
  </div>`;

  const changed = () => { ctx.dirty = true; };

  function count(g) {
    const c = (v.counts || {})[String(g)];
    const a = after ? (after[String(g)] || { count: 0, weight: 0 }) : null;
    const now = c ? `${n0(c.count)} 件` : "0 件";
    return a && (a.count !== (c ? c.count : 0)) ? `${now} → <b>${n0(a.count)} 件</b>` : now;
  }

  function drawList() {
    const used = draft.order.filter((g) => named(draft.rows[g - 1]));
    const unused = draft.order.filter((g) => !named(draft.rows[g - 1]));
    const item = (g, rank) => {
      const r = draft.rows[g - 1];
      const warn = rowWarnings(r).length + (r.not_use === "*" ? rowWarnings(r, "not_").length : 0);
      return `<li class="cond-item${g === selected ? " is-sel" : ""}" data-g="${g}" tabindex="0">
        <span class="cond-rank">${rank}</span>
        <span class="cond-main"><span class="cond-name">${esc(r.name || "（名前なし）")}<small>番号 ${g}</small>
          ${r.vc === "*" ? '<span class="tag tag--vc">VC 優先</span>' : ""}${r.not_use === "*" ? '<span class="tag">除外あり</span>' : ""}
          ${warn ? '<span class="tag tag--warn">確認</span>' : ""}</span>
          <span class="cond-sum">${esc(sentence(r))}</span></span>
        <span class="cond-count">${count(g)}</span>
        <span class="cond-move"><button class="btn btn--xs" type="button" data-up="${g}" title="上へ（先に見る）" ${rank === 1 ? "disabled" : ""}>▲</button>
          <button class="btn btn--xs" type="button" data-down="${g}" title="下へ（後で見る）" ${rank === used.length ? "disabled" : ""}>▼</button></span></li>`;
    };
    const unc = (v.counts || {})["0"];
    $("#condList").innerHTML = used.map((g, i) => item(g, i + 1)).join("")
      + `<li class="cond-item cond-item--none"><span class="cond-rank">—</span><span class="cond-main"><span class="cond-name">どれにも当てはまらない（未分類）</span></span>
          <span class="cond-count">${unc ? n0(unc.count) : 0} 件${after && (after["0"] || { count: 0 }).count !== (unc ? unc.count : 0) ? ` → <b>${n0((after["0"] || { count: 0 }).count)} 件</b>` : ""}</span><span></span></li>`
      + (unused.length ? `<li class="cond-sep">使っていない番号（名前を入れると使えます）</li>` + unused.map((g) =>
        `<li class="cond-item cond-item--unused${g === selected ? " is-sel" : ""}" data-g="${g}" tabindex="0"><span class="cond-rank"></span>
          <span class="cond-main"><span class="cond-name">番号 ${g}</span></span><span></span><span></span></li>`).join("") : "");
    for (const li of main.querySelectorAll(".cond-item[data-g]")) {
      const go = () => { selected = Number(li.dataset.g); drawList(); drawEditor(); };
      li.addEventListener("click", (e) => { if (!e.target.closest("button")) go(); });
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    }
    const move = (g, d) => {
      const used2 = draft.order.filter((x) => named(draft.rows[x - 1]));
      const i = used2.indexOf(g), j = i + d;
      if (j < 0 || j >= used2.length) return;
      [used2[i], used2[j]] = [used2[j], used2[i]];
      draft.order = [...used2, ...draft.order.filter((x) => !named(draft.rows[x - 1]))];
      changed(); drawList();
    };
    for (const b of main.querySelectorAll("[data-up]")) b.addEventListener("click", () => move(Number(b.dataset.up), -1));
    for (const b of main.querySelectorAll("[data-down]")) b.addEventListener("click", () => move(Number(b.dataset.down), 1));
  }

  function rangeRows(r, p) {
    return RANGES.map((rg) => {
      const lo = lowerOf(r, rg, p), up = upperOf(r, rg, p);
      const z = p ? r.not_use === "*" : true;      // 旧シートの空欄（0 として比べる）を 0 と見せるか
      const zl = z && !NEW_KEYS.has(rg.lo);
      return `<tr><th>${rg.label}</th>
        <td><input class="input input--num" data-lo="${rg.label}" data-p="${p}" value="${esc(toInput(lo.v, zl))}" placeholder="なし">
          ${rg.gt ? `<select class="input input--op" data-op="${rg.label}" data-p="${p}"><option value="ge" ${lo.op === "ge" ? "selected" : ""}>以上</option><option value="gt" ${lo.op === "gt" ? "selected" : ""}>より大きい</option></select>` : '<span class="op">以上</span>'}</td>
        <td class="tilde">〜</td>
        <td><input class="input input--num" data-hi="${rg.label}" data-p="${p}" value="${esc(toInput(up.v, z && up.op === "le"))}" placeholder="なし">
          <select class="input input--op" data-hop="${rg.label}" data-p="${p}"><option value="le" ${up.op === "le" ? "selected" : ""}>以下</option><option value="lt" ${up.op === "lt" ? "selected" : ""}>未満</option></select></td>
        <td class="note">${rg.unit}</td></tr>`;
    }).join("");
  }

  function drawEditor() {
    const g = selected, r = draft.rows[g - 1];
    const rank = draft.order.filter((x) => named(draft.rows[x - 1])).indexOf(g) + 1;
    const warns = [...rowWarnings(r), ...(r.not_use === "*" ? rowWarnings(r, "not_") : [])];
    $("#editor").innerHTML = `
      <header><h3>${esc(r.name || `番号 ${g}`)}</h3><span class="note">番号 ${g}${rank ? `・${rank} 番目に見る` : "・使っていない"}</span></header>
      <div class="pad stack cond-edit">
        <div class="formrow"><label>グループ名 <input class="input" id="eName" value="${esc(r.name || "")}" style="width:16em"></label>
          <label class="check"><input type="checkbox" id="eVc" ${r.vc === "*" ? "checked" : ""}> VC 付きのロットはこのグループを優先</label></div>
        <p class="note">${r.vc === "*" ? "VC 付きのロットは、ここに当てはまれば、上のグループより優先してここに入ります。VC の無いロットも、上に当てはまらなければここに入ります。" : "上から順に見て、最初に当てはまればここに入ります。"}</p>
        <h4>当てはまる条件</h4>
        <div class="formrow"><label>材質（${esc($("#basisMain") ? $("#basisMain").value : cs.basis_main)}） <input class="input" id="eMat" value="${esc(isStar(r.mat) ? "" : (r.mat ?? ""))}" placeholder="どれでも" style="width:10em"></label>
          <label>製造材質 <input class="input" id="eMmat" value="${esc(isStar(r.mmat) || isBlank(r.mmat) ? "" : r.mmat)}" placeholder="どれでも" style="width:16em"></label></div>
        <p class="note">空欄はどれでも。カンマで区切って並べられ、末尾の * は「…で始まる」（例 3N*,1070）。寸法は${esc(cs.basis_main)}の値で比べます。</p>
        <table class="table table--dense cond-ranges"><tbody>${rangeRows(r, "")}</tbody></table>
        <p class="cond-say">→ ${esc(sentence(r))} のロット</p>
        <h4><label class="check"><input type="checkbox" id="eNot" ${r.not_use === "*" ? "checked" : ""}> 除外する範囲を使う</label></h4>
        <div id="notBox" ${r.not_use === "*" ? "" : "hidden"}>
          <p class="note">上の条件に当てはまっても、ここに<b>全部</b>当てはまるロットはこのグループに入れません（次のグループを見ます）。</p>
          <div class="formrow"><label>材質 <input class="input" id="eNotMat" value="${esc(isStar(r.not_mat) || isBlank(r.not_mat) ? "" : r.not_mat)}" placeholder="どれでも" style="width:10em"></label>
            <label>製造材質 <input class="input" id="eNotMmat" value="${esc(isStar(r.not_mmat) || isBlank(r.not_mmat) ? "" : r.not_mmat)}" placeholder="どれでも" style="width:16em"></label></div>
          <table class="table table--dense cond-ranges"><tbody>${rangeRows(r, "not_")}</tbody></table>
          <p class="cond-say">→ ${esc(sentence(r, "not_"))} のロットは除外</p>
        </div>
        ${warns.length ? `<div class="problems">${warns.map(esc).join("\n")}</div>` : ""}
      </div>`;
    const set = (k, val) => { r[k] = val; changed(); drawList(); };
    $("#eName").addEventListener("change", (e) => { set("name", e.target.value.trim()); drawEditor(); });
    $("#eVc").addEventListener("change", (e) => { set("vc", e.target.checked ? "*" : null); drawEditor(); });
    $("#eMat").addEventListener("change", (e) => { set("mat", fromInput(e.target.value)); drawEditor(); });
    $("#eNot").addEventListener("change", (e) => { set("not_use", e.target.checked ? "*" : null); drawEditor(); });
    $("#eNotMat").addEventListener("change", (e) => { set("not_mat", fromInput(e.target.value)); drawEditor(); });
    $("#eMmat").addEventListener("change", (e) => { set("mmat", fromInput(e.target.value)); drawEditor(); });
    $("#eNotMmat").addEventListener("change", (e) => { set("not_mmat", fromInput(e.target.value)); drawEditor(); });
    const setUpper = (label, p) => {
      const rg = RANGES.find((x) => x.label === label);
      const val = fromInput($(`#editor [data-hi="${label}"][data-p="${p}"]`).value);
      const op = $(`#editor [data-hop="${label}"][data-p="${p}"]`).value;
      if (op === "lt") { r[p + rg.lt] = val; r[p + rg.hi] = "*"; } else { r[p + rg.hi] = val; r[p + rg.lt] = "*"; }
      changed(); drawList(); drawEditor();
    };
    for (const inp of $("#editor").querySelectorAll("[data-hi]")) inp.addEventListener("change", () => setUpper(inp.dataset.hi, inp.dataset.p));
    for (const sl of $("#editor").querySelectorAll("[data-hop]")) sl.addEventListener("change", () => setUpper(sl.dataset.hop, sl.dataset.p));
    const setLower = (label, p) => {
      const rg = RANGES.find((x) => x.label === label);
      const val = fromInput($(`#editor [data-lo="${label}"][data-p="${p}"]`).value);
      const opEl = $(`#editor [data-op="${label}"][data-p="${p}"]`);
      const op = opEl ? opEl.value : "ge";
      if (op === "gt") { r[p + rg.gt] = val; r[p + rg.lo] = "*"; } else { r[p + rg.lo] = val; if (rg.gt) r[p + rg.gt] = "*"; }
      changed(); drawList(); drawEditor();
    };
    for (const inp of $("#editor").querySelectorAll("[data-lo]")) inp.addEventListener("change", () => setLower(inp.dataset.lo, inp.dataset.p));
    for (const s of $("#editor").querySelectorAll("[data-op]")) s.addEventListener("change", () => setLower(s.dataset.op, s.dataset.p));
  }

  const collect = () => ({
    rev: cs.rev, rows: draft.rows, order: draft.order,
    basis_main: $("#basisMain").value, basis_not: $("#basisNot").value,
    rule_not_material: main.querySelector('input[name="ruleMat"]:checked').value,
    rule_not_hit: main.querySelector('input[name="ruleHit"]:checked').value,
  });
  for (const el of main.querySelectorAll("details select, details input[type=radio]")) el.addEventListener("change", changed);

  drawList();
  drawEditor();
  $("#useSpec").addEventListener("click", () => {
    for (const [g, row] of Object.entries(v.equipment || {})) draft.rows[Number(g) - 1] = JSON.parse(JSON.stringify(row));
    changed(); drawList(); drawEditor();
    toast("設備仕様の条件を入れました（まだ保存していません）。「影響を確認」で変わるロットを見てから保存してください。", "ok");
  });

  $("#preview").addEventListener("click", async () => {
    try {
      const r = await api.post("/api/conditions/preview", { conditions: collect(), y, m, cal }, "振り分けを試しています…");
      const out = $("#previewOut");
      if (r.problems.length) { out.innerHTML = `<p class="problems">${r.problems.map(esc).join("\n")}</p>`; return; }
      after = r.counts || null;
      drawList();
      const w = r.changes.reduce((s, c) => s + (c.weight || 0), 0);
      out.innerHTML = `<section class="card"><header><h3>この変更で振り分けが変わるロット（${ymLabel(r.year, r.month)}）</h3>
        <span class="note">${n0(r.changes.length)} 件・${kg(w)} kg（一覧の件数も「→」で変わった後を出しています。手動訂正のあるロットは訂正が優先されます）</span></header>
        <div class="pad pad--flush"><div class="tablewrap tablewrap--mid"><table class="table table--dense">
        <thead><tr><th>ﾛｯﾄ番号</th><th>いま</th><th>変更後</th><th>重量kg</th><th>訂正</th></tr></thead>
        <tbody>${r.changes.map((c) => `<tr><td>${esc(c.lot)}</td><td>${esc(c.before_name)}</td><td><b>${esc(c.after_name)}</b></td>
          <td class="n">${kg(c.weight)}</td><td>${c.override !== null ? esc(c.override) : ""}</td></tr>`).join("")
          || '<tr><td colspan="5">変わるロットはありません</td></tr>'}</tbody></table></div></div></section>`;
    } catch (e) { toastError(e); }
  });

  $("#tryGo").addEventListener("click", async () => {
    const lot = $("#tryLot").value.trim();
    if (!lot) return;
    try {
      const r = await api.post("/api/conditions/try", { conditions: collect(), lot, cal }, "試しています…");
      const ok = (b) => b ? '<span class="ok">○</span>' : '<span class="ng">×</span>';
      const rows = (r.trace || []).filter((st) => st.name).map((st) => {
        const checks = (st.main || []).map((c) => `${esc(c.field)}${ok(c.ok)}`).join(" ");
        const nots = st.not ? `除外: ${st.not_hit ? "<b>当たり</b>" : "外れ"}` : "";
        return `<tr class="${st.row === r.group ? "is-sel" : ""}"><td class="n">${st.rank}</td><td>${esc(st.name)}<small> 番号 ${st.row}</small></td>
          <td>${checks}</td><td>${nots}</td><td>${esc(st.result || "")}</td></tr>`;
      }).join("");
      $("#tryOut").innerHTML = `
        <p>ロット <b>${esc(r.lot)}</b> → <b>${esc(r.name)}</b>${r.now !== r.group ? `（いまの条件表では ${esc(r.now_name)}）` : "（いまの条件表と同じ）"}
          ${r.override ? `<span class="note">※ このロットには手動訂正（${esc(r.override.grp)}）があり、集計では訂正が優先されます</span>` : ""}</p>
        <p class="note">${Object.entries(r.values).map(([k, x]) => `${esc(k)} ${esc(x ?? "")}`).join("・")}${r.is_plate ? "" : "・板ではない（どのグループにも入りません）"}</p>
        <div class="tablewrap"><table class="table table--dense"><thead><tr><th>順番</th><th>グループ</th><th>条件</th><th>除外</th><th>結果</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
    } catch (e) { toastError(e); }
  });
  $("#exportCond").addEventListener("click", async () => {
    if (ctx.dirty && !(await confirmBox("まだ保存していない変更があります。出力するのは保存済みの条件表です。出力しますか？", { ok: "出力" }))) return;
    try {
      const r = await api.post("/api/conditions/export", {}, "出力しています…");
      if (r.written) toast(`条件表を出力しました（${r.rows} グループ）: ${r.path}`, "ok");
      else toast(r.message, "warn", { label: "パスの設定へ", onClick: () => { location.hash = "#settings"; } });
    } catch (e) { toastError(e); }
  });
  $("#tryLot").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#tryGo").click(); });

  $("#saveCond").addEventListener("click", async () => {
    if (!(await confirmBox("条件表を保存します。全 PC の集計に効きます（過去の月も、この条件で数え直します）。", { ok: "保存" }))) return;
    try {
      const r = await api.post("/api/conditions", { ...collect(), note: "画面から保存" }, "保存しています…");
      ctx.dirty = false;
      toast("条件表を保存しました。", "ok");
      const ex = r.exported || {};
      if (ex.written) toast(`条件表を出力しました: ${ex.path}`, "ok");
      else if (ex.path === undefined && ex.message && !ex.message.includes("未設定")) toast(ex.message, "warn");
      await ctx.reloadState();
      ctx.refresh();
    } catch (e) {
      toastError(e);
      if (e.status === 409) { ctx.dirty = false; }
    }
  });
}
