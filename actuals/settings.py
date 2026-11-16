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
        """この PC のデータの場所の config（前の版の PC ごとの設定・ログインが残っている所。いまは書かない）。"""
        return self.base / "config"

    @property
    def tool_config(self) -> Path:
        """**設定とログインの置き場所**: ツールのフォルダの config（全員で同じツールを開くので全 PC 共通）。

        ACTUALS_TOOL_CONFIG で替えられる（試験・開発用。"{DATA}" はこの PC のデータの場所）。
        """
        env = os.environ.get("ACTUALS_TOOL_CONFIG", "").strip()
        if env:
            return Path(env.replace("{DATA}", str(self.base)))
        return APP_ROOT / "config"

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


# 画面から変えられる項目（キー、段、見出し、何に使うか、種類）。種類: dir / file:<拡張子> / text / number / bool
# 段は、データの流れの順: ① SymNavi から取り出す → ② 取り込んで集計する → ③ 書き出す・記録する
GROUPS: Dict[str, str] = {
    "extract": "① データ更新（SymNavi で RNE を実行して、当月の倉入実績を取り出す）",
    "store": "② 集計データの置き場所",
    "output": "③ 書き出しとログ",
    "advanced": "詳しい設定（ふつうは変えない）",
}
EDITABLE: List[Tuple[str, str, str, str, str]] = [
    ("rne_path", "extract", "RNE ファイル",
     "SymNavi に渡す問い合わせのファイル。旧ブックと同じフォルダにあった「梱包実績(当月_倉入れ日付順).RNE」を選ぶ。"
     "「データ更新」を押すと、この RNE で当月の倉入実績を取り出し、そのまま取り込む。", "file:.rne"),
    ("symnavi_exe", "extract", "SymNavi.exe",
     "SymNavi 本体の場所。旧ツールと同じ C:\\NAVICL\\bin\\SymNavi.exe が既定。", "file:.exe"),
    ("symnavi_server", "extract", "SymNavi のサーバー名", "旧ツールと同じ NlmFaNgyNavd が既定。", "text"),
    ("shared_dir", "store", "共有フォルダ",
     "取り込んだ倉入実績（月ごとに蓄積）・グループの条件表・手動訂正を置く。全 PC で同じ場所にすると、"
     "全員が同じ集計を見る（どの PC で「データ更新」しても全員に反映）。空欄ならこの PC の中だけに保存（試用）。", "dir"),
    ("export_dir", "output", "CSV の書き出し先",
     "月の集計・明細・年の集計の「CSV に」ボタンで書くフォルダ。", "dir"),
    ("log_dir", "output", "ログの出力パス",
     "ログ（動きの記録）を書く場所。困った時に原因を追うために使う。この下に actualsdashboard フォルダを作って書く"
     "（出力パスの直下には置かない）。共有フォルダ（例 \\\\サーバー\\共有\\logs）にすると全 PC の分を 1 か所で見られる"
     "（名前に PC 名が入るので混ざらない）。空欄ならこの PC の logs。届かない時もこの PC の logs に書き、止まらない。"
     "保存するとすぐ切り替わる（再起動は不要）。", "dir"),
    ("work_dir", "advanced", "作業フォルダ",
     "SymNavi に結果を一時的に保存させる場所（取り込んだら消す）。%LOCALAPPDATA% の中は不可"
     "（Microsoft Store 版の Python だと SymNavi から見えない）。", "dir"),
    ("startup_wait_sec", "advanced", "SymNavi の起動を待つ秒数", "既定 20 秒。", "number"),
    ("result_wait_sec", "advanced", "SymNavi の結果を待つ秒数", "既定 120 秒。時間のかかる時だけ増やす。", "number"),
    ("cscript_path", "advanced", "cscript.exe",
     "Excel 経由の予備の取り出しで使う。ACE が 32bit の時は C:\\Windows\\SysWOW64\\cscript.exe。", "file:.exe"),
]
PATH_KEYS = [k for k, _, _, _, kind in EDITABLE if kind == "dir" or kind.startswith("file")]


class SettingsError(ValueError):
    pass


class SettingsInputError(ValueError):
    """入力の誤り（422）。"""


def expand(value: str, paths: Paths) -> str:
    return str(value or "").replace("{DATA}", str(paths.base)).replace("{APP_ROOT}", str(APP_ROOT))


def is_full_path(text: str) -> bool:
    """フルパスか（C:\\…・\\\\サーバー\\…・/…）。Windows の形はどの OS でも見分ける。"""
    import ntpath
    return bool(text) and (os.path.isabs(text) or ntpath.isabs(text))


# 入っていない項目の値（ツールの config/settings.json に書かれていなければこれ）
DEFAULTS: Dict[str, Any] = {
    "rne_path": "", "symnavi_exe": "C:\\NAVICL\\bin\\SymNavi.exe", "symnavi_server": "NlmFaNgyNavd",
    "shared_dir": "", "export_dir": "{DATA}/export", "log_dir": "", "work_dir": "{DATA}/work",
    "startup_wait_sec": 20, "result_wait_sec": 120, "cscript_path": "C:\\Windows\\System32\\cscript.exe",
    "lock_wait_sec": 10, "log_keep_days": 90,
}


class Settings:
    """設定は**ツールのフォルダの config\\settings.json 1 つ**（全員で同じツールを開くので全 PC 共通。配ればそのまま付いていく）。

    画面の「保存」もここへ書く。PC ごとの設定は持たない（前の版の <データの場所>\\config\\settings.json は読まない）。
    {DATA} は各 PC のデータの場所（CSV・作業フォルダなど、PC ごとに分けたい所に使う）。
    """

    def __init__(self, paths: Paths):
        self.paths = paths
        self.file = paths.tool_config / "settings.json"
        self.default_file = self.file          # 前の名前（画面・試験から）
        self.user_file = self.file
        self.old_local_file = paths.config / "settings.json"

    def _defaults(self) -> Dict[str, Any]:
        return dict(DEFAULTS)

    def _user(self) -> Dict[str, Any]:
        try:
            d = json.loads(self.file.read_text(encoding="utf-8-sig"))
            return d if isinstance(d, dict) else {}
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
            raw = self.file.read_bytes()
        except OSError:
            raw = b""
        return hashlib.sha1(raw).hexdigest()[:12]

    def resolved(self) -> Dict[str, Any]:
        s = self.load()
        out = copy.deepcopy(s)
        for k in ("shared_dir", "rne_path", "symnavi_exe", "export_dir", "log_dir", "work_dir", "cscript_path"):
            out[k] = expand(s.get(k, ""), self.paths)
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
            if k in ("startup_wait_sec", "result_wait_sec"):
                try:
                    v = int(str(v).strip())
                except ValueError:
                    raise SettingsInputError("秒数は数字で入れてください。")
                if not 1 <= v <= 3600:
                    raise SettingsInputError("秒数は 1〜3600 で入れてください。")
            else:
                v = str(v or "").strip().strip('"')
                if k == "log_dir" and v and not is_full_path(expand(v, self.paths)):
                    raise SettingsInputError("ログの出力パスはフルパスで入れてください（例 \\\\サーバー\\共有\\logs）。"
                                             "空欄ならこの PC の logs に書きます。")
            before = cur.get(k, defaults.get(k))
            cur[k] = v                              # 既定と同じでも書く（ファイルを見れば全部分かるように）
            if before != v:
                log_lines.append(f"{k}: {before!r} → {v!r}")
        for k, v in defaults.items():
            cur.setdefault(k, v)
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_name(f".{self.file.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.file)
        except OSError as e:
            raise SettingsInputError(f"ツールのフォルダの設定に書けません（{self.file}: {e}）。"
                                     "共有フォルダの書き込みの権限を確かめてください。")
        return self.revision(), log_lines
