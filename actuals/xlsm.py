"""旧ブック（.xlsm / .xlsx）を標準ライブラリだけで読む（移行と照合のため）。

読むのはセルの値（数式は保存されている計算結果）だけ。日付の表示形式のセルは datetime にする。
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from . import columns as C
from .conditions import FIELD_KEYS, ConditionSet, empty_row

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_BUILTIN_DATE = set(range(14, 23)) | {45, 46, 47, 50, 51, 52, 53, 54, 55, 56, 57, 58}
_REF = re.compile(r"([A-Z]+)(\d+)")


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


class Workbook:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._z = zipfile.ZipFile(self.path)
        self._shared = self._read_shared()
        self._date_styles = self._read_date_styles()
        self.sheets = self._read_sheet_map()
        self._cache: Dict[str, Dict[Tuple[int, int], Any]] = {}

    def close(self) -> None:
        self._z.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _xml(self, name: str):
        return ET.fromstring(self._z.read(name))

    def _read_shared(self) -> List[str]:
        try:
            root = self._xml("xl/sharedStrings.xml")
        except KeyError:
            return []
        out = []
        for si in root.findall("m:si", NS):
            out.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")
                               if not _in_phonetic(si, t)))
        return out

    def _read_date_styles(self) -> set:
        root = self._xml("xl/styles.xml")
        custom = {}
        for nf in root.findall("m:numFmts/m:numFmt", NS):
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", nf.get("formatCode") or "").lower()
            custom[int(nf.get("numFmtId"))] = bool(re.search(r"[ymdh]", code)) and "0.0" not in code
        out = set()
        for i, xf in enumerate(root.findall("m:cellXfs/m:xf", NS)):
            fid = int(xf.get("numFmtId") or 0)
            if fid in _BUILTIN_DATE or custom.get(fid):
                out.add(i)
        return out

    def _read_sheet_map(self) -> Dict[str, str]:
        wb = self._xml("xl/workbook.xml")
        rels = self._xml("xl/_rels/workbook.xml.rels")
        target = {r.get("Id"): r.get("Target") for r in rels.findall("pr:Relationship", NS)}
        out = {}
        for s in wb.findall("m:sheets/m:sheet", NS):
            rid = s.get(f"{{{NS['r']}}}id")
            t = target.get(rid, "")
            out[s.get("name")] = "xl/" + t.lstrip("/").replace("xl/", "", 1) if not t.startswith("xl/") else t
        return out

    def cells(self, sheet: str) -> Dict[Tuple[int, int], Any]:
        if sheet in self._cache:
            return self._cache[sheet]
        root = self._xml(self.sheets[sheet])
        out: Dict[Tuple[int, int], Any] = {}
        for c in root.iter(f"{{{NS['m']}}}c"):
            m = _REF.fullmatch(c.get("r") or "")
            if not m:
                continue
            key = (int(m.group(2)), col_index(m.group(1)))
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "inlineStr":
                out[key] = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
                continue
            if v is None or v.text is None:
                continue
            text = v.text
            if t == "s":
                out[key] = self._shared[int(text)]
            elif t in ("str", "e"):
                out[key] = text
            elif t == "b":
                out[key] = text == "1"
            else:
                num = float(text)
                if int(c.get("s") or 0) in self._date_styles:
                    out[key] = datetime(1899, 12, 30) + timedelta(seconds=round(num * 86400))
                else:
                    out[key] = int(num) if num.is_integer() and abs(num) < 1e15 else num
        self._cache[sheet] = out
        return out

    def value(self, sheet: str, row: int, col: int) -> Any:
        return self.cells(sheet).get((row, col))

    def max_row(self, sheet: str, col: Optional[int] = None) -> int:
        keys = self.cells(sheet).keys()
        rows = [r for (r, c) in keys if col is None or c == col]
        return max(rows) if rows else 0


def _in_phonetic(si, t) -> bool:
    # ふりがな（rPh）の文字は値に含めない
    for rph in si.findall("m:rPh", NS):
        if t in list(rph.iter()):
            return True
    return False


# ---------- 旧ブックの各シート ----------
def read_data_rows(wb: Workbook, sheet: str = "DATA") -> Tuple[List[str], List[Dict[str, Any]]]:
    cells = wb.cells(sheet)
    max_col = max((c for (r, c) in cells if r == 1), default=0)
    header = [str(cells.get((1, c)) or "") for c in range(1, max_col + 1)]
    last = wb.max_row(sheet, 1)
    rows = []
    for r in range(2, last + 1):
        rec = {header[c - 1]: cells.get((r, c)) for c in range(1, max_col + 1) if header[c - 1]}
        if rec.get(C.LOT) in (None, ""):
            continue
        rows.append(rec)
    return header, rows


def read_conditions(wb: Workbook, sheet: str = "設定") -> ConditionSet:
    rows = []
    for r in range(5, 20):
        row = empty_row()
        for i, key in enumerate(FIELD_KEYS):
            v = wb.value(sheet, r, 3 + i)       # C 列から W 列
            row[key] = v if v != "" else None
        rows.append(row)
    return ConditionSet(rows=rows, basis_main=wb.value(sheet, 3, 5) or "製造",
                        basis_not=wb.value(sheet, 3, 15) or "製造")


def read_overrides(wb: Workbook, sheet: str = "修正") -> Dict[str, Any]:
    """修正シート A 列（訂正用）を、同じ行の K 列（ロット番号）に結び付けて読む。"""
    cells = wb.cells(sheet)
    out = {}
    for (r, c), v in cells.items():
        if c != 1 or r < 2 or v in (None, ""):
            continue
        lot = cells.get((r, 11))
        if lot not in (None, ""):
            out[str(lot)] = v
    return out
