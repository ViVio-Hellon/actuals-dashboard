"""条件表: 見る順番（番号とは別）・件数・ロットで試す。順番が既定なら旧ツールと同じ結果。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from actuals.conditions import ConditionSet, vba_sheet_conditions as default_conditions
from actuals.engine import classify, compute_lot
from actuals.service import Service, UserError
from actuals.settings import Paths
from tests.helpers import golden


def _rows(g):
    return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in g["rows"]]


class Order(unittest.TestCase):
    def test_default_order_and_roundtrip(self):
        cs = default_conditions()
        self.assertEqual(cs.order, list(range(1, 16)))
        d = cs.to_dict()
        d["order"] = [3, 1, 2]
        self.assertEqual(ConditionSet.from_dict(d).order[:4], [3, 1, 2, 4])           # 抜けた番号は後ろに足す
        d["order"] = ["x", 99, 2, 2]
        self.assertEqual(ConditionSet.from_dict(d).order[:3], [2, 1, 3])               # 壊れた値は捨てる
        self.assertEqual(ConditionSet.from_dict({"rows": []}).order, list(range(1, 16)))  # 前の版の保存（order 無し）

    def test_order_changes_priority_but_not_numbers(self):
        g = golden()
        cs = default_conditions()
        lots = [compute_lot(r) for r in _rows(g)]
        base = [classify(x, cs).group for x in lots]
        moved = ConditionSet.from_dict({**cs.to_dict(), "order": [9] + [n for n in range(1, 16) if n != 9]})
        after = [classify(x, moved).group for x in lots]
        self.assertTrue(any(a == 9 and b == 1 for a, b in zip(after, base)))   # 丸徳(1) だったロットが L-1(9) へ
        self.assertTrue(all(a in (None, *range(1, 16)) for a in after))       # 番号はそのまま
        t = classify(lots[0], moved, want_trace=True).trace
        self.assertEqual((t[0]["rank"], t[0]["row"]), (1, 9))                  # 経過は順番と番号の両方


class Screen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        home = self.tmp / "home"
        (home / "config").mkdir(parents=True)
        (home / "config" / "settings.json").write_text(json.dumps({"shared_dir": str(self.tmp / "share")}))
        self.svc = Service(Paths(home))
        self.g = golden()
        self.svc.import_rows(self.g["header"], _rows(self.g), "paste", "t")
        old = self.svc.master.conditions()                                    # 受領ブックの設定シートの条件で試す
        d = ConditionSet.from_dict({**old.to_dict(), "rows": self.g["conditions"]["rows"]})
        self.svc.master.save_conditions(d, old.rev)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_counts_per_group(self):
        v = self.svc.conditions(2022, 6, "genba")
        c = v["counts"]
        self.assertEqual(c["9"]["count"], 211)                                 # L-1（受領ブックの件数と同じ）
        self.assertEqual(sum(x["count"] for x in c.values()), 973)            # 板形状の全件
        self.assertEqual(v["conditions"]["order"], list(range(1, 16)))

    def test_preview_returns_counts_after(self):
        d = self.svc.conditions()["conditions"]
        d["order"] = [9] + [n for n in range(1, 16) if n != 9]
        r = self.svc.preview_conditions(d, 2022, 6, "genba")
        self.assertGreater(r["counts"]["9"]["count"], 211)
        self.assertTrue(r["changes"])

    def test_try_lot_with_draft(self):
        lot = _rows(self.g)[0]["ﾛｯﾄ番号"]
        d = self.svc.conditions()["conditions"]
        r = self.svc.try_lot(d, lot, "genba")
        self.assertEqual((r["group"], r["now"]), (r["now"], r["group"]))
        self.assertTrue(r["trace"])
        self.assertIn("板厚", r["values"])
        d["order"] = [9] + [n for n in range(1, 16) if n != 9]
        r2 = self.svc.try_lot(d, lot, "genba")
        self.assertEqual(r2["trace"][0]["row"], 9)
        with self.assertRaises(UserError):
            self.svc.try_lot(d, "NOPE", "genba")

    def test_order_is_saved(self):
        d = self.svc.conditions()["conditions"]
        d["order"] = [14] + [n for n in range(1, 16) if n != 14]
        self.svc.save_conditions(d)
        self.assertEqual(self.svc.master.conditions().order[0], 14)


if __name__ == "__main__":
    unittest.main()
