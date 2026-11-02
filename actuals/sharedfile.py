"""共有フォルダ上の SQLite を、複数の PC から安全に読み書きする。

SMB 上の SQLite に複数の PC が直接書くと、ロックが正しく効かずに壊れることがある（SQLite 公式の注意）。
そこで共有上のファイルは「完成品を差し替えるだけ」にする（RNEConverter と同じ方針）:

書く   1. 共有フォルダに <名前>.lock を作る（O_EXCL。取れなければ最大 lock_wait 秒待つ）
       2. 共有のファイルを手元に写し、手元で 1 トランザクションで直す（版番号を 1 つ上げる）
       3. 手元のファイルを共有に一時名で置き、os.replace で差し替える
       4. 日ごとの控えを backup/ に残し、ロックを消す
読む   共有のファイルが変わっていれば（大きさ・更新時刻）手元に写し、手元の写しを読み取り専用で開いて
       すぐ閉じる。読む側はロックを取らない（差し替えは原子的なので、半端な状態は見えない）
       共有に届かない時は、手元にある最後の写しを読む（画面に「控えを表示中」と出す）

接続は持ち続けない（長時間の放置で古いデータを掴んだままにしない）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple, TypeVar, Union

from . import logsetup

log = logging.getLogger("actuals.shared")
T = TypeVar("T")


class SharedBusy(RuntimeError):
    """別の PC が書いている（ロックが取れない）。"""


class SharedUnreachable(RuntimeError):
    """共有フォルダに届かない。"""


class _TimedOut(Exception):
    pass


def within(fn: Callable[[], T], timeout: float) -> T:
    """共有フォルダの応答が止まっても画面を止めない（別スレッドで試し、時間を過ぎたら諦める）。"""
    box: Dict[str, Any] = {}

    def run():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise _TimedOut()
    if "e" in box:
        raise box["e"]
    return box["v"]


def who() -> Dict[str, str]:
    return {"user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "pc": os.environ.get("COMPUTERNAME") or socket.gethostname()}


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SharedFile:
    def __init__(self, shared_path: Path, cache_dir: Path, schema: Callable[[sqlite3.Connection], None],
                 lock_wait: float = 10.0, lock_stale_sec: float = 180.0, net_timeout: float = 8.0,
                 keep_backups: int = 14):
        self.shared = Path(shared_path)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.copy_path = self.cache_dir / self.shared.name
        self.fp_path = self.cache_dir / (self.shared.name + ".json")
        self.schema = schema
        self.lock_wait = lock_wait
        self.lock_stale_sec = lock_stale_sec
        self.net_timeout = net_timeout
        self.keep_backups = keep_backups
        self._mu = threading.Lock()
        self.last_error = ""
        self.stale = False          # 共有に届かず、手元の控えを読んでいる

    # ---------- 読む ----------
    def _fingerprint(self) -> Optional[Tuple[int, int]]:
        try:
            st = within(lambda: self.shared.stat(), self.net_timeout)
        except FileNotFoundError:
            return None
        except (_TimedOut, OSError) as e:
            raise SharedUnreachable(f"共有フォルダに届きません: {self.shared.parent}（{type(e).__name__}）") from e
        return st.st_size, st.st_mtime_ns

    def _cached_fp(self) -> Optional[list]:
        try:
            return json.loads(self.fp_path.read_text(encoding="utf-8")).get("fp")
        except (OSError, ValueError):
            return None

    def refresh(self) -> bool:
        """共有のファイルが変わっていれば手元に写す。写したら True。"""
        with self._mu:
            try:
                fp = self._fingerprint()
            except SharedUnreachable as e:
                self.stale, self.last_error = True, str(e)
                return False
            self.stale, self.last_error = False, ""
            if fp is None:
                return False
            if self.copy_path.exists() and self._cached_fp() == list(fp):
                return False
            tmp = self.copy_path.with_suffix(".copying")
            t0 = time.monotonic()
            try:
                within(lambda: shutil.copyfile(self.shared, tmp), max(self.net_timeout, 30))
                os.replace(tmp, self.copy_path)
            except (_TimedOut, OSError) as e:
                self.stale, self.last_error = True, f"共有のファイルを写せません: {e}"
                log.warning("共有のファイルを写せません（手元の控えで続けます）: %s（%s）", self.shared, e)
                return False
            logsetup.loaded("共有のファイルを手元に写す（ほかの PC の保存を反映）", self.shared, None,
                            time.monotonic() - t0, bytes=fp[0])
            self.fp_path.write_text(json.dumps({"fp": list(fp), "at": now_text()}), encoding="utf-8")
            return True

    def fingerprint(self) -> str:
        """手元の写しの指紋（画面の再集計・キャッシュの鍵）。"""
        fp = self._cached_fp()
        return "-".join(map(str, fp)) if fp else "none"

    @contextmanager
    def read(self) -> Iterator[Optional[sqlite3.Connection]]:
        """手元の写しを読み取り専用で開く。まだ一度も作られていなければ None。"""
        logsetup.using(self.shared)
        self.refresh()
        if not self.copy_path.exists():
            yield None
            return
        conn = sqlite3.connect(f"file:{self.copy_path.as_posix()}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ---------- 書く ----------
    @property
    def lock_path(self) -> Path:
        return self.shared.with_suffix(".lock")

    def _acquire(self) -> None:
        t0 = time.time()
        me = json.dumps({**who(), "pid": os.getpid(), "at": now_text()}, ensure_ascii=False)
        while True:
            try:
                fd = within(lambda: os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY), self.net_timeout)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(me)
                return
            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    holder = self.lock_path.read_text(encoding="utf-8")
                except OSError:
                    age, holder = 0, ""
                if age > self.lock_stale_sec:
                    log.warning("古いロックを外します（%d 秒前）: %s %s", int(age), self.lock_path, holder)
                    try:
                        self.lock_path.unlink()
                    except OSError:
                        pass
                    continue
                if time.time() - t0 > self.lock_wait:
                    who_ = ""
                    try:
                        h = json.loads(holder)
                        who_ = f"（{h.get('pc', '')} {h.get('user', '')} が {h.get('at', '')} から）"
                    except ValueError:
                        pass
                    raise SharedBusy(f"別の PC が保存中です{who_}。少し待ってからもう一度押してください。")
                time.sleep(0.3)
            except (_TimedOut, OSError) as e:
                raise SharedUnreachable(f"共有フォルダに書けません: {self.shared.parent}（{e}）") from e

    def _release(self) -> None:
        try:
            self.lock_path.unlink()
        except OSError as e:
            log.warning("ロックを消せません: %s (%s)", self.lock_path, e)

    def write(self, mutate: Callable[[sqlite3.Connection], T], what: str = "",
              count: Union[None, int, Callable[[T], Optional[int]]] = None) -> T:
        """ロックを取り、手元で直してから共有を差し替える。mutate は 1 トランザクションの中で呼ばれる。

        what・count はログ（保存先・件数・処理時間）に書くため。count は結果から数える関数でもよい。
        """
        t0 = time.monotonic()
        logsetup.using(self.shared)
        try:
            within(lambda: self.shared.parent.mkdir(parents=True, exist_ok=True), self.net_timeout)
        except (_TimedOut, OSError) as e:
            raise SharedUnreachable(f"共有フォルダに届きません: {self.shared.parent}（{e}）") from e
        with self._mu:
            self._acquire()
            try:
                work = self.cache_dir / (self.shared.stem + ".work.sqlite3")
                if work.exists():
                    work.unlink()
                if within(lambda: self.shared.exists(), self.net_timeout):
                    within(lambda: shutil.copyfile(self.shared, work), max(self.net_timeout, 30))
                conn = sqlite3.connect(str(work), timeout=10, isolation_level=None)
                try:
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA journal_mode=DELETE")
                    conn.execute("BEGIN IMMEDIATE")
                    self.schema(conn)
                    result = mutate(conn)
                    conn.execute("COMMIT")
                    ok = conn.execute("PRAGMA quick_check").fetchone()[0]
                    if ok != "ok":
                        raise RuntimeError(f"保存前の点検で異常が見つかりました: {ok}")
                except BaseException:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
                finally:
                    conn.close()
                self._backup()
                self._publish(work)
                os.replace(work, self.copy_path)
                fp = self._fingerprint()
                if fp:
                    self.fp_path.write_text(json.dumps({"fp": list(fp), "at": now_text()}), encoding="utf-8")
                try:
                    n = count(result) if callable(count) else count
                except Exception:  # noqa: BLE001
                    n = None
                logsetup.saved(what or self.shared.name, self.shared, n, time.monotonic() - t0)
                return result
            finally:
                self._release()

    def _publish(self, work: Path) -> None:
        tmp = self.shared.with_name(f".{self.shared.name}.{os.getpid()}.tmp")
        within(lambda: shutil.copyfile(work, tmp), max(self.net_timeout, 60))
        last: Optional[Exception] = None
        for i in range(10):
            try:
                os.replace(tmp, self.shared)
                return
            except PermissionError as e:   # 読んでいる PC が一瞬開いている（Windows）
                last = e
                time.sleep(0.3 * (i + 1))
        try:
            tmp.unlink()
        except OSError:
            pass
        raise SharedUnreachable(f"共有のファイルを差し替えられません（別の PC が開いたままの可能性）: {last}")

    def _backup(self) -> None:
        """その日の最初の保存の前に、共有のファイルを backup/ に控える（14 日分）。"""
        try:
            if not self.shared.exists():
                return
            bdir = self.shared.parent / "backup"
            bdir.mkdir(exist_ok=True)
            dst = bdir / f"{self.shared.stem}_{datetime.now():%Y%m%d}{self.shared.suffix}"
            if not dst.exists():
                t0 = time.monotonic()
                shutil.copyfile(self.shared, dst)
                logsetup.saved("その日最初の控え", dst, None, time.monotonic() - t0)
            olds = sorted(bdir.glob(f"{self.shared.stem}_*{self.shared.suffix}"))
            for p in olds[:-self.keep_backups]:
                p.unlink()
        except OSError as e:
            log.warning("控えを作れません（保存は続けます）: %s", e)
