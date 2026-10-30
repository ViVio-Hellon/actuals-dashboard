"""パスの設定（設定画面の「パスの設定」）。

- list_dir  … 「選ぶ…」のフォルダ一覧（ブラウザはフルパスを渡せないので、サーバが中を見せる。RNEConverter と同じ作り）
- check     … 保存する前に、入れたパスで使えるかを確かめる（ある・書ける・中身が合っている）
- rneconverter_status … RNEConverter の RNE 一覧の KURAIRE（有効か・RNE ファイルがあるか・出力先が取り込み元と同じか）
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


def check_source(path_text: str) -> Dict[str, Any]:
    """取り込むファイル（置くフォルダ + 名前 から決まる）。まだ無いのは、データ更新の前なら普通。"""
    if not path_text:
        return _item("missing", "「作ったファイルを置くフォルダ」と「作るファイルの名前」を入れると決まります。")
    p = Path(path_text)
    ex = _try(lambda: p.is_file(), False)
    if ex == "timeout":
        return _item("timeout", f"応答がありません（{NET_TIMEOUT:.0f} 秒）。")
    if not ex:
        return _item("warn", "まだありません。「データ更新」を押すと RNEConverter が作ります。")

    def head():
        with open(p, "rb") as f:
            return f.read(16)
    if _try(head, b"") != b"SQLite format 3\x00":
        return _item("missing", "同じ名前のファイルがありますが、SQLite ではありません。名前を変えてください。")
    return _item("ok", f"あります（{_mtime(p)} 更新）。このファイルから取り込みます。")


def check_python(value: str) -> Dict[str, Any]:
    if not value.strip():
        return _item("ok", f"このツールと同じ Python を使います: {sys.executable}")
    p = Path(value.strip().strip('"'))
    return _item("ok", "あります。") if _try(lambda: p.is_file(), False) is True else _item("missing", "ファイルがありません。")


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def rneconverter_status(rne_dir_text: str, paths: Paths) -> Dict[str, Any]:
    """RNEConverter が「データ更新」に使えるか（RNEConverter の設定は読むだけで書き換えない）。"""
    if not rne_dir_text.strip():
        return _item("missing", "未設定です。RNEConverter のフォルダ（start_app.py があるフォルダ）を選んでください。"
                                "未設定でも、ほかの PC が作ったファイルの取り込みはできます。", rows=[])
    root = Path(expand(rne_dir_text, paths))
    if _try(lambda: (root / "start_app.py").is_file(), False) is not True:
        return _item("missing", "このフォルダに start_app.py がありません（RNEConverter のフォルダを選んでください）。",
                     rows=[])
    rows: List[Dict[str, Any]] = []
    main_py = _try(lambda: (root / "app" / "main.py").read_text(encoding="utf-8"), "") or ""
    new_enough = '"--rne"' in main_py
    rows.append({"label": "RNEConverter の版", "state": "ok" if new_enough else "missing",
                 "value": "RNE ファイルと置き場所を指定して実行できる版" if new_enough
                 else "古い版です（RNE ファイルと置き場所を渡せません）",
                 "note": "" if new_enough else "RNEConverter を新しい版（claude/exciting-wright-gi2v64 以降）に入れ替えてください。"})
    env = os.environ.get("RNE_CONVERTER_HOME")
    data = Path(env) if env else Path(os.environ.get("USERPROFILE") or Path.home()) / "RNEConverter"
    cred = _read_json(data / "config" / "credentials.json") or {}
    has_cred = bool(cred.get("id") and cred.get("pass")) or bool(os.environ.get("RNE_SYMNAVI_ID"))
    rows.append({"label": "SymNavi のログイン", "state": "ok" if has_cred else "missing",
                 "value": "この PC の RNEConverter に登録済み" if has_cred else "この PC の RNEConverter に未登録",
                 "note": "" if has_cred else "RNEConverter を起動し、設定タブ →「認証」で ID とパスワードを登録してください。"})
    bad = [r for r in rows if r["state"] != "ok"]
    return _item("ok" if not bad else "missing", "使えます。" if not bad else "下の項目を確認してください。", rows=rows)


def check_all(values: Dict[str, Any], paths: Paths) -> Dict[str, Any]:
    from .settings import source_file
    v = {k: str(values.get(k) or "") for k in ("shared_dir", "rne_path", "rne_output_dir", "rne_output",
                                               "rneconverter_dir", "python_exe", "export_dir", "log_dir")}
    name = v["rne_output"].strip()
    if name.lower().endswith(".sqlite3"):
        name = name[:-8]
    out_dir = expand(v["rne_output_dir"], paths) if v["rne_output_dir"].strip() else ""
    src = source_file(out_dir, name)
    if not name:
        name_state = _item("missing", "未設定です。")
    elif any(ch in name for ch in '\\/:*?"<>|'):
        name_state = _item("missing", "使えない文字があります（\\ / : * ? \" < > |）。")
    else:
        name_state = _item("ok", f"「{name}.sqlite3」を作ります。")
    return {
        "rne_path": check_rne(v["rne_path"], paths),
        "rne_output_dir": _writable(Path(out_dir), create=False) if out_dir else _item("missing", "未設定です。"),
        "rne_output": name_state,
        "rneconverter_dir": rneconverter_status(v["rneconverter_dir"], paths),
        "source": {**check_source(src), "path": src},
        "shared_dir": check_shared(v["shared_dir"], paths),
        "python_exe": check_python(v["python_exe"]),
        "export_dir": _writable(Path(expand(v["export_dir"], paths)), create=True) if v["export_dir"]
        else _item("missing", "未設定です。"),
        "log_dir": _writable(Path(expand(v["log_dir"], paths)), create=True) if v["log_dir"]
        else _item("missing", "未設定です。"),
    }
