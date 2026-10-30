"""SymNavi の設定の型（RNEConverter の app/config.py から SymNaviConfig・CscriptConfig を写したもの。値の意味も同じ）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SymNaviConfig:
    exe: str = r"C:\NAVICL\bin\SymNavi.exe"
    args: str = "-d -u"
    server: str = "NlmFaNgyNavd"
    dde_app: str = "SymNavi"
    dde_topic: str = "Macro"
    sheet_name: str = "Page1"
    process_name: str = "SymNavi.exe"
    startup_wait_sec: int = 20
    dde_timeout_sec: int = 120
    dde_client: str = "python"     # "python" = Windows の DDE を直接使う（Excel 不要）/ "excel" = 旧方式（見えない Excel 経由）
    save_dir: str = "{DATA}/work"   # SymNavi に結果を保存させるフォルダ。%LOCALAPPDATA% の中は不可（Store 版 Python で SymNavi から見えない）
    excel_fallback: bool = True    # 直接で SymNavi が保存できない時、旧 VBA と同じ Excel 経由に自動で切り替える
    save_format: str = "BIFF"      # SymNavi の [Save] の形式。BIFF = Excel 97-2003（旧 VBA と同じ。Python で読む）/ TXT / CSV
    receive: str = "api"           # 結果の受け取り方。api = 拡張 API（画面を使わない。取り出せない RNE だけ file）/ api_compare = API で測って file と比べる / file = 保存させた xls / clipboard = [Copy()] でクリップボード（失敗したら file）/ compare = 両方で取り比べて記録（出力は file）
    reuse_session: bool = True     # SymNavi を 1 回だけ起動し、すべての RNE を続けて処理する（False = RNE ごとに起動し直す）


@dataclass
class CscriptConfig:
    path: str = r"C:\Windows\System32\cscript.exe"
    timeout_sec: int = 900
