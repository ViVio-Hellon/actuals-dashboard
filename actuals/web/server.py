"""ブラウザ画面のサーバー（標準ライブラリの http.server だけ。RNEConverter の app/web/server.py と同じ作り）。

【多重起動】 web.lock を取れたプロセスだけがサーバーになる。2 つ目の起動は、動いている画面を開いて終わる。
【放置】     DB の接続は持たない（要求ごとに開閉）。画面をすべて閉じたら 2 分後、問い合わせが無いまま
            12 時間（idle_exit_minutes）たったら終わる。PC のスリープ中は数えない。
【安全】     127.0.0.1 だけで待ち受け、Host ヘッダーを確かめ、API には起動ごとのトークンを要求する。
【エラー】   {"error": {"code", "message"}}（python-web-tools と同じ形）。予期しないものはエラー番号を付けてログへ。
"""
from __future__ import annotations

import io
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .. import credentials, logsetup, paths_check
from ..aggregate import ALL
from ..service import Service, UserError
from ..settings import APP_ID, EDITABLE, GROUPS, Paths, SettingsError, SettingsInputError, app_info
from ..sharedfile import SharedBusy, SharedUnreachable
from ..source import SourceError
from ..stores import Conflict, ImportError_

log = logging.getLogger("actuals.web")

# デスクトップ版の画面の宛先（外枠 src-tauri の SCHEME と同じ。Windows の WebView2 は http://app.localhost）
DESKTOP_HOSTS = ("app.localhost", "localhost")

# 業務上ありうる断り（ERROR ではなく WARNING「中断」として残す）
logsetup.expected_errors(UserError, Conflict, SharedBusy, SharedUnreachable, SourceError, ImportError_,
                         SettingsError, SettingsInputError, paths_check.PathInputError)

# 操作履歴に残す POST（画面の名前, 残す項目）。パスワードなど秘密は入れない。
OPERATIONS = {
    "/api/rne/run": ("データ更新", ()),
    "/api/rne/cancel": ("データ更新の中断", ()),
    "/api/import/sqlite": ("取り込み（.sqlite3）", ("path",)),
    "/api/import/paste": ("取り込み（貼り付け）", ()),
    "/api/import/xls": ("取り込み（xls）", ()),
    "/api/import/workbook": ("旧ブックからの移行", ("path", "with_conditions")),
    "/api/local/move": ("この PC に貯めた分を共有フォルダへ移す", ()),
    "/api/credentials": ("SymNavi のログインの登録", ("id",)),
    "/api/override": ("手動訂正", ("lot", "grp", "reason")),
    "/api/conditions": ("条件表の保存", ("rev", "note")),
    "/api/conditions/preview": ("条件表の影響の確認", ("y", "m", "cal")),
    "/api/export": ("CSV の書き出し", ("kind", "y", "m", "cal", "sel", "fy")),
    "/api/export/open": ("書き出し先を開く", ()),
    "/api/paths/open": ("フォルダを開く", ("path",)),
    "/api/settings": ("パスの設定の保存", ()),
    "/api/shutdown": ("ツールの終了", ("force",)),
}
QUIET_POSTS = {"/api/paths/check"}     # 入力のたびに来る確認は、遅い・失敗の時だけ残す

STATIC = Path(__file__).resolve().parent / "static"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class LockHeld(RuntimeError):
    pass


class LocalLock:
    """この PC で 1 つだけ（RNEConverter の locking.LocalLock と同じ。死んだプロセスのロックは回収）。"""

    def __init__(self, path: Path, mode: str = "browser"):
        self.path = path
        self.mode = mode
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(lock_record(self.mode), f)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    rec = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    rec = {}
                if is_ours(rec):
                    raise LockHeld(f"pid={rec.get('pid')}")
                if rec:
                    log.warning("残っていた web.lock を片付けます（書いたプロセスはもう動いていません。番号 %s は別のプログラム"
                                "か、終わっています）: %s", rec.get("pid"), rec)
                try:
                    self.path.unlink()
                except OSError:
                    pass
        raise LockHeld(str(self.path))

    def __exit__(self, *a):
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass


def _proc_started(pid: int) -> Optional[float]:
    """プロセスが起動した時刻（エポック秒）。分からなければ None。

    Windows はプロセスの番号を使い回すので、番号だけでは「あの時のツール」か分からない。起動時刻も比べる。
    """
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                c, e, kt, u = (wintypes.FILETIME() for _ in range(4))
                if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(u)):
                    return None
                ft = (c.dwHighDateTime << 32) | c.dwLowDateTime     # 1601-01-01 からの 100ns
                return ft / 1e7 - 11644473600.0
            finally:
                k.CloseHandle(h)
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat", encoding="ascii") as f:
            btime = next(int(line.split()[1]) for line in f if line.startswith("btime"))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except Exception:  # noqa: BLE001
        return None


MODE_LABEL = {"browser": "ブラウザ版", "desktop": "デスクトップ版"}


def lock_record(mode: str = "browser") -> Dict[str, Any]:
    """web.lock・server.json に書く: どちらの版か・番号・書いた時刻・**起動時刻**。

    ブラウザ版とデスクトップ版は同じ web.lock を取り合う（同時に動かさない。後から開いた方が止まる）。
    デスクトップ版は窓（exe）の番号も書く（stop.bat が窓に「閉じて」と頼む相手）。
    """
    rec = {"mode": mode, "pid": os.getpid(), "time": datetime.now().isoformat(timespec="seconds"),
           "started": _proc_started(os.getpid())}
    if mode == "desktop":
        try:
            rec["exe_pid"] = int(os.environ.get("ACTUALS_EXE_PID") or 0) or None
        except ValueError:
            rec["exe_pid"] = None
    return rec


def other_running(paths: Paths, me: str) -> Optional[Dict[str, Any]]:
    """もう一方の版（me でない方）が動いていれば、その記録。"""
    rec = _read_json(paths.runtime / "web.lock")
    if rec and rec.get("mode", "browser") != me and is_ours(rec):
        return rec
    return None


START_TOLERANCE_SEC = 2.0


def is_ours(rec: Dict[str, Any]) -> bool:
    """記録を書いたツールが、いまも動いているか。番号と起動時刻の両方が合う時だけ True。

    前の版の記録（起動時刻なし）は「書いた時刻より後に起動したプロセスは、書いた本人ではない」で見分ける。
    起動時刻が取れない時だけ、番号が生きているかで判断する（前と同じ）。
    """
    try:
        pid = int(rec.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if not pid or not _pid_alive(pid):
        return False
    st = _proc_started(pid)
    if st is None:
        return True
    if rec.get("started") is not None:
        try:
            return abs(st - float(rec["started"])) <= START_TOLERANCE_SEC
        except (TypeError, ValueError):
            return False
    try:
        written = datetime.fromisoformat(str(rec.get("time"))).timestamp()
    except ValueError:
        return True
    return st <= written + START_TOLERANCE_SEC


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _tidy(paths: Paths, why: str) -> None:
    """残った web.lock・server.json を片付ける。"""
    for f in (paths.runtime / "web.lock", paths.runtime / "server.json"):
        try:
            f.unlink()
        except OSError:
            pass
    log.warning("片付けました。Start.vbs で起動できます（%s）", why)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))  # type: ignore[attr-defined]
        ctypes.windll.kernel32.CloseHandle(h)  # type: ignore[attr-defined]
        return code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # 終わったが親が回収していない（ゾンビ）は動いていない扱い
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


class WebApp:
    CLOSED_GRACE_SEC = 120
    WATCH_SEC = 15
    SLEEP_GAP_SEC = 90

    def __init__(self, paths: Paths):
        self.paths = paths
        self.service = Service(paths)
        self.token = secrets.token_urlsafe(24)
        self.instance = f"{os.getpid()}-{int(time.time())}-{secrets.token_hex(3)}"
        self.info = app_info()
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.port = 0
        self.idle_exit_sec = int(self.info.get("server", {}).get("idle_exit_minutes", 720)) * 60
        self.last_seen = time.time()
        self.tabs: Dict[str, float] = {}
        self.closed_at: Optional[float] = None
        self._last_tick: Optional[float] = None
        self.shutdown_reason = ""
        self.bridge = None                 # デスクトップ版: 外枠（exe）へ知らせる口（bridge.py が付ける）

    def seen(self, tab: str) -> None:
        now = time.time()
        self.last_seen = now
        if tab:
            self.tabs[tab[:40]] = now
            self.closed_at = None

    def bye(self, tab: str) -> None:
        self.tabs.pop(tab[:40], None)
        if not self.tabs:
            self.closed_at = time.time()

    def idle_check(self, now: float) -> Optional[str]:
        if self._last_tick is not None and now - self._last_tick > self.SLEEP_GAP_SEC:
            self.last_seen = now   # スリープから戻った。止まっていた間は数えない
            if self.closed_at is not None:
                self.closed_at = now
        self._last_tick = now
        if self.service.rne.running:
            self.last_seen = now
            return None
        if self.closed_at is not None and not self.tabs and now - self.closed_at > self.CLOSED_GRACE_SEC \
                and now - self.last_seen > self.CLOSED_GRACE_SEC:
            return "closed"
        if self.idle_exit_sec > 0 and now - self.last_seen > self.idle_exit_sec:
            return "idle"
        return None

    def idle_watch(self) -> None:
        while self.httpd is not None:
            time.sleep(self.WATCH_SEC)
            why = self.idle_check(time.time())
            if why:
                log.info("終了します（%s）", "画面がすべて閉じられた" if why == "closed" else "長時間の問い合わせ無し")
                self.request_shutdown(why)
                return

    def request_shutdown(self, reason: str) -> None:
        self.shutdown_reason = reason

        def _stop():
            time.sleep(0.3)
            if self.service.rne.running:
                self.service.rne.cancel()   # SymNavi・Excel を残さない
            if self.httpd:
                self.httpd.shutdown()
            if self.bridge is not None:     # デスクトップ版: 外枠に「終わってよい」を知らせる（窓が閉じる）
                self.bridge("quit")
        threading.Thread(target=_stop, daemon=True).start()


def _int(q: Dict[str, list], key: str, default: int) -> int:
    try:
        return int((q.get(key) or [default])[0])
    except (TypeError, ValueError):
        raise UserError(f"{key} の指定が不正です。")


def _sel(v: Any) -> Any:
    if v in (None, "", ALL):
        return ALL
    try:
        return int(v)
    except (TypeError, ValueError):
        raise UserError("グループの指定が不正です。")


def make_handler(app: WebApp):
    svc = app.service

    class Handler(BaseHTTPRequestHandler):
        server_version = "ActualsDashboard"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug("http %s", fmt % args)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            if app.bridge is not None:      # デスクトップ版: 外枠（exe）の中の宛先だけ（ネットワークを通らない）
                return host in DESKTOP_HOSTS
            return host in (f"127.0.0.1:{app.port}", f"localhost:{app.port}")

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _err(self, code: int, err_code: str, message: str, **extra) -> None:
            self._json(code, {"error": {"code": err_code, "message": message}, **extra})

        def _raw_body(self, limit: int) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise UserError("送信内容が大きすぎます。")
            return self.rfile.read(n) if n else b""

        def _body(self) -> Dict[str, Any]:
            raw = self._raw_body(5_000_000) or b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError:
                raise UserError("送信内容の形が不正です。")
            if not isinstance(data, dict):
                raise UserError("送信内容の形が不正です。")
            return data

        def _guard(self, api: bool) -> bool:
            if not self._host_ok():
                self._send(403, b"forbidden host", "text/plain; charset=utf-8")
                return False
            if api and not secrets.compare_digest(self.headers.get("X-Tool-Token") or "", app.token):
                self._err(403, "token", "画面の有効期限が切れました。ページを再読み込みしてください。", reload=True)
                return False
            app.seen(self.headers.get("X-Tab-Id") or "")
            return True

        def _handle(self, fn, name: str = "", quiet: bool = True) -> None:
            logsetup.new_operation_id()
            try:
                with logsetup.timed(name or f"{self.command} {urlparse(self.path).path}", quiet=quiet):
                    fn()
            except UserError as e:
                self._err(422, "rejected", str(e))
            except Conflict as e:
                self._err(409, "stale", str(e), current=e.current)
            except SharedBusy as e:
                self._err(503, "busy", str(e))
            except SharedUnreachable as e:
                self._err(503, "unreachable", str(e))
            except (SourceError, ImportError_) as e:
                self._err(422, "import", str(e))
            except SettingsError as e:
                self._err(409, "stale", str(e))
            except SettingsInputError as e:
                self._err(422, "rejected", str(e))
            except paths_check.PathInputError as e:
                self._err(422, "path", str(e))
            except Exception as e:  # noqa: BLE001
                ref = logsetup.record_error(e, where=f"{self.command} {self.path}")   # timed で記録済みなら同じ番号
                self._err(500, "unexpected", f"予期しないエラーが起きました（{ref}）。ログに詳細があります: "
                                             f"{logsetup.active_dir()}（{type(e).__name__}: {e}）")
            finally:
                logsetup.clear_operation()

        # ----- GET -----
        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/api/health":
                if not self._host_ok():
                    return self._send(403, b"forbidden host", "text/plain; charset=utf-8")
                return self._json(200, {"app_id": APP_ID, "version": app.info.get("version"), "pid": os.getpid(),
                                        "ready": True, "instance": app.instance,
                                        "running": svc.rne.running})
            if u.path.startswith("/api/"):
                if not self._guard(api=True):
                    return
                q = parse_qs(u.query)
                return self._handle(lambda: self._get(u.path, q), f"画面の読み込み {u.path}")
            if not self._guard(api=False):
                return
            name = "index.html" if u.path in ("/", "/index.html") else u.path.lstrip("/")
            target = (STATIC / name).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            body = target.read_bytes()
            if target.name == "index.html":
                log.info("画面を開きました（%s）", "デスクトップ版" if app.bridge is not None else "ブラウザ版")
                body = (body.decode("utf-8").replace("{{TOKEN}}", app.token).replace("{{INSTANCE}}", app.instance)
                        .replace("{{VERSION}}", str(app.info.get("version")))
                        .replace("{{NAME}}", str(app.info.get("display_name")))).encode("utf-8")
            self._send(200, body, CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
                       {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                   "script-src 'self'; img-src 'self' data:; connect-src 'self'"
                                                   + (" ipc: http://ipc.localhost" if app.bridge is not None else "")
                                                   + "; "
                                                   "frame-ancestors 'none'"})

        def _get(self, path: str, q: Dict[str, list]) -> None:
            cal = (q.get("cal") or ["genba"])[0]
            if path == "/api/state":
                st = svc.state()
                st["instance"] = app.instance
                st["log_dir"] = logsetup.active_dir()
                return self._json(200, st)
            if path == "/api/month":
                return self._json(200, svc.month(_int(q, "y", 0), _int(q, "m", 0), cal, _sel((q.get("sel") or [""])[0])))
            if path == "/api/cell":
                g = (q.get("g") or [ALL])[0]
                g = g if g in (ALL, "unclassified") else _sel(g)
                day = _int(q, "d", 0) or None
                sh = _int(q, "s", 0) or None
                return self._json(200, svc.cell_lots(_int(q, "y", 0), _int(q, "m", 0), cal, g, day, sh))
            if path == "/api/year":
                return self._json(200, svc.year(_int(q, "fy", 0), cal))
            if path == "/api/lot":
                return self._json(200, svc.lot_detail((q.get("lot") or [""])[0], cal))
            if path == "/api/find":
                return self._json(200, {"lots": svc.find_lots((q.get("q") or [""])[0])})
            if path == "/api/review":
                return self._json(200, svc.review(_int(q, "y", 0), _int(q, "m", 0), cal))
            if path == "/api/conditions":
                return self._json(200, svc.conditions())
            if path == "/api/history":
                return self._json(200, svc.history_summary())
            if path == "/api/rne":
                return self._json(200, svc.rne.status())
            if path == "/api/fs":
                exts = tuple(e.lower() for e in (q.get("ext") or [""])[0].split(",") if e)
                return self._json(200, paths_check.list_dir((q.get("path") or [""])[0], exts))
            if path == "/api/settings":
                return self._json(200, {"values": svc.settings.load(), "resolved": svc.conf(),
                                        "credentials": credentials.status(app.paths),
                                        "revision": svc.settings.revision(),
                                        "fields": [{"key": k, "group": g, "label": lb, "note": n, "kind": kd}
                                                   for k, g, lb, n, kd in EDITABLE],
                                        "groups": GROUPS,
                                        "data_dir": str(app.paths.base), "log_dir": logsetup.active_dir(),
                                        "log": logsetup.status()})
            return self._err(404, "not_found", "not found")

        # ----- POST -----
        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/api/bye":
                if not self._host_ok():
                    return self._send(403, b"forbidden host", "text/plain; charset=utf-8")
                try:
                    data = json.loads(self._raw_body(10000).decode("utf-8") or "{}")
                except (ValueError, UserError):
                    data = {}
                if secrets.compare_digest(str(data.get("token") or ""), app.token):
                    app.bye(str(data.get("tab") or ""))
                return self._json(200, {"ok": True})
            if not self._guard(api=True):
                return
            label = OPERATIONS.get(u.path, (u.path, ()))[0]
            if u.path == "/api/import/xls":
                q = parse_qs(u.query)
                name = (q.get("name") or ["SymNavi の xls"])[0]

                def run_xls():
                    logsetup.operation(label, name=name)
                    self._json(200, svc.import_xls(self._raw_body(30_000_000), name))
                return self._handle(run_xls, label, quiet=False)
            self._handle(lambda: self._post(u.path, self._body()), label, quiet=u.path in QUIET_POSTS)

        def _post(self, path: str, d: Dict[str, Any]) -> None:
            if path in OPERATIONS:
                label, keys = OPERATIONS[path]
                detail = {k: d.get(k) for k in keys if d.get(k) not in (None, "")}
                if path == "/api/import/paste":
                    detail["lines"] = str(d.get("text") or "").count("\n")
                logsetup.operation(label, **detail)
            if path == "/api/local/move":
                return self._json(200, svc.move_local_to_shared())
            if path == "/api/import/sqlite":
                return self._json(200, svc.import_sqlite(str(d.get("path") or "")))
            if path == "/api/import/paste":
                return self._json(200, svc.import_paste(str(d.get("text") or "")))
            if path == "/api/import/workbook":
                return self._json(200, svc.import_workbook(str(d.get("path") or ""), bool(d.get("with_conditions"))))
            if path == "/api/rne/run":
                return self._json(202, svc.run_update())
            if path == "/api/rne/cancel":
                return self._json(200, svc.cancel_update())
            if path == "/api/credentials":
                try:
                    return self._json(200, credentials.save(app.paths, str(d.get("id") or ""),
                                                            str(d.get("password") or "")))
                except ValueError as e:
                    raise UserError(str(e))
            if path == "/api/override":
                grp = d.get("grp")
                seen = d.get("seen")
                return self._json(200, svc.set_override(str(d.get("lot") or ""),
                                                        None if grp in (None, "") else int(grp),
                                                        str(d.get("reason") or ""),
                                                        None if seen in (None, "") else int(seen)))
            if path == "/api/conditions/preview":
                return self._json(200, svc.preview_conditions(d.get("conditions") or {}, int(d.get("y") or 0),
                                                              int(d.get("m") or 0), str(d.get("cal") or "genba")))
            if path == "/api/conditions":
                return self._json(200, svc.save_conditions(d))
            if path == "/api/export":
                return self._json(200, svc.export(str(d.get("kind")), int(d.get("y") or 0), int(d.get("m") or 0),
                                                  str(d.get("cal") or "genba"), _sel(d.get("sel")),
                                                  int(d.get("fy") or 0)))
            if path == "/api/export/open":
                return self._json(200, svc.open_export_folder())
            if path == "/api/paths/check":
                return self._json(200, paths_check.check_all(d.get("values") or {}, app.paths))
            if path == "/api/paths/open":
                if d.get("key") == "log_dir":      # ログは <出力パス>\actualsdashboard を開く（無ければ作る）
                    folder = paths_check.log_folder(str(d.get("path") or ""), app.paths)
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
                    return self._json(200, {"opened": paths_check.open_path(str(folder)), "folder": str(folder)})
                return self._json(200, {"opened": paths_check.open_path(str(d.get("path") or ""))})
            if path == "/api/settings":
                t0 = time.monotonic()
                rev, lines = svc.settings.save(d.get("values") or {}, str(d.get("revision") or ""))
                logsetup.saved("パスの設定", app.paths.config / "settings.json", len(lines), time.monotonic() - t0)
                if lines:
                    log.info("設定を保存しました:\n  %s", "\n  ".join(lines))
                s = svc.conf()
                logsetup.setup(s["log_dir"], app.paths.logs, keep_days=int(s.get("log_keep_days") or 90))
                try:
                    leftover = svc.local_leftover()
                except Exception:  # noqa: BLE001  知らせるだけ。保存は済んでいる
                    log.warning("この PC に貯めた分を確かめられません", exc_info=True)
                    leftover = None
                return self._json(200, {"revision": rev, "leftover": leftover})
            if path == "/api/shutdown":
                if svc.rne.running and not d.get("force"):
                    raise UserError("データ更新の実行中です。終わってから終了してください。")
                app.request_shutdown("user")
                return self._json(200, {"stopping": True})
            return self._err(404, "not_found", "not found")

    return Handler


# ---------- デスクトップ版: ソケットを使わずに 1 件答える ----------
class _Capture(io.BytesIO):
    """応答を受ける入れ物。handler が終わりに close() しても中身を残す。"""

    def close(self) -> None:  # noqa: D401
        pass


class _Conn:
    """handler に渡す「接続」の代わり（読む口と書く口だけ）。"""

    def __init__(self, raw: bytes):
        self.rfile, self.wfile = io.BytesIO(raw), _Capture()

    def makefile(self, mode: str, *a, **k):
        return self.rfile if "r" in mode else self.wfile

    def sendall(self, data: bytes) -> None:
        self.wfile.write(data)


def call_handler(handler_cls, method: str, path: str, query: str, headers, body: bytes):
    """ブラウザ版と**同じ handler** に要求を 1 件通し、(状態, 見出し, 本文) を返す（デスクトップ版の bridge.py から）。

    待ち受けはしない。要求を HTTP の形のバイト列にして handler に読ませ、書いた応答を読み取る。
    画面の処理・トークン・Host の確認・エラーの形は、ブラウザ版とまったく同じ道を通る。
    """
    target = path + (f"?{query}" if query else "")
    lines = [f"{method} {target} HTTP/1.1"]
    seen_len = False
    for k, v in headers:
        if str(k).lower() == "content-length":
            continue
        lines.append(f"{k}: {v}")
    lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body
    conn = _Conn(raw)

    class _Srv:
        server_name, server_port = "app.localhost", 0

    h = handler_cls.__new__(handler_cls)
    h.request, h.client_address, h.server = conn, ("127.0.0.1", 0), _Srv()
    h.rfile, h.wfile = conn.rfile, conn.wfile
    h.close_connection = True
    h.handle_one_request()
    data = conn.wfile.getvalue()
    head, _, rest = data.partition(b"\r\n\r\n")
    hl = head.decode("iso-8859-1").split("\r\n")
    try:
        status = int(hl[0].split(" ", 2)[1])
    except (IndexError, ValueError):
        return 500, [("Content-Type", "text/plain; charset=utf-8")], "応答を作れませんでした".encode("utf-8")
    out = []
    for line in hl[1:]:
        k, _, v = line.partition(":")
        if k.lower() not in ("connection", "server", "date"):
            out.append((k.strip(), v.strip()))
    return status, out, rest


# ---------- 起動 ----------
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 はプロキシを通さない


def _probe(url: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        with _LOCAL.open(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            info = json.loads(r.read().decode("utf-8"))
        return info if info.get("app_id") == APP_ID else None
    except Exception:  # noqa: BLE001
        return None


def _open_browser(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url, new=2)
    except Exception as e:  # noqa: BLE001
        log.warning("ブラウザを開けません: %s（%s を手で開いてください）", e, url)


def notify(message: str) -> None:
    log.error("%s", message)
    # 窓で知らせるのは、出力先が無い時（Start.vbs → pythonw）だけ。start.bat・stop.bat・ほかのプログラムから
    # 呼ばれた時（出力が管につながっている）は、見えない窓が OK を待ったまま止まらないよう、出力に書くだけにする
    if sys.stdout is not None:
        print(message, flush=True)
    if sys.platform == "win32" and sys.stdout is None and not os.environ.get("ACTUALS_NO_DIALOG"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "倉入実績集計", 0x10)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def serve(paths: Paths, open_browser: bool = True, port: Optional[int] = None,
          ready: Optional[threading.Event] = None, holder: Optional[Dict[str, Any]] = None) -> int:
    info_file = paths.runtime / "server.json"
    lock = LocalLock(paths.runtime / "web.lock")
    try:
        lock.__enter__()
    except LockHeld:
        info = _read_json(info_file)
        url = info.get("url")
        held = _read_json(paths.runtime / "web.lock")
        if held.get("mode", "browser") == "desktop":
            # デスクトップ版が動いている。後から開いたブラウザ版が止まる（デスクトップ版には触らない）
            notify("倉入実績集計のデスクトップ版が動いています。ブラウザ版は同時に使えません。"
                   "デスクトップ版の窓を使うか、閉じてから起動してください。")
            return 5
        t0 = time.time()
        while True:                      # 本当に前回のツールが動いている。5 秒まで応答を待つ
            if url and _probe(url):
                log.info("すでに起動しています。開いている画面を表示します: %s", url)
                if open_browser:
                    _open_browser(url)
                return 0
            if time.time() - t0 >= 5:
                break
            time.sleep(0.5)
        pid = int(held.get("pid") or 0)
        if is_ours(held):                 # 前回のツール（番号と起動時刻が合う）が応答しない → 終わらせて起動し直す
            log.warning("前回のツールが 5 秒たっても応答しません。終わらせてから起動し直します（pid %d）", pid)
            _kill(pid)
            for _ in range(20):
                if not _pid_alive(pid):
                    break
                time.sleep(0.25)
        _tidy(paths, f"応答しない前回のツール pid {pid} の後")
        try:
            lock.__enter__()
        except LockHeld:
            notify("倉入実績集計は起動中ですが、画面に接続できません。少し待ってからもう一度起動するか、stop.bat で終了してください。")
            return 3
    app = WebApp(paths)
    sv = app.info.get("server", {})
    first = port if port is not None else int(sv.get("port", 8751))
    tries = [first] if port == 0 else [first + i for i in range(max(1, int(sv.get("port_retry", 5))))]
    httpd = None
    last_err: Optional[Exception] = None
    for p in tries:
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), make_handler(app))
            break
        except OSError as e:
            last_err = e
    if httpd is None:
        lock.__exit__(None, None, None)
        notify(f"画面用のポートを確保できませんでした（{tries[0]}〜{tries[-1]}）: {last_err}")
        return 4
    httpd.daemon_threads = True
    app.httpd = httpd
    app.port = httpd.server_address[1]
    url = f"http://127.0.0.1:{app.port}/"
    info_file.write_text(json.dumps({"url": url, "instance": app.instance, **lock_record()}), encoding="utf-8")
    log.info("画面を開始しました: %s (pid=%d, 版 %s)", url, os.getpid(), app.info.get("version"))
    if holder is not None:
        holder["app"], holder["url"] = app, url
    threading.Thread(target=app.idle_watch, daemon=True).start()

    def after_start():
        for _ in range(50):
            if _probe(url, timeout=0.5):
                break
            time.sleep(0.1)
        if ready is not None:
            ready.set()
        if open_browser:
            _open_browser(url)
    threading.Thread(target=after_start, daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.httpd = None
        try:
            info_file.unlink()
        except OSError:
            pass
        lock.__exit__(None, None, None)
        log.info("画面を終了しました（%s）", app.shutdown_reason or "停止")
        logsetup.set_stop_reason({"user": "画面の「終了」（stop.bat を含む）", "closed": "画面をすべて閉じた（自動終了）",
             "idle": "長い時間問い合わせが無い（自動終了）"}.get(
            app.shutdown_reason, app.shutdown_reason or "停止"))
    return 0


def _kill(pid: int) -> bool:
    """応答しない画面のプロセスを止める（Windows は taskkill /T /F で子プロセスごと）。"""
    try:
        if os.name == "nt":
            import subprocess
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=0x08000000)
            return r.returncode == 0
        import signal
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _ask_close(pid: int) -> bool:
    """窓に「閉じて」と頼む（Windows は taskkill の /F 無し = 窓を閉じる知らせ。画面の終了と同じ確認を通る）。"""
    try:
        if os.name == "nt":
            import subprocess
            return subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True,
                                  creationflags=0x08000000).returncode == 0
        import signal
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _stop_desktop(paths: Paths, held: Dict[str, Any], force_wait: float) -> int:
    """stop.bat から、デスクトップ版を止める。窓（exe）に閉じてもらい、終わらなければ exe ごと止める。

    exe を止めると、Python と SymNavi・Excel も一緒に止まる（外枠が Windows のジョブに入れている）。
    """
    exe = int(held.get("exe_pid") or 0)
    py = int(held.get("pid") or 0)
    target = exe if exe and _pid_alive(exe) else py
    print(f"デスクトップ版に終了を頼みます（窓の pid {target}）…")
    _ask_close(target)
    t0 = time.time()
    while time.time() - t0 < force_wait:
        if not _pid_alive(py):
            print("終了しました。")
            return 0
        time.sleep(0.5)
    print(f"{int(force_wait)} 秒たっても終わらないため、強制終了します（pid {target}）。")
    _kill(target)
    if target != py:
        _kill(py)
    time.sleep(1)
    if _pid_alive(py):
        print("止められませんでした。タスクマネージャーで 倉入実績集計.exe と python を終了してください。")
        return 1
    for f in (paths.runtime / "server.json", paths.runtime / "web.lock"):
        try:
            f.unlink()
        except OSError:
            pass
    log.warning("片付けました。Start.vbs で起動できます（stop.bat: デスクトップ版を強制終了 pid %d）", target)
    print("終了しました（強制終了）。片付けました。")
    return 0


def stop_running(paths: Paths, force_wait: float = 15.0) -> int:
    """stop.bat から。まず画面に終了を頼み、応答しない・終わらない時は強制終了してロックを片付ける。

    終了コード: 0 = 止めた（または動いていなかった）、1 = 止められなかった。
    """
    import re
    info_file = paths.runtime / "server.json"
    lock_file = paths.runtime / "web.lock"
    info = _read_json(info_file)
    held = _read_json(lock_file) or info
    url, pid = info.get("url", ""), int(held.get("pid") or info.get("pid") or 0)
    if not is_ours(held if held.get("pid") else info):
        # 動いていない。番号が残っていても、別のプログラムが同じ番号を使っているだけ（終わらせない）
        had = lock_file.exists() or info_file.exists()
        for f in (info_file, lock_file):
            try:
                f.unlink()
            except OSError:
                pass
        if had:
            log.warning("片付けました。Start.vbs で起動できます（stop.bat: 残っていた web.lock・server.json。pid %s）", pid)
            print("倉入実績集計は動いていません。残っていたファイルを片付けました。Start.vbs で起動できます。")
        else:
            print("倉入実績集計は動いていません。")
        return 0
    if held.get("mode") == "desktop":
        return _stop_desktop(paths, held, force_wait)
    asked = False
    if url and _probe(url):
        try:
            with _LOCAL.open(url, timeout=3) as r:
                token = re.search(r'name="app-token" content="([^"]+)"', r.read().decode("utf-8")).group(1)  # type: ignore
            req = urllib.request.Request(url.rstrip("/") + "/api/shutdown", data=b'{"force": true}', method="POST",
                                         headers={"Content-Type": "application/json", "X-Tool-Token": token})
            with _LOCAL.open(req, timeout=5) as r:
                r.read()
            asked = True
            print("終了を頼みました。終わるのを待っています…")
        except Exception as e:  # noqa: BLE001
            print(f"画面に終了を頼めませんでした（{e}）。")
    else:
        print("画面が応答しません。")
    if asked:
        t0 = time.time()
        while time.time() - t0 < force_wait:
            if not _pid_alive(pid):
                print("終了しました。")
                return 0
            time.sleep(0.5)
        print(f"{int(force_wait)} 秒たっても終わらないため、強制終了します（pid {pid}）。")
    else:
        print(f"強制終了します（pid {pid}）。")
    _kill(pid)
    time.sleep(1)
    if _pid_alive(pid):
        print("止められませんでした。タスクマネージャーで python / pythonw を終了してください。")
        return 1
    for f in (info_file, lock_file):
        try:
            f.unlink()
        except OSError:
            pass
    log.warning("片付けました。Start.vbs で起動できます（stop.bat: 強制終了 pid %d）", pid)
    print("終了しました（強制終了）。片付けました。Start.vbs で起動できます。")
    return 0
