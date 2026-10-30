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


# 画面から変えられる項目（キー、段、見出し、何に使うか、種類）。種類: dir / file:<拡張子> / text / bool
# 段は、データの流れの順: ① SymNavi から取り出す → ② 取り込んで集計する → ③ 書き出す・記録する
GROUPS: Dict[str, str] = {
    "extract": "① データ更新（SymNavi から当月の倉入実績を取り出す）",
    "store": "② 集計データの置き場所",
    "output": "③ 書き出しと記録",
    "advanced": "詳しい設定（ふつうは変えない）",
}
EDITABLE: List[Tuple[str, str, str, str, str]] = [
    ("rne_path", "extract", "RNE ファイル",
     "SymNavi に渡す問い合わせのファイル。旧ブックと同じフォルダにあった「梱包実績(当月_倉入れ日付順).RNE」を選ぶ。"
     "「データ更新」を押すと、この RNE で当月の倉入実績を取り出す。", "file:.rne"),
    ("rne_output_dir", "extract", "作ったファイルを置くフォルダ",
     "RNEConverter が取り出した倉入実績を SQLite のファイルにして置く場所。このツールはここから取り込む。"
     "共有フォルダにすると、どの PC で「データ更新」しても全員が取り込める。", "dir"),
    ("rne_output", "extract", "作るファイルの名前",
     "拡張子は付けない。KURAIRE なら「KURAIRE.sqlite3」ができる。", "text"),
    ("rneconverter_dir", "extract", "RNEConverter のフォルダ",
     "SymNavi を動かして RNE を実行するツールの場所（start_app.py があるフォルダ）。"
     "SymNavi の ID・パスワードは RNEConverter の設定画面で登録する。", "dir"),
    ("shared_dir", "store", "共有フォルダ",
     "取り込んだ倉入実績（月ごとに蓄積）・グループの条件表・手動訂正を置く。全 PC で同じ場所にすると、"
     "全員が同じ集計を見る。空欄ならこの PC の中だけに保存（試用）。", "dir"),
    ("export_dir", "output", "CSV の書き出し先",
     "月の集計・明細・年の集計の「CSV に」ボタンで書くフォルダ。", "dir"),
    ("log_dir", "output", "ログの保存先",
     "動きの記録。困った時に原因を追うために使う。共有フォルダにすると全 PC の分を 1 か所で見られる。", "dir"),
    ("python_exe", "advanced", "Python",
     "RNEConverter を動かす python.exe。空欄ならこのツールと同じ Python。", "file:.exe"),
    ("source_table", "advanced", "作るファイルの中の表の名前", "既定は「倉入実績」。", "text"),
    ("auto_import", "advanced", "新しいファイルができていたら自動で取り込む",
     "別の PC が「データ更新」した時も、画面を開いていれば取り込む。", "bool"),
]
PATH_KEYS = [k for k, _, _, _, kind in EDITABLE if kind == "dir" or kind.startswith("file")]


def source_file(output_dir: str, name: str) -> str:
    """取り込むファイル = 作ったファイルを置くフォルダ + 作るファイルの名前 + .sqlite3。"""
    if not output_dir or not name:
        return ""
    return str(Path(output_dir) / f"{name}.sqlite3")


class SettingsError(ValueError):
    pass


class SettingsInputError(ValueError):
    """入力の誤り（422）。"""


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
        for k in ("shared_dir", "rne_path", "rne_output_dir", "rneconverter_dir", "export_dir", "log_dir", "python_exe"):
            out[k] = expand(s.get(k, ""), self.paths)
        out["rne_output"] = str(s.get("rne_output") or "").strip()
        out["source_path"] = source_file(out["rne_output_dir"], out["rne_output"])
        out["shared_configured"] = bool(out["shared_dir"])
        if not out["shared_dir"]:
            out["shared_dir"] = str(self.paths.base / "shared")
        return out

    def save(self, changes: Dict[str, Any], base_rev: str) -> Tuple[str, List[str]]:
        if base_rev != self.revision():
            raise SettingsError("別のタブで設定が保存されています。読み直してから変更してください。")
        allowed = {k for k, _, _, _, _ in EDITABLE}
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
                if k == "rne_output":
                    v = v[:-8] if v.lower().endswith(".sqlite3") else v
                    if any(ch in v for ch in '\\/:*?"<>|'):
                        raise SettingsInputError("作るファイルの名前に使えない文字があります（\\ / : * ? \" < > |）。")
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
