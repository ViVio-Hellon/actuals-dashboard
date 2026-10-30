"""VBScript が書き出した TSV（UTF-8、1 行目が列名）を読む。

旧 VBA は Page1 シートの 1 行目を列名、2 行目以降をデータとして扱っていた。
ここでも同じ前提で、列名の改行・前後空白を除去する（fldAccdb の vbLf 除去と同じ）。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Tuple


class TsvFormatError(Exception):
    pass


def _clean_header(name: str, index: int) -> str:
    n = name.replace("\r", "").replace("\n", "").strip()
    return n if n else f"F{index + 1}"


def unique_columns(names: List[str]) -> List[str]:
    """列名の重複を _2, _3 … を付けて解く。SQLite は英字の大文字・小文字を区別しないので、それも重複とみなす
    （例: "BOX番号" と "Box番号"）。止めずに出力し、どの列を変えたかをログに残す。"""
    import logging
    seen: dict = {}
    out: List[str] = []
    for n in names:
        key = n.casefold()
        if key not in seen:
            seen[key] = 1
            out.append(n)
            continue
        k = seen[key]
        while True:
            k += 1
            cand = f"{n}_{k}"
            if cand.casefold() not in seen:
                break
        seen[key] = k
        seen[cand.casefold()] = 1
        out.append(cand)
        logging.getLogger(__name__).warning("列名が重なるため変えました: %s → %s", n, cand)
    return out


def read_tsv(path: Path, encoding: str = "utf-8-sig") -> Tuple[List[str], List[List[str]]]:
    """列名リストと行リスト（各行は文字列のリスト）を返す。短い行は空文字で埋め、長い行は切り詰める。"""
    with open(path, "r", encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE, escapechar=None)
        try:
            header = next(reader)
        except StopIteration:
            raise TsvFormatError(f"TSV が空です: {path}")
        columns = unique_columns([_clean_header(h, i) for i, h in enumerate(header)])
        n = len(columns)
        rows: List[List[str]] = []
        for raw in reader:
            if not raw or (len(raw) == 1 and raw[0] == ""):
                continue
            if len(raw) < n:
                raw = raw + [""] * (n - len(raw))
            elif len(raw) > n:
                raw = raw[:n]
            rows.append(raw)
    return columns, rows
