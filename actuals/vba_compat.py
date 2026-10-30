"""VBA / Excel と同じ結果を出すための小さな部品。

旧ブックの結果と 1 件もずれないことを最優先にしている（tests/test_golden.py が保存値と全件照合する）。
ここ以外で数値の丸めや比較の決まりを書かない。

- vba_val      … VBA の Val()。文字の先頭から数として読める所までを数にする
- vba_round    … VBA の Format(x, "0.00") の丸め（四捨五入。2 進の誤差は 15 桁で丸めてから）
- variant_cmp  … VBA の Variant どうしの比較（空は 0 か ""、数値と文字では数値が小さい）
- excel_value  … Excel のセルへ文字を貼った時の型変換（"5083" → 5083、日時の文字 → datetime）
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

Number = (int, float)

_VAL_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?")
_NUM_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_COMMA_NUM_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_DT_RE = re.compile(
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:\.\d+)?)?)?")


def is_number(v: Any) -> bool:
    return isinstance(v, Number) and not isinstance(v, bool)


def vba_val(v: Any) -> float:
    """VBA の Val()。空は 0。日付は文字（yyyy/mm/dd …）にしてから読むので年になる。"""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return -1.0 if v else 0.0
    if is_number(v):
        return float(v)
    if isinstance(v, datetime):
        v = v.strftime("%Y/%m/%d %H:%M:%S")
    elif isinstance(v, date):
        v = v.strftime("%Y/%m/%d")
    # Val は空白・タブ・改行を読み飛ばす（"1 2" は 12）
    s = re.sub(r"[ \t\r\n]", "", str(v))
    m = _VAL_RE.match(s)
    if not m:
        return 0.0
    text = m.group(0).replace("d", "e").replace("D", "e")
    try:
        return float(text)
    except ValueError:  # "1e" のような途中で切れた指数
        return float(re.match(r"[+-]?(?:\d+\.?\d*|\.\d+)", text).group(0))


def vba_round(x: float, digits: int) -> float:
    """Format(x, "0.00") と同じ丸め。1.005 は 1.01 になる（Python の round では 1.0）。"""
    q = Decimal(format(float(x), ".15g")).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return float(q)


def format_fixed(x: Any, digits: int) -> str:
    """表示用。Format(x, "0.000") のように、数でない値はそのまま返す。"""
    if x is None or x == "":
        return ""
    if not is_number(x):
        return str(x)
    q = Decimal(format(float(x), ".15g")).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{q:.{digits}f}"


def _cmp_key(a: Any, b: Any):
    if a is None:
        a = 0 if is_number(b) else ""
    if b is None:
        b = 0 if is_number(a) else ""
    return a, b


def variant_cmp(a: Any, b: Any) -> int:
    """VBA の Variant の比較。-1 / 0 / 1。

    空（None）は相手が数なら 0、文字なら "" として比べる。
    数と文字を比べると数のほうが小さい（"*" >= 5 は真、"*" <= 5 は偽。どちらも Or で
    「* なら条件なし」と組み合わされているので、結果には影響しない）。
    文字どうしは大文字小文字を区別する（Option Compare Binary）。
    """
    a, b = _cmp_key(a, b)
    if isinstance(a, datetime):
        a = excel_serial(a)
    if isinstance(b, datetime):
        b = excel_serial(b)
    an, bn = is_number(a), is_number(b)
    if an and bn:
        return (a > b) - (a < b)
    if an and not bn:
        return -1
    if bn and not an:
        return 1
    sa, sb = str(a), str(b)
    return (sa > sb) - (sa < sb)


def v_eq(a: Any, b: Any) -> bool:
    return variant_cmp(a, b) == 0


def v_le(a: Any, b: Any) -> bool:
    return variant_cmp(a, b) <= 0


def v_ge(a: Any, b: Any) -> bool:
    return variant_cmp(a, b) >= 0


def v_lt(a: Any, b: Any) -> bool:
    return variant_cmp(a, b) < 0


def excel_serial(dt: datetime) -> float:
    delta = dt - datetime(1899, 12, 30)
    return delta.days + delta.seconds / 86400.0


def parse_datetime(v: Any) -> Optional[datetime]:
    """日時として読めれば datetime。'2022-06-01 15:36:30'、'2022/6/1 15:36'、'2022-06-01' など。"""
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if not isinstance(v, str):
        return None
    m = _DT_RE.fullmatch(v.strip())
    if not m:
        return None
    y, mo, d, hh, mi, ss = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
    except ValueError:
        return None


def excel_value(v: Any) -> Any:
    """Excel のセルに貼った時と同じ型にする（旧 VBA は DATA シートに貼ってから判定していた）。

    空文字は空（None）。数に見える文字は数（"01" → 1、"5083" → 5083、"1,234" → 1234）。
    日時に見える文字は datetime。それ以外は文字のまま。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if is_number(v) or isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v)
    t = s.strip()
    if t == "":
        return None
    if _NUM_RE.fullmatch(t) or _COMMA_NUM_RE.fullmatch(t):
        t2 = t.replace(",", "")
        try:
            f = float(t2)
        except ValueError:
            return s
        if f.is_integer() and "e" not in t2.lower() and "." not in t2 and abs(f) < 1e15:
            return int(f)
        return f
    dt = parse_datetime(t)
    if dt is not None:
        return dt
    return s


def like_contains(value: Any, needle: str) -> bool:
    """VBA の  value Like "*needle*" 。空は空文字として扱う。"""
    if value is None:
        return False
    if isinstance(value, datetime):
        value = value.strftime("%Y/%m/%d %H:%M:%S")
    elif is_number(value):
        value = format(value, "g") if isinstance(value, float) else str(value)
    return needle in str(value)
