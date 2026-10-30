"""Navigator 拡張 API（C:\\NAVICL\\Rtl\\SymNaviA.dll）で問い合わせを実行し、各段階の時間を測る（試験）。

SymNaviA.dll は 32 bit の DLL なので、64 bit の Python からは読み込めない。Windows 標準の 32 bit PowerShell
（%SystemRoot%\\SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe）を仲介役にし、ps\\navi_api.ps1 を実行する。
追加のインストールは要らない。パスワードはコマンドの引数ではなく、子プロセスの環境変数で渡す。

  Python ── subprocess ── powershell.exe（32 bit）── Add-Type（P/Invoke）── SymNaviA.dll ── Navigator Server

関数と引数は docs/02_Navigator調査.md 12-2 章（逆アセンブルから読み取ったもの）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

PS_SCRIPT = Path(__file__).resolve().parent / "ps" / "navi_api.ps1"


class NaviApiError(Exception):
    pass


def powershell32() -> Path:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return Path(root) / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def api_dll(symnavi_exe: str) -> Path:
    """SymNavi.exe（…\\bin）から見た SymNaviA.dll（…\\Rtl）。"""
    return Path(symnavi_exe).parent.parent / "Rtl" / "SymNaviA.dll"


def _bootstrap() -> str:
    # 実行ポリシーで .ps1 が止められていても動くよう、短い起動文だけをコマンドで渡し、本体は文字として読んで実行する
    cmd = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; Invoke-Expression ([IO.File]::ReadAllText($env:RNE_NAVI_PS1, [Text.Encoding]::UTF8))"
    return base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")


def run_probe(symnavi_exe: str, server: str, cred: Dict[str, str], jobs: List[Dict[str, str]], work: Path,
              timeout_sec: float, on_line: Optional[Callable[[str], None]] = None,
              register: Optional[Callable[[subprocess.Popen], None]] = None) -> Dict[str, object]:
    """jobs = [{"tag", "rne", "out"}]。戻り値は ps1 の結果（steps / jobs / error ほか）。"""
    if sys.platform != "win32":
        raise NaviApiError("拡張 API は Windows でのみ使えます")
    ps = powershell32()
    dll = api_dll(symnavi_exe)
    if not ps.is_file():
        raise NaviApiError(f"32 bit の PowerShell がありません: {ps}")
    if not dll.is_file():
        raise NaviApiError(f"拡張 API の DLL がありません: {dll}")
    work.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time())}_{os.getpid()}"
    jobs_file = work / f"navi_api_jobs_{stamp}.json"
    result_file = work / f"navi_api_result_{stamp}.json"
    jobs_file.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ)
    env.update({"RNE_NAVI_PS1": str(PS_SCRIPT), "RNE_NAVI_DLL": str(dll), "RNE_NAVI_BIN": str(Path(symnavi_exe).parent),
                "RNE_NAVI_ID": cred.get("id", ""), "RNE_NAVI_PASS": cred.get("pass", ""), "RNE_NAVI_SERVER": server,
                "RNE_NAVI_JOBS": str(jobs_file), "RNE_NAVI_RESULT": str(result_file)})
    cmd = [str(ps), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", _bootstrap()]
    log.info("拡張 API を呼びます: %s（%d 本、32 bit PowerShell、DLL %s）", ps, len(jobs), dll)
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if register:
        register(proc)
    lines: List[str] = []
    # 何も出力しないまま止まっても必ず終わらせる（readline は出力が無いと戻らない）
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        try:
            proc.kill()
        except OSError:
            pass

    watchdog = threading.Timer(timeout_sec, _kill)
    watchdog.daemon = True
    watchdog.start()
    try:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = _decode(raw).rstrip("\r\n")
            if line.startswith(("#< CLIXML", "<Objs ")):   # PowerShell の進み具合の記録（中身は無い）
                continue
            if line.strip():
                lines.append(line)
                log.info("  [api] %s", line)
                if on_line:
                    on_line(line)
        proc.wait(timeout=30)
        if timed_out.is_set():
            raise NaviApiError(f"拡張 API の測定が {timeout_sec:.0f} 秒で終わらないため止めました（最後の出力: {(lines or [''])[-1][:200]}）")
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()
        try:
            jobs_file.unlink()
        except OSError:
            pass
    try:
        result = json.loads(result_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        tail = " / ".join(lines[-5:])
        raise NaviApiError(f"拡張 API の結果がありません（rc={proc.returncode}）: {tail[-400:]}") from e
    finally:
        try:
            result_file.unlink()
        except OSError:
            pass
    result["seconds"] = round(time.time() - t0, 1)
    return result


def _decode(b: bytes) -> str:
    for enc in ("cp932", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("cp932", errors="replace")


def read_records(path: Path, fields: int) -> Tuple[List[List[str]], str]:
    """API の出力（1 行 1 レコード）を列に分ける。戻り値: (行, 区切りの説明)。区切りが分からなければ 1 列のまま。"""
    rows = [l.rstrip("\n") for l in Path(path).read_text(encoding="utf-8").split("\n")]
    if rows and rows[-1] == "":
        rows.pop()
    if not rows:
        return [], "0 行"
    for sep, name in (("\t", "タブ"), (",", "カンマ")):
        counts = {r.count(sep) for r in rows[:50]}
        if fields > 1 and counts == {fields - 1}:
            return [r.split(sep) for r in rows], name
    return [[r] for r in rows], f"区切りが分かりません（1 行目: {rows[0][:80]!r}）"


_DEC = None


def norm_number(v: str) -> str:
    """表示用の書式の数（35.000 / 1220.00 / 1,234.50）を、xls と同じ形（35 / 1220 / 1234.5）にする。数でなければそのまま。"""
    import re
    global _DEC
    if _DEC is None:
        # 先頭が 0 の数字の並び（コード・番号。例 0012、001.50）は数ではないので変えない
        _DEC = re.compile(r"-?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?")
    t = v.strip()
    if not t or not _DEC.fullmatch(t):
        return v
    t = t.replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    if t in ("-0", ""):
        t = "0"
    return t


def compare_text(tag: str, job: Dict[str, object], dde_tsv: Path, dde_sec: float) -> str:
    """拡張 API と今の方式（DDE・xls）の、時間と中身の違い。ログに 1 行で残す。
    値は、そのまま比べた数と、数の書式（末尾の 0・桁区切り）をそろえて比べた数の両方を出す。"""
    from ..transform.tsv_reader import read_tsv
    sec = job.get("sec") or {}
    parts = " / ".join(f"{k} {v} 秒" for k, v in sec.items() if k != "total")   # type: ignore[union-attr]
    head = f"拡張 API {sec.get('total', '?')} 秒（{parts}）・{job.get('rows_written')} 行 × {job.get('fields')} 列"   # type: ignore[union-attr]
    if not job.get("ok"):
        return f"API と比べる {tag}: {head}・失敗（{job.get('error')}） ／ 今の方式 {dde_sec:.1f} 秒"
    cols, rows = read_tsv(dde_tsv)
    try:
        api_rows, how = read_records(Path(str(job.get("out"))), int(job.get("fields") or 0))
    except OSError as e:
        return f"API と比べる {tag}: {head}・出力を読めません（{e}） ／ 今の方式 {dde_sec:.1f} 秒"
    if not (api_rows and len(api_rows[0]) == len(cols)):
        cmp = f"列の数が合いません（{how}）。1 行目: {(job.get('sample') or [''])[0][:120]}"   # type: ignore[index]
        return f"API と比べる {tag}: {head}・{cmp} ／ 今の方式 {dde_sec:.1f} 秒・{len(rows):,} 行 × {len(cols)} 列"
    raw, left, examples = 0, 0, []
    by_col: Dict[str, int] = {}
    for i in range(min(len(rows), len(api_rows))):
        for j, (a, b) in enumerate(zip(rows[i], api_rows[i])):
            if a == b:
                continue
            raw += 1
            if norm_number(a) == norm_number(b):
                continue
            left += 1
            by_col[cols[j]] = by_col.get(cols[j], 0) + 1
            if len(examples) < 6:
                examples.append(f"「{cols[j]}」{i + 2} 行目 今「{a[:30]}」/ API「{b[:30]}」")
    worst = "、".join(f"{c} {n:,}" for c, n in sorted(by_col.items(), key=lambda x: -x[1])[:6])
    cmp = (f"区切り {how}・行数 {'同じ' if len(rows) == len(api_rows) else f'ちがう（今 {len(rows):,} / API {len(api_rows):,}）'}・"
           f"値のちがい そのまま {raw:,} 個 → 数の書式をそろえると {left:,} 個"
           + (f"（列: {worst}。例: {' / '.join(examples)}）" if left else ""))
    return f"API と比べる {tag}: {head}・{cmp} ／ 今の方式 {dde_sec:.1f} 秒・{len(rows):,} 行 × {len(cols)} 列"


def layout_text(tag: str, job: Dict[str, object], dde_cols: List[str]) -> str:
    """API が返した列の情報（管理ポイント・データ項目の名前）と、今の方式の列名がどれだけ合うか。"""
    lay = [str(x) for x in (job.get("layout") or [])]   # type: ignore[union-attr]
    names = []
    for x in lay:
        for key in (" name0=", " name1=", " name="):
            if key in x:
                v = x.split(key, 1)[1].split(" name1=")[0].strip()
                if v:
                    names.append(v)
    hit = [c for c in dde_cols if c in names]
    return (f"拡張 API の列の情報 {tag}: {len(lay)} 件・今の列名 {len(dde_cols)} 個のうち {len(hit)} 個が見つかる。"
            f"中身: {' | '.join(lay)[:1500]}")


def header_of(job: Dict[str, object]) -> List[str]:
    """API の行の列名。実機の 4 本では、管理ポイント（種類 1）の後にデータ項目（種類 4）が、行の列と同じ順に並んだ。
    種類 3 のデータ項目は列ではなかった（2026-10-28）。"""
    cols = [c for c in (job.get("cols") or []) if isinstance(c, dict)]   # type: ignore[union-attr]
    cps = [str(c.get("name") or "") for c in cols if c.get("what") == "cp" and int(c.get("kind", -1)) == 1]
    dis = [str(c.get("name") or "") for c in cols if c.get("what") == "di" and int(c.get("kind", -1)) == 4]
    return cps + dis


def build_tsv(job: Dict[str, object], dst: Path) -> Tuple[int, int]:
    """API の結果を、今の方式と同じ形の TSV（UTF-8、1 行目が列名、数は xls と同じ書き方）にする。
    列名の数・行数が合わなければ NaviApiError（その RNE は今の方式で取り出し直す）。"""
    if not job.get("ok"):
        raise NaviApiError(f"拡張 API で取り出せませんでした: {job.get('error')}")
    fields = int(job.get("fields") or 0)
    header = header_of(job)
    if fields <= 0 or len(header) != fields or not all(header):
        raise NaviApiError(f"列名を決められません（列 {fields} 個、名前 {len(header)} 個）")
    lines = [l for l in Path(str(job.get("out"))).read_text(encoding="utf-8").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    want = int(job.get("records") or -1)
    if want >= 0 and len(lines) != want:
        raise NaviApiError(f"行数が合いません（件数 {want:,}、読めた行 {len(lines):,}）")
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(h.replace("\t", " ") for h in header) + "\n")
        for n, line in enumerate(lines, 1):
            cells = line.split("\t")
            if len(cells) != fields:
                raise NaviApiError(f"{n} 行目の列の数が {len(cells)} 個です（{fields} 個のはず）")
            f.write("\t".join(norm_number(c.replace("\\r", " ").replace("\\n", " ")) for c in cells) + "\n")
    return len(lines), fields
