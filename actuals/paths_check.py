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


def check_source(value: str, paths: Paths, table: str = "") -> Dict[str, Any]:
    if not value.strip():
        return _item("missing", "取り込み元が未設定です。")
    p = Path(expand(value, paths))
    ex = _try(lambda: p.is_file(), False)
    if ex == "timeout":
        return _item("timeout", f"応答がありません（{NET_TIMEOUT:.0f} 秒）。")
    if not ex:
        parent = _try(lambda: p.parent.is_dir(), False)
        if parent is True:
            return _item("warn", "フォルダはありますが、ファイルはまだありません。RNEConverter で KURAIRE を実行すると作られます。")
        return _item("missing", "ファイルも、置くフォルダもありません。")

    def head():
        with open(p, "rb") as f:
            return f.read(16)
    h = _try(head, b"")
    if h != b"SQLite format 3\x00":
        return _item("missing", "SQLite のファイルではありません（RNEConverter の出力 .sqlite3 を指定してください）。")
    return _item("ok", f"あります（{_mtime(p)} 更新）。")


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


def _deep(base: Dict[str, Any], upd: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (upd or {}).items():
        out[k] = _deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def rneconverter_status(rne_dir_text: str, output: str, source_value: str, paths: Paths) -> Dict[str, Any]:
    """RNEConverter の設定を読んで、KURAIRE が動ける状態かを見る（RNEConverter の設定は書き換えない）。"""
    if not rne_dir_text.strip():
        return _item("warn", "RNEConverter のフォルダが未設定です。「データ更新」ボタンは使えません"
                             "（RNEConverter を別に実行し、出力を取り込むことはできます）。", rows=[])
    root = Path(expand(rne_dir_text, paths))
    if _try(lambda: (root / "start_app.py").is_file(), False) is not True:
        return _item("missing", "このフォルダに start_app.py がありません（RNEConverter のフォルダを指定してください）。", rows=[])
    env = os.environ.get("RNE_CONVERTER_HOME")
    data = Path(env) if env else Path(os.environ.get("USERPROFILE") or Path.home()) / "RNEConverter"
    settings = _deep(_read_json(root / "config" / "settings.json") or {},
                     _read_json(data / "config" / "settings.json") or {})
    user_list = data / "config" / "rne_list.json"
    rne_list = _read_json(user_list) if user_list.exists() else _read_json(root / "config" / "rne_list.json")
    list_from = str(user_list if user_list.exists() else root / "config" / "rne_list.json")

    def ex(s: str) -> str:
        return str(s or "").replace("{APP_ROOT}", str(root)).replace("{DATA}", str(data))
    rows: List[Dict[str, Any]] = []
    entry = next((e for e in (rne_list or []) if e.get("output") == output), None)
    if entry is None:
        return _item("missing", f"RNEConverter の RNE 一覧（{list_from}）に {output} がありません。"
                                f"RNEConverter の設定タブ → RNE 一覧で、出力名 {output} の行を追加してください。", rows=rows)
    rows.append({"label": "RNE 一覧の行", "value": f"{output}（{'有効' if entry.get('enabled', True) else '無効'}）",
                 "state": "ok" if entry.get("enabled", True) else "warn",
                 "note": "" if entry.get("enabled", True) else "RNEConverter の設定タブ → RNE 一覧で有効にしてください。"})
    rne = str(entry.get("rne") or "")
    rne_dir = Path(ex((settings.get("extract") or {}).get("rne_dir", "{APP_ROOT}/rne")))
    rp = Path(rne) if (Path(rne).is_absolute() or rne.startswith("\\\\") or (len(rne) > 1 and rne[1] == ":")) else rne_dir / rne
    hit = _try(lambda: rp.is_file(), False)
    rows.append({"label": "RNE ファイル", "value": str(rp), "state": "ok" if hit is True else "missing",
                 "note": "" if hit is True else "ありません。旧ブックと同じフォルダにあった RNE をここに置いてください。"})
    save_dir = ex((settings.get("output") or {}).get("save_dir", ""))
    expected = str(Path(save_dir) / f"{output}.sqlite3") if save_dir else ""
    same = bool(expected) and os.path.normcase(os.path.normpath(expected)) == \
        os.path.normcase(os.path.normpath(expand(source_value, paths)))
    rows.append({"label": "RNEConverter の出力先", "value": expected or "（未設定）", "state": "ok" if same else "warn",
                 "note": "" if same else "取り込み元と違います。「取り込み元をこれにする」で合わせられます。",
                 "suggest_source": "" if same else expected})
    bad = [r for r in rows if r["state"] != "ok"]
    state = "ok" if not bad else ("missing" if any(r["state"] == "missing" for r in bad) else "warn")
    msg = "「データ更新」で使えます。" if state == "ok" else "下の項目を確認してください。"
    return _item(state, msg, rows=rows, list_from=list_from)


def check_all(values: Dict[str, Any], paths: Paths) -> Dict[str, Any]:
    v = {k: str(values.get(k) or "") for k in ("shared_dir", "source_path", "source_table", "rneconverter_dir",
                                               "rne_output", "python_exe", "export_dir", "log_dir")}
    return {
        "shared_dir": check_shared(v["shared_dir"], paths),
        "source_path": check_source(v["source_path"], paths, v["source_table"]),
        "rneconverter_dir": rneconverter_status(v["rneconverter_dir"], v["rne_output"] or "KURAIRE",
                                                v["source_path"], paths),
        "python_exe": check_python(v["python_exe"]),
        "export_dir": _writable(Path(expand(v["export_dir"], paths)), create=True) if v["export_dir"]
        else _item("missing", "未設定です。"),
        "log_dir": _writable(Path(expand(v["log_dir"], paths)), create=True) if v["log_dir"]
        else _item("missing", "未設定です。"),
    }
