"""条件表の出力先: 条件表を .sqlite3 で書く（保存するたび・ボタン）。"""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from actuals import paths_check as P
from actuals.service import Service
from actuals.settings import Paths, Settings


class ConditionsExport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / "config").mkdir(parents=True)
        self.out = self.tmp / "出力"
        self._settings({"shared_dir": str(self.tmp / "share"), "conditions_export_dir": str(self.out)})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self, d):
        (self.home / "config" / "settings.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def _read(self):
        con = sqlite3.connect(str(self.out / "actualsdashboard_条件表.sqlite3"))
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute('SELECT * FROM "条件表" ORDER BY "見る順番"')]
            info = dict(con.execute('SELECT "項目", "値" FROM "情報"').fetchall())
        finally:
            con.close()
        return rows, info

    def test_export_contents(self):
        svc = Service(Paths(self.home))
        r = svc.export_conditions()
        self.assertTrue(r["written"], r)
        rows, info = self._read()
        self.assertEqual(r["rows"], len(rows))
        self.assertEqual([x["番号"] for x in rows], list(range(1, 15)))          # 名前のある 14 グループ
        lvc = next(x for x in rows if x["グループ名"] == "LVC")
        self.assertEqual((lvc["VC優先"], lvc["板厚MIN"], lvc["板厚MAX"], lvc["単重MAX"]), (1, 1, 20, 265))
        self.assertIsNone(lvc["材質"])                                          # 条件なしは NULL
        self.assertEqual((lvc["除外を使う"], lvc["除外_板厚MAX未満"]), (1, 1.5))
        self.assertTrue(lvc["除外_製造材質"].startswith("-99*,3N*"))
        toto = next(x for x in rows if x["グループ名"] == "ﾄｯﾄ")
        self.assertEqual(toto["VC優先"], 0)
        self.assertIsNone(toto["除外_板厚MIN"])
        self.assertIn("決まり方", info)
        self.assertEqual(info["寸法の基準"], "ｵｰﾀﾞｰ")

    def test_written_on_every_save_in_check_order(self):
        svc = Service(Paths(self.home))
        d = svc.conditions()["conditions"]
        d["order"] = [12] + [n for n in range(1, 16) if n != 12]
        out = svc.save_conditions(d)
        self.assertTrue(out["exported"]["written"])
        rows, info = self._read()
        self.assertEqual((rows[0]["見る順番"], rows[0]["番号"], rows[0]["グループ名"]), (1, 12, "LVC"))
        self.assertEqual(info["版"], str(svc.master.conditions().rev))

    def test_not_set_means_not_written(self):
        self._settings({"shared_dir": str(self.tmp / "share")})
        svc = Service(Paths(self.home))
        r = svc.export_conditions()
        self.assertFalse(r["written"])
        self.assertIn("未設定", r["message"])
        d = svc.conditions()["conditions"]
        self.assertFalse(svc.save_conditions(d)["exported"]["written"])          # 保存は通る
        self.assertFalse(self.out.exists())

    def test_setting_and_check(self):
        s = Settings(Paths(self.home))
        self.assertEqual(s.load()["conditions_export_dir"], str(self.out))
        self.assertEqual(P.check_all({}, Paths(self.home))["conditions_export_dir"]["state"], "ok")
        self.assertEqual(P.check_all({"conditions_export_dir": str(self.out)}, Paths(self.home))["conditions_export_dir"]["state"], "ok")


if __name__ == "__main__":
    unittest.main()
