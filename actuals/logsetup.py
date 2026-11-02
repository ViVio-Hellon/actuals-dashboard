"""ログ（all-tools 日報複合ツールと同じ決まり）。

【置き場所と名前】 <ログの出力パス> は「パスの設定」③-2。決めていない・届かない時は、この PC の
データの場所の logs（%USERPROFILE%\\ActualsDashboard\\logs）に同じ形で書く（ログが書けないことで止めない）。

    <ログの出力パス>\\actualsdashboard\\
      actualsdashboard_<PC>_<YYYYMMDD>.log           動いた記録（全部。人が読む）
      actualsdashboard_記録_<PC>_<YYYYMMDD>.jsonl    出来事（1 行 1 件の JSON: 起動・終了・処理・読込・保存・警告・エラー・操作）

  ツール ID は actualsdashboard（半角英小文字）。名前に PC 名を入れるので、共有フォルダでも何台もが同じファイルに書かない。

【前の形】(v0.1.0) <ログの出力パス>\\actuals-dashboard\\actuals-dashboard_<PC>_<YYYYMMDD>.log
  移さない・消さない（移行として）・もう書かない。古いものを片付ける時だけ、新しい形と同じに扱う
  （この PC・このツールのものだけ）。ほかのツールのファイル・フォルダ（nippou\\ kanban\\ など）には触らない。

【常に残すもの】
  起動・終了      started() / stopped()   ツール名・版・PC・利用者・稼働秒
  処理の開始・終了 timed()                 開始と終了を組で。処理時間で重さを分ける（下の表）
  データ読み込み   loaded()                読込先・件数・秒
  データ保存      saved()                 保存先・件数・秒
  エラー          record_error()          下の「エラーの時」
  警告            log.warning(...)        すべて出来事にも残る（継続か中断かは文中に書く）
  操作履歴        operation()             画面で押した主な操作（パスワードなどは入れない）

【処理時間】 3 秒未満 INFO ／ 3 秒以上・10 秒以上・30 秒以上 WARNING（どれかを書く）／ エラー ERROR。
  終わらない処理も見張り、30 秒・10 分を超えた時とその後 10 分ごとに「長引いています」を WARNING で残す
  （止まったまま強制終了されても、どこで止まっていたかが分かる）。

【エラーの時】 日時・ツール名と版・PC 名・利用者・実行していた処理（入れ子）・エラーの種類・メッセージ・
  発生箇所（スタックトレース）・使っていたファイルや DB・直前の処理（最近の記録）。画面にはエラー番号
  （E yymmdd-HHMMSS-xx）を出し、同じ番号で記録を探せる。

書くのは裏の 1 本のスレッド（共有に届かない時に画面を待たせない）。届かなければ手元へ書き、5 分後にまた試す。
各行に [操作番号]（画面からの要求ごとの 16 進 6 桁）。登録されたパスワードは、どの記録にも出さない（**** にする）。
"""
from __future__ import annotations

import atexit
import collections
import getpass
import json
import logging
import os
import queue
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Sequence, Tuple

TOOL_ID = "actualsdashboard"
TOOL_NAME = "倉入実績集計"
PREFIX = f"{TOOL_ID}_"
EVENT_KIND = "記録"
#: 前の形（v0.1.0）。片付ける時・探す時だけ使う
LEGACY_DIR = "actuals-dashboard"
LEGACY_PREFIX = "actuals-dashboard_"

#: 処理時間の分け方（秒, ログに書く言葉）。大きい方から見る
SLOW_STEPS: Tuple[Tuple[float, str], ...] = ((30.0, "30秒以上"), (10.0, "10秒以上"), (3.0, "3秒以上"))
#: 終わらない処理の見張り（秒）: 最初の知らせ、その後の間隔
LONG_FIRST, LONG_EVERY = 30.0, 600.0
WATCH_INTERVAL = 5.0
RETRY_SECONDS = 300.0
QUEUE_MAX = 20000

log = logging.getLogger("actuals.log")

_local = threading.local()
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')
_SECRETS: set = set()
_EXPECTED: List[type] = []
_RECENT: Deque[str] = collections.deque(maxlen=40)
_active: Dict[int, "_Frame"] = {}
_active_mu = threading.Lock()
_info: Dict[str, Any] = {"version": "", "started": None, "stopped": False, "stop_reason": "", "mode": ""}


# ---------------------------------------------------------------- 名前
def safe(name: str) -> str:
    """ファイル名に入れる PC 名（使えない字と空白は _）。"""
    return _UNSAFE.sub("_", str(name or "")).strip("_") or "PC"


def pc_name() -> str:
    return safe(os.environ.get("COMPUTERNAME") or socket.gethostname())


def user_name() -> str:
    try:
        return os.environ.get("USERNAME") or getpass.getuser()
    except Exception:  # noqa: BLE001
        return ""


def tool_dir(base: Path) -> Path:
    """<ログの出力パス>\\actualsdashboard（出力パスの直下には置かない）。"""
    return Path(base) / TOOL_ID


def text_name(pc: str, day: date) -> str:
    return f"{PREFIX}{safe(pc)}_{day:%Y%m%d}.log"


def event_name(pc: str, day: date) -> str:
    return f"{PREFIX}{EVENT_KIND}_{safe(pc)}_{day:%Y%m%d}.jsonl"


def own_files(base: Path, pc: str) -> List[Tuple[Path, str]]:
    """このツールの、この PC のログ（フォルダ, glob）。新しい形が先、前の形が後。"""
    pc = safe(pc)
    return [(tool_dir(base), f"{PREFIX}{pc}_*.log"),
            (tool_dir(base), f"{PREFIX}{EVENT_KIND}_{pc}_*.jsonl"),
            (Path(base) / LEGACY_DIR, f"{LEGACY_PREFIX}{pc}_*.log")]


def log_places(base: Path) -> List[Tuple[Path, str]]:
    """ログを読む・探す所（フォルダ, 名前の頭）。全 PC 分。新しい形が先。ほかのツールの物は含めない。"""
    return [(tool_dir(base), PREFIX), (Path(base) / LEGACY_DIR, LEGACY_PREFIX)]


_DAY = re.compile(r"_(\d{8})\.(?:log|jsonl)$")


def _file_day(p: Path) -> Optional[date]:
    m = _DAY.search(p.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------- 秘密
def secret(value: str) -> None:
    """記録に出してはいけない文字（パスワード）を覚える。出てきたら **** にする。"""
    if value and len(value) >= 2:
        _SECRETS.add(value)


def _mask(text: str) -> str:
    for s in _SECRETS:
        if s in text:
            text = text.replace(s, "****")
    return text


# ---------------------------------------------------------------- 書く係（裏の 1 本）
class _Writer:
    def __init__(self) -> None:
        self.q: "queue.Queue[Tuple[str, Any]]" = queue.Queue(maxsize=QUEUE_MAX)
        self.primary: Optional[Path] = None
        self.local: Path = Path("logs")
        self.pc = pc_name()
        self.problem = ""
        self.failed_at = 0.0
        self.active: Optional[Path] = None
        self.dropped = 0
        self.thread: Optional[threading.Thread] = None
        self._mu = threading.Lock()

    def configure(self, primary: Optional[Path], local: Path) -> None:
        with self._mu:
            self.primary, self.local = primary, local
            self.problem, self.failed_at = "", 0.0
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="log-writer", daemon=True)
                self.thread.start()

    def put(self, item: Tuple[str, Any]) -> None:
        try:
            self.q.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def _bases(self) -> List[Path]:
        bases: List[Path] = []
        if self.primary is not None and (not self.failed_at or time.time() - self.failed_at >= RETRY_SECONDS):
            bases.append(self.primary)
        if not bases or self.local != bases[0]:
            bases.append(self.local)
        return bases

    def _write(self, name_of: Callable[[str, date], str], day: date, text: str) -> None:
        for base in self._bases():
            try:
                d = tool_dir(base)
                d.mkdir(parents=True, exist_ok=True)
                with open(d / name_of(self.pc, day), "a", encoding="utf-8") as f:
                    f.write(text)
                if self.primary is not None and base == self.primary and self.failed_at:
                    self.failed_at, self.problem = 0.0, ""
                self.active = base
                return
            except OSError as e:
                if self.primary is not None and base == self.primary:
                    self.failed_at = time.time()
                    self.problem = f"{tool_dir(base)} に書けません（{type(e).__name__}: {e}）。この PC の {tool_dir(self.local)} に書いています"
                continue

    def _cleanup(self, keep_days: int) -> None:
        limit = date.today() - timedelta(days=max(1, keep_days))
        bases = {self.local} | ({self.primary} if self.primary is not None else set())
        for base in bases:
            for folder, pattern in own_files(base, self.pc):
                try:
                    for p in folder.glob(pattern):
                        d = _file_day(p) or datetime.fromtimestamp(p.stat().st_mtime).date()
                        if d < limit:
                            p.unlink()
                except OSError:
                    continue

    def _loop(self) -> None:
        while True:
            kind, payload = self.q.get()
            try:
                if kind == "text":
                    self._write(text_name, payload[0], payload[1])
                elif kind == "event":
                    self._write(event_name, payload[0], payload[1])
                elif kind == "cleanup":
                    self._cleanup(int(payload))
                elif kind == "flush":
                    payload.set()
            except Exception:  # noqa: BLE001  ログの係は止まらない
                pass


_writer = _Writer()


def flush(timeout: float = 10.0) -> bool:
    """ここまでの記録を書き終えるまで待つ（試験・終了時）。"""
    if _writer.thread is None or not _writer.thread.is_alive():
        return True
    ev = threading.Event()
    _writer.put(("flush", ev))
    return ev.wait(timeout)


# ---------------------------------------------------------------- 処理の枠
class _Frame:
    def __init__(self, name: str, kind: str, fields: Dict[str, Any], files: Sequence[Any], quiet: bool):
        self.name, self.kind, self.fields, self.quiet = name, kind, dict(fields), quiet
        self.files: List[str] = [str(f) for f in files if f]
        self.t0 = time.monotonic()
        self.started = datetime.now()
        self.thread = threading.current_thread().name
        self.op = getattr(_local, "op", "-")
        self.count: Optional[int] = None
        self.result: Dict[str, Any] = {}
        self.next_warn = LONG_FIRST

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def note(self, **fields: Any) -> None:
        """終わりの記録に足す（件数など）。"""
        self.result.update(fields)

    def use(self, *paths: Any) -> None:
        for p in paths:
            if p and str(p) not in self.files:
                self.files.append(str(p))


def _stack() -> List[_Frame]:
    st = getattr(_local, "stack", None)
    if st is None:
        st = _local.stack = []
    return st


def using(*paths: Any) -> None:
    """いま動いている処理が使っているファイル・DB（エラーの時に残す）。"""
    st = _stack()
    if st:
        st[-1].use(*paths)


def expected_errors(*classes: type) -> None:
    """業務上ありうる断り（入力の誤り・別の人が保存中 など）。ERROR ではなく WARNING「中断」にする。"""
    for c in classes:
        if c not in _EXPECTED:
            _EXPECTED.append(c)


def slow_label(seconds: float) -> str:
    for limit, label in SLOW_STEPS:
        if seconds >= limit:
            return label
    return ""


def _fmt(fields: Dict[str, Any]) -> str:
    items = [f"{k}={v}" for k, v in fields.items() if v not in (None, "")]
    return f"（{' '.join(items)}）" if items else ""


def _ev(kind: str, *parts: Dict[str, Any], **fields: Any) -> Dict[str, Any]:
    """logging の extra。後に書いたものが勝つ（呼び出し側の項目名が重なっても落ちない）。"""
    ev: Dict[str, Any] = {}
    for p in parts:
        ev.update(p)
    ev.update(fields)
    ev["kind"] = kind
    return {"event": ev}


@contextmanager
def timed(name: str, *, kind: str = "処理", files: Sequence[Any] = (), quiet: bool = False,
          expected: Optional[Callable[[BaseException], bool]] = None, **fields: Any) -> Iterator[_Frame]:
    """処理の開始と終了を組で残し、処理時間を測る。

    quiet=True は「開始」を書かず、終了も 3 秒以上・失敗の時だけ書く（画面の読み込みなど回数の多いもの）。
    """
    fr = _Frame(name, kind, fields, files, quiet)
    st = _stack()
    st.append(fr)
    with _active_mu:
        _active[id(fr)] = fr
    if not quiet:
        log.info("▶ %s 開始%s", name, _fmt(fields), extra=_ev("処理開始", fields, process=name))
    try:
        yield fr
    except Exception as e:
        secs = fr.elapsed()
        if isinstance(e, tuple(_EXPECTED)) or (expected is not None and expected(e)):
            log.warning("■ %s 中断 %.2f 秒（%s: %s）", name, secs, type(e).__name__, e,
                        extra=_ev("処理終了", fields, fr.result, process=name, result="中断", seconds=round(secs, 3),
                                  started=fr.started.isoformat(timespec="milliseconds"),
                                  reason=f"{type(e).__name__}: {e}"))
        else:
            ref = getattr(e, "_log_ref", None) or record_error(e)
            log.error("■ %s 失敗 %.2f 秒（%s %s）", name, secs, ref, type(e).__name__,
                      extra=_ev("処理終了", fields, fr.result, process=name, result="エラー", seconds=round(secs, 3),
                                ref=ref, started=fr.started.isoformat(timespec="milliseconds")))
        raise
    else:
        secs = fr.elapsed()
        label = slow_label(secs)
        if not (quiet and not label):
            extra = dict(fields, **fr.result)
            if fr.count is not None:
                extra["count"] = fr.count
            log.log(logging.WARNING if label else logging.INFO, "■ %s 終了 %.2f 秒%s%s", name, secs,
                    f" [{label}]" if label else "", _fmt(extra),
                    extra=_ev("処理終了", extra, process=name, result="正常", seconds=round(secs, 3), slow=label,
                              started=fr.started.isoformat(timespec="milliseconds")))
    finally:
        st.pop()
        with _active_mu:
            _active.pop(id(fr), None)


def _io(kind: str, what: str, target: Any, count: Optional[int], seconds: float, **fields: Any) -> None:
    label = slow_label(seconds)
    n = "" if count is None else f" {count:,} 件"
    log.log(logging.WARNING if label else logging.INFO, "%s %s: %s%s %.2f 秒%s%s", kind, what, target, n, seconds,
            f" [{label}]" if label else "", _fmt(fields),
            extra=_ev(kind, fields, what=what, target=str(target), count=count, seconds=round(seconds, 3), slow=label))
    using(target)


def loaded(what: str, target: Any, count: Optional[int], seconds: float, **fields: Any) -> None:
    """データ読み込み: 読込先・件数・処理時間。"""
    _io("読込", what, target, count, seconds, **fields)


def saved(what: str, target: Any, count: Optional[int], seconds: float, **fields: Any) -> None:
    """データ保存: 保存先・件数・処理時間。"""
    _io("保存", what, target, count, seconds, **fields)


def operation(action: str, **details: Any) -> None:
    """操作履歴（画面で押した主な操作）。パスワードなど秘密は渡さない。"""
    log.info("操作: %s%s", action, _fmt(details), extra=_ev("操作", details, action=action))


# ---------------------------------------------------------------- エラー
def new_operation_id() -> str:
    oid = secrets.token_hex(3)
    _local.op = oid
    return oid


def set_operation(oid: str) -> None:
    _local.op = oid or "-"


def current_operation() -> str:
    return getattr(_local, "op", "-")


def clear_operation() -> None:
    _local.op = "-"


def new_error_ref() -> str:
    return f"E {datetime.now():%y%m%d-%H%M%S}-{secrets.token_hex(1)}"


def _context() -> Dict[str, Any]:
    st = list(_stack())
    files: List[str] = []
    for fr in st:
        for f in fr.files:
            if f not in files:
                files.append(f)
    return {"processes": [{"name": fr.name, "elapsed": round(fr.elapsed(), 3),
                           "started": fr.started.isoformat(timespec="milliseconds"), **fr.fields} for fr in st],
            "process": " > ".join(fr.name for fr in st), "files": files, "recent": list(_RECENT)[-15:]}


def record_error(exc: BaseException, where: str = "") -> str:
    """エラーを詳しく残し、エラー番号を返す（同じ例外は 1 回だけ）。"""
    ref = getattr(exc, "_log_ref", None)
    if ref:
        return ref
    ref = new_error_ref()
    try:
        exc._log_ref = ref  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ctx = _context()
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    lines = [f"エラー {ref}: {type(exc).__name__}: {exc}",
             f"  ツール: {TOOL_NAME}（{TOOL_ID}）版 {_info['version']}  PC: {pc_name()}  利用者: {user_name()}  pid: {os.getpid()}",
             f"  実行していた処理: {ctx['process'] or where or '（処理の外）'}"]
    for p in ctx["processes"]:
        lines.append(f"    - {p['name']}（{p['started']} から {p['elapsed']} 秒）")
    lines.append(f"  使っていたファイル・DB: {', '.join(ctx['files']) or '（なし）'}")
    lines.append("  直前の処理:")
    lines += [f"    {r}" for r in ctx["recent"]] or ["    （記録なし）"]
    lines.append(tb.rstrip())
    log.error("\n".join(lines), extra=_ev("エラー", ctx, ref=ref, where=where, error_type=type(exc).__name__,
                                          error_message=str(exc), traceback=tb, _full=True))
    return ref


# ---------------------------------------------------------------- 起動・終了
def started(mode: str, **fields: Any) -> None:
    _info.update(started=datetime.now(), stopped=False, mode=mode)
    log.info("起動: %s（%s）版 %s  モード %s  PC %s  利用者 %s  pid %d  Python %s%s", TOOL_NAME, TOOL_ID,
             _info["version"], mode, pc_name(), user_name(), os.getpid(), sys.version.split()[0], _fmt(fields),
             extra=_ev("起動", fields, mode=mode, python=sys.version.split()[0], executable=sys.executable))


def set_stop_reason(reason: str) -> None:
    _info["stop_reason"] = reason


def stopped(rc: int = 0, reason: str = "") -> None:
    if _info.get("stopped") or _info.get("started") is None:
        return
    _info["stopped"] = True
    reason = reason or _info.get("stop_reason") or "正常終了"
    up = (datetime.now() - _info["started"]).total_seconds()
    lvl = logging.INFO if rc == 0 else logging.WARNING
    log.log(lvl, "終了: %s 版 %s  モード %s  理由 %s  終了コード %d  稼働 %.1f 秒", TOOL_NAME, _info["version"],
            _info["mode"], reason, rc, up,
            extra=_ev("終了", mode=_info["mode"], reason=reason, rc=rc, uptime=round(up, 1)))
    flush(5.0)


def _at_exit() -> None:
    if _info.get("started") is not None and not _info.get("stopped"):
        stopped(0, "プロセスの終了（終了の記録なし）")
    flush(3.0)


atexit.register(_at_exit)


# ---------------------------------------------------------------- 見張り（終わらない処理）
def _watch() -> None:
    while True:
        time.sleep(WATCH_INTERVAL)
        try:
            with _active_mu:
                frames = list(_active.values())
            for fr in frames:
                secs = fr.elapsed()
                if secs >= fr.next_warn:
                    fr.next_warn = LONG_EVERY if fr.next_warn < LONG_EVERY else fr.next_warn + LONG_EVERY
                    label = "10分以上" if secs >= 600 else slow_label(secs)
                    log.warning("処理が長引いています: %s %d 秒経過 [%s]（%s から。まだ終わっていません）", fr.name,
                                int(secs), label, fr.started.strftime("%H:%M:%S"),
                                extra=dict(_ev("長引き", fr.fields, process=fr.name, seconds=int(secs), slow=label,
                                               started=fr.started.isoformat(timespec="milliseconds"),
                                               thread=fr.thread, files=fr.files), op=fr.op))
        except Exception:  # noqa: BLE001
            pass


_watcher: Dict[str, Optional[threading.Thread]] = {"t": None}


# ---------------------------------------------------------------- logging への取り付け
class _OpFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "op"):
            record.op = getattr(_local, "op", "-")
        return True


class _TextHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = _mask(self.format(record))
            _writer.put(("text", (date.fromtimestamp(record.created), text + "\n")))
            if not (getattr(record, "event", None) or {}).get("_full"):
                first = _mask(record.getMessage()).split("\n", 1)[0]
                _RECENT.append(f"{datetime.fromtimestamp(record.created):%H:%M:%S} [{record.threadName}] "
                               f"{record.levelname} {record.name}: {first}"[:300])
        except Exception:  # noqa: BLE001
            self.handleError(record)


class _EventHandler(logging.Handler):
    """出来事（JSONL）: event の付いた記録と、すべての WARNING・ERROR。"""

    def emit(self, record: logging.LogRecord) -> None:
        ev = getattr(record, "event", None)
        if ev is None and record.levelno < logging.WARNING:
            return
        try:
            d: Dict[str, Any] = {
                "at": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "tool": TOOL_ID, "tool_name": TOOL_NAME, "version": _info["version"], "pc": pc_name(),
                "user": user_name(), "pid": record.process, "op": getattr(record, "op", "-"),
                "thread": record.threadName, "level": record.levelname, "logger": record.name,
                "where": f"{record.pathname}:{record.lineno} {record.funcName}"}
            if ev is not None:
                d.update({k: v for k, v in ev.items() if not k.startswith("_")})
                d.setdefault("message", record.getMessage().split("\n", 1)[0])
            else:
                d["kind"] = "エラー" if record.levelno >= logging.ERROR else "警告"
                d["message"] = record.getMessage()
                if record.exc_info:
                    exc = record.exc_info[1]
                    d.update(error_type=type(exc).__name__, error_message=str(exc),
                             traceback="".join(traceback.format_exception(*record.exc_info)), **_context())
                    if exc is not None and not getattr(exc, "_log_ref", None):
                        d["ref"] = new_error_ref()
                        try:
                            exc._log_ref = d["ref"]  # type: ignore[attr-defined]
                        except Exception:  # noqa: BLE001
                            pass
                elif record.levelno >= logging.ERROR:
                    d.update(_context())
            line = _mask(json.dumps(d, ensure_ascii=False, default=str))
            _writer.put(("event", (date.fromtimestamp(record.created), line + "\n")))
        except Exception:  # noqa: BLE001
            self.handleError(record)


def setup(log_dir: str, fallback: Path, console: bool = False, keep_days: int = 90, version: str = "") -> None:
    """何度呼んでもよい（設定でログの出力パスを変えた時）。"""
    if version:
        _info["version"] = version
    root = logging.getLogger()
    before = status().get("wanted") if _writer.thread is not None else ""
    for h in list(root.handlers):
        if getattr(h, "_actuals", False):
            root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(op)s] %(levelname)s %(name)s: %(message)s")
    handlers: List[logging.Handler] = [_TextHandler(), _EventHandler()]
    if console:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h._actuals = True  # type: ignore[attr-defined]
        h.setFormatter(fmt)
        h.addFilter(_OpFilter())
        root.addHandler(h)
    root.setLevel(logging.INFO)
    _writer.configure(Path(log_dir) if log_dir else None, Path(fallback))
    _writer.put(("cleanup", keep_days))
    if _watcher["t"] is None or not _watcher["t"].is_alive():
        _watcher["t"] = threading.Thread(target=_watch, name="log-watch", daemon=True)
        _watcher["t"].start()
    after = status().get("wanted")
    if before and before != after:
        log.info("ログの書き先を移しました: %s → %s", before, after)


def status() -> Dict[str, Any]:
    """画面に出す: いま書いている所・名前・書けない理由・前の形のフォルダ。"""
    base = _writer.active or _writer.primary or _writer.local
    today = date.today()
    wanted = _writer.primary or _writer.local
    legacy = [str(Path(b) / LEGACY_DIR) for b in {wanted, _writer.local} if (Path(b) / LEGACY_DIR).is_dir()] \
        if wanted else []
    return {"dir": str(tool_dir(base)), "wanted": str(tool_dir(wanted)) if wanted else "",
            "text_file": text_name(_writer.pc, today), "event_file": event_name(_writer.pc, today),
            "problem": _writer.problem, "legacy": legacy, "tool_id": TOOL_ID}


def active_dir() -> str:
    return status()["dir"]


def detach() -> None:
    """取り付けた記録の口を外す（試験の後片付け）。"""
    flush(5.0)
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_actuals", False):
            root.removeHandler(h)
    _writer.primary, _writer.active, _writer.problem, _writer.failed_at = None, None, "", 0.0
