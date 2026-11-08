"""デスクトップ版（Tauri）からの入口 ── **ポートを使わない**（python-web-tools の bridge.py と同じ作り）

ブラウザ版（Start.vbs → start_app.py）は 127.0.0.1 のポートで待ち受け、ブラウザから繋いでもらう。
デスクトップ版では、窓を持つ外枠（Rust/Tauri、src-tauri/）がこのプロセスを子として起動し、
**標準入出力**で要求を渡す。ソケットは 1 つも開かない。

    外枠（Rust） ──標準入力──▶ bridge.py ──▶ ブラウザ版と同じ handler（actuals/web/server.py）
                 ◀──標準出力──

【やりとりの形】1 件 = 見出し 1 行（JSON）+ 本文（見出しの len バイト）

    要求  {"id": 7, "method": "POST", "path": "/api/x", "query": "a=1", "headers": [[k, v], ...], "len": 12}\\n<本文>
    応答  {"id": 7, "status": 200, "headers": [[k, v], ...], "len": 345}\\n<本文>
    知らせ {"event": "started" | "quit" | "fatal", ...}\\n

【同時に動かさない】ブラウザ版と同じ web.lock を取り合う。ブラウザ版が動いていれば起動せず、
理由を外枠に知らせる（外枠が窓に出す）。デスクトップ版どうしの二重起動は外枠が OS のロックで止める。

【標準出力を守る】やりとりに使う標準出力へほかの誰かが 1 文字でも書くと、以降すべてずれる。
最初に本物の標準出力を別に取り、記述子 1 は標準エラーへ付け替える。

Python の標準ライブラリだけで動く（各ラインPCに追加のライブラリを入れられないため）。
"""
from __future__ import annotations

import json
import os
import sys

sys.dont_write_bytecode = True
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, BinaryIO, Dict, Optional, Tuple  # noqa: E402

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))

WORKERS = 8
# 1 件の本文の上限（壊れた見出しで巨大な読み込みをしない）。外枠（src-tauri/src/bridge.rs の MAX_BODY）と同じ値
MAX_BODY = int(os.environ.get("ACTUALS_BRIDGE_MAX_BODY", "") or 64 * 1024 * 1024)
TOO_LARGE_MESSAGE = "64MB を超えるものは渡せません。"


class FrameTooLarge(Exception):
    def __init__(self, head: dict, size: int) -> None:
        super().__init__(f"本文が大きすぎます: {size}")
        self.head, self.size = head, size


def _discard(stream: BinaryIO, size: int) -> None:
    left = size
    while left > 0:
        chunk = stream.read(min(left, 1024 * 1024))
        if not chunk:
            raise EOFError("本文の途中で終わりました")
        left -= len(chunk)


def read_frame(stream: BinaryIO) -> Optional[Tuple[dict, bytes]]:
    """1 件読む。相手が閉じたら None。本文が上限を超えたら FrameTooLarge（読み捨て済み。次から続けられる）。"""
    line = stream.readline()
    if not line:
        return None
    head = json.loads(line.decode("utf-8"))
    size = int(head.get("len", 0) or 0)
    if size < 0:
        raise ValueError(f"本文の長さが不正です: {size}")
    if size > MAX_BODY:
        _discard(stream, size)
        raise FrameTooLarge(head, size)
    body = b""
    while len(body) < size:
        chunk = stream.read(size - len(body))
        if not chunk:
            raise EOFError("本文の途中で終わりました")
        body += chunk
    return head, body


class FrameWriter:
    """応答と知らせを 1 件ずつ丸ごと書く（複数のスレッドから来る）。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def write(self, head: dict, body: bytes = b"") -> None:
        head = dict(head)
        if body or "id" in head:
            head["len"] = len(body)
        data = json.dumps(head, ensure_ascii=False).encode("utf-8") + b"\n" + body
        with self._lock:
            self._stream.write(data)
            self._stream.flush()

    def event(self, name: str, **fields: Any) -> None:
        self.write({"event": name, **fields})


def _protect_stdout() -> BinaryIO:
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    return proto


def serve(writer: FrameWriter, reader: BinaryIO) -> int:
    """標準入力から要求を読み、別スレッドで答える。相手が閉じたら終わる。"""
    from actuals import logsetup
    from actuals.settings import Paths, Settings, app_info
    from actuals.web import server as S

    paths = Paths.default()
    s = Settings(paths).resolved()
    logsetup.setup(s["log_dir"], paths.logs, console=False, keep_days=int(s.get("log_keep_days") or 90),
                   version=str(app_info().get("version") or ""))
    logsetup.started("デスクトップ版", data_dir=str(paths.base), log_dir=logsetup.status()["wanted"],
                     exe_pid=os.environ.get("ACTUALS_EXE_PID", ""))
    log = __import__("logging").getLogger("actuals.bridge")
    rc = 1
    try:
        lock = S.LocalLock(paths.runtime / "web.lock", "desktop")
        try:
            lock.__enter__()
        except S.LockHeld:
            held = S._read_json(paths.runtime / "web.lock")
            who = S.MODE_LABEL.get(held.get("mode", "browser"), "ブラウザ版")
            message = (f"倉入実績集計の{who}が動いています。デスクトップ版とブラウザ版は同時に使えません。"
                       if who != "デスクトップ版" else "倉入実績集計のデスクトップ版がもう動いています。")
            log.warning("起動しません: %s（%s）", message, held)
            logsetup.set_stop_reason(f"{who}が動いていたため起動しない")
            writer.event("fatal", message=message,
                         hint=f"{who}の画面を使うか、閉じてから（止まらない時は stop.bat）デスクトップ版を開いてください。",
                         log_dir=logsetup.active_dir())
            rc = 5
            return rc
        app = S.WebApp(paths)
        app.token = os.environ.get("ACTUALS_TOKEN") or app.token
        app.bridge = lambda name: _safe_event(writer, name)
        handler = S.make_handler(app)
        info_file = paths.runtime / "server.json"
        info_file.write_text(json.dumps({"url": "", "instance": app.instance, **S.lock_record("desktop")}),
                             encoding="utf-8")
        pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="bridge")

        def answer(head: dict, body: bytes) -> None:
            try:
                status, headers, data = S.call_handler(handler, (head.get("method") or "GET").upper(),
                                                       head.get("path") or "/", head.get("query") or "",
                                                       head.get("headers") or [], body)
            except Exception as exc:  # noqa: BLE001  1 件の失敗で止めない
                ref = logsetup.record_error(exc, where=f"bridge {head.get('path')}")
                status, headers = 500, [("Content-Type", "text/plain; charset=utf-8")]
                data = f"内部エラー（{ref}）: {exc}".encode("utf-8")
            try:
                writer.write({"id": head.get("id"), "status": status, "headers": [list(h) for h in headers]}, data)
            except (BrokenPipeError, OSError):
                pass

        log.info("外枠からの要求を受け付けます（ポートは使いません）")
        writer.event("started", version=app.info.get("version"))
        try:
            while True:
                try:
                    frame = read_frame(reader)
                except FrameTooLarge as big:
                    log.warning("大きすぎる要求を断りました: %s（%s バイト）", big.head.get("path"), big.size)
                    _safe_write(writer, {"id": big.head.get("id"), "status": 413,
                                         "headers": [["Content-Type", "application/json"]]},
                                json.dumps({"error": {"code": "too_large", "message": TOO_LARGE_MESSAGE}},
                                           ensure_ascii=False).encode("utf-8"))
                    continue
                except (ValueError, EOFError) as exc:
                    log.error("要求を読めませんでした: %s", exc)
                    break
                if frame is None:
                    log.info("外枠が閉じました")
                    break
                pool.submit(answer, *frame)
        finally:
            if app.service.rne.running:       # SymNavi・Excel を残さない
                app.service.rne.cancel()
            pool.shutdown(wait=True)
            for f in (info_file,):
                try:
                    f.unlink()
                except OSError:
                    pass
            lock.__exit__(None, None, None)
            logsetup.set_stop_reason({"user": "画面の「終了」", "": "窓を閉じた"}.get(
                app.shutdown_reason, app.shutdown_reason or "窓を閉じた"))
        rc = 0
        return rc
    except Exception as exc:  # noqa: BLE001
        ref = logsetup.record_error(exc, where="デスクトップ版の起動")
        _safe_event(writer, "fatal", message=f"起動中に思わぬエラー（{ref}）: {exc}",
                    hint="ログを確認してください。", log_dir=logsetup.active_dir())
        return 1
    finally:
        logsetup.stopped(rc)


def _safe_event(writer: FrameWriter, name: str, **fields: Any) -> None:
    try:
        writer.event(name, **fields)
    except (BrokenPipeError, OSError):
        pass


def _safe_write(writer: FrameWriter, head: Dict[str, Any], body: bytes) -> None:
    try:
        writer.write(head, body)
    except (BrokenPipeError, OSError):
        pass


def main() -> int:
    proto = _protect_stdout()
    writer = FrameWriter(proto)
    if sys.version_info < (3, 8):
        writer.event("fatal", message=f"Python {sys.version.split()[0]} は古すぎます", hint="Python 3.8 以上を入れてください。")
        return 1
    return serve(writer, sys.stdin.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
