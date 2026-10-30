/*
  lotpanel.js — 右から出る引き出し

  セル（グループ×日）のロット一覧 → ロットを押すと「判定の経過」（旧 チェックツールの置き換え）。
  判定の経過は本処理と同じ判定エンジンの記録をそのまま描く（チェックツール独自の判定はしない）。
*/
import { api, qs } from "./api.js";
import { toast, toastError } from "./toast.js";
import { $, esc, kg, n0 } from "./util.js";

const drawer = () => $("#drawer");

export function close() { drawer().hidden = true; }

function show(title, html) {
  $("#drawerTitle").textContent = title;
  $("#drawerBody").innerHTML = html;
  drawer().hidden = false;
}

function groupLabel(no, names) {
  if (no === null || no === undefined) return "未分類";
  if (no === 0) return "0 集計しない";
  return `${no} ${names[no - 1] || "（名前なし）"}`;
}

export async function openCell(p, ctx) {
  const { y, m, cal } = ctx.nav;
  try {
    const res = await api.get(`/api/cell?${qs({ y, m, cal, g: p.g, d: p.d, s: p.s })}`);
    const title = `${p.label}　${n0(res.count)} 件・${kg(res.weight)} kg`;
    const rows = res.lots.map((l) => `
      <tr data-lot="${esc(l.lot)}" tabindex="0">
        <td>${esc(l.lot)}</td><td class="n">${esc(l.day ?? "")}</td><td class="n">${esc(l.shift ?? "")}</td>
        <td>${esc(l.o_mat ?? "")}</td><td class="n">${esc(l.t ?? "")}</td><td class="n">${esc(l.w ?? "")}</td>
        <td class="n">${esc(l.l ?? "")}</td><td class="n">${esc(l.unit_w)}</td><td class="n">${esc(l.vc ?? "")}</td>
        <td class="n">${kg(l.weight)}</td><td class="n">${n0(l.count)}</td>
        <td>${l.override !== null && l.override !== undefined ? `<span class="tag tag--main">訂正 ${esc(l.override)}</span>` : ""}</td>
      </tr>`).join("");
    show(title, `
      <p class="note">ロットを押すと、条件表の各行で何に当たったか（判定の経過）が出ます。</p>
      <div class="tablewrap"><table class="table table--dense"><thead><tr>
        <th>ﾛｯﾄ番号</th><th>日</th><th>直</th><th>ｵｰﾀﾞｰ材質</th><th>板厚</th><th>板幅</th><th>板丈</th><th>計算単重</th>
        <th>VC</th><th>重量kg</th><th>枚数</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="12">ありません</td></tr>'}</tbody></table></div>`);
    for (const tr of $("#drawerBody").querySelectorAll("tr[data-lot]")) {
      const go = () => openLot(tr.dataset.lot, ctx, () => openCell(p, ctx));
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    }
  } catch (e) { toastError(e); }
}

function okCell(x) {
  if (!x) return '<td class="na">—</td>';
  return x.ok ? '<td class="ok">○</td>' : '<td class="ng">×</td>';
}

export async function openLot(lot, ctx, back = null) {
  const { cal } = ctx.nav;
  try {
    const d = await api.get(`/api/lot?${qs({ lot, cal })}`);
    const names = d.names;
    const trace = (d.trace || []).map((st) => {
      const nf = Object.fromEntries((st.not || []).map((x) => [x.field, x]));
      const mf = Object.fromEntries((st.main || []).map((x) => [x.field, x]));
      const cls = st.not_hit ? "nothit" : (st.result && st.result.includes("確定")) || st.row === d.auto ? "hit" : "";
      return `<tr class="${cls}"><td class="n">${st.row}</td><td>${esc(st.name)}</td>
        ${["材質", "板厚", "板幅", "板丈", "単重"].map((f) => okCell(nf[f])).join("")}
        ${["材質", "板厚", "板幅", "板丈", "単重"].map((f) => okCell(mf[f])).join("")}
        <td>${esc(st.result || "")}</td></tr>`;
    }).join("");
    const ov = d.override;
    const opts = ['<option value="">訂正しない（自動判定のまま）</option>']
      .concat(names.map((nm, i) => nm ? `<option value="${i + 1}" ${ov && ov.grp === i + 1 ? "selected" : ""}>${i + 1} ${esc(nm)}</option>` : ""))
      .concat([`<option value="0" ${ov && ov.grp === 0 ? "selected" : ""}>0 集計しない</option>`]).join("");
    const diff = d.vba_group !== d.auto
      ? `<span class="tag tag--fill" title="旧 VBA の判定方式（NOT 条件で判定をやめる等）なら">旧ツールの判定: ${esc(groupLabel(d.vba_group, names))}</span>` : "";
    show(`ロット ${d.lot}`, `
      ${back ? '<div><button class="btn btn--sm" type="button" id="lotBack">← 一覧へ戻る</button></div>' : ""}
      <div class="verdict">集計に使うグループ <b>${esc(groupLabel(d.final, names))}</b>
        <span class="muted">自動判定: ${esc(groupLabel(d.auto, names))}${d.simul ? `・同時VC ${esc(d.simul)}` : ""}</span>${diff}</div>
      <dl class="kv">
        <dt>集計日（${cal === "genba" ? "現場暦" : "太陽暦"}）</dt><dd>${d.year ?? "—"}/${d.month ?? "—"}/${d.day ?? "—"}　直 ${d.shift ?? "—"}</dd>
        <dt>計算単重</dt><dd>${esc(d.unit_w)} kg（製造板厚×板幅×板丈×比重÷10⁶、小数2桁に四捨五入）</dd>
        <dt>倉入重量・枚数</dt><dd>${kg(d.weight)} kg・${n0(d.count)} 枚</dd>
        <dt>寸法の基準</dt><dd>通常 ${esc(d.basis_main)}／NOT ${esc(d.basis_not)}</dd>
        <dt>判定方式</dt><dd>${esc(d.rules.join("／"))}</dd>
      </dl>
      <div class="card"><header><h3>手動訂正（旧 修正シート A 列）</h3></header><div class="pad">
        <div class="formrow">
          <select class="input" id="ovGrp">${opts}</select>
          <input class="input input--wide" id="ovReason" placeholder="理由（例: 条件にどうしても当たらないため）" value="${esc(ov ? ov.reason : "")}">
          <button class="btn btn--commit" type="button" id="ovSave">保存</button>
        </div>
        ${ov ? `<p class="note">いまの訂正: ${esc(groupLabel(ov.grp, names))}（${esc(ov.by_pc)} ${esc(ov.by_user)}・${esc(ov.at)}）</p>` : ""}
        <p class="note">ロット番号に結び付けて保存します。データを取り直しても同じロットに効きます（全 PC 共通）。</p>
      </div></div>
      <div class="card"><header><h3>判定の経過（条件表を上から）</h3></header><div class="pad pad--flush">
        <div class="tablewrap"><table class="table table--dense trace"><thead>
          <tr><th rowspan="2">行</th><th rowspan="2">グループ</th><th colspan="5">NOT 条件</th><th colspan="5">収集条件</th><th rowspan="2">結果</th></tr>
          <tr><th>材質</th><th>厚</th><th>幅</th><th>丈</th><th>単重</th><th>材質</th><th>厚</th><th>幅</th><th>丈</th><th>単重</th></tr>
        </thead><tbody>${trace}</tbody></table></div></div></div>
      <details class="card"><summary class="pad">取り込んだ値（${d.fields.length} 列）</summary><div class="pad">
        <dl class="kv">${d.fields.map((f) => `<dt>${esc(f.name)}</dt><dd>${esc(f.value ?? "")}</dd>`).join("")}</dl></div></details>`);
    if (back) $("#lotBack").addEventListener("click", back);
    $("#ovSave").addEventListener("click", async () => {
      const g = $("#ovGrp").value;
      try {
        await api.post("/api/override", { lot: d.lot, grp: g === "" ? null : Number(g), reason: $("#ovReason").value,
                                          seen: ov ? ov.grp : null });
        toast(g === "" ? "訂正を外しました。" : "訂正を保存しました。", "ok");
        await ctx.refresh();
        await openLot(lot, ctx, back);
      } catch (e) {
        toastError(e);
        if (e.status === 409) openLot(lot, ctx, back);
      }
    });
  } catch (e) { toastError(e); }
}
