/*
  app.js — 外枠（帯・左のレール・画面の切替・接続の見張り）

  画面は #month / #year / #review / #master / #data / #settings。
  いま見ている月・暦・グループはこのタブの中だけで持ち、サーバは毎回それを受けて集計する
  （サーバにも画面にも古い集計を持ち続けない）。
*/
import { api, TAB } from "./api.js";
import { toast, toastError } from "./toast.js";
import { confirmBox } from "./askbox.js";
import { $, esc, n0, ymLabel } from "./util.js";
import * as lotpanel from "./lotpanel.js";
import * as month from "./views/month.js";
import * as year from "./views/year.js";
import * as review from "./views/review.js";
import * as master from "./views/master.js";
import * as data from "./views/data.js";
import * as settings from "./views/settings.js";

const VIEWS = { month, year, review, master, data, settings };
const POLL_MS = 15000;

function load(key, dflt) { try { return localStorage.getItem(key) ?? dflt; } catch { return dflt; } }
function save(key, v) { try { localStorage.setItem(key, v); } catch { /* 保存できなくても動く */ } }

export const ctx = {
  state: null,
  nav: { y: 0, m: 0, cal: load("cal", "genba"), sel: "all", fy: 0 },
  autoNav: true,         // 月をまだ自分で選んでいない（データが入ったら、その既定の月に合わせる）
  view: "month",
  instance: "",
  dirty: false,          // 条件表などを編集中（自動で描き直さない）
  setNav(patch, auto = false) {
    if (!auto && (patch.y || patch.fy)) this.autoNav = false;
    Object.assign(this.nav, patch);
    if (patch.cal) save("cal", patch.cal);
    ribbon();
  },
  async refresh() { await render(); },
  openLot: (lot) => lotpanel.openLot(lot, ctx),
  openCell: (p) => lotpanel.openCell(p, ctx),
  async reloadState() { await pollState(true); },
};

function ribbon() {
  const s = ctx.state;
  const { y, m, cal } = ctx.nav;
  $("#rb-month").textContent = y ? ymLabel(y, m) : "—";
  $("#rb-cal").textContent = s ? s.calendars[cal] : "—";
  if (s) {
    const last = s.history.last;
    $("#rb-data").textContent = last ? `${last.at.replace("T", " ").slice(5, 16)}・${n0(s.history.lots)}件` : "未取り込み";
    $("#rb-data").dataset.state = last ? "" : "unset";
    $("#rb-shared").textContent = s.shared.configured ? (s.shared.stale ? "控えを表示中" : "共通") : "この PC だけ";
    $("#rb-shared").dataset.state = (!s.shared.configured || s.shared.stale) ? "unset" : "";
    $("#rb-shared-slot").title = `共有フォルダ: ${s.shared.dir}`;
    const run = s.rne;
    $("#rb-running").hidden = !run.running;
    $("#rb-run-how").textContent = run.running ? `${run.progress}%・${run.elapsed} 秒・${run.step}` : "";
    $("#rneRun").disabled = run.running;
    const un = $("#badge-data");
    un.hidden = s.rne.ok !== false;          // 前回のデータ更新が失敗した時だけ印を出す
    un.textContent = "!";
    const stale = $("#staleband");
    const msgs = [];
    if (!s.shared.configured) msgs.push("共有フォルダが未設定のため、条件表・手動訂正・取り込んだ実績はこの PC の中だけに保存しています（設定画面で指定してください）。");
    if (s.shared.stale) msgs.push(`共有フォルダに届かないため、この PC にある最後の控えを表示しています。${s.shared.error || ""}`);
    stale.hidden = msgs.length === 0;
    $("#staletext").textContent = msgs.join(" ");
  }
}

function setActive() {
  for (const a of document.querySelectorAll("#rail a")) {
    if (a.dataset.view === ctx.view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;
  const main = $("#main");
  setActive();
  try {
    const v = VIEWS[ctx.view] || month;
    await v.render(main, ctx, () => seq !== renderSeq);
  } catch (e) {
    if (seq === renderSeq) {
      main.innerHTML = `<div class="view"><p class="problems">${esc(e.message)}</p></div>`;
    }
  }
}

function route() {
  const name = (location.hash || "#month").slice(1);
  ctx.view = VIEWS[name] ? name : "month";
  if (ctx.dirty) toast("保存していない変更は破棄しました。", "warn");
  ctx.dirty = false;
  render();
}

let offline = false;
async function pollState(force = false) {
  try {
    const s = await api.get("/api/state");
    if (ctx.instance && s.instance !== ctx.instance) {
      // ツールが起動し直された（トークンも変わっている）。読み直してつなぐ
      location.reload();
      return;
    }
    ctx.instance = s.instance;
    const wasRunning = ctx.state && ctx.state.rne.running;
    const prevImport = ctx.state && ctx.state.history.last && ctx.state.history.last.id;
    ctx.state = s;
    if (!ctx.nav.y || (ctx.autoNav && (ctx.nav.y !== s.default.year || ctx.nav.m !== s.default.month))) {
      const first = !ctx.nav.y;
      ctx.setNav({ y: s.default.year, m: s.default.month, fy: s.default.fy }, true);
      if (!first) force = true;
    }
    if (offline) { offline = false; $("#offline").hidden = true; }
    ribbon();
    const newImport = s.history.last && s.history.last.id;
    if (wasRunning && !s.rne.running) {
      const r = s.rne;
      if (r.ok) toast(`データ更新が終わりました（${r.step}）`, "ok");
      else toast(`データ更新ができませんでした: ${r.message}`, "error");
    }
    if (force || (prevImport && newImport !== prevImport) || (wasRunning && !s.rne.running)) {
      if (!ctx.dirty) await render();
    }
  } catch (e) {
    if (e.status === 403 && e.body && e.body.reload) { location.reload(); return; }
    if (!offline) { offline = true; $("#offline").hidden = false; }
  }
}

function initTheme() {
  const t = load("theme", "");
  if (t) document.documentElement.dataset.theme = t;
  $("#themeToggle").addEventListener("click", () => {
    const dark = (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    save("theme", next);
  });
}

async function runRne() {
  const s = ctx.state;
  if (s && !s.rne.configured) {
    toast("データ更新に使う RNE ファイルか、SymNavi の ID・パスワードが未設定です。「パスの設定」で指定してください。", "warn");
    location.hash = "#settings";
    return;
  }
  if (!(await confirmBox(`SymNavi で「${s ? s.rne.rne_name : ""}」を実行して当月の倉入実績を取り出し、取り込みます。\nSymNavi が起動します（数十秒〜2 分ほど）。よろしいですか？`, { ok: "データ更新" }))) return;
  try {
    await api.post("/api/rne/run", {});
    toast("データ更新を始めました。進み具合は上の帯と「データ」画面に出ます。", "info");
    await pollState();
    const t = setInterval(async () => {
      await pollState();
      if (ctx.state && !ctx.state.rne.running) clearInterval(t);
    }, 2000);
  } catch (e) { toastError(e); }
}

async function quit() {
  if (!(await confirmBox("ツールを終了します。", { ok: "終了", danger: true }))) return;
  try {
    await api.post("/api/shutdown", {});
    document.body.innerHTML = '<div class="empty">終了しました。このタブは閉じてください。もう一度使う時は Start.vbs を起動してください。</div>';
  } catch (e) { toastError(e); }
}

function init() {
  initTheme();
  $("#rneRun").addEventListener("click", runRne);
  $("#quit").addEventListener("click", quit);
  $("#reconnect").addEventListener("click", () => pollState(true));
  $("#drawerClose").addEventListener("click", lotpanel.close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") lotpanel.close(); });
  window.addEventListener("hashchange", route);
  window.addEventListener("pagehide", () => {
    try { navigator.sendBeacon("/api/bye", JSON.stringify({ token: api.token, tab: TAB })); } catch { /* 閉じる時なので無視 */ }
  });
  pollState().then(route);
  setInterval(() => pollState(), POLL_MS);
}

init();
