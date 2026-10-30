"""SymNavi 連携。

旧 VBA の データ作成() の前半に相当する:
  Shell で SymNavi.exe を起動 → DDE [Open][Save BIFF][Close] → Temp.xls → 配列

Python 標準ライブラリには DDE も BIFF 読取も無いので、その 2 点だけ VBScript に委ねる。
  Python ── subprocess ── cscript.exe ── symnavi_dde.vbs ── Excel.Application (COM) ── DDE ── SymNavi.exe
                                     └── xls_to_tsv.vbs ── ADODB + ACE OLEDB (Excel 8.0) ── Temp.xls → TSV

SymNavi プロセスの起動と終了は Python が握る（PID で終了。旧 VBA の Word.Tasks による強制 Close は不要）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..config import CscriptConfig, SymNaviConfig
from . import clipboard, dde, naviapi, winwatch
from ..transform import xls_reader

log = logging.getLogger(__name__)

VBS_DIR = Path(__file__).resolve().parent / "vbs"

# この秒数を過ぎても SymNavi の結果が届かなければ、画面で確認を促す（旧 VBA では 15〜30 秒ほど）
SLOW_HINT_SEC = 35

# 実行中の子プロセス（SymNavi / cscript）。窓を閉じられた・中断されたときに必ず終了させる
_children: "set[subprocess.Popen]" = set()
# symnavi_dde.vbs が作った（見えない）Excel の PID。cscript を止めても Excel は残るので、PID で終了させる
_owned_pids: "set[int]" = set()


def _register(proc: subprocess.Popen) -> subprocess.Popen:
    _children.add(proc)
    return proc


def _unregister(proc: subprocess.Popen) -> None:
    _children.discard(proc)


def kill_children() -> int:
    """残っている子プロセスをすべて終了させる。戻り値は終了させた数。"""
    n = 0
    for pid in list(_owned_pids):
        if _kill_pid(pid):
            n += 1
        _owned_pids.discard(pid)
    for proc in list(_children):
        if proc.poll() is None:
            try:
                # まず普通に閉じる（SymNavi はサーバからログオフできる）。応答が無ければ強制終了
                if winwatch.post_close(proc.pid):
                    try:
                        proc.wait(timeout=5)
                        n += 1
                        _children.discard(proc)
                        continue
                    except subprocess.TimeoutExpired:
                        pass
                proc.kill()
                proc.wait(timeout=5)
                n += 1
            except Exception:  # noqa: BLE001
                pass
        _children.discard(proc)
    return n


def _kill_pid(pid: int) -> bool:
    """PID のプロセスを終了させる（Windows のみ）。終了させたら True。"""
    if sys.platform != "win32" or pid <= 0:
        return False
    from ..locking import pid_alive
    if not pid_alive(pid):
        return False
    r = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.warning("残っていたツールの Excel を終了させました (pid=%d rc=%d)", pid, r.returncode)
    return r.returncode == 0


import atexit  # noqa: E402

atexit.register(kill_children)


class ExtractError(Exception):
    """code はエラーコード（diagnose.CATALOG。例: SYM-004 = SymNavi の結果が届かない）。"""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class VbsRunner:
    """cscript.exe で VBScript を実行し、終了コードと標準出力を扱う。"""

    def __init__(self, cfg: CscriptConfig) -> None:
        self.cfg = cfg

    def run(self, script: Path, args: Sequence[str],
            on_line: Optional[Callable[[str], None]] = None) -> str:
        """VBScript を実行し、標準出力を 1 行ずつ on_line に渡す（進捗表示用）。"""
        if sys.platform != "win32":
            raise ExtractError("VBScript の実行は Windows でのみ可能です（検証には extract.mode=tsv_dir を使ってください）", "ENV-002")
        cscript = self.cfg.path or "cscript.exe"
        cmd: List[str] = [cscript, "//Nologo", str(script), *[str(a) for a in args]]
        log.info("cscript: %s", " ".join(cmd))
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        lines: List[str] = []
        try:
            proc = _register(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=creation))
        except FileNotFoundError as e:
            raise ExtractError(f"cscript が見つかりません: {cscript}", "ENV-002") from e
        deadline = time.time() + self.cfg.timeout_sec
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = _decode(raw).rstrip("\r\n")
                if line.strip():
                    lines.append(line)
                    log.info("  [%s] %s", script.stem, line)
                    if on_line:
                        on_line(line)
                if time.time() > deadline:
                    proc.kill()
                    raise ExtractError(f"VBScript がタイムアウトしました ({self.cfg.timeout_sec}s): {script.name}", "SYM-005")
            rc = proc.wait(timeout=max(1.0, deadline - time.time()))
        except subprocess.TimeoutExpired as e:
            proc.kill()
            raise ExtractError(f"VBScript がタイムアウトしました ({self.cfg.timeout_sec}s): {script.name}", "SYM-005") from e
        except BaseException:
            # on_line から止められた（SymNavi がエラーを出した等）。cscript を残さない
            if proc.poll() is None:
                proc.kill()
            raise
        finally:
            proc.stdout.close()
            _unregister(proc)
        out = "\n".join(lines)
        if rc != 0:
            code = "XLS-001" if script.stem == "xls_to_tsv" else ("RNE-001" if rc == 3 else "SYM-099")
            raise ExtractError(f"VBScript が失敗しました ({script.name} rc={rc}): {out.strip()[-500:]}", code)
        return out


def _decode(b: bytes) -> str:
    for enc in ("cp932", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("cp932", errors="replace")


ProgressFn = Callable[[float, str], None]


def _kv(line: str) -> Dict[str, str]:
    """"wait sec=5 limit=300 file=-1" → {"sec": "5", ...}"""
    out: Dict[str, str] = {}
    for part in line.split():
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _short_dir(path: Path) -> Path:
    """Windows の 8.3 形式の短いパス（使えなければそのまま）。"""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        return path
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 260)  # type: ignore[attr-defined]
        if 0 < n < 260 and buf.value:
            return Path(buf.value)
    except Exception:  # noqa: BLE001
        pass
    return path


_K32 = None


def _file_released(path: Path) -> bool:
    """ほかのプログラムが書き込みのために開いていなければ True（Windows 以外は常に True）。
    共有なしで開いてみて、共有違反（32）なら SymNavi がまだ書いている。"""
    global _K32
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes
    if _K32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _K32 = k
    h = _K32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)   # GENERIC_READ、共有なし、OPEN_EXISTING
    if h is None or h == ctypes.c_void_p(-1).value:
        return False
    _K32.CloseHandle(h)
    return True


class Extractor:
    def extract(self, rne_path: Path, tag: str, work_dir: Path,
                progress: Optional[ProgressFn] = None) -> Path:
        raise NotImplementedError

    def close_session(self) -> None:
        """続けて使っている相手（SymNavi）があれば閉じる。実行の最後に必ず呼ぶ。"""

    def before_run(self, items: List[Tuple[str, Optional[Path]]], work_dir: Path) -> None:
        """実行の最初に 1 回（取り出しの前）。items = [(出力名, RNE のパス)]。"""


class _Session:
    """1 回の実行の間、続けて使う SymNavi（プロセス・小窓の見張り・DDE の会話）。"""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.watcher: Optional["winwatch.WindowWatcher"] = None
        self.api = None                      # 直接（DDEML）の会話。Excel 経由では使わない
        self.uses = 0                        # この SymNavi で処理した RNE の数
        self.started = time.time()
        self.dialog: Dict[str, str] = {"text": "", "error": ""}   # 今の RNE の小窓の記録
        self.report: ProgressFn = lambda frac, text: None
        self.ready = False                   # 起動して画面待ちまで済んだら True

    def alive(self) -> bool:
        # proc が None なのは試験（起動を差し替え）の時
        return self.ready and (self.proc is None or self.proc.poll() is None)


class SymNaviExtractor(Extractor):
    def __init__(self, cfg: SymNaviConfig, credentials: Dict[str, str], runner: VbsRunner) -> None:
        self.cfg = cfg
        self.cred = credentials
        self.runner = runner
        self.dde_factory = None   # 試験用: DDE の代わりを差し込む
        self.saved: Optional[Tuple[Path, str]] = None   # 実際に保存できた場所と形式
        self._use_excel = False   # この実行の途中で Excel 経由に切り替えたら True（以降の RNE も Excel 経由）
        self._sess: Optional[_Session] = None
        self._reuse_broken = False   # 続けて使った SymNavi が命令を受け付けなかったら True（以降は RNE ごとに起動）
        self._marks: List[Tuple[str, float]] = []
        self._saved_enc: Optional[str] = None
        self._compare: Dict[str, object] = {}   # 「比べる」の時: 受け取り方ごとの (結果, 秒, 失敗の理由)
        self._api_jobs: Dict[str, Dict[str, object]] = {}   # 「拡張 API と比べる」の時: 出力名 → API の測定結果
        self._api_tsv: Dict[str, Tuple[Path, int, int, Dict[str, object]]] = {}   # 「拡張 API」の時: 出力名 → (TSV, 行, 列, 結果)
        self._api_failed: Dict[str, str] = {}   # 「拡張 API」で取り出せなかった出力名 → 理由（今の方式で取り出す）

    def _command_line(self) -> str:
        # 旧 VBA: MYEXE & " " & "-d -u" & """ID"",""PASS"",""SRV"""  → -u の直後に引用符付きの 3 値
        return f'"{self.cfg.exe}" {self.cfg.args}"{self.cred["id"]}","{self.cred["pass"]}","{self.cfg.server}"'

    def _shown_command(self) -> str:
        """ログ用。ID とパスワードは残さない。"""
        return f'"{self.cfg.exe}" {self.cfg.args}"<ID>","****","{self.cfg.server}"'

    def _start(self) -> subprocess.Popen:
        if not Path(self.cfg.exe).exists():
            raise ExtractError(f"SymNavi が見つかりません: {self.cfg.exe}", "SYM-001")
        cmd = self._command_line()
        log.info("SymNavi 起動: %s", self._shown_command())
        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
            # 旧 VBA と同じ「最小化・フォーカスあり」（vbMinimizedFocus = SW_SHOWMINIMIZED）。
            # SymNavi が確認を出したときに、タスクバーで気づけるようにする
            si.wShowWindow = 2
        # 作業フォルダは SymNavi のインストール先（bin）にする。SymNavi は bin の雛形・設定（RNE.XLS、Rne.ini など）を
        # 使うことがあり、ツールのフォルダから起動すると見つけられない可能性があるため
        cwd = str(Path(self.cfg.exe).parent) if Path(self.cfg.exe).parent.is_dir() else None
        log.info("SymNavi の作業フォルダ: %s", cwd)
        return _register(subprocess.Popen(cmd, startupinfo=si, cwd=cwd))

    # 普通に閉じるのを待つ秒数。強制終了すると Navigator サーバ側に接続が残り、
    # しばらく（ヘルプでは約 1 分）次のログインができないことがある
    GRACEFUL_CLOSE_SEC = 10

    def _stop(self, proc: Optional[subprocess.Popen]) -> None:
        if proc is None:
            return
        if proc.poll() is None:
            sent = winwatch.post_close(proc.pid)
            how = "普通に閉じました"
            try:
                proc.wait(timeout=self.GRACEFUL_CLOSE_SEC if sent else 0.1)
            except subprocess.TimeoutExpired:
                how = f"閉じる指示に {self.GRACEFUL_CLOSE_SEC} 秒応答がないため強制終了しました" if sent else "強制終了しました"
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            log.info("SymNavi: %s", how)
        _unregister(proc)
        log.info("SymNavi 終了 rc=%s", proc.returncode)
        # 名前（SymNavi.exe）での一括強制終了はしない。利用者が自分で開いている SymNavi まで落とし、
        # サーバ側に接続を残すため。残っている数は実行記録（実行後のプロセス数）で分かる

    # 画面が出るまでの待ちの上限。出ない作りの SymNavi だった場合に毎回長く待たないよう短めにし、
    # 一度「出なかった」ら以降の RNE では待たない
    WINDOW_WAIT_MAX_SEC = 15

    def _wait_window(self, proc: subprocess.Popen, watcher: "winwatch.WindowWatcher", report) -> None:
        """SymNavi の画面が出る（起動・ログインが進む）まで命令を待つ。旧 VBA より早く命令を送って
        受け流されるのを避けるため。Windows 以外・画面が出ない作りなら 1 秒だけ待つ。"""
        if not winwatch.available() or getattr(self, "_no_window", False):
            time.sleep(1.0)
            return
        limit = min(self.cfg.startup_wait_sec, self.WINDOW_WAIT_MAX_SEC)
        t0 = time.time()
        found = watcher.wait_symnavi(
            limit, cancel=lambda: proc.poll() is not None,
            tick=lambda s: report(0.02 + 0.03 * min(1.0, s / max(1, limit)),
                                  f"SymNavi の画面が出るのを待っています（{int(s)} 秒 / {limit} 秒）"))
        if found:
            # 起動直後の命令は SymNavi が断る（NOTPROCESSED）が、その時は送り直すので長くは待たない
            log.info("SymNavi の画面が出ました（%.1f 秒）。0.5 秒おいてから命令を送ります", time.time() - t0)
            time.sleep(0.5)
        elif proc.poll() is not None:
            log.warning("SymNavi が %.1f 秒で終了しました（rc=%s）。このまま接続を試みます", time.time() - t0, proc.returncode)
        else:
            self._no_window = True
            log.warning("SymNavi の画面が %d 秒出ませんでした。このまま命令を送ります（以降の RNE では待ちません）", limit)

    @staticmethod
    def _close_excel(pid: int) -> None:
        """ツールが作った Excel が残っていれば終了させる（cscript を止めると Excel だけ残るため）。"""
        if not pid:
            return
        from ..locking import pid_alive
        for _ in range(30):     # VBScript が自分で閉じるのを最大 3 秒待つ
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        _kill_pid(pid)
        _owned_pids.discard(pid)

    def _dde_via_excel(self, rne_path: Path, xls: Path, report, dialog: Dict[str, str], excel: Dict[str, int]) -> None:
        """旧方式: symnavi_dde.vbs が見えない Excel を作り、Excel の DDEExecute で命令を送る。"""
        def dde_line(line: str) -> None:
            # symnavi_dde.vbs の出力を、画面に出す日本語と % に写す
            if dialog.get("error"):
                # SymNavi がエラーの小窓を出した（ツールが記録して OK 済み）。待ち続けても結果は来ない
                raise ExtractError(f"SymNavi がエラーを表示しました:「{dialog['error']}」（Excel 経由）", "SYM-006")
            kv = _kv(line)
            if line.startswith("excel hwnd="):
                pid = winwatch.pid_of_hwnd(int(kv.get("hwnd", "0") or 0))
                if pid:
                    excel["pid"] = pid
                    _owned_pids.add(pid)
                    log.info("ツールが使う Excel: pid=%d（終わったら必ず閉じる）", pid)
            elif line.startswith("connect wait"):
                report(0.05, f"SymNavi のログインを待っています（{kv.get('sec', '?')} 秒）")
            elif line.startswith("connect ok"):
                report(0.12, "SymNavi に接続しました")
            elif line.startswith("cmd open ok"):
                report(0.18, f"RNE を開きました: {rne_path.name}")
                self._keep_minimized(self._sess.proc if self._sess is not None else None)
            elif line.startswith("cmd save"):
                report(0.22, "SymNavi が問い合わせを実行しています（結果を待っています）")
            elif line.startswith("wait "):
                sec, limit = int(kv.get("sec", 0)), max(1, int(kv.get("limit", 1)))
                report(0.22 + 0.30 * min(1.0, sec / limit), self.wait_text(sec, limit, dialog))
            elif line.startswith("found workbook"):
                report(0.55, "SymNavi の結果を Excel から受け取りました")
            elif line.startswith("output ok"):
                report(0.58, "SymNavi の結果を受け取りました")
            else:
                report(None, line)  # % は変えず文言だけ

        report(0.05, "SymNavi に接続しています（ログイン待ち）")
        try:
            self.runner.run(VBS_DIR / "symnavi_dde.vbs",
                            [self.cfg.dde_app, self.cfg.dde_topic, str(rne_path), str(xls),
                             str(self.cfg.startup_wait_sec), str(self.cfg.dde_timeout_sec),
                             self.cfg.sheet_name],
                            on_line=dde_line)
        except ExtractError as e:
            if "rc=6" in str(e):
                raise ExtractError(
                    f"SymNavi の結果が {self.cfg.dde_timeout_sec} 秒たっても届きませんでした（{rne_path.name}）。"
                    "保存先ファイルも、Excel のブックもできていません。"
                    "SymNavi の画面（タスクバー）に確認やエラーが出ていないか見てください。"
                    "時間のかかる RNE なら、設定 → 参照先 → 詳細 の「保存完了待ち秒数」を増やしてください。", "SYM-004") from e
            if "rc=4" in str(e):
                raise ExtractError(
                    f"SymNavi に接続できませんでした（{self.cfg.startup_wait_sec} 秒）。SymNavi.exe の場所、"
                    "ログイン ID / パスワード、サーバー名を確認してください。", "SYM-002") from e
            if "rc=5" in str(e):
                raise ExtractError(f"SymNavi が RNE を開けませんでした: {rne_path}。{str(e)[-200:]}", "SYM-003") from e
            raise

    def wait_text(self, sec: int, limit: int, dialog: Dict[str, str], what: str = "SymNavi の結果を待っています") -> str:
        text = f"{what}（{sec} 秒 / 上限 {limit} 秒）"
        if sec >= SLOW_HINT_SEC:
            # 旧 VBA では SIKALOT でも 15〜30 秒ほど。それを過ぎたら、止まっている可能性を知らせる
            text += "。通常より時間がかかっています。タスクバーの SymNavi に確認画面が出ていないか見てください"
        if dialog.get("text"):
            text += "。" + dialog["text"]
        return text

    # 続けて使っている SymNavi に「開く」を送り直す上限。起動直後ではないので長くは待たない
    REUSE_OPEN_WAIT_SEC = 15

    def _mark(self, label: str) -> None:
        """抽出の内訳（どこに何秒かかったか）を記録する。最後にまとめてログに出す。"""
        self._marks.append((label, time.time()))

    def _log_marks(self, tag: str) -> None:
        m = self._marks
        parts = [f"{label} {t - m[i][1]:.1f} 秒" for i, (label, t) in enumerate(m[1:])]
        log.info("抽出の内訳 %s: %s / 合計 %.1f 秒", tag, " / ".join(parts), m[-1][1] - m[0][1])

    def _log_compare(self, tag: str, main_tsv: Path, main_out: Path) -> None:
        """「比べる」: ファイル（xls）で受け取った表と、クリップボード・CSV 保存で受け取った表の時間と中身の違いを記録する。"""
        from ..transform.tsv_reader import read_tsv
        cmp = self._compare
        file_sec = float(cmp.get("file_sec") or 0.0)   # type: ignore[arg-type]
        main_cols, main_rows = read_tsv(main_tsv)
        lines = [f"ファイル（xls）{file_sec:.1f} 秒・{len(main_rows):,} 行 × {len(main_cols)} 列"]
        for key, label in (("clipboard", "クリップボード"), ("csv", "CSV で保存")):
            if key not in cmp:
                continue
            got, sec, why = cmp[key]   # type: ignore[misc]
            if got is None:
                lines.append(f"{label} {sec:.1f} 秒・受け取れず（{why}）")
                continue
            path, kind, enc = got
            try:
                alt_tsv = main_tsv.with_name(main_tsv.stem + f"_{key}.tsv")
                if kind == "BIFF":
                    xls_reader.xls_to_tsv(path, alt_tsv, self.cfg.sheet_name)
                    kind_text = "中身は xls（形式の指定は効かない）"
                else:
                    text_to_tsv(path, alt_tsv, "CSV" if kind == "CSV" else "TXT", encoding=enc)
                    kind_text = f"中身は {kind}"
                cols, rows = read_tsv(alt_tsv)
                diffs, examples = 0, []
                for i in range(min(len(rows), len(main_rows))):
                    a, b = main_rows[i], rows[i]
                    for j in range(min(len(a), len(b), len(main_cols))):
                        if a[j] != b[j]:
                            diffs += 1
                            if len(examples) < 4:
                                examples.append(f"「{main_cols[j]}」{i + 2} 行目 xls「{a[j][:30]}」/ {label}「{b[j][:30]}」")
                same_head = "列名同じ" if cols == main_cols else f"列名ちがう（{label}: {', '.join(cols[:5])}…）"
                lines.append(f"{label} {sec:.1f} 秒・{len(rows):,} 行 × {len(cols)} 列・{kind_text}・{same_head}・"
                             f"値のちがい {diffs:,} 個" + (f"（例: {' / '.join(examples)}）" if examples else ""))
            except Exception as e:  # noqa: BLE001
                lines.append(f"{label} {sec:.1f} 秒・読めず（{e}）")
            finally:
                for f in (path, main_tsv.with_name(main_tsv.stem + f"_{key}.tsv")):
                    try:
                        Path(f).unlink()
                    except OSError:
                        pass
        log.warning("比べる %s: %s", tag, " ／ ".join(lines))

    def _keep_minimized(self, proc: Optional[subprocess.Popen]) -> None:
        """SymNavi の画面を最小化に戻す。SymNavi（MFC）は 2 本目以降の RNE を開くたびに画面を元の大きさに戻して
        前に出す（最小化で起動する指定は最初の 1 回しか効かない）。小窓は最小化中も見張りで見える。"""
        pid = proc.pid if proc is not None else 0
        if winwatch.available() and pid and winwatch.minimize_main(pid):
            log.info("SymNavi の画面が前に出たため、最小化に戻しました")

    def _dde_direct(self, rne_path: Path, xls: Path, report, dialog: Dict[str, str],
                    proc: Optional[subprocess.Popen], watcher: Optional["winwatch.WindowWatcher"] = None,
                    fmt: str = "BIFF", sess: Optional[_Session] = None) -> None:
        """Windows の DDE（DDEML）を直接使う。Excel は使わない。命令ごとに処理されたかが分かる。

        sess を渡すと、DDE の会話をその SymNavi と続けて使う（2 本目からは接続を省き、終わっても会話は切らない）。"""
        def L(line: str) -> None:
            log.info("  [dde] %s", line)

        dead = lambda: proc is not None and proc.poll() is not None  # noqa: E731
        api = sess.api if sess is not None else None
        reused = api is not None
        if api is None:
            try:
                api = (self.dde_factory or dde.Win32Dde)()
            except dde.DdeError as e:
                raise ExtractError(str(e), "ENV-002") from e
        limit = int(self.cfg.dde_timeout_sec)
        open_wait = min(self.cfg.startup_wait_sec, self.REUSE_OPEN_WAIT_SEC) if reused else self.cfg.startup_wait_sec
        connected = reused
        try:
            # --- 接続（SymNavi の起動・ログインを待つ）。続けて使う時は済んでいる ---
            t0 = time.time()
            while not connected and not api.connect(self.cfg.dde_app, self.cfg.dde_topic):
                sec = time.time() - t0
                if sec > self.cfg.startup_wait_sec or dead():
                    L(f"connect failed sec={int(sec)} error={dde.err_name(api.last_error())}"
                      f"{' symnavi_exited rc=' + str(proc.returncode) if dead() else ''}")
                    raise ExtractError(
                        f"SymNavi に接続できませんでした（{int(sec)} 秒）。SymNavi.exe の場所、"
                        "ログイン ID / パスワード、サーバー名を確認してください。", "SYM-002")
                L(f"connect wait sec={int(sec)} error={dde.err_name(api.last_error())}")
                report(0.05, f"SymNavi のログインを待っています（{int(sec)} 秒）")
                api.pump(1.0)
            if reused:
                L(f"connect reuse n={sess.uses}")   # n = この SymNavi で何本目か
                report(0.12, "起動済みの SymNavi に続けて頼みます")
            else:
                connected = True
                if sess is not None:
                    sess.api = api
                L(f"connect ok sec={int(time.time() - t0)}")
                report(0.12, "SymNavi に接続しました")
                self._mark("接続・ログイン")

            # --- RNE を開く ---
            def open_retry(tries: int, waited: float, why: str) -> None:
                if why == "NOTPROCESSED" and watcher is not None:
                    watcher.poll()
                    watcher.dismiss_start_screen()
                report(0.12 + 0.05 * min(1.0, waited / max(1, self.cfg.startup_wait_sec)),
                       f"SymNavi の準備ができるのを待っています（開く命令を送り直し中 {int(waited)} 秒 / "
                       f"{open_wait} 秒）" + (f"。{dialog['text']}" if dialog.get("text") else ""))

            ok, _res, err = dde.execute_with_retry(api, f'[Open("{rne_path}")]', 60_000, open_wait,
                                                   L, "open", dead, on_retry=open_retry)
            if not ok:
                raise ExtractError(
                    f"SymNavi が RNE を開く命令を {open_wait} 秒送り直しても処理しませんでした"
                    f"（{dde.err_name(err)}）: {rne_path}。SymNavi の画面（タスクバーの「Navigator クライアント」）に"
                    "何が出ているか見てください。ログインに時間がかかるなら、設定 → 参照先 → 詳細 の「起動待ち秒数」を増やしてください。",
                    "SYM-003")
            report(0.18, f"RNE を開きました: {rne_path.name}")
            self._mark("開く")
            self._keep_minimized(proc)
            self._wait_busy_window(watcher, limit, report, dead, L)
            self._keep_minimized(proc)
            self._mark("問い合わせ（処理中）")

            # --- クリップボードで受け取る（設定 symnavi.receive）。だめならファイル（保存）で受け取る ---
            mode = (self.cfg.receive or "file").lower()
            if mode in ("clipboard", "compare"):
                t_c = time.time()
                try:
                    got_clip = self._copy_once(api, xls.parent, xls.stem, limit, report, dialog, dead, L, proc)
                except ExtractError as e:
                    if dead():
                        raise
                    dialog.update(text="", error="")
                    self._compare["clipboard"] = (None, time.time() - t_c, str(e))
                    log.warning("クリップボードで受け取れなかったため、ファイル（保存）で受け取ります: %s", e)
                else:
                    if mode == "clipboard":
                        self.saved = got_clip[:2]
                        self._saved_enc = got_clip[2]
                        return
                    self._compare["clipboard"] = (got_clip, time.time() - t_c, "")
                if mode == "compare":
                    # 保存の形式が効くかを確かめる（引数の間に空白の無い書き方で CSV を頼む）
                    csv_path = xls.with_name(xls.stem + "_cmp.csv")
                    try:
                        csv_path.unlink()
                    except OSError:
                        pass
                    t_s = time.time()
                    L(f"save compare format=CSV path={csv_path}")
                    ok_csv = self._save_once(api, csv_path, "CSV", limit, report, dialog, dead, L, compact=True)
                    took = time.time() - t_s
                    dialog.update(text="", error="")
                    if ok_csv:
                        kind = "CSV" if not xls_reader.is_xls(csv_path) else "BIFF"
                        self._compare["csv"] = ((csv_path, kind, "cp932"), took, "")
                    else:
                        self._compare["csv"] = (None, took, "保存されなかった")

            # --- 保存。SymNavi が「保存できませんでした」を出したら、保存先・形式を変えて試す ---
            attempts = self._save_attempts(xls, fmt, rne_path)
            t_file0 = time.time()
            failures: List[str] = []
            for i, (path, f) in enumerate(attempts, 1):
                dialog["error"] = ""
                try:
                    path.unlink()
                except OSError:
                    pass
                L(f"save try={i}/{len(attempts)} format={f} path={path}")
                got = self._save_once(api, path, f, limit, report, dialog, dead, L)
                if got:
                    self.saved = (path, f)
                    if mode == "compare":
                        self._compare["file_sec"] = time.time() - t_file0
                    if i > 1:
                        log.warning("SymNavi は %s（%s）になら保存できました。うまくいかなかった保存先: %s",
                                    path, f, " / ".join(failures))
                    return
                failures.append(f"{path}（{f}）: {dialog.get('error') or '保存されなかった'}")
            hint = ("SymNavi に結果を保存させるフォルダ（設定 → 参照先 → 詳細）が SymNavi から書けるか確かめてください。"
                    "%LOCALAPPDATA% の中は、Microsoft Store 版の Python では SymNavi から見えません。")
            raise ExtractError(f"SymNavi が結果を保存できませんでした。{hint} 試した保存先: " + " / ".join(failures), "SYM-006")
        finally:
            # [Close()] は開いた RNE（文書）を閉じる命令。SymNavi 本体は閉じない（旧 VBA も別に閉じていた）
            try:
                if connected:
                    ok, _res, err = api.execute("[Close()]", 10_000)
                    L(f"cmd close {'ok' if ok else 'error=' + dde.err_name(err)}")
            except Exception:  # noqa: BLE001
                pass
            if sess is None or sess.api is not api:   # 続けて使う会話でなければ（接続できなかった時も）ここで切る
                try:
                    api.close()
                except Exception:  # noqa: BLE001
                    pass

    def _save_attempts(self, first: Path, fmt: str, rne_path: Path) -> List[Tuple[Path, str]]:
        """直接の方式で試す保存先と形式（1 回だけ。失敗したら Excel 経由＝旧 VBA と同じ方式に切り替える）。
        小窓を何度も出させないため、ここで保存先や形式をあれこれ変えて試すことはしない。"""
        return [(first, fmt)]

    BUSY_TITLES = ("処理中",)

    def _wait_busy_window(self, watcher, limit: int, report, dead, L) -> None:
        """SymNavi が「処理中」の小窓（問い合わせ・読み込み中）を出している間は保存を頼まない。"""
        if watcher is None or not winwatch.available():
            return
        t0 = time.time()
        seen = False
        while time.time() - t0 < limit and not dead():
            busy = [w for w in watcher.poll() if watcher.is_symnavi(w) and str(w.get("title") or "") in self.BUSY_TITLES]
            if not busy:
                break
            seen = True
            report(0.2, f"SymNavi が問い合わせを実行しています（{int(time.time() - t0)} 秒）")
            time.sleep(0.25)
        if seen:
            L(f"busy window closed sec={time.time() - t0:.1f}")
        time.sleep(0.3)   # 終わった直後は少し待つ（断られても保存の命令は送り直す）

    # 保存の命令 1 回の待ち。長く待つと、SymNavi が小窓を出している間ずっと止まって見える（中断も効かない）
    SAVE_ACK_SEC = 15
    # 保存の命令が断られた後、念のためファイルを待つ秒数
    NOT_ACCEPTED_GRACE_SEC = 3
    # 保存の確認: ファイルを見る間隔と、大きさが変わらなければ書き終わりとみなす秒数
    # （保存の命令が受け付けられた＝SymNavi が書き終えて返事をした時は、間隔 1 回分だけ確かめる）
    FILE_POLL_SEC = 0.25
    STABLE_SEC = 0.75

    def _copy_once(self, api, out_dir: Path, stem: str, limit: int, report, dialog: Dict[str, str], dead, L,
                   proc: Optional[subprocess.Popen]) -> Tuple[Path, str, str]:
        """[Copy()] で SymNavi に問い合わせ表をクリップボードへ出させて読む。戻り値: (ファイル, 形式, 文字コード)。

        利用者のコピーと重なったら使わない（通し番号と持ち主で確かめる）。読んだら利用者の文字を戻す。"""
        if not clipboard.available():
            raise ExtractError("クリップボードは Windows でのみ使えます", "CLP-001")
        user_text = clipboard.get_text()
        seq0 = clipboard.sequence()
        report(0.25, "SymNavi が結果をクリップボードに出しています")
        ok, _res, err = dde.execute_with_retry(api, "[Copy()]", max(limit, 1) * 1000, self.cfg.startup_wait_sec, L, "copy",
                                               cancel=lambda: dead() or bool(dialog.get("error")))
        seq1 = seq0
        try:
            if dialog.get("error"):
                raise ExtractError(f"SymNavi がエラーを表示しました:「{dialog['error']}」（コピー）", "CLP-001")
            if not ok:
                raise ExtractError(f"SymNavi がコピーの命令を処理しませんでした（{dde.err_name(err)}）", "CLP-001")
            t0 = time.time()
            while clipboard.sequence() == seq0 and time.time() - t0 < 5:
                api.pump(0.1)
            seq1 = clipboard.sequence()
            if seq1 == seq0:
                raise ExtractError("SymNavi がクリップボードに何も出しませんでした", "CLP-001")
            owner = clipboard.owner_pid()
            if proc is not None and owner and owner != proc.pid:
                raise ExtractError("クリップボードが SymNavi 以外のアプリに書き換えられました（利用者のコピーと重なった可能性）", "CLP-001")
            try:
                name, data, names = clipboard.read_table()
            except clipboard.ClipboardError as e:
                raise ExtractError(str(e), "CLP-001") from e
            if clipboard.sequence() != seq1:
                raise ExtractError("読んでいる間にクリップボードが書き換えられました", "CLP-001")
            L(f"copy ok formats={','.join(names)} using={name} bytes={len(data)}")
        finally:
            # SymNavi の表を置いたままにせず、利用者が入れていた文字を戻す。利用者がその後に別の物をコピーしていたら触らない
            if seq1 != seq0 and clipboard.sequence() == seq1:
                try:
                    clipboard.put_text(user_text)
                except Exception:  # noqa: BLE001
                    pass
        self._mark("コピー（クリップボード）")
        if name == "CF_UNICODETEXT":
            path = out_dir / f"{stem}_clip.txt"
            path.write_bytes(data.decode("utf-16-le", errors="replace").encode("utf-8"))
            return path, "TXT", "utf-8"
        path = out_dir / f"{stem}_clip.csv"
        path.write_bytes(data)
        return path, "CSV", "cp932"

    def _save_once(self, api, path: Path, fmt: str, limit: int, report, dialog: Dict[str, str], dead, L,
                   compact: bool = False) -> bool:
        """[Save] を送り、ファイルができて大きさが落ち着いたら True。SymNavi がエラーの小窓を出したら False。"""
        ts = time.time()
        stop = threading.Event()

        def tick() -> None:
            while not stop.wait(1.0):
                sec = int(time.time() - ts)
                report(0.22 + 0.30 * min(1.0, sec / max(1, limit)),
                       self.wait_text(sec, limit, dialog, "SymNavi が結果を保存しています"))

        th = threading.Thread(target=tick, name="dde-tick", daemon=True)
        th.start()
        try:
            # 小窓（エラー）が出たら送り直さない。断られても同じ小窓が積み上がるだけなので
            ok, _res, err = dde.execute_with_retry(
                api, (f'[Save("{path}","{fmt}")]' if compact else f'[Save("{path}", "{fmt}")]'), self.SAVE_ACK_SEC * 1000, self.cfg.startup_wait_sec, L, "save",
                cancel=lambda: dead() or bool(dialog.get("error")))
        finally:
            stop.set()
            th.join(2)
        self._mark("保存の指示")
        # 小窓は少し遅れて出ることがあるので、ファイルを待つ間も見る
        last, since, last_echo, last_sec = -1, 0.0, -5, -1
        need = self.FILE_POLL_SEC if ok else self.STABLE_SEC
        while True:
            if dialog.get("error"):
                L(f"save failed by dialog: {dialog['error'][:120]}")
                return False
            size = path.stat().st_size if path.exists() else -1
            now = time.time()
            if size != last:
                last, since = size, now
            # 大きさが落ち着いていて、SymNavi がファイルを閉じている（Windows では書き込み中は開けない）
            if size > 0 and now - since >= need and _file_released(path):
                L(f"output ok via=file sec={now - ts:.1f}: {path} ({size} bytes)")
                self._mark("保存の確認")
                return True
            sec = int(now - ts)
            if not ok and err not in (dde.EXECACKTIMEOUT,) and sec >= self.NOT_ACCEPTED_GRACE_SEC and size < 0:
                # 命令自体が断られ、ファイルも小窓も無い
                L(f"save not accepted error={dde.err_name(err)}")
                dialog.setdefault("error", "")
                dialog["error"] = dialog["error"] or f"保存の命令が処理されませんでした（{dde.err_name(err)}）"
                return False
            if sec - last_echo >= 5:
                L(f"wait sec={sec} limit={limit} file={size}")
                last_echo = sec
            if sec != last_sec:
                report(0.22 + 0.30 * min(1.0, sec / max(1, limit)), self.wait_text(sec, limit, dialog))
                last_sec = sec
            if sec > limit or dead():
                L(f"output missing after {sec}s: {path}")
                if dead():
                    raise ExtractError("SymNavi が終了しました（中断された可能性）", "SYM-099")
                raise ExtractError(
                    f"SymNavi の結果が {limit} 秒たっても届きませんでした。保存先ファイルができていません。"
                    "SymNavi の画面（タスクバー）に確認やエラーが出ていないか見てください。"
                    "時間のかかる RNE なら、設定 → 参照先 → 詳細 の「保存完了待ち秒数」を増やしてください。", "SYM-004")
            api.pump(self.FILE_POLL_SEC)

    def _xls_by_ace(self, out: Path, tsv: Path, report) -> None:
        """予備: ACE（cscript + xls_to_tsv.vbs）で xls を読む。"""
        def tsv_line(line: str) -> None:
            if line.startswith("progress rows="):
                try:
                    n = int(line.split("=")[1])
                    report(min(0.95, 0.6 + 0.35 * min(1.0, n / 20000)), f"xls を読み込み中 {n:,} 行")
                except ValueError:
                    pass

        self.runner.run(VBS_DIR / "xls_to_tsv.vbs", [str(out), self.cfg.sheet_name, str(tsv)], on_line=tsv_line)

    @staticmethod
    def _raise_if_symnavi_error(dialog: Dict[str, str], out: Path, fmt: str) -> None:
        if dialog.get("error"):
            hint = ""
            if fmt == "BIFF":
                hint = ("Excel 97-2003 形式（BIFF）での保存に失敗しています。設定 → 参照先 → 詳細 の"
                        "「SymNavi の保存形式」を「タブ区切りテキスト」にしてください。")
            raise ExtractError(f"SymNavi がエラーを表示しました:「{dialog['error']}」（保存先 {out}、形式 {fmt}）。{hint}",
                               "SYM-006")

    # エラーとみなす文言（OK だけの小窓でこれを含むもの）
    ERROR_WORDS = ("できません", "失敗", "エラー", "ありません", "不正", "誤り")

    def _on_dialog(self, w: Dict[str, object], watcher: "winwatch.WindowWatcher", dialog: Dict[str, str],
                   report) -> None:
        """SymNavi / Excel の小窓が出た時。

        - SymNavi の起動画面 → キャンセル（watcher.dismiss_start_screen）
        - SymNavi の「OK」だけの小窓 → 文言を記録して OK を押す。エラーの文言ならその RNE を失敗にする
          （押さないと SymNavi は次の命令を受け付けず、画面に小窓が積み上がる）
        - それ以外（はい / いいえ、広報メッセージ、Excel の小窓）は押さずに知らせる
        """
        def mask(t: str) -> str:
            for m in watcher.mask:
                if len(m) >= 2:
                    t = t.replace(m, "<伏せ字>")
            return t

        labels = [str(x) for x in (w.get("labels") or [])]          # type: ignore[union-attr]
        buttons = [str(x) for x in (w.get("buttons") or [])]        # type: ignore[union-attr]
        if watcher.is_symnavi(w):
            if watcher.dismiss_start_screen():
                return
            norm = [b.replace("Ｏ", "O").replace("Ｋ", "K").strip().upper() for b in buttons]
            if norm and all(b == "OK" for b in norm):
                msg = mask(" ".join(labels)) or "（文言なし）"
                if winwatch.click_button(int(w["hwnd"]), buttons[0]):
                    log.warning("SymNavi の小窓を記録して OK を押しました:「%s」", msg)
                if any(k in msg for k in self.ERROR_WORDS):
                    dialog["error"] = msg
                    dialog["text"] = f"SymNavi のエラー:「{msg}」"
                    report(None, dialog["text"])
                return
        inside = " / ".join(labels + buttons)[:200]
        text = mask(f"{'SymNavi' if watcher.is_symnavi(w) else 'Excel'} に小窓「{w.get('title') or '題名なし'}」"
                    + (f"（{inside}）" if inside else "") + "が出ています。タスクバーから開いて内容を確認してください")
        dialog["text"] = text
        report(None, text)

    # ---------- 拡張 API と比べる（試験） ----------
    API_TIMEOUT_SEC = 900

    def before_run(self, items: List[Tuple[str, Optional[Path]]], work_dir: Path) -> None:
        """受け取り方「拡張 API と比べる」: SymNavi の画面を起動する前に、全 RNE を拡張 API で実行して時間を測る。
        失敗しても今の方式の取り出しは続ける（出力は今の方式の方）。"""
        mode = self.cfg.receive or ""
        if mode not in ("api", "api_compare"):
            return
        stamp = f"{datetime.now():%Y%m%dT%H%M%S}"
        jobs = [{"tag": tag, "rne": str(p), "out": str(work_dir / f"{tag}_api_{stamp}.txt")}
                for tag, p in items if p is not None and Path(p).exists()]
        if not jobs:
            return
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            res = naviapi.run_probe(self.cfg.exe, self.cfg.server, self.cred, jobs, work_dir, self.API_TIMEOUT_SEC,
                                    register=_register)
        except Exception as e:  # noqa: BLE001  測定の失敗で実行は止めない
            log.warning("拡張 API で%sませんでした（今の方式で続けます）: %s", "取り出せ" if mode == "api" else "測れ", e)
            for j in jobs:
                self._api_failed[j["tag"]] = str(e)
            return
        steps = res.get("steps") or []
        login = next((s for s in steps if s.get("name") == "login"), {})   # type: ignore[union-attr]
        log.log(logging.WARNING if mode == "api_compare" or res.get("error") else logging.INFO,
                 "拡張 API の%s: 全体 %s 秒、ログイン %s 秒（rc=%s %s）、データベース接続 %s、PowerShell %s・%d bit・%s%s",
                    "測定" if mode == "api_compare" else "取り出し", res.get("seconds"), login.get("sec"), login.get("rc"), login.get("err") or "", res.get("connect") or "-",
                    res.get("ps_version"), int(res.get("ptr_size") or 0) * 8, res.get("language_mode"),
                    f"、エラー: {res.get('error')}" if res.get("error") else "")
        for j in jobs:
            job = dict((res.get("jobs") or {}).get(j["tag"]) or {})   # type: ignore[union-attr]
            if not job:
                self._api_failed[j["tag"]] = "拡張 API の結果がありません"
                continue
            job["out"] = j["out"]
            if mode == "api_compare":
                self._api_jobs[j["tag"]] = job
                continue
            # 拡張 API で取り出す: 列名を付け、数の書き方を xls と同じにした TSV にする。だめならこの RNE は今の方式で
            tsv = work_dir / f"{j['tag']}_{stamp}_api.tsv"
            try:
                n_rows, n_cols = naviapi.build_tsv(job, tsv)
                self._api_tsv[j["tag"]] = (tsv, n_rows, n_cols, job)
            except (naviapi.NaviApiError, OSError, ValueError) as e:
                self._api_failed[j["tag"]] = str(e)
                log.warning("%s: 拡張 API の結果を使えません（今の方式で取り出します）: %s", j["tag"], e)
            finally:
                try:
                    Path(j["out"]).unlink()
                except OSError:
                    pass

    # ---------- 続けて使う SymNavi ----------
    # この失敗の後は、同じ SymNavi を次の RNE にも使ってよい（SymNavi 自体は応答している）
    KEEP_SESSION_CODES = ("RNE-001", "SYM-006", "XLS-001", "TSV-001")

    def _session(self, report: ProgressFn) -> _Session:
        """起動済みの SymNavi があればそれを、無ければ起動して返す。"""
        sess = self._sess
        if sess is not None and sess.alive():
            log.info("起動済みの SymNavi を続けて使います（%d 本目。起動・ログインを省略）", sess.uses + 1)
            report(0.1, "起動済みの SymNavi を続けて使います（起動・ログインを省略）")
            return sess
        if sess is not None:
            log.warning("続けて使う予定の SymNavi が終了していました（rc=%s）。起動し直します",
                        sess.proc.returncode if sess.proc is not None else "?")
            self.close_session()
        sess = _Session()
        watcher = winwatch.WindowWatcher(self.cfg.exe, on_dialog=lambda w: self._on_dialog(w, watcher, sess.dialog, sess.report),
                                         mask=[self.cred.get("id", ""), self.cred.get("pass", "")])
        sess.watcher = watcher
        self._sess = sess
        report(0.02, "SymNavi を起動しています")
        if winwatch.available():
            watcher.poll()   # 起動前の Excel の画面も記録しておく（残った Excel・開いているブック）
        try:
            sess.proc = self._start()
            self._wait_window(sess.proc, watcher, report)
            watcher.start()
        except BaseException:
            self.close_session()
            raise
        sess.ready = True
        self._mark("起動・画面待ち")
        return sess

    def close_session(self) -> None:
        """続けて使っていた SymNavi を閉じる（DDE の会話を切り、SymNavi を普通に閉じる）。何度呼んでもよい。"""
        sess, self._sess = self._sess, None
        if sess is None:
            return
        if sess.api is not None:
            try:
                sess.api.close()
            except Exception:  # noqa: BLE001
                pass
            sess.api = None
        if sess.watcher is not None:
            sess.watcher.stop()
        self._stop(sess.proc)
        if sess.uses > 1:
            log.info("SymNavi を閉じました（%d 本を 1 回の起動で処理、%.0f 秒）", sess.uses, time.time() - sess.started)

    def extract(self, rne_path: Path, tag: str, work_dir: Path,
                progress: Optional[ProgressFn] = None) -> Path:
        if not rne_path.exists():
            raise ExtractError(f"RNE ファイルがありません: {rne_path}", "RNE-001")
        report = progress or (lambda frac, text: None)
        got = self._api_tsv.pop(tag, None)
        if got is not None:
            tsv, n_rows, n_cols, job = got
            sec = job.get("sec") or {}
            log.info("抽出の内訳 %s: 拡張 API（画面なし） %s / 合計 %s 秒（%d 行 × %d 列）", tag,
                     " / ".join(f"{k} {v} 秒" for k, v in sec.items() if k != "total"),   # type: ignore[union-attr]
                     sec.get("total"), n_rows, n_cols)   # type: ignore[union-attr]
            report(1.0, f"拡張 API で取り出しました（{n_rows:,} 行 × {n_cols} 列）")
            return tsv
        if tag in self._api_failed:
            log.warning("%s: 拡張 API で取り出せなかったため、SymNavi の画面で取り出します（%s）", tag, self._api_failed.pop(tag))
        stamp = f"{datetime.now():%Y%m%dT%H%M%S}_{os.getpid()}"
        work_dir.mkdir(parents=True, exist_ok=True)
        # SymNavi に保存させるフォルダは %LOCALAPPDATA% の外（既定は <データの場所>\work）。
        # Microsoft Store 版の Python では、Python が AppData に作ったフォルダは Python 専用の場所に振り替えられ、
        # SymNavi からは見えないため「集計値を…に保存できませんでした」になる（実機で確認）。旧 VBA もブックの隣に保存していた
        # 展開されていない書き方（{DATA} など。設定を通さずに作った時）のままなら作業フォルダを使う
        save_dir = Path(self.cfg.save_dir) if self.cfg.save_dir and "{" not in self.cfg.save_dir else work_dir
        save_dir.mkdir(parents=True, exist_ok=True)
        # TSV も同じフォルダに置く（Excel 経由のとき cscript が書く。cscript からも %LOCALAPPDATA% の中は見えないことがある）
        tsv = save_dir / f"{tag}_{stamp}.tsv"
        reuse = self.cfg.reuse_session and not self._reuse_broken
        self._marks = [("開始", time.time())]
        for attempt in (1, 2):
            sess = self._session(report)
            reused = sess.uses > 0
            sess.uses += 1
            keep = False
            try:
                result = self._extract_once(sess, rne_path, tag, save_dir, tsv, report)
                keep = True
                return result
            except ExtractError as e:
                keep = e.code in self.KEEP_SESSION_CODES and sess.alive()
                if reused and attempt == 1 and e.code in ("SYM-002", "SYM-003") and sess.alive():
                    # 続けて使った SymNavi が命令を受け付けない（前の RNE の小窓が残っている等）。
                    # 起動し直してこの RNE をやり直す。この実行の残りは RNE ごとに起動する
                    log.warning("続けて使った SymNavi が命令を受け付けませんでした。SymNavi を起動し直してやり直します"
                                "（この実行の残りの RNE は 1 本ごとに起動します）: %s", e)
                    report(0.02, "SymNavi が応答しないため、起動し直してやり直しています")
                    self._reuse_broken = True
                    reuse = False
                    keep = False
                    self.close_session()
                    continue
                raise
            finally:
                sess.dialog = {"text": "", "error": ""}
                sess.report = lambda frac, text: None
                if not keep or not reuse:
                    self.close_session()
        raise AssertionError("unreachable")

    def _extract_once(self, sess: _Session, rne_path: Path, tag: str, save_dir: Path, tsv: Path, report) -> Path:
        """起動済みの SymNavi で 1 本分: 開く → 保存 → 閉じる（文書だけ）→ 読む。"""
        proc, watcher = sess.proc, sess.watcher
        # 保存形式。Excel 経由は旧 VBA と同じ BIFF。直接は設定（既定 BIFF）
        use_excel = self.cfg.dde_client == "excel" or self._use_excel
        fmt = "BIFF" if use_excel else (self.cfg.save_format or "TXT").upper()
        ext = {"TXT": ".txt", "CSV": ".csv", "BIFF": ".xls"}.get(fmt, ".txt")
        out = save_dir / f"{tag[:8]}{ext}"
        try:
            out.unlink()
        except OSError:
            pass
        ok = False
        self.saved, self._saved_enc, self._compare = None, None, {}
        dialog = {"text": "", "error": ""}
        sess.dialog, sess.report = dialog, report
        excel = {"pid": 0}
        try:
            if use_excel:
                self._dde_via_excel(rne_path, out, report, dialog, excel)
            else:
                self.saved = None
                try:
                    self._dde_direct(rne_path, out, report, dialog, proc, watcher, fmt, sess=sess)
                except ExtractError as e:
                    if e.code != "SYM-006" or not self.cfg.excel_fallback:
                        raise
                    # 直接では SymNavi が保存できなかった。旧 VBA と同じ方式（Excel 経由・Excel 97-2003）でやり直す。
                    # 以降の RNE も、この実行の間は最初から Excel 経由にする（小窓を何度も出させない）
                    log.warning("直接の方式では SymNavi が保存できなかったため、旧 VBA と同じ Excel 経由に切り替えます: %s", e)
                    report(0.2, "旧 VBA と同じ方式（Excel 経由）でやり直しています")
                    self._use_excel = True
                    if sess.api is not None:   # 直接の会話は以後使わない
                        try:
                            sess.api.close()
                        except Exception:  # noqa: BLE001
                            pass
                        sess.api = None
                    fmt = "BIFF"
                    out = save_dir / f"{tag[:8]}.xls"
                    try:
                        out.unlink()
                    except OSError:
                        pass
                    dialog.update(text="", error="")
                    self._dde_via_excel(rne_path, out, report, dialog, excel)
                if self.saved:
                    out, fmt = self.saved
            if use_excel:
                self._mark("SymNavi（Excel 経由）")
            if not out.exists() or out.stat().st_size == 0:
                raise ExtractError(f"SymNavi の保存結果がありません: {out}", "SYM-004")
            log.info("SymNavi の保存結果: %s (%d bytes, 形式 %s)", out, out.stat().st_size, fmt)

            # 指定した形式ではなく、ファイルの中身で読み方を決める（SymNavi は TXT を指定しても xls で保存することがある）
            size = out.stat().st_size
            if xls_reader.is_xls(out):
                report(0.7, f"SymNavi の結果（Excel 97-2003）を読み込んでいます ({size:,} bytes)")
                try:
                    try:
                        n_rows, n_cols, sheet = xls_reader.xls_to_tsv(out, tsv, self.cfg.sheet_name)
                    except xls_reader.XlsFormatError as first:
                        # 書き終わりの直前に読んだ可能性。1 秒おいて 1 度だけ読み直す
                        log.warning("xls を読めませんでした。1 秒おいて読み直します: %s", first)
                        time.sleep(1.0)
                        n_rows, n_cols, sheet = xls_reader.xls_to_tsv(out, tsv, self.cfg.sheet_name)
                    log.info("xls（シート %s）から TSV へ: %d 行 × %d 列（Python で読込。ACE は使わない）", sheet, n_rows, n_cols)
                    if sheet != self.cfg.sheet_name:
                        log.warning("シート「%s」が無いため、シート「%s」を読みました", self.cfg.sheet_name, sheet)
                except xls_reader.XlsFormatError as e:
                    if sys.platform != "win32":
                        raise ExtractError(f"SymNavi の結果（xls）を読めません: {e}", "XLS-001") from e
                    log.warning("Python で xls を読めないため、ACE（xls_to_tsv.vbs）で読みます: %s", e)
                    self._xls_by_ace(out, tsv, report)
            else:
                report(0.7, f"SymNavi の結果（テキスト）を読み込んでいます ({size:,} bytes)")
                n_rows, n_cols = text_to_tsv(out, tsv, "CSV" if fmt == "CSV" else "TXT", encoding=self._saved_enc or "cp932")
                log.info("テキストから TSV へ: %d 行 × %d 列", n_rows, n_cols)
            self._mark("読込（TSV へ）")
            self._log_marks(tag)
            job = self._api_jobs.pop(tag, None)
            if job is not None:
                try:
                    log.warning("%s", naviapi.compare_text(tag, job, tsv, self._marks[-1][1] - self._marks[0][1]))
                    if job.get("layout"):
                        from ..transform.tsv_reader import read_tsv
                        log.warning("%s", naviapi.layout_text(tag, job, read_tsv(tsv)[0]))
                except Exception as e:  # noqa: BLE001
                    log.warning("拡張 API と比べられませんでした: %s", e)
                try:
                    Path(str(job.get("out"))).unlink()
                except OSError:
                    pass
            if self._compare:
                self._log_compare(tag, tsv, out)
            report(1.0, "抽出完了")
            if not tsv.exists():
                raise ExtractError(f"TSV が作られませんでした: {tsv}", "XLS-001" if fmt == "BIFF" else "TSV-001")
            ok = True
            return tsv
        finally:
            self._close_excel(excel["pid"])
            if ok and out.exists():
                out.unlink()
            elif out.exists():
                log.warning("失敗したため SymNavi の保存結果を残します（調査用）: %s", out)


def text_to_tsv(src: Path, dst: Path, fmt: str = "TXT", encoding: str = "cp932") -> Tuple[int, int]:
    """SymNavi が保存したタブ区切り / CSV テキスト（Shift-JIS）を、後段の TSV（UTF-8、1 行目が列名）にする。

    SymNavi のオプションで「表題」「脚注」を出す設定だと、表の前後に 1 列だけの行が付くので除く。
    """
    import csv
    import io
    raw = src.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ExtractError(f"SymNavi の結果がテキストではありません（{src.name}、先頭 {raw[:8].hex()}）", "TSV-001")
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8", errors="replace")
    else:
        text = raw.decode(encoding, errors="replace")
    if fmt == "CSV":
        rows = [r for r in csv.reader(io.StringIO(text))]
    else:
        rows = [line.split("\t") for line in text.splitlines()]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ExtractError(f"SymNavi の保存結果が空です: {src}", "TSV-001")
    width = max(len(r) for r in rows[:50])
    head = next(i for i, r in enumerate(rows) if len(r) == width)
    if head:
        log.info("表の前の %d 行を読み飛ばしました（表題とみなす）: %s", head, [" ".join(r)[:60] for r in rows[:head]])
    body = rows[head:]
    tail = 0
    while width > 1 and len(body) > 1 and len(body[-1]) == 1:
        body.pop()
        tail += 1
    if tail:
        log.info("表の後ろの %d 行を読み飛ばしました（脚注とみなす）", tail)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        for r in body:
            f.write("\t".join(c.replace("\t", " ").replace("\r", " ").replace("\n", " ") for c in r) + "\n")
    return len(body) - 1, width


class TsvDirExtractor(Extractor):
    """検証用: 既に TSV があるものとして読む（Windows 以外や、SymNavi なしでの動作確認）。"""

    def __init__(self, tsv_dir: Path) -> None:
        self.tsv_dir = tsv_dir

    def extract(self, rne_path: Path, tag: str, work_dir: Path,
                progress: Optional[ProgressFn] = None) -> Path:
        cand = [self.tsv_dir / f"{tag}.tsv", self.tsv_dir / f"{rne_path.stem}.tsv"]
        for p in cand:
            if p.exists():
                log.info("TSV を使用: %s", p)
                if progress:
                    progress(1.0, f"TSV を使用: {p.name}")
                return p
        raise ExtractError(f"TSV が見つかりません: {cand}", "TSV-002")
