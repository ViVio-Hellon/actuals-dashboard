"""受領ブック（2022/06）の保存値と、Python の判定・集計を全件照合する。

旧 VBA と同じ判定方式（ConditionSet.vba_compatible）で 1 件もずれないこと。
修正版の判定方式で結果が変わるロットも、ここで一覧として固定する（意図しない変化を検出するため）。
"""
import json
import unittest
from pathlib import Path

from actuals import aggregate as A
from actuals.conditions import ConditionSet, default_conditions
from actuals.engine import GENBA, TAIYO

FIX = Path(__file__).parent / "fixtures" / "golden_202206.json"


def load():
    return json.loads(FIX.read_text(encoding="utf-8"))


class GoldenVbaCompatible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = load()
        cls.cs = ConditionSet.from_dict(cls.g["conditions"]).vba_compatible()
        cls.rows = A.make_rows(cls.g["rows"], cls.cs, cls.g["overrides"])
        cls.names = cls.cs.names()
        cls.exp = cls.g["expected"]

    def test_conditions_match_default(self):
        """初期値として持っている条件表が、受領ブックの設定シートと同じ。"""
        self.assertEqual(default_conditions().rows, ConditionSet.from_dict(self.g["conditions"]).rows)

    def test_per_lot(self):
        """修正シート: 自動グループ・計算単重・枚数・直・年月日（987 行）。"""
        fix = self.exp["fix"]
        self.assertEqual(len(self.rows), len(fix))
        for i, r in enumerate(self.rows):
            e = fix[str(i + 2)]
            with self.subTest(row=i + 2, lot=r.lot.lot):
                self.assertEqual(r.lot.lot, e["lot"])
                self.assertEqual(r.auto, e["auto"])
                self.assertAlmostEqual(r.lot.unit_w, e["unit_w"], places=9)
                self.assertEqual(r.lot.count, e["count"])
                self.assertEqual(r.lot.choku, e["choku"])
                self.assertEqual(r.lot.ymd(GENBA), (e["year"], e["month"], e["day"]))

    def test_month_matrix(self):
        m = self.exp["month"]
        y, mo = map(int, m["label"].split("/"))
        got = A.month_matrix(self.rows, self.names, GENBA, y, mo)
        self.assertEqual([g["name"] if g["named"] else None for g in got["groups"]], m["names"])
        n = 0
        for gi, gr in enumerate(got["groups"]):
            if not gr["named"]:
                self.assertTrue(all(v is None for v in m["weight"][gi]))
                continue
            for d in range(31):
                self.assertAlmostEqual(gr["weight"][d], m["weight"][gi][d], places=6)
                self.assertEqual(gr["count"][d], m["count"][gi][d])
                n += 1
        for d in range(31):
            self.assertAlmostEqual(got["day_weight"][d], m["day_weight"][d], places=6)
            self.assertEqual(got["day_count"][d], m["day_count"][d])
        self.assertEqual(n, 14 * 31)

    def test_graph(self):
        gr = self.exp["graph"]
        sel = gr["selected_row"] - 2
        y, mo = map(int, self.exp["month"]["label"].split("/"))
        det = A.detail_rows(self.rows, GENBA, y, mo, sel)
        got = [A.detail_record(r, GENBA) for r in det]
        self.assertEqual(len(got), len(gr["detail"]))
        for a, e in zip(got, gr["detail"]):
            # 表示は文字（Format の桁）。旧シートでは数に戻っている
            norm = [float(x) if isinstance(x, str) and x.replace(".", "", 1).isdigit() else x for x in a]
            e2 = [x if x is not None else "" for x in e]
            self.assertEqual([str(x) for x in norm[:5]], [str(x) for x in e2[:5]])
            for k in range(10, 14):
                self.assertAlmostEqual(float(norm[k]), float(e2[k]), places=6)
            self.assertEqual(norm[23], e2[23])
            self.assertEqual(norm[24], e2[24])
        sh = A.shift_daily(det, GENBA)
        for d in range(31):
            exp = gr["shift"][d]
            self.assertEqual([sh[1]["weight"][d], sh[1]["count"][d], sh[2]["weight"][d], sh[2]["count"][d],
                              sh[3]["weight"][d], sh[3]["count"][d]], exp)
        m = A.month_matrix(self.rows, self.names, GENBA, y, mo)
        tw, tc, _ = A.selected_total(m, self.names, sel)
        for d in range(31):
            self.assertAlmostEqual(tw[d], gr["total"][d][0], places=6)
            self.assertEqual(tc[d], gr["total"][d][1])
        hits = A.hit_counts(self.rows, GENBA, y, mo)
        self.assertEqual(hits[sel], gr["hits"][sel - 1])
        # 旧シートの ALL 行は「表示中の明細の件数」（= 選んだグループの件数）。Web 版は全グループの件数を
        # 常に並べるので、ALL は全グループの合計になる
        self.assertEqual(gr["hits"][15], gr["hits"][sel - 1])
        self.assertEqual(hits[A.ALL], sum(hits[g] for g in A.GROUPS))
        det_all = A.detail_rows(self.rows, GENBA, y, mo, A.ALL)
        self.assertEqual(hits[A.ALL], len(det_all))

    def test_text_input_same_result(self):
        """値がすべて文字で来ても（Excel から貼付け・混在列の SQLite）同じ判定になる。"""
        as_text = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in self.g["rows"]]
        rows2 = A.make_rows(as_text, self.cs, self.g["overrides"])
        self.assertEqual([r.auto for r in rows2], [r.auto for r in self.rows])
        self.assertEqual([r.lot.count for r in rows2], [r.lot.count for r in self.rows])


class GoldenFixedRules(unittest.TestCase):
    """修正版（NOT 材質は材質と比べる、NOT に当たった行だけ外す）で変わるロット。"""

    def test_changes_are_only_former_unclassified(self):
        g = load()
        cs_fixed = ConditionSet.from_dict(g["conditions"])
        self.assertEqual((cs_fixed.rule_not_material, cs_fixed.rule_not_hit), ("material", "skip"))
        old = A.make_rows(g["rows"], cs_fixed.vba_compatible())
        new = A.make_rows(g["rows"], cs_fixed)
        changed = {o.lot.lot: (o.auto, n.auto) for o, n in zip(old, new) if o.auto != n.auto}
        # 旧 VBA で未分類だった 17 件だけが変わる（丸徳ﾗｲﾝ の NOT 条件で判定が止まっていたもの）
        self.assertEqual(len(changed), 17)
        self.assertTrue(all(a is None for a, b in changed.values()))
        # VC=0 は ﾄｯﾄ（6）、VC=1 は単重により SVC（11）か LVC（12）。2026-10 時点で確認した結果
        expect = {"N4041P0": 6, "N414N52": 6, "N424J52": 6, "N454M52": 11, "N5041D0": 11, "N254R52": 11,
                  "N314N52": 6, "N4276Y0": 6, "N4412G0": 6, "N424P51": 6, "N583R52": 6, "N185R51": 12,
                  "N4412H0": 6, "N5213Q1": 6, "N5120Q0": 12, "N5270M0": 11, "N6041B0": 6}
        self.assertEqual({k: b for k, (a, b) in changed.items()}, expect)


if __name__ == "__main__":
    unittest.main()
