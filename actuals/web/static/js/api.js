/*
  api.js — サーバとのやりとり（python-web-tools の api.js と同じ約束）

  JS が持つのは「呼ぶ」「受け取ったものを描く」だけ。業務の判断と文言はサーバが持つ。
  断りは {"error": {"code", "message"}}。message をそのまま画面に出す。
*/

const TOKEN = document.querySelector('meta[name="app-token"]').content;

export const TAB = (() => {
  try {
    let id = sessionStorage.getItem("tab-id");
    if (!id) { id = Math.random().toString(36).slice(2, 10); sessionStorage.setItem("tab-id", id); }
    return id;
  } catch { return Math.random().toString(36).slice(2, 10); }
})();

export class ApiError extends Error {
  constructor(status, body) {
    const info = (body && body.error) || {};
    super(info.message || `通信に失敗しました (HTTP ${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = info.code || "";
    this.body = body;
  }
}

let busyCount = 0;
function busy(on, text) {
  const band = document.getElementById("busyband");
  busyCount += on ? 1 : -1;
  if (!band) return;
  if (busyCount > 0 && text) document.getElementById("busylines").textContent = text;
  band.hidden = busyCount <= 0;
}

async function request(path, options = {}, waitText = "") {
  let timer = null;
  let shown = false;
  if (waitText) timer = setTimeout(() => { shown = true; busy(true, waitText); }, 250);   // 速く返るものには出さない
  try {
    const res = await fetch(path, {
      ...options,
      cache: "no-store",
      headers: {
        "X-Tool-Token": TOKEN,
        "X-Tab-Id": TAB,
        ...(typeof options.body === "string" ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    let body = null;
    if ((res.headers.get("Content-Type") || "").includes("application/json")) body = await res.json();
    if (!res.ok) throw new ApiError(res.status, body);
    return body;
  } finally {
    if (timer) clearTimeout(timer);
    if (shown) busy(false);
  }
}

export function qs(params) {
  return Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
}

export const api = {
  get: (path, wait) => request(path, {}, wait),
  post: (path, data, wait) => request(path, { method: "POST", body: JSON.stringify(data ?? {}) }, wait),
  postBytes: (path, bytes, wait) => request(path, {
    method: "POST", body: bytes, headers: { "Content-Type": "application/octet-stream" } }, wait),
  token: TOKEN,
};
