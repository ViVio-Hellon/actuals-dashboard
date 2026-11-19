"""設備仕様（梱包のデータベースの「設備仕様」表）を初期値に入れた条件表。"""
import unittest

from actuals.conditions import EQUIPMENT_SPEC, ConditionSet, default_conditions, empty_row, normalize_row, validate
from actuals.engine import classify, mat_match
from actuals import columns as C
from tests.test_engine import lot


class EquipmentSpec(unittest.TestCase):
    def test_values_from_table(self):
        cs = default_conditions()
        self.assertEqual(validate(cs), [])
        r = {x["name"]: x for x in cs.rows}
        self.assertEqual([r["SVC"][k] for k in ("t_min", "t_max", "w_min", "w_max", "l_min", "l_max", "wt_min", "wt_max")],
                         [0.6, 4, 400, 1250, 830, 2500, 0, 30])
        self.assertEqual([r["HVC"][k] for k in ("t_min", "t_max", "w_min", "w_max", "l_min", "l_max", "wt_max")],
                         [8, 200, 800, 1900, 1300, 3500, 2000])
        self.assertEqual([r["ﾄｯﾄ"][k] for k in ("t_min", "t_max", "w_min", "w_max", "l_min", "l_max", "wt_max")],
                         [0.1, 5, 840, 1650, 2440, 4100, 80])
        self.assertEqual([r["LVC"][k] for k in ("t_min", "t_max", "w_min", "w_max", "l_min", "l_max", "wt_max")],
                         [1, 20, 730, 1740, 1500, 5000, 265])
        self.assertTrue(all(r[n]["vc"] == "*" for n in ("SVC", "LVC", "HVC")))   # VC の設備
        self.assertIsNone(r["ﾄｯﾄ"]["vc"])
        self.assertEqual(set(EQUIPMENT_SPEC), {6, 11, 12, 13})

    def test_lvc_thin_special_materials(self):
        """LVC: 指定の製造材質（-99*,3N*,…,1070…）は板厚 1.5 以上、ほかは 1 以上。"""
        cs = default_conditions()
        def L(mat, t):
            return lot(**{C.VC: 1, C.O_T: t, C.O_W: 1000, C.O_L: 3000, C.M_T: t, C.M_W: 1000, C.M_L: 3000,
                          C.M_MAT: mat, C.O_MAT: mat})
        self.assertEqual(classify(L("5052", 1.2), cs).group, 12)            # ふつうの材質: 1.2 は LVC
        self.assertNotEqual(classify(L("1070", 1.2), cs).group, 12)         # 1070 は 1.5 未満なので外す
        self.assertNotEqual(classify(L("3N99", 1.2), cs).group, 12)         # 3N* の前方一致
        self.assertEqual(classify(L("1070", 1.5), cs).group, 12)            # 1.5 ちょうどは入る

    def test_mat_list_and_prefix(self):
        self.assertTrue(mat_match("-99*,3N*,1070", "-99A"))
        self.assertTrue(mat_match("-99*,3N*,1070", 1070))
        self.assertFalse(mat_match("-99*,3N*,1070", "10700"))
        self.assertTrue(mat_match("*", "x"))
        self.assertTrue(mat_match("3SR", "3SR"))                                        # 旧シートの 1 つの値

    def test_new_columns_blank_means_no_condition(self):
        r = normalize_row({"name": "x"})
        self.assertEqual((r["mmat"], r["wt_min"], r["t_lt"]), ("*", "*", "*"))
        self.assertIsNone(r["t_max"])                                                   # 旧シートの列は前のまま（空＝0）
        self.assertEqual(empty_row()["not_t_lt"], "*")
        old = ConditionSet.from_dict({"rows": [{"name": "a", "mat": "*"}]})            # 前の版の保存
        self.assertEqual(old.rows[0]["l_lt"], "*")


if __name__ == "__main__":
    unittest.main()
