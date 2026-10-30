/*
  条件表（旧 設定シート 5〜19 行）

  1 行が 1 グループ。上から順に照合する。値は「数」「*（条件なし）」「空欄（0 として比べる）」。
  保存の前に「影響を確認」で、表示中の月の振り分けがどう変わるかを見られる。
  保存は全 PC 共通（版番号で、別の人の保存を上書きしない）。
*/
import { api } from "../api.js";
import { toast, toastError } from "../toast.js";
import { confirmBox } from "../askbox.js";
import { $, esc, kg, n0, ymLabel } from "../util.js";

let draft = null;

function cellText(v) { return v === null || v === undefined ? "" : String(v); }

export async function render(main, ctx, stale) {
  const v = await api.get("/api/conditions", "読み込んでいます…");
  if (stale()) return;
  const cs = v.conditions;
  draft = JSON.parse(JSON.stringify(cs));
  const fields = v.fields;
  const mainF = fields.slice(0, 11);
  const notF = fields.slice(11);
  const inputCls = (k) => k.kind === "name" ? "name" : k.kind === "mat" ? "mat" : k.kind === "flag" ? "flag" : "";
  const rows = cs.rows.map((r, i) => `<tr data-row="${i}"><td class="n">${i + 1}</td>${fields.map((f) =>
    `<td class="${f.key.startsWith("not_") ? "not" : ""}"><input class="${inputCls(f)}" data-key="${f.key}" value="${esc(cellText(r[f.key]))}" aria-label="${i + 1}行目 ${esc(f.label)}"></td>`).join("")}</tr>`).join("");
  const sel = (name, cur) => `<select class="input" id="${name}">${["製造", "ｵｰﾀﾞｰ"].map((b) => `<option ${b === cur ? "selected" : ""}>${b}</option>`).join("")}</select>`;
  const radio = (name, opts, cur) => Object.entries(opts).map(([k, label]) =>
    `<label><input type="radio" name="${name}" value="${k}" ${k === cur ? "checked" : ""}>${esc(label)}</label>`).join("");
  main.innerHTML = `
  <div class="view">
    <div class="view__head">
      <h2>条件表</h2>
      <span class="note">版 ${cs.rev}・${esc(cs.updated_at)} ${esc(cs.updated_by)}（全 PC 共通）</span>
      <span class="grow"></span>
      <button class="btn" type="button" id="preview">影響を確認（${ymLabel(ctx.nav.y, ctx.nav.m)}）</button>
      <button class="btn btn--commit" type="button" id="saveCond">保存</button>
    </div>
    <section class="card"><div class="pad">
      <div class="formrow">
        <label>収集条件の寸法・材質 ${sel("basisMain", cs.basis_main)}</label>
        <label>NOT 条件の寸法・材質 ${sel("basisNot", cs.basis_not)}</label>
        <span class="note">単重はどちらの場合も計算単重（製造寸法から計算）で比べます。</span>
      </div>
      <div class="formrow"><b>NOT 条件の材質</b>${radio("ruleMat", v.rule_not_material, cs.rule_not_material)}</div>
      <div class="formrow"><b>NOT 条件に当たったら</b>${radio("ruleHit", v.rule_not_hit, cs.rule_not_hit)}</div>
      <p class="note">上から順に照合し、最初に当たった行のグループになります。VC 有無が * の行は、VC のロット（VC=1）が当たればその行で確定します。
        「*」は条件なし。空欄は 0 として比べます（旧ツールと同じ）。グループ名が空の行は表示も合計もしません。</p>
      ${v.warnings.length ? `<p class="problems">${v.warnings.map(esc).join("\n")}</p>` : ""}
    </div></section>
    <section class="card"><div class="pad pad--flush"><div class="tablewrap"><table class="table table--dense cond-table">
      <thead><tr><th rowspan="2">No</th><th colspan="${mainF.length}">収集条件</th><th colspan="${notF.length}" class="not">NOT 条件（当たったら外す）</th></tr>
        <tr>${fields.map((f) => `<th class="${f.key.startsWith("not_") ? "not" : ""}" title="旧 設定シート ${f.col} 列">${esc(f.label.replace("NOT ", ""))}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody></table></div></div></section>
    <div id="previewOut"></div>
    <section class="card"><header><h3>保存の記録</h3></header><div class="pad pad--flush"><div class="tablewrap tablewrap--mid">
      <table class="table table--dense"><thead><tr><th>日時</th><th>保存した人</th><th>版</th><th>メモ</th></tr></thead>
      <tbody>${v.audit.map((a) => `<tr><td>${esc(a.at)}</td><td>${esc(a.by)}</td><td>${esc(a.target)}</td><td style="text-align:left">${esc(a.note)}</td></tr>`).join("")
        || '<tr><td colspan="4">ありません</td></tr>'}</tbody></table></div></div></section>
  </div>`;

  const collect = () => {
    const out = { rev: cs.rev, basis_main: $("#basisMain").value, basis_not: $("#basisNot").value,
                  rule_not_material: main.querySelector('input[name="ruleMat"]:checked').value,
                  rule_not_hit: main.querySelector('input[name="ruleHit"]:checked').value, rows: [] };
    for (const tr of main.querySelectorAll("tr[data-row]")) {
      const r = {};
      for (const inp of tr.querySelectorAll("input[data-key]")) r[inp.dataset.key] = inp.value;
      out.rows.push(r);
    }
    return out;
  };
  for (const inp of main.querySelectorAll(".cond-table input")) {
    const orig = inp.value;
    inp.addEventListener("input", () => { inp.dataset.changed = inp.value !== orig ? "1" : ""; ctx.dirty = true; });
  }
  for (const el of main.querySelectorAll("select, input[type=radio]")) el.addEventListener("change", () => { ctx.dirty = true; });

  $("#preview").addEventListener("click", async () => {
    try {
      const r = await api.post("/api/conditions/preview", { conditions: collect(), y: ctx.nav.y, m: ctx.nav.m, cal: ctx.nav.cal },
                               "振り分けを試しています…");
      const out = $("#previewOut");
      if (r.problems.length) { out.innerHTML = `<p class="problems">${r.problems.map(esc).join("\n")}</p>`; return; }
      const w = r.changes.reduce((s, c) => s + (c.weight || 0), 0);
      out.innerHTML = `<section class="card"><header><h3>この変更で振り分けが変わるロット（${ymLabel(r.year, r.month)}）</h3>
        <span class="note">${n0(r.changes.length)} 件・${kg(w)} kg（手動訂正のあるロットは訂正が優先されます）</span></header>
        <div class="pad pad--flush"><div class="tablewrap tablewrap--mid"><table class="table table--dense">
        <thead><tr><th>ﾛｯﾄ番号</th><th>いま</th><th>変更後</th><th>重量kg</th><th>訂正</th></tr></thead>
        <tbody>${r.changes.map((c) => `<tr><td>${esc(c.lot)}</td><td>${esc(c.before_name)}</td><td><b>${esc(c.after_name)}</b></td>
          <td class="n">${kg(c.weight)}</td><td>${c.override !== null ? esc(c.override) : ""}</td></tr>`).join("")
          || '<tr><td colspan="5">変わるロットはありません</td></tr>'}</tbody></table></div></div></section>`;
    } catch (e) { toastError(e); }
  });
  $("#saveCond").addEventListener("click", async () => {
    if (!(await confirmBox("条件表を保存します。全 PC の集計に効きます（過去の月も、この条件で数え直します）。", { ok: "保存" }))) return;
    try {
      await api.post("/api/conditions", { ...collect(), note: "画面から保存" }, "保存しています…");
      ctx.dirty = false;
      toast("条件表を保存しました。", "ok");
      await ctx.reloadState();
      ctx.refresh();
    } catch (e) {
      toastError(e);
      if (e.status === 409) { ctx.dirty = false; }
    }
  });
}
