"""判定・計算の境界値と例外ケース（旧 VBA と同じ比べ方になっているか）。"""
import unittest
from datetime import datetime

from actuals import aggregate as A
from actuals import columns as C
from actuals.conditions import ConditionSet, default_conditions, empty_row, parse_cell, validate
from actuals.engine import GENBA, TAIYO, classify, compute_lot
from actuals.vba_compat import excel_value, format_fixed, variant_cmp, vba_round, vba_val


def lot(**kw):
    base = {C.LOT: "T1", C.INSP: "I", C.ORDER: 1, C.USE: "U", C.O_MAT: "52S", C.O_TMP: "H32", C.O_T: 5,
            C.O_W: 1000, C.O_L: 2000, C.M_MAT: "52S", C.M_TMP: "H32", C.M_T: 5, C.M_W: 1000, C.M_L: 2000,
            C.SHAPE: 10, C.VC: 0, C.KURA_DATE: "2022-06-01 00:00:00", C.WEIGHT: 1000, C.SG: 2.7, C.UNIT_W: 27,
            C.COURSE: "HOT", C.BOX_END: "2022-06-01 10:00:00", C.BOX_DATE: "2022-06-01 10:00:00"}
    base.update(kw)
    return compute_lot(base)


def one_rule(**kw):
    """1 行目だけ条件のある表（他は空）。"""
    r = empty_row()
    r.update({"name": "G1", "mat": "*", "t_min": "*", "t_max": "*", "w_min": "*", "w_max": "*", "l_min": "*",
              "l_gt": "*", "l_max": "*", "wt_max": "*"})
    r.update(kw)
    rows = [r] + [empty_row() for _ in range(14)]
    return ConditionSet(rows=rows, basis_main="ｵｰﾀﾞｰ", basis_not="ｵｰﾀﾞｰ")


class VbaCompat(unittest.TestCase):
    def test_val(self):
        self.assertEqual(vba_val("12.5mm"), 12.5)
        self.assertEqual(vba_val(" 1 2 "), 12.0)
        self.assertEqual(vba_val("H14"), 0.0)
        self.assertEqual(vba_val(None), 0.0)
        self.assertEqual(vba_val("1e3"), 1000.0)
        self.assertEqual(vba_val(datetime(2022, 6, 1, 15, 0)), 2022.0)

    def test_round_half_up_like_format(self):
        self.assertEqual(vba_round(1.005, 2), 1.01)       # Python の round では 1.0
        self.assertEqual(vba_round(2.5, 0), 3.0)          # 偶数丸めではない
        self.assertEqual(vba_round(0.125, 2), 0.13)
        self.assertEqual(format_fixed(100, 3), "100.000")

    def test_variant_compare(self):
        self.assertEqual(variant_cmp(None, 0), 0)          # 空は 0
        self.assertEqual(variant_cmp(None, ""), 0)         # 空は ""
        self.assertEqual(variant_cmp(5, "*"), -1)          # 数 < 文字
        self.assertEqual(variant_cmp("a", "A"), 1)         # 大文字小文字を区別

    def test_excel_value(self):
        self.assertEqual(excel_value("01"), 1)
        self.assertEqual(excel_value("5083"), 5083)
        self.assertEqual(excel_value("2,500"), 2500)
        self.assertEqual(excel_value("52S"), "52S")
        self.assertEqual(excel_value("2022/6/1 7:05"), datetime(2022, 6, 1, 7, 5))
        self.assertIsNone(excel_value(""))


class Calculations(unittest.TestCase):
    def test_unit_weight_and_count(self):
        x = lot(**{C.M_T: 3, C.M_W: 1250, C.M_L: 2500, C.SG: 2.68, C.WEIGHT: 4754.4})
        self.assertEqual(x.unit_w, 25.13)                  # 3×1250×2500×2.68÷10⁶ = 25.125 → 25.13
        self.assertEqual(x.count, 189)

    def test_zero_specific_gravity(self):
        x = lot(**{C.SG: None})
        self.assertEqual(x.unit_w, 0.0)
        self.assertIsNone(x.count)                         # 単重 0 なら枚数は空

    def test_shift_boundaries(self):
        cases = {"06:59:59": 3, "07:00:00": 1, "14:59:59": 1, "15:00:00": 2, "22:59:59": 2, "23:00:00": 3,
                 "00:00:00": 3}
        for t, expect in cases.items():
            with self.subTest(t=t):
                self.assertEqual(lot(**{C.BOX_DATE: f"2022-06-01 {t}"}).choku, expect)

    def test_calendar_dates(self):
        x = lot(**{C.BOX_DATE: "2022-07-01 01:00:00", C.KURA_DATE: "2022-06-30 00:00:00"})
        self.assertEqual(x.ymd(GENBA), (2022, 7, 1))
        self.assertEqual(x.ymd(TAIYO), (2022, 6, 30))
        self.assertIsNone(x.shift(TAIYO))                  # 太陽暦では直を出さない

    def test_plate_detection(self):
        self.assertTrue(lot(**{C.SHAPE: 10}).is_plate)
        self.assertFalse(lot(**{C.SHAPE: 30, C.M_L: 0}).is_plate)
        self.assertTrue(lot(**{C.SHAPE: 30, C.M_L: 100}).is_plate)   # コイルでも板丈 > 0 なら板


class Classification(unittest.TestCase):
    def test_min_max_inclusive(self):
        cs = one_rule(t_min=5, t_max=5)
        self.assertEqual(classify(lot(**{C.O_T: 5}), cs).group, 1)
        self.assertIsNone(classify(lot(**{C.O_T: 5.0001}), cs).group)

    def test_length_strict_min(self):
        cs = one_rule(l_gt=2000)
        self.assertIsNone(classify(lot(**{C.O_L: 2000}), cs).group)  # MIN＜ は含まない
        self.assertEqual(classify(lot(**{C.O_L: 2000.5}), cs).group, 1)

    def test_unit_weight_max_uses_rounded_value(self):
        # 3×1250×2500×2.68÷10⁶ = 25.125 → 25.13。≦25.13 は当たり、≦25.12 は外れ
        kw = {C.M_T: 3, C.M_W: 1250, C.M_L: 2500, C.SG: 2.68}
        self.assertEqual(classify(lot(**kw), one_rule(wt_max=25.13)).group, 1)
        self.assertIsNone(classify(lot(**kw), one_rule(wt_max=25.12)).group)

    def test_blank_threshold_is_zero(self):
        self.assertIsNone(classify(lot(), one_rule(t_max=None)).group)   # 空欄の ≦MAX は 0 → 外れ
        self.assertEqual(classify(lot(), one_rule(t_min=None)).group, 1)  # 空欄の MIN≦ は 0 → 当たり

    def test_basis_switch(self):
        cs = one_rule(t_min=6)
        x = lot(**{C.O_T: 5, C.M_T: 6})
        self.assertIsNone(classify(x, cs).group)
        cs.basis_main = "製造"
        self.assertEqual(classify(x, cs).group, 1)

    def test_vc_row_overrides_first_match(self):
        cs = default_conditions()
        x = lot(**{C.O_T: 3, C.O_W: 1000, C.O_L: 2000, C.M_T: 3, C.M_W: 1000, C.M_L: 2000, C.VC: 1})
        self.assertEqual(classify(x, cs).group, 11)        # 丸徳ﾗｲﾝ に先に当たるが VC 行 SVC で確定
        x0 = lot(**{C.O_T: 3, C.O_W: 1000, C.O_L: 2000, C.M_T: 3, C.M_W: 1000, C.M_L: 2000, C.VC: 0})
        self.assertEqual(classify(x0, cs).group, 1)
        x_str = lot(**{C.O_T: 3, C.O_W: 1000, C.O_L: 2000, C.M_T: 3, C.M_W: 1000, C.M_L: 2000, C.VC: "01"})
        self.assertEqual(classify(x_str, cs).group, 11)

    def test_simultaneous_vc_blocks_vc_override(self):
        cs = default_conditions()
        kw = {C.O_T: 3, C.O_W: 1000, C.O_L: 2000, C.M_T: 3, C.M_W: 1000, C.M_L: 2000, C.VC: 1,
              C.COURSE: "HOT LS4 VC KEN", C.FACE_VC: "B535", C.BACK_VC: ""}
        j = classify(lot(**kw), cs)
        self.assertEqual((j.group, j.simul), (1, "L"))
        j2 = classify(lot(**{**kw, C.COURSE: "FS4 VC", C.FACE_VC: "AL-200"}), cs)
        self.assertEqual((j2.group, j2.simul), (1, "F"))
        j3 = classify(lot(**{**kw, C.BACK_VC: "X"}), cs)    # 裏VC があれば同時VC ではない
        self.assertEqual((j3.group, j3.simul), (11, None))

    def test_simultaneous_vc_absent_columns(self):
        """表VC・裏VC の列が無い今の RNE では、旧 VBA と同じく同時VC は成立しない。"""
        cs = default_conditions()
        j = classify(lot(**{C.O_T: 3, C.M_T: 3, C.VC: 1, C.COURSE: "LS4 VC"}), cs)
        self.assertIsNone(j.simul)

    def test_not_condition_rules(self):
        cs = default_conditions()   # 1 行目 丸徳ﾗｲﾝ: NOT 板厚 3〜4・幅 1250・丈 2500
        kw = {C.O_T: 3, C.O_W: 1250, C.O_L: 2500, C.M_T: 3, C.M_W: 1250, C.M_L: 2500, C.SG: 2.68}
        self.assertEqual(classify(lot(**kw), cs).group, 6)                    # 修正版: 次の行（ﾄｯﾄ）へ
        self.assertIsNone(classify(lot(**kw), cs.vba_compatible()).group)     # 旧 VBA: 判定をやめて未分類

    def test_not_material_compares_material(self):
        cs = default_conditions()   # 9 行目 L-1: NOT 材質 1070・丈 0 < 丈 ≦ 73
        kw = {C.O_MAT: "1070", C.O_TMP: "H14", C.O_T: 10, C.O_W: 1000, C.O_L: 50,
              C.M_MAT: "1070", C.M_T: 10, C.M_W: 1000, C.M_L: 50}
        self.assertNotEqual(classify(lot(**kw), cs).group, 9)                 # 修正版: 材質 1070 で外れる
        self.assertEqual(classify(lot(**kw), cs.vba_compatible()).group, 9)   # 旧 VBA: Val(調質)=0 なので当たらない

    def test_not_plate_is_skipped(self):
        self.assertIsNone(classify(lot(**{C.SHAPE: 30, C.M_L: 0}), one_rule()).group)

    def test_trace_records_each_row(self):
        j = classify(lot(), default_conditions(), want_trace=True)
        self.assertTrue(j.trace)
        self.assertIn("result", j.trace[0])


class Aggregation(unittest.TestCase):
    def test_override_and_unnamed_group(self):
        cs = default_conditions()
        rows = A.make_rows([dict(lot().raw, **{C.LOT: "A"}), dict(lot().raw, **{C.LOT: "B"})], cs, {"B": 15})
        m = A.month_matrix(rows, cs.names(), GENBA, 2022, 6)
        # B は名前の無いグループ 15 に訂正 → 表示も合計もしない（旧 月集計と同じ）
        self.assertEqual(m["weight_total"], 1000)
        self.assertIsNone(m["groups"][14]["weight"])

    def test_override_zero_excludes(self):
        cs = default_conditions()
        rows = A.make_rows([lot().raw], cs, {"T1": 0})
        self.assertEqual(A.month_matrix(rows, cs.names(), GENBA, 2022, 6)["weight_total"], 0)
        self.assertEqual(A.detail_rows(rows, GENBA, 2022, 6, A.ALL), [])

    def test_detail_filters_year(self):
        cs = default_conditions()
        rows = A.make_rows([lot().raw, dict(lot().raw, **{C.LOT: "T2", C.BOX_DATE: "2021-06-01 10:00:00"})], cs)
        self.assertEqual([r.lot.lot for r in A.detail_rows(rows, GENBA, 2022, 6, A.ALL)], ["T1"])

    def test_fiscal_year(self):
        cs = default_conditions()
        rows = A.make_rows([dict(lot().raw, **{C.BOX_DATE: "2023-03-31 10:00:00"})], cs)
        y = A.year_matrix(rows, cs.names(), GENBA, 2022)
        self.assertEqual(len(y["days"]), 365)
        self.assertEqual(y["months"][-1]["weight_total"], 1000)


class ConditionInput(unittest.TestCase):
    def test_parse_cell(self):
        self.assertEqual(parse_cell("num", "１２.５"), 12.5)
        self.assertEqual(parse_cell("num", "＊"), "*")
        self.assertIsNone(parse_cell("num", " "))
        self.assertEqual(parse_cell("mat", "1070"), 1070)   # 旧シートと同じく数に見えれば数
        self.assertEqual(parse_cell("mat", "52S"), "52S")

    def test_validate(self):
        cs = default_conditions()
        self.assertEqual(validate(cs), [])
        cs.rows[0]["t_min"] = "abc"
        cs.rows[1]["vc"] = "x"
        self.assertEqual(len(validate(cs)), 2)


if __name__ == "__main__":
    unittest.main()
