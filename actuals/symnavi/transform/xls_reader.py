"""Excel 97-2003（.xls、BIFF8）を標準ライブラリだけで読む。

SymNavi の [Save(…)] は、形式に何を指定しても Excel 97-2003 で保存することがある（実機で確認: TXT を指定して
.txt に保存させたファイルの中身が xls だった）。これまでは ACE（cscript + xls_to_tsv.vbs）で読んでいたが、
ACE の 32bit / 64bit と cscript の組み合わせに左右されるので、Python で直接読む。

読むのは 1 枚のシート（既定 Page1。無ければ先頭）の値だけ。対応するセル:
  文字（共有文字列 LABELSST、LABEL、RSTRING）、数値（NUMBER、RK、MULRK）、数式の結果（FORMULA + STRING）、
  真偽・エラー（BOOLERR）。空セルは空文字。
日付の表示形式のセルは "YYYY-MM-DD HH:MM:SS"（時刻が 0 なら "YYYY-MM-DD"）にする。
整数の数値は "12"、小数は有効 15 桁の文字にする（Excel で表示される値に近い）。

ファイルの入れ物（OLE 複合ファイル）と BIFF8 のレコードの仕様は、Microsoft の [MS-CFB] / [MS-XLS] による。
"""
from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF


class XlsFormatError(Exception):
    pass


def is_xls(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == OLE_MAGIC
    except OSError:
        return False


# ---------------- OLE 複合ファイル ----------------
def _ole_stream(data: bytes, names: Sequence[str]) -> bytes:
    if data[:8] != OLE_MAGIC:
        raise XlsFormatError("Excel 97-2003 のファイルではありません")
    sector_shift, mini_shift = struct.unpack_from("<HH", data, 0x1E)
    ssize, msize = 1 << sector_shift, 1 << mini_shift
    n_fat, first_dir = struct.unpack_from("<II", data, 0x2C)
    mini_cutoff, first_minifat, n_minifat, first_difat, n_difat = struct.unpack_from("<IIIII", data, 0x38)

    def sector(sid: int) -> bytes:
        off = (sid + 1) * ssize
        return data[off:off + ssize]

    # DIFAT（FAT のセクタ番号の一覧）
    difat = list(struct.unpack_from("<109I", data, 0x4C))
    sid = first_difat
    for _ in range(n_difat):
        if sid in (ENDOFCHAIN, FREESECT):
            break
        vals = struct.unpack(f"<{ssize // 4}I", sector(sid))
        difat.extend(vals[:-1])
        sid = vals[-1]
    fat: List[int] = []
    for s in difat[:n_fat]:
        if s in (ENDOFCHAIN, FREESECT):
            continue
        fat.extend(struct.unpack(f"<{ssize // 4}I", sector(s)))

    def chain(start: int, table: List[int], limit: int) -> List[int]:
        out, s = [], start
        while s not in (ENDOFCHAIN, FREESECT) and len(out) < limit:
            if s >= len(table):
                raise XlsFormatError("ファイルが壊れています（セクタの鎖）")
            out.append(s)
            s = table[s]
        return out

    big_limit = len(data) // ssize + 1

    def read_big(start: int, size: int) -> bytes:
        return b"".join(sector(s) for s in chain(start, fat, big_limit))[:size]

    # ディレクトリ
    dir_bytes = b"".join(sector(s) for s in chain(first_dir, fat, big_limit))
    entries = []
    for i in range(0, len(dir_bytes) - 127, 128):
        e = dir_bytes[i:i + 128]
        name_len = struct.unpack_from("<H", e, 0x40)[0]
        name = e[:max(0, name_len - 2)].decode("utf-16-le", errors="replace")
        etype = e[0x42]
        start, size = struct.unpack_from("<II", e, 0x74)
        entries.append((name, etype, start, size))
    if not entries:
        raise XlsFormatError("ファイルが壊れています（ディレクトリが空）")
    root = entries[0]
    target = next((e for e in entries if e[1] == 2 and e[0] in names), None)
    if target is None:
        raise XlsFormatError("Excel のブックが入っていません（Workbook が見つかりません）")
    _, _, start, size = target
    if size >= mini_cutoff:
        return read_big(start, size)
    # 小さいストリームはミニストリームの中にある
    mini_stream = read_big(root[2], root[3])
    minifat: List[int] = []
    if n_minifat:
        mf = read_big(first_minifat, n_minifat * ssize)
        minifat = list(struct.unpack(f"<{len(mf) // 4}I", mf))
    return b"".join(mini_stream[s * msize:(s + 1) * msize] for s in chain(start, minifat, len(minifat) + 1))[:size]


# ---------------- BIFF8 ----------------
class _Segments:
    """SST などが CONTINUE レコードにまたがる時の読み手。文字の途中で次の塊に移る時は、
    塊の先頭 1 バイトが「その先の文字が 2 バイトか」を表す（[MS-XLS] 2.5.293）。"""

    def __init__(self, parts: List[bytes]) -> None:
        self.parts, self.i, self.pos = parts, 0, 0

    def _need(self) -> None:
        while self.i < len(self.parts) and self.pos >= len(self.parts[self.i]):
            self.i += 1
            self.pos = 0
        if self.i >= len(self.parts):
            raise XlsFormatError("文字列の表が途中で終わっています")

    def take(self, n: int) -> bytes:
        out = b""
        while n > 0:
            self._need()
            p = self.parts[self.i]
            chunk = p[self.pos:self.pos + n]
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return out

    def chars(self, cch: int, high: bool) -> str:
        out = []
        while cch > 0:
            # 文字が今の塊の終わりちょうどから始まる時も、次の塊の先頭には文字幅の 1 バイトがある（xlrd と同じ扱い）
            if self.i >= len(self.parts):
                raise XlsFormatError("文字列の表が途中で終わっています")
            p = self.parts[self.i]
            width = 2 if high else 1
            avail = (len(p) - self.pos) // width
            if avail == 0:
                # 次の塊へ。先頭 1 バイトがこの先の文字幅
                self.i += 1
                self.pos = 0
                if self.i >= len(self.parts) or not self.parts[self.i]:
                    raise XlsFormatError("文字列の表が途中で終わっています")
                high = bool(self.parts[self.i][0] & 1)
                self.pos = 1
                continue
            n = min(cch, avail)
            raw = p[self.pos:self.pos + n * width]
            out.append(raw.decode("utf-16-le" if high else "latin-1"))
            self.pos += n * width
            cch -= n
        return "".join(out)


def _unicode_string(buf: bytes, off: int, len_bytes: int = 2) -> Tuple[str, int]:
    """XLUnicodeString（len_bytes=2）/ ShortXLUnicodeString（1）。戻り値は (文字列, 次の位置)。"""
    cch = buf[off] if len_bytes == 1 else struct.unpack_from("<H", buf, off)[0]
    off += len_bytes
    flags = buf[off]
    off += 1
    high = bool(flags & 1)
    n = cch * (2 if high else 1)
    s = buf[off:off + n].decode("utf-16-le" if high else "latin-1", errors="replace")
    return s, off + n


def _parse_sst(parts: List[bytes]) -> List[str]:
    seg = _Segments(parts)
    _total, unique = struct.unpack("<II", seg.take(8))
    out: List[str] = []
    for _ in range(unique):
        cch = struct.unpack("<H", seg.take(2))[0]
        flags = seg.take(1)[0]
        runs = struct.unpack("<H", seg.take(2))[0] if flags & 0x08 else 0
        ext = struct.unpack("<I", seg.take(4))[0] if flags & 0x04 else 0
        out.append(seg.chars(cch, bool(flags & 1)))
        if runs:
            seg.take(4 * runs)
        if ext:
            seg.take(ext)
    return out


def _rk(v: int) -> float:
    if v & 0x02:
        n = v >> 2
        if n & 0x20000000:
            n -= 0x40000000
        val: float = n
    else:
        val = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return val / 100 if v & 0x01 else val


_BUILTIN_DATE_FORMATS = set(range(14, 23)) | {45, 46, 47}
_DATE_CHARS = re.compile(r"[ymdhs]", re.I)


def _is_date_format(fmt: str) -> bool:
    f = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", fmt)   # 引用・エスケープ・[色] を除く
    return bool(_DATE_CHARS.search(f)) and "General" not in fmt


def _excel_date(v: float) -> str:
    base = datetime(1899, 12, 30)
    d = base + timedelta(days=v)
    d = d.replace(microsecond=0) if d.microsecond < 500000 else (d + timedelta(seconds=1)).replace(microsecond=0)
    return d.strftime("%Y-%m-%d") if abs(v - round(v)) < 1e-9 else d.strftime("%Y-%m-%d %H:%M:%S")


def _num_text(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return format(v, ".15g")


def read_xls(path: Path, sheet: str = "Page1") -> Tuple[str, List[List[str]]]:
    """(読んだシート名, 行のリスト) を返す。行は文字列のリストで、短い行は空文字で埋める。"""
    data = Path(path).read_bytes()
    wb = _ole_stream(data, ("Workbook", "Book"))
    # 1 周目: ブック全体（シートの場所、共有文字列、表示形式）
    records: List[Tuple[int, int, bytes]] = []
    pos = 0
    while pos + 4 <= len(wb):
        rid, ln = struct.unpack_from("<HH", wb, pos)
        records.append((pos, rid, wb[pos + 4:pos + 4 + ln]))
        pos += 4 + ln
    sheets: List[Tuple[str, int]] = []
    sst: List[str] = []
    formats: Dict[int, str] = {}
    xf_fmt: List[int] = []
    i = 0
    while i < len(records):
        off, rid, d = records[i]
        if rid == 0x0085:                                  # BOUNDSHEET
            spos = struct.unpack_from("<I", d, 0)[0]
            name, _ = _unicode_string(d, 6, 1)
            if d[5] == 0:                                  # ワークシート
                sheets.append((name, spos))
        elif rid == 0x00FC:                                # SST（後ろに CONTINUE が続くことがある）
            parts = [d]
            while i + 1 < len(records) and records[i + 1][1] == 0x003C:
                i += 1
                parts.append(records[i][2])
            sst = _parse_sst(parts)
        elif rid == 0x041E:                                # FORMAT
            ifmt = struct.unpack_from("<H", d, 0)[0]
            formats[ifmt], _ = _unicode_string(d, 2, 2)
        elif rid == 0x00E0:                                # XF
            xf_fmt.append(struct.unpack_from("<H", d, 2)[0])
        elif rid == 0x000A:                                # ブック全体の部分の終わり
            break
        i += 1
    if not sheets:
        raise XlsFormatError("ワークシートがありません")
    chosen = next((s for s in sheets if s[0] == sheet), None) or next(
        (s for s in sheets if s[0].lower() == sheet.lower()), None) or sheets[0]

    def is_date_xf(xf: int) -> bool:
        if xf >= len(xf_fmt):
            return False
        f = xf_fmt[xf]
        return f in _BUILTIN_DATE_FORMATS or (f in formats and _is_date_format(formats[f]))

    def num(xf: int, v: float) -> str:
        return _excel_date(v) if is_date_xf(xf) and 0 <= v < 2958466 else _num_text(v)

    # 2 周目: 選んだシートのセル
    cells: Dict[Tuple[int, int], str] = {}
    start = next(k for k, r in enumerate(records) if r[0] == chosen[1])
    pending_formula: Optional[Tuple[int, int]] = None
    for off, rid, d in records[start + 1:]:
        if rid == 0x000A:                                  # EOF
            break
        if rid == 0x00FD:                                  # LABELSST
            r, c, _xf, isst = struct.unpack_from("<HHHI", d, 0)
            cells[(r, c)] = sst[isst] if isst < len(sst) else ""
        elif rid == 0x0203:                                # NUMBER
            r, c, xf = struct.unpack_from("<HHH", d, 0)
            cells[(r, c)] = num(xf, struct.unpack_from("<d", d, 6)[0])
        elif rid == 0x027E:                                # RK
            r, c, xf, v = struct.unpack_from("<HHHI", d, 0)
            cells[(r, c)] = num(xf, _rk(v))
        elif rid == 0x00BD:                                # MULRK
            r, c0 = struct.unpack_from("<HH", d, 0)
            n = (len(d) - 6) // 6
            for k in range(n):
                xf, v = struct.unpack_from("<HI", d, 4 + 6 * k)
                cells[(r, c0 + k)] = num(xf, _rk(v))
        elif rid in (0x0204, 0x00D6):                      # LABEL / RSTRING
            r, c = struct.unpack_from("<HH", d, 0)
            cells[(r, c)], _ = _unicode_string(d, 6, 2)
        elif rid == 0x0006:                                # FORMULA（結果だけ使う）
            r, c, xf = struct.unpack_from("<HHH", d, 0)
            res = d[6:14]
            if res[6:8] == b"\xff\xff":
                kind = res[0]
                if kind == 0:
                    pending_formula = (r, c)               # 文字の結果は次の STRING に入っている
                elif kind == 1:
                    cells[(r, c)] = "TRUE" if res[2] else "FALSE"
                else:
                    cells[(r, c)] = ""
            else:
                cells[(r, c)] = num(xf, struct.unpack("<d", res)[0])
        elif rid == 0x0207 and pending_formula is not None:  # STRING
            cells[pending_formula], _ = _unicode_string(d, 0, 2)
            pending_formula = None
        elif rid == 0x0205:                                # BOOLERR
            r, c, _xf, val, err = struct.unpack_from("<HHHBB", d, 0)
            cells[(r, c)] = "" if err else ("TRUE" if val else "FALSE")
    if not cells:
        return chosen[0], []
    n_rows = max(r for r, _ in cells) + 1
    n_cols = max(c for _, c in cells) + 1
    rows = [[cells.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    return chosen[0], rows


def xls_to_tsv(src: Path, dst: Path, sheet: str = "Page1") -> Tuple[int, int, str]:
    """xls の 1 シートを、後段の TSV（UTF-8、1 行目が列名）にする。戻り値は (データ行数, 列数, シート名)。"""
    name, rows = read_xls(src, sheet)
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise XlsFormatError(f"シート「{name}」にデータがありません")
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write("\t".join(c.replace("\t", " ").replace("\r", " ").replace("\n", " ") for c in r) + "\n")
    return len(rows) - 1, len(rows[0]), name
