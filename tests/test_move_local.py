"""共有フォルダを決める前に、この PC に貯めた分を共有フォルダへ移す。共有にある物は上書きしない。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from actuals.service import Service, UserError
from actuals.settings import Paths
from tests.helpers import golden


def _rows(g, a, b):
    return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in g["rows"][a:b]]


class MoveLocal(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / "config").mkdir(parents=True)
        self.share = self.tmp / "共有"
        self.g = golden()
        self._settings({})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self, d):
        (self.home / "config" / "settings.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def _local(self):
        """② が空欄のまま、この PC に 30 ロット・手動訂正 1 件・条件表の変更を貯める。"""
        svc = Service(Paths(self.home))
        svc.import_rows(self.g["header"], _rows(self.g, 0, 30), "paste", "試験")
        self.lot_a = _rows(self.g, 0, 1)[0]["ﾛｯﾄ番号"]
        svc.set_override(self.lot_a, 3, "この PC で直した", None)
        cs = svc.master.conditions()
        cs.rows[1]["name"] = "この PC で付けた名前"
        svc.master.save_conditions(cs, cs.rev)
        self.assertIsNone(svc.local_leftover())          # ② が空欄の間は、この PC が置き場所そのもの
        return svc

    def test_move_merges_without_overwriting(self):
        self._local()
        # 共有には別の PC が先に 20〜40 行目を入れ、10 行目の手動訂正も付けている
        self._settings({"shared_dir": str(self.share)})
        other = Service(Paths(self.home))
        other.import_rows(self.g["header"], _rows(self.g, 20, 40), "paste", "別の PC")
        lot_b = _rows(self.g, 25, 26)[0]["ﾛｯﾄ番号"]
        other.set_override(lot_b, 6, "共有で直した", None)
        before = other.history.lot(lot_b)

        svc = Service(Paths(self.home))
        lf = svc.local_leftover()
        self.assertEqual((lf["lots"], lf["new_lots"], lf["overrides"], lf["conditions"]), (30, 20, 1, True))
        r = svc.move_local_to_shared()
        self.assertEqual((r["lots"]["added"], r["lots"]["kept"]), (20, 10))
        self.assertEqual((r["overrides_added"], r["overrides_kept"]), (1, 0))
        self.assertEqual(r["conditions"], "copied")       # 共有はまだ初期値だった
        self.assertEqual(svc.history.summary()["lots"], 40)
        self.assertEqual(svc.history.lot(lot_b), before)  # 共有にあったロットは共有のまま
        ov = svc.master.overrides()
        self.assertEqual((ov[self.lot_a]["grp"], ov[self.lot_a]["reason"]), (3, "この PC で直した"))
        self.assertEqual(ov[lot_b]["grp"], 6)
        self.assertEqual(svc.master.conditions().rows[1]["name"], "この PC で付けた名前")
        self.assertEqual(svc.history.summary()["last"]["kind"], "move")
        # この PC の物は名前を変えて残す。もう一度は出ない・二重に入らない
        names = sorted(p.name for p in (self.home / "shared").iterdir() if p.suffix == ".sqlite3")
        self.assertTrue(any("_移した_" in n for n in names), names)
        self.assertNotIn("actuals_history.sqlite3", names)
        self.assertIsNone(svc.local_leftover())
        with self.assertRaises(UserError):
            svc.move_local_to_shared()

    def test_shared_conditions_already_saved_are_kept(self):
        self._local()
        self._settings({"shared_dir": str(self.share)})
        other = Service(Paths(self.home))
        cs = other.master.conditions()
        cs.rows[2]["name"] = "共有で付けた名前"
        other.master.save_conditions(cs, cs.rev)
        svc = Service(Paths(self.home))
        r = svc.move_local_to_shared()
        self.assertEqual(r["conditions"], "kept")
        got = svc.master.conditions()
        self.assertEqual((got.rows[2]["name"], got.rows[1]["name"] == "この PC で付けた名前"), ("共有で付けた名前", False))

    def test_switch_does_not_show_old_place(self):
        """② を変えた直後、共有にまだ何も無い時に、この PC の写しを共有の物として読まない。"""
        svc = self._local()
        self.assertEqual(svc.history.summary()["lots"], 30)
        self._settings({"shared_dir": str(self.share)})
        self.assertEqual(svc.history_summary()["lots"], 0)
        self.assertEqual(svc.local_leftover()["new_lots"], 30)

    def test_nothing_to_move(self):
        self._settings({"shared_dir": str(self.share)})
        svc = Service(Paths(self.home))
        self.assertIsNone(svc.local_leftover())


if __name__ == "__main__":
    unittest.main()
