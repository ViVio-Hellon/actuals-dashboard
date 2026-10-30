"""「データ更新」: RNEConverter に倉入実績の抽出を頼む（SymNavi の処理はこのツールに持たない）。

    <python> <RNEConverter>/start_app.py --rne <RNE ファイル> --output <名前> --save-dir <置くフォルダ> --table 倉入実績

RNEConverter が SymNavi でその RNE を実行し、<置くフォルダ>/<名前>.sqlite3 を作る（RNEConverter の RNE 一覧・
保存した設定は書き換えない）。終わったらこのツールが取り込む。
終了コード（RNEConverter）: 0 成功 / 1 一部失敗 / 2 設定エラー / 3 別の実行と重なった。
"""
from __future__ import annotations

import collections
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional

log = logging.getLogger("actuals.rne")

RC_TEXT = {0: "成功", 1: "失敗（RNEConverter の履歴タブで原因を確認してください）",
           2: "RNEConverter の設定エラー（SymNavi の ID・パスワードが RNEConverter に登録されているか確認してください）",
           3: "RNEConverter が別の実行中でした（終わってからもう一度押してください）"}


class RneRunner:
    def __init__(self):
        self._mu = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None
        self.lines: Deque[str] = collections.deque(maxlen=200)
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.rc: Optional[int] = None
        self.after: Dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self.proc is not None and self.rc is None

    def status(self) -> Dict[str, Any]:
        now = time.time()
        return {"running": self.running, "rc": self.rc, "rc_text": RC_TEXT.get(self.rc, "") if self.rc is not None
                else "", "elapsed": int((self.finished or now) - self.started) if self.started else 0,
                "lines": list(self.lines)[-30:], "after": self.after}

    def start(self, rne_dir: str, rne_path: str, output: str, save_dir: str, table: str, python_exe: str,
              on_done: Callable[[int], Dict[str, Any]]) -> None:
        with self._mu:
            if self.running:
                raise RuntimeError("すでに実行中です。終わるまでお待ちください。")
            root = Path(rne_dir)
            entry = root / "start_app.py"
            if not rne_dir or not entry.is_file():
                raise RuntimeError(f"RNEConverter のフォルダが見つかりません: {rne_dir or '（未設定）'}。"
                                   "設定画面で start_app.py のあるフォルダを指定してください。")
            exe = python_exe or sys.executable
            if exe.lower().endswith("pythonw.exe"):
                cand = Path(exe).with_name("python.exe")
                exe = str(cand) if cand.exists() else exe
            args = [exe, str(entry), "--rne", rne_path, "--output", output, "--save-dir", save_dir,
                    "--key-column", "ﾛｯﾄ番号", "--text-column", "ﾛｯﾄ番号", "--text-column", "検査番号"]
            if table:
                args += ["--table", table]
            flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            log.info("RNEConverter を呼びます: %s", " ".join(args))
            self.lines.clear()
            self.rc, self.finished, self.after = None, None, {}
            self.started = time.time()
            self.proc = subprocess.Popen(args, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=flags, env=env)
        threading.Thread(target=self._pump, args=(on_done,), daemon=True).start()

    def _pump(self, on_done: Callable[[int], Dict[str, Any]]) -> None:
        assert self.proc and self.proc.stdout
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.lines.append(line)
        rc = self.proc.wait()
        self.finished = time.time()
        log.info("RNEConverter が終わりました: rc=%s（%d 秒）", rc, int(self.finished - (self.started or 0)))
        try:
            self.after = on_done(rc) or {}
        except Exception as e:  # noqa: BLE001
            log.exception("抽出後の取り込みに失敗")
            self.after = {"error": str(e)}
        self.rc = rc
