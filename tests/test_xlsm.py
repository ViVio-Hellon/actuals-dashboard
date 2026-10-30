"""旧ブック（xlsx 形式）を標準ライブラリだけで読む。最小のブックを作って確かめる。"""
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from actuals import xlsm

CT = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
WB = ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
      '<sheet name="DATA" sheetId="1" r:id="rId1"/><sheet name="修正" sheetId="2" r:id="rId2"/>'
      '<sheet name="設定" sheetId="3" r:id="rId3"/></sheets></workbook>')
RELS = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Target="worksheets/sheet3.xml"/></Relationships>')
STYLES = ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs count="2">'
          '<xf numFmtId="0"/><xf numFmtId="22"/></cellXfs></styleSheet>')


def sheet(cells):
    rows = {}
    for ref, v in cells.items():
        r = int("".join(ch for ch in ref if ch.isdigit()))
        rows.setdefault(r, []).append((ref, v))
    out = []
    for r in sorted(rows):
        cs = []
        for ref, v in rows[r]:
            if isinstance(v, tuple):   # 日付（serial, style 1）
                cs.append(f'<c r="{ref}" s="1"><v>{v[0]}</v></c>')
            elif isinstance(v, str):
                cs.append(f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>')
            else:
                cs.append(f'<c r="{ref}"><v>{v}</v></c>')
        out.append(f'<row r="{r}">{"".join(cs)}</row>')
    return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(out) + "</sheetData></worksheet>")


class XlsmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "old.xlsm"
        data = {"A1": "ﾛｯﾄ番号", "B1": "VC", "C1": "BOX実績_作業終了_日付",
                "A2": "L001", "B2": 1, "C2": (44713.65, ), "A3": "L002", "B3": 0, "C3": (44714.0,)}
        fix = {"A1": "訂正用", "K1": "ﾛｯﾄ番号", "A3": 7, "K2": "L001", "K3": "L002"}
        cond = {"E3": "ｵｰﾀﾞｰ", "O3": "製造", "C5": "丸徳ﾗｲﾝ", "E5": "*", "F5": 0.1, "N5": "*", "C6": "L-1"}
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("[Content_Types].xml", CT)
            z.writestr("xl/workbook.xml", WB)
            z.writestr("xl/_rels/workbook.xml.rels", RELS)
            z.writestr("xl/styles.xml", STYLES)
            z.writestr("xl/worksheets/sheet1.xml", sheet(data))
            z.writestr("xl/worksheets/sheet2.xml", sheet(fix))
            z.writestr("xl/worksheets/sheet3.xml", sheet(cond))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read(self):
        with xlsm.Workbook(self.path) as wb:
            header, rows = xlsm.read_data_rows(wb)
            self.assertEqual(header, ["ﾛｯﾄ番号", "VC", "BOX実績_作業終了_日付"])
            self.assertEqual(rows[0]["BOX実績_作業終了_日付"], datetime(2022, 6, 1, 15, 36))
            self.assertEqual(rows[1]["VC"], 0)
            self.assertEqual(xlsm.read_overrides(wb), {"L002": 7})   # 行の位置ではなくロット番号に結び付ける
            cs = xlsm.read_conditions(wb)
            self.assertEqual((cs.basis_main, cs.basis_not), ("ｵｰﾀﾞｰ", "製造"))
            self.assertEqual(cs.rows[0]["name"], "丸徳ﾗｲﾝ")
            self.assertEqual(cs.rows[0]["t_min"], 0.1)
            self.assertEqual(cs.rows[1]["name"], "L-1")


if __name__ == "__main__":
    unittest.main()
