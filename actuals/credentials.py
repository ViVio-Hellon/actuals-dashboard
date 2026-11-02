"""SymNavi の ID・パスワード（この PC だけ。共有フォルダ・リポジトリには置かない）。

置き場所: <データの場所>\\config\\credentials.json（RNEConverter と同じ形 {"id", "pass"}）。
ここに無ければ、同じ PC の RNEConverter に登録済みのもの → 環境変数 RNE_SYMNAVI_ID / RNE_SYMNAVI_PASS の順に使う。
パスワードは画面に返さない・ログに残さない。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Tuple

from . import logsetup
from .settings import Paths


def _read(p: Path) -> Dict[str, str]:
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        return {"id": str(d.get("id") or ""), "pass": str(d.get("pass") or "")}
    except (OSError, ValueError):
        return {"id": "", "pass": ""}


def _rneconverter_file() -> Path:
    env = os.environ.get("RNE_CONVERTER_HOME")
    base = Path(env) if env else Path(os.environ.get("USERPROFILE") or Path.home()) / "RNEConverter"
    return base / "config" / "credentials.json"


def load(paths: Paths) -> Tuple[Dict[str, str], str]:
    """(ID・パスワード, どこのものか)。読んだパスワードはログに出さないよう覚えさせる。"""
    cred, where = _load(paths)
    logsetup.secret(cred["pass"])
    return cred, where


def _load(paths: Paths) -> Tuple[Dict[str, str], str]:
    own = _read(paths.config / "credentials.json")
    if own["id"] and own["pass"]:
        return own, "このツール"
    rc = _read(_rneconverter_file())
    if rc["id"] and rc["pass"]:
        return rc, "この PC の RNEConverter に登録済みのもの"
    env = {"id": os.environ.get("RNE_SYMNAVI_ID", ""), "pass": os.environ.get("RNE_SYMNAVI_PASS", "")}
    if env["id"] and env["pass"]:
        return env, "環境変数"
    return {"id": own["id"], "pass": own["pass"]}, ""


def status(paths: Paths) -> Dict[str, Any]:
    cred, where = load(paths)
    return {"id": cred["id"], "has_password": bool(cred["pass"]), "ready": bool(cred["id"] and cred["pass"]),
            "source": where}


def save(paths: Paths, user_id: str, password: str) -> Dict[str, Any]:
    """password が空なら、登録済みのパスワードを残す（ID だけ変える）。"""
    p = paths.config / "credentials.json"
    cur = _read(p)
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("ID を入れてください。")
    new = {"id": user_id, "pass": password if password else cur["pass"]}
    if not new["pass"]:
        raise ValueError("パスワードを入れてください。")
    logsetup.secret(new["pass"])
    t0 = time.monotonic()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    logsetup.saved("SymNavi のログイン（中身は書かない）", p, 1, time.monotonic() - t0, id=new["id"])
    return status(paths)
