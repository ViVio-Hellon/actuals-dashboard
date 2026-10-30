"""設定とデータの置き場所。

データの場所  %USERPROFILE%\\ActualsDashboard（環境変数 ACTUALS_DASHBOARD_HOME で変更可）
              config（画面で変えた設定）/ runtime（ロック・サーバー情報）/ logs / cache（共有の写し）/ export
              %LOCALAPPDATA% は使わない（Microsoft Store 版 Python が隠すため。RNEConverter と同じ理由）
設定          ツールの config/settings.json が既定値。画面で変えた項目だけデータの場所に差分で残す
              （ツールのフォルダを新しい版に入れ替えても設定は消えない）
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
APP_ID = "actuals-dashboard"
DISPLAY_NAME = "倉入実績集計"


def app_info() -> Dict[str, Any]:
    try:
        return json.loads((APP_ROOT / "config" / "app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"app_id": APP_ID, "display_name": DISPLAY_NAME, "version": "0.0.0"}


@dataclass
class Paths:
    base: Path

    @classmethod
    def default(cls) -> "Paths":
        env = os.environ.get("ACTUALS_DASHBOARD_HOME")
        base = Path(env) if env else Path(os.environ.get("USERPROFILE") or Path.home()) / "ActualsDashboard"
        p = cls(base)
        for d in (p.config, p.runtime, p.logs, p.cache, p.export):
            d.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def config(self) -> Path:
        return self.base / "config"

    @property
    def runtime(self) -> Path:
        return self.base / "runtime"

    @property
    def logs(self) -> Path:
        return self.base / "logs"

    @property
    def cache(self) -> Path:
        return self.base / "cache"

    @property
    def export(self) -> Path:
        return self.base / "export"


# 画面から変えられる項目（キー、見出し、説明、種類）。種類: dir / file:<拡張子> / text / bool
EDITABLE: List[Tuple[str, str, str, str]] = [
    ("shared_dir", "共有フォルダ", "条件表・手動訂正・蓄積した実績を置く。全 PC で同じ場所を指定する。"
     "空欄の時はこの PC の中だけに保存（試用）。", "dir"),
    ("source_path", "取り込み元（倉入実績）", "RNEConverter が出す KURAIRE.sqlite3。"
     "RNEConverter の出力先（配布先フォルダ）と同じ場所。", "file:.sqlite3"),
    ("rneconverter_dir", "RNEConverter のフォルダ", "「データ更新」で抽出を頼む先（start_app.py があるフォルダ）。", "dir"),
    ("python_exe", "Python", "RNEConverter を動かす python.exe。空欄ならこのツールと同じ Python。", "file:.exe"),
    ("export_dir", "CSV の書き出し先", "月集計・明細・年集計の CSV を置くフォルダ。", "dir"),
    ("log_dir", "ログの保存先", "共有フォルダにすると全 PC の記録を 1 か所で見られる。", "dir"),
    ("source_table", "取り込み元の表の名前", "RNEConverter の RNE 一覧の table_name（既定 倉入実績）。空欄なら自動。", "text"),
    ("rne_output", "RNEConverter の出力名", "RNE 一覧の output（既定 KURAIRE）。", "text"),
    ("auto_import", "新しいデータが出ていたら自動で取り込む", "", "bool"),
]
PATH_KEYS = [k for k, _, _, kind in EDITABLE if kind == "dir" or kind.startswith("file")]


class SettingsError(ValueError):
    pass


def expand(value: str, paths: Paths) -> str:
    return str(value or "").replace("{DATA}", str(paths.base)).replace("{APP_ROOT}", str(APP_ROOT))


class Settings:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.default_file = APP_ROOT / "config" / "settings.json"
        self.user_file = paths.config / "settings.json"

    def _defaults(self) -> Dict[str, Any]:
        return json.loads(self.default_file.read_text(encoding="utf-8-sig"))

    def _user(self) -> Dict[str, Any]:
        try:
            return json.loads(self.user_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}

    def load(self) -> Dict[str, Any]:
        d = self._defaults()
        for k, v in self._user().items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                d[k] = {**d[k], **v}
            else:
                d[k] = v
        return d

    def revision(self) -> str:
        try:
            raw = self.user_file.read_bytes()
        except OSError:
            raw = b""
        return hashlib.sha1(raw).hexdigest()[:12]

    def resolved(self) -> Dict[str, Any]:
        s = self.load()
        out = copy.deepcopy(s)
        for k in ("shared_dir", "source_path", "rneconverter_dir", "export_dir", "log_dir", "python_exe"):
            out[k] = expand(s.get(k, ""), self.paths)
        out["shared_configured"] = bool(out["shared_dir"])
        if not out["shared_dir"]:
            out["shared_dir"] = str(self.paths.base / "shared")
        return out

    def save(self, changes: Dict[str, Any], base_rev: str) -> Tuple[str, List[str]]:
        if base_rev != self.revision():
            raise SettingsError("別のタブで設定が保存されています。読み直してから変更してください。")
        allowed = {k for k, _, _, _ in EDITABLE}
        cur = self._user()
        defaults = self._defaults()
        log_lines = []
        for k, v in changes.items():
            if k not in allowed:
                continue
            if k == "auto_import":
                v = bool(v)
            else:
                v = str(v or "").strip().strip('"')
            before = cur.get(k, defaults.get(k))
            if v == defaults.get(k):
                cur.pop(k, None)
            else:
                cur[k] = v
            if before != v:
                log_lines.append(f"{k}: {before!r} → {v!r}")
        self.user_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.user_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.user_file)
        return self.revision(), log_lines
