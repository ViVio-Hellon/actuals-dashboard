"""「データ更新」: SymNavi で RNE を実行し、当月の倉入実績を取り出して、そのまま取り込む。

旧 VBA の「データ作成」（SymNavi.exe を起動 → DDE で Open → Save(BIFF) → Close → xls を読む）にあたる。
SymNavi とのやり取りは RNEConverter のものを写した actuals/symnavi/ をそのまま使う（受け取り方は RNEConverter と同じ:
拡張 API で取り出し、だめな時は画面（DDE）で保存させた xls を読む。さらにだめなら旧 VBA と同じ Excel 経由）。

取り出した表は中間のファイルを作らず、共有の蓄積（actuals_history.sqlite3）にロット番号で取り込む。
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

log = logging.getLogger("actuals.update")

TAG = "KURAIRE"


class _Lines(logging.Handler):
    """SymNavi まわりのログを、画面の進み具合の欄にも出す。"""

    def __init__(self, sink: Deque[str]):
        super().__init__(logging.INFO)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.append(f"{time.strftime('%H:%M:%S')} {record.getMessage()}")
        except Exception:  # noqa: BLE001
            pass


class UpdateRunner:
    def __init__(self):
        self._mu = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.lines: Deque[str] = collections.deque(maxlen=300)
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.ok: Optional[bool] = None
        self.message = ""
        self.progress = 0.0
        self.step = ""
        self.after: Dict[str, Any] = {}
        self.cancelled = False

    @property
    def running(self) -> bool:
        return self.thread is not None and self.ok is None

    def status(self) -> Dict[str, Any]:
        now = time.time()
        return {"running": self.running, "ok": self.ok, "message": self.message,
                "rc": None if self.ok is None else (0 if self.ok else 1), "rc_text": self.message,
                "progress": round(self.progress * 100), "step": self.step,
                "elapsed": int((self.finished or now) - self.started) if self.started else 0,
                "lines": list(self.lines)[-40:], "after": self.after}

    def start(self, job: Callable[[Callable[[Optional[float], str], None]], Dict[str, Any]]) -> None:
        with self._mu:
            if self.running:
                raise RuntimeError("データ更新の実行中です。終わるまでお待ちください。")
            self.lines.clear()
            self.ok, self.message, self.after, self.progress, self.step = None, "", {}, 0.0, "開始"
            self.cancelled = False
            self.started, self.finished = time.time(), None
            self.thread = threading.Thread(target=self._run, args=(job,), name="data-update", daemon=True)
            self.thread.start()

    def _report(self, frac: Optional[float], text: str) -> None:
        if frac is not None:
            self.progress = max(0.0, min(1.0, frac))
        if text and text != self.step:
            self.step = text
            self.lines.append(f"{time.strftime('%H:%M:%S')} {text}")

    def _run(self, job) -> None:
        handler = _Lines(self.lines)
        logging.getLogger("actuals.symnavi").addHandler(handler)
        try:
            self.after = job(self._report) or {}
            self.ok = True
            self.message = "取り込みました"
        except Exception as e:  # noqa: BLE001
            log.exception("データ更新に失敗")
            self.after = {"error": str(e)}
            self.message = ("中断しました" if self.cancelled else str(e)) or type(e).__name__
            self.ok = False
        finally:
            logging.getLogger("actuals.symnavi").removeHandler(handler)
            self.finished = time.time()

    def cancel(self) -> bool:
        if not self.running:
            return False
        self.cancelled = True
        from .symnavi.extract.symnavi import kill_children
        n = kill_children()
        log.warning("データ更新を中断しました（終了させた子プロセス: %d）", n)
        return True


def extract_rows(rne_path: Path, work_dir: Path, exe: str, server: str, credentials: Dict[str, str],
                 startup_wait_sec: int, result_wait_sec: int, cscript_path: str,
                 report: Callable[[Optional[float], str], None]) -> Dict[str, Any]:
    """SymNavi で RNE を実行し、(列, 行) を返す。行は列名 → 文字の辞書（Excel の Page1 と同じ値）。"""
    from .symnavi.config import CscriptConfig, SymNaviConfig
    from .symnavi.extract.symnavi import SymNaviExtractor, VbsRunner
    from .symnavi.transform.tsv_reader import read_tsv
    if not credentials.get("id") or not credentials.get("pass"):
        raise RuntimeError("SymNavi の ID・パスワードが登録されていません（パスの設定 → SymNavi のログイン）。")
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg = SymNaviConfig(exe=exe, server=server, save_dir=str(work_dir), startup_wait_sec=int(startup_wait_sec),
                        dde_timeout_sec=int(result_wait_sec))
    ext = SymNaviExtractor(cfg, credentials, VbsRunner(CscriptConfig(path=cscript_path)))
    try:
        report(0.01, "SymNavi の準備をしています")
        try:
            ext.before_run([(TAG, rne_path)], work_dir)
        except Exception:  # noqa: BLE001  RNEConverter と同じく、準備の失敗では止めない
            log.warning("拡張 API の準備でエラー（画面で取り出します）", exc_info=True)
        tsv = ext.extract(rne_path, TAG, work_dir, progress=report)
    finally:
        try:
            ext.close_session()
        except Exception:  # noqa: BLE001
            log.warning("SymNavi を閉じる時にエラー", exc_info=True)
    columns, raw = read_tsv(tsv)
    try:
        tsv.unlink()
    except OSError:
        pass
    rows: List[Dict[str, Any]] = [dict(zip(columns, r)) for r in raw]
    return {"columns": columns, "rows": rows}
