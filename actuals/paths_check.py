"""パスの設定（設定画面の「パスの設定」）。

- list_dir  … 「選ぶ…」のフォルダ一覧（ブラウザはフルパスを渡せないので、サーバが中を見せる。RNEConverter と同じ作り）
- check     … 保存する前に、入れたパスで使えるかを確かめる（ある・書ける・中身が合っている）
- 「データ更新」に要るもの（RNE ファイル・SymNavi.exe・サーバー名・ログイン・作業フォルダ）
- open_path … エクスプローラーで開く（Windows のみ）

共有フォルダが応答しなくても画面を止めないよう、ファイルに触る所はすべて時間を区切る。
"""
from __future__ import annotations

import json
import os
import string
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sharedfile import _TimedOut, within
from .settings import Paths, expand

NET_TIMEOUT = 5.0


class PathInputError(ValueError):
    pass


def _try(fn, default=None):
    try:
        return within(fn, NET_TIMEOUT)
    except _TimedOut:
        return "timeout"
    except OSError:
        return default


# ---------- 選ぶ… ----------
def list_dir(path_text: str, exts: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """サブフォルダと、指定した拡張子のファイル。空ならドライブ一覧（Windows）。"""
    path_text = (path_text or "").strip().strip('"')
    if not path_text:
        if sys.platform == "win32":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return {"path": "", "parent": None, "dirs": drives, "files": []}
        path_text = "/"
    p = Path(path_text)
    if p.is_file() or (p.suffix and not _try(lambda: p.is_dir(), False)):
        p = p.parent
    ok = _try(lambda: p.is_dir(), False)
    if ok == "timeout":
        raise PathInputError(f"フォルダの応答がありません（{NET_TIMEOUT:.0f} 秒）: {p}")
    if not ok:
        raise PathInputError(f"フォルダがありません: {p}")

    def scan():
        dirs, files = [], []
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if child.is_dir():
                    if not child.name.startswith("."):
                        dirs.append(child.name)
                elif not exts or child.suffix.lower() in exts:
                    files.append(child.name)
            except OSError:
                continue
        return dirs, files
    try:
        dirs, files = within(scan, NET_TIMEOUT * 2)
    except _TimedOut:
        raise PathInputError(f"フォルダの一覧に時間がかかっています: {p}")
    except OSError as e:
        raise PathInputError(f"フォルダを開けません: {p}（{e}）")
    parent = str(p.parent) if p.parent != p else ("" if sys.platform == "win32" else None)
    return {"path": str(p), "parent": parent, "dirs": dirs[:500], "files": files[:500]}


def open_path(path_text: str) -> bool:
    p = Path((path_text or "").strip().strip('"'))
    if os.name != "nt":
        return False
    target = p if _try(lambda: p.is_dir(), False) is True else p.parent
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


# ---------- 確かめる ----------
def _item(state: str, message: str, **extra) -> Dict[str, Any]:
    return {"state": state, "message": message, **extra}


def _writable(d: Path, create: bool) -> Dict[str, Any]:
    exists = _try(lambda: d.is_dir(), False)
    if exists == "timeout":
        return _item("timeout", f"応答がありません（{NET_TIMEOUT:.0f} 秒）。ネットワークか場所を確認してください。")
    if not exists:
        if not create:
            return _item("missing", "フォルダがありません。")
        made = _try(lambda: d.mkdir(parents=True, exist_ok=True) or True, False)
        if made is not True:
            return _item("missing", "フォルダがなく、作ることもできません。")
    probe = d / f".write_test_{uuid.uuid4().hex[:8]}"

    def w():
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    ok = _try(w, False)
    if ok == "timeout":
        return _item("timeout", "書き込みの応答がありません。")
    if ok is not True:
        return _item("readonly", "フォルダはありますが、書き込めません（権限を確認してください）。")
    return _item("ok", "使えます（書き込めます）。")


def _mtime(p: Path) -> str:
    st = _try(lambda: p.stat())
    if not st or st == "timeout":
        return ""
    return datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")


def check_shared(value: str, paths: Paths) -> Dict[str, Any]:
    if not value.strip():
        d = paths.base / "shared"
        return _item("warn", f"未設定です。条件表・手動訂正・蓄積した実績は、この PC の中（{d}）だけに保存されます。"
                             "全 PC 共通にするには、共有フォルダを指定してください。")
    d = Path(expand(value, paths))
    r = _writable(d, create=True)
    if r["state"] == "ok":
        found = []
        for name, label in (("actuals_master.sqlite3", "条件表・手動訂正"), ("actuals_history.sqlite3", "蓄積した実績")):
            f = d / name
            if _try(lambda f=f: f.is_file(), False) is True:
                found.append(f"{label}（{_mtime(f)} 更新）")
        r["message"] = ("使えます。" + ("あるもの: " + "、".join(found) if found
                                      else "まだ何もありません（最初の保存・取り込みで作られます）。"))
    return r


def check_rne(value: str, paths: Paths) -> Dict[str, Any]:
    if not value.strip():
        return _item("missing", "未設定です。「選ぶ…」で 梱包実績(当月_倉入れ日付順).RNE を選んでください。")
    p = Path(expand(value, paths))
    ex = _try(lambda: p.is_file(), False)
    if ex == "timeout":
        return _item("timeout", f"応答がありません（{NET_TIMEOUT:.0f} 秒）。")
    if not ex:
        return _item("missing", "ファイルがありません。")
    if p.suffix.lower() != ".rne":
        return _item("warn", "拡張子が .RNE ではありません。SymNavi の問い合わせファイルか確認してください。")
    return _item("ok", f"あります（{_mtime(p)} 更新）。")


def check_exe(value: str, paths: Paths, missing_state: str = "missing", what: str = "") -> Dict[str, Any]:
    if not value.strip():
        return _item(missing_state, "未設定です。")
    p = Path(expand(value, paths))
    ex = _try(lambda: p.is_file(), False)
    if ex == "timeout":
        return _item("timeout", "応答がありません。")
    if ex is not True:
        return _item(missing_state, f"ありません。{what}")
    return _item("ok", "あります。")


def check_work(value: str, paths: Paths) -> Dict[str, Any]:
    if not value.strip():
        return _item("missing", "未設定です。")
    d = Path(expand(value, paths))
    local = os.environ.get("LOCALAPPDATA", "")
    if local and str(d).lower().startswith(local.lower()):
        return _item("missing", "%LOCALAPPDATA% の中は使えません（SymNavi から見えないことがある）。別の場所にしてください。")
    return _writable(d, create=True)


def log_folder(value: str, paths: Paths) -> Path:
    """ログを書くフォルダ（<ログの出力パス>\\actualsdashboard。空欄はこの PC の logs の下）。"""
    from . import logsetup
    text = (value or "").strip().strip('"')
    return logsetup.tool_dir(Path(expand(text, paths)) if text else paths.logs)


def check_log(value: str, paths: Paths) -> Dict[str, Any]:
    """ログの出力パス。書けるかと、実際に書くフォルダ・ファイル名を返す。書けなくても止まらない（この PC へ書く）。"""
    from . import logsetup
    folder = log_folder(value, paths)
    names = (f"{logsetup.text_name(logsetup.pc_name(), datetime.now().date())}・"
             f"{logsetup.event_name(logsetup.pc_name(), datetime.now().date())}")
    text = (value or "").strip()
    if not text:
        return _item("ok", f"未設定のため、この PC の {folder} に書きます（{names}）。"
                           "共有フォルダを指定すると全 PC の分を 1 か所で見られます。", folder=str(folder))
    from .settings import is_full_path
    if not is_full_path(expand(text.strip('"'), paths)):
        return _item("missing", "フルパスで指定してください（例 \\\\サーバー\\共有\\logs または D:\\logs）。",
                     folder=str(folder))
    r = _writable(folder, create=True)
    if r["state"] == "ok":
        return _item("ok", f"使えます。{folder} に書きます（{names}）。", folder=str(folder))
    return _item(r["state"], f"{r['message']} このままでもツールは止まらず、この PC の "
                             f"{log_folder('', paths)} に書きます。", folder=str(folder))


def check_all(values: Dict[str, Any], paths: Paths) -> Dict[str, Any]:
    from . import credentials
    v = {k: str(values.get(k) if values.get(k) is not None else "") for k in (
        "shared_dir", "rne_path", "symnavi_exe", "symnavi_server", "export_dir", "log_dir", "work_dir", "cscript_path")}
    cred = credentials.status(paths)
    return {
        "rne_path": check_rne(v["rne_path"], paths),
        "symnavi_exe": check_exe(v["symnavi_exe"], paths, what="SymNavi が入っている PC で使ってください（旧ツールと同じ C:\\NAVICL\\bin\\SymNavi.exe）。"),
        "symnavi_server": _item("ok", "旧ツールと同じです。") if v["symnavi_server"].strip() == "NlmFaNgyNavd"
        else (_item("warn", "旧ツール（NlmFaNgyNavd）と違います。") if v["symnavi_server"].strip() else _item("missing", "未設定です。")),
        "credentials": (_item("ok", f"登録済み（ID {cred['id']}・{cred['source']}）。") if cred["ready"]
                        else _item("missing", "未登録です。下の「SymNavi のログイン」で ID とパスワードを登録してください。")),
        "shared_dir": check_shared(v["shared_dir"], paths),
        "export_dir": _writable(Path(expand(v["export_dir"], paths)), create=True) if v["export_dir"]
        else _item("missing", "未設定です。"),
        "log_dir": check_log(v["log_dir"], paths),
        "work_dir": check_work(v["work_dir"], paths),
        "cscript_path": check_exe(v["cscript_path"], paths, missing_state="warn",
                                  what="Excel 経由の予備の取り出しだけで使うので、ふつうは無くても動きます。"),
        "startup_wait_sec": _item("ok", ""), "result_wait_sec": _item("ok", ""),
    }
