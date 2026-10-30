"""グループの条件表（旧 設定シート 5〜19 行）。

1 行が 1 グループ。列は旧シートの列（C〜W）に対応する。値は 3 種類だけ:
  None（空欄。VBA では 0 または "" として比べる）／ "*"（条件なし）／ 数または文字
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .columns import BASES

GROUP_COUNT = 15

# key, 旧シートの列, 見出し, 種類（name / flag / mat / num）
FIELDS: List[Tuple[str, str, str, str]] = [
    ("name", "C", "グループ名", "name"),
    ("vc", "D", "VC有無", "flag"),
    ("mat", "E", "材質", "mat"),
    ("t_min", "F", "板厚 MIN≦", "num"),
    ("t_max", "G", "板厚 ≦MAX", "num"),
    ("w_min", "H", "板幅 MIN≦", "num"),
    ("w_max", "I", "板幅 ≦MAX", "num"),
    ("l_min", "J", "板丈 MIN≦", "num"),
    ("l_gt", "K", "板丈 MIN＜", "num"),
    ("l_max", "L", "板丈 ≦MAX", "num"),
    ("wt_max", "M", "単重 ≦MAX", "num"),
    ("not_use", "N", "NOT 使用", "flag"),
    ("not_mat", "O", "NOT 材質", "mat"),
    ("not_t_min", "P", "NOT 板厚 MIN≦", "num"),
    ("not_t_max", "Q", "NOT 板厚 ≦MAX", "num"),
    ("not_w_min", "R", "NOT 板幅 MIN≦", "num"),
    ("not_w_max", "S", "NOT 板幅 ≦MAX", "num"),
    ("not_l_min", "T", "NOT 板丈 MIN≦", "num"),
    ("not_l_gt", "U", "NOT 板丈 MIN＜", "num"),
    ("not_l_max", "V", "NOT 板丈 ≦MAX", "num"),
    ("not_wt_max", "W", "NOT 単重 ≦MAX", "num"),
]
FIELD_KEYS = [f[0] for f in FIELDS]
FIELD_KIND = {f[0]: f[3] for f in FIELDS}

# 判定方式。既定は「修正版」（2026-10 業務確認: NOT 条件の扱いは VBA 側の誤り）
RULE_NOT_MATERIAL = {"material": "材質と比べる（修正版）", "temper_val": "調質の数値と比べる（VBA と同じ）"}
RULE_NOT_HIT = {"skip": "その行だけ外して次の行へ（修正版）", "stop": "判定をやめる（VBA と同じ）"}


@dataclass
class ConditionSet:
    rows: List[Dict[str, Any]]                      # 15 行。キーは FIELD_KEYS
    basis_main: str = "ｵｰﾀﾞｰ"                       # 旧 設定!E3
    basis_not: str = "ｵｰﾀﾞｰ"                        # 旧 設定!O3
    rule_not_material: str = "material"
    rule_not_hit: str = "skip"
    rev: int = 0
    updated_at: str = ""
    updated_by: str = ""

    def names(self) -> List[str]:
        return [str(r.get("name") or "") for r in self.rows]

    def to_dict(self) -> Dict[str, Any]:
        return {"rows": copy.deepcopy(self.rows), "basis_main": self.basis_main, "basis_not": self.basis_not,
                "rule_not_material": self.rule_not_material, "rule_not_hit": self.rule_not_hit,
                "rev": self.rev, "updated_at": self.updated_at, "updated_by": self.updated_by}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConditionSet":
        rows = [normalize_row(r) for r in (d.get("rows") or [])][:GROUP_COUNT]
        while len(rows) < GROUP_COUNT:
            rows.append(empty_row())
        return cls(rows=rows, basis_main=d.get("basis_main") or "ｵｰﾀﾞｰ", basis_not=d.get("basis_not") or "ｵｰﾀﾞｰ",
                   rule_not_material=d.get("rule_not_material") or "material",
                   rule_not_hit=d.get("rule_not_hit") or "skip", rev=int(d.get("rev") or 0),
                   updated_at=d.get("updated_at") or "", updated_by=d.get("updated_by") or "")

    def vba_compatible(self) -> "ConditionSet":
        """旧 VBA と同じ判定方式にした写し（照合・並行運用の確認用）。"""
        c = copy.deepcopy(self)
        c.rule_not_material, c.rule_not_hit = "temper_val", "stop"
        return c


def empty_row() -> Dict[str, Any]:
    return {k: None for k in FIELD_KEYS}


def normalize_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out = empty_row()
    for k in FIELD_KEYS:
        out[k] = r.get(k)
    return out


_NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")


def parse_cell(kind: str, text: Any) -> Any:
    """画面から来た文字を、旧シートに打った時と同じ型にする（数に見えれば数）。"""
    if text is None:
        return None
    if not isinstance(text, str):
        return text
    s = text.strip().translate(str.maketrans("０１２３４５６７８９．＊－", "0123456789.*-"))
    if s == "":
        return None
    if s == "*":
        return "*"
    if _NUM.fullmatch(s):
        f = float(s)
        return int(f) if f.is_integer() and "." not in s else f
    return text.strip() if kind in ("mat", "name") else s


class ConditionError(ValueError):
    def __init__(self, problems: List[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def validate(cs: ConditionSet) -> List[str]:
    """入力の誤りを文章で返す。空なら保存してよい。"""
    problems: List[str] = []
    if cs.basis_main not in BASES or cs.basis_not not in BASES:
        problems.append("寸法の基準は「製造」か「ｵｰﾀﾞｰ」を選んでください。")
    if cs.rule_not_material not in RULE_NOT_MATERIAL or cs.rule_not_hit not in RULE_NOT_HIT:
        problems.append("判定方式の指定が不正です。")
    if len(cs.rows) != GROUP_COUNT:
        problems.append(f"条件は {GROUP_COUNT} 行です。")
    for i, r in enumerate(cs.rows, start=1):
        for key, col, label, kind in FIELDS:
            v = r.get(key)
            if v is None:
                continue
            if kind == "flag" and v != "*":
                problems.append(f"{i} 行目「{label}」は * か空欄にしてください（いま: {v}）。")
            elif kind == "num" and not (v == "*" or (isinstance(v, (int, float)) and not isinstance(v, bool))):
                problems.append(f"{i} 行目「{label}」は数か * にしてください（いま: {v}）。")
            elif kind == "name" and not isinstance(v, (str, int, float)):
                problems.append(f"{i} 行目「{label}」が不正です。")
    return problems


def blank_warnings(cs: ConditionSet) -> List[str]:
    """空欄が 0 として比べられる箇所（旧 VBA と同じ動き）を知らせる。"""
    out: List[str] = []
    for i, r in enumerate(cs.rows, start=1):
        if r.get("mat") in (None, ""):
            continue  # 材質が空の行はどのロットにも当たらない（未使用の行）
        blanks = [label for key, col, label, kind in FIELDS[3:11] if r.get(key) is None]
        if blanks:
            out.append(f"{i} 行目 {r.get('name') or ''}: {', '.join(blanks)} が空欄です（0 として比べます）。")
    return out


# 受領ブック（設定シート 5〜19 行、E3 / O3 = ｵｰﾀﾞｰ）の値。共有の条件表がまだ無い時の初期値
_DEFAULT_SHEET = [
    # name, vc, mat, t_min, t_max, w_min, w_max, l_min, l_gt, l_max, wt_max, not_use, not_mat, P..W
    ["丸徳ﾗｲﾝ", None, "*", 0.1, 7, 150, 1250, 0, "*", 2502.5, "*", "*", "*", 3, 4, 1250, 1250, 2500, "*", 2500, "*"],
    ["自転車部品", None, "75S", 6, 7.8, 150, 175, 800, "*", 850, "*"],
    ["陰極板", None, "*", 6, 6.5, 1200, 1325, 660, "*", 700, "*"],
    ["ﾊﾞﾗﾝｻｰﾗｲﾝ(532H)", None, "532H", "*", "*", "*", "*", "*", "*", "*", "*"],
    ["3SR指定", None, "3SR", "*", "*", 850, "*", 2500, "*", "*", "*"],
    ["ﾄｯﾄ", None, "*", 0.8, 6, 650, 1601, 2400, "*", 4002, 80],
    ["ﾄｯﾄ(3SR)", None, "3SR", "*", "*", "*", "*", "*", "*", "*", "*"],
    ["ﾊﾞﾗﾝｻｰﾗｲﾝ", None, "*", 0.8, 6, 400, 1601, 935, "*", 6002, "*"],
    ["L-1", None, "*", 4, "*", "*", "*", "*", "*", "*", "*", "*", 1070, "*", "*", "*", "*", 0, 0, 73, "*"],
    ["生地その他", None, "*", "*", 6, "*", "*", "*", "*", "*", "*"],
    ["SVC", "*", "*", 0.6, 15, 400, 1250, 830, "*", 4002, 30],
    ["LVC", "*", "*", 1, 20, 730, 1600, 1500, "*", 5002, "*"],
    ["HVC", "*", "*", 20, "*", 800, "*", 1300, "*", "*", "*"],
    ["VCその他", "*", "*", 6, "*", "*", "*", "*", "*", "*", "*", "*", "*", "*", "*", 1000, 1550, 2000, "*", 3055, "*"],
    [],
]


def default_conditions() -> ConditionSet:
    rows = []
    for vals in _DEFAULT_SHEET:
        r = empty_row()
        for k, v in zip(FIELD_KEYS, vals):
            r[k] = v
        rows.append(r)
    return ConditionSet(rows=rows)
