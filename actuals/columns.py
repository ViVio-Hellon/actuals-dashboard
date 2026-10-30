"""RNE（倉入実績）の列名。

旧 VBA は DATA シートの列の位置（A〜AH、36・37 列目）で読んでいた。Web 版は列名で読み、
必要な列が無い時は取り込みを止めて知らせる（RNE の列の並びが変わっても別の列を読まない）。
"""
from __future__ import annotations

from typing import Dict, Iterable, List

LOT = "ﾛｯﾄ番号"
CAST = "鋳造番号"
INSP = "検査番号"
ORDER = "ｵｰﾀﾞｰ番号"
USE = "用途ｺｰﾄﾞ"
O_MAT, O_TMP, O_T, O_W, O_L = "ｵｰﾀﾞｰ材質", "ｵｰﾀﾞｰ調質", "ｵｰﾀﾞｰ板厚", "ｵｰﾀﾞｰ板幅", "ｵｰﾀﾞｰ板丈"
M_MAT, M_TMP, M_T, M_W, M_L = "製造材質", "製造調質", "製造板厚", "製造板幅", "製造板丈"
SHAPE = "形状"
VC = "VC"
KURA_DATE = "最小の倉入完了_年月日"        # 太陽暦の集計日（旧 V 列）
WEIGHT = "実績_倉入重量"                   # 旧 W 列
SG = "比重"                                # 旧 X 列
UNIT_W = "製品単重"                        # 旧 AA 列（表示用。判定は計算単重を使う）
COURSE = "実績_設備ｺｰｽ"                    # 旧 AB 列（同時VC の判定）
BOX_END = "BOX実績_作業終了_年月日時分秒"  # 旧 AF 列（並べ替え・既定の集計月）
BOX_DATE = "BOX実績_作業終了_日付"         # 旧 AH 列（現場暦の集計日と直）
FACE_VC = "表VC"                           # 旧 36 列目（現在の RNE には無い。同時VC は保留中）
BACK_VC = "裏VC"                           # 旧 37 列目

# 受領ブックの DATA シートの列順（34 列）。表示と CSV の既定の並び
DATA_COLUMNS: List[str] = [
    LOT, CAST, INSP, ORDER, USE, O_MAT, O_TMP, O_T, O_W, O_L, M_MAT, M_TMP, M_T, M_W, M_L,
    SHAPE, "表面", "加工", "表面加工", "洗浄", VC, KURA_DATE, WEIGHT, SG, "受注枚数", "設計枚数", UNIT_W,
    COURSE, "残仕掛設備ｺｰｽ", "ｺﾝﾏ5本ｶｰﾄﾞ区分", "BOX実績_設備名", BOX_END, "BOX実績_作業終了_直", BOX_DATE,
]

# 判定・計算・集計に使う列。無ければ取り込まない
REQUIRED: List[str] = [
    LOT, INSP, ORDER, USE, O_MAT, O_TMP, O_T, O_W, O_L, M_MAT, M_TMP, M_T, M_W, M_L,
    SHAPE, VC, KURA_DATE, WEIGHT, SG, UNIT_W, COURSE, BOX_END, BOX_DATE,
]

# 寸法と材質を「製造」と「ｵｰﾀﾞｰ」のどちらで見るか（設定シート E3 / O3）
BASIS_COLUMNS: Dict[str, Dict[str, str]] = {
    "製造": {"mat": M_MAT, "tmp": M_TMP, "t": M_T, "w": M_W, "l": M_L},
    "ｵｰﾀﾞｰ": {"mat": O_MAT, "tmp": O_TMP, "t": O_T, "w": O_W, "l": O_L},
}
BASES = ("製造", "ｵｰﾀﾞｰ")


def missing_required(columns: Iterable[str]) -> List[str]:
    have = set(columns)
    return [c for c in REQUIRED if c not in have]
