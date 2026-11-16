"""設定とログインはツールのフォルダの config に 1 つ（全員で同じツールを開くので全 PC 共通・配れる）。"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from actuals import credentials as CR
from actuals import paths_check as P
from actuals.service import Service, UserError
from actuals.settings import Paths, Settings


class SharedSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.tool = self.tmp / "共有" / "ツール" / "config"           # 全員で開くツールのフォルダ
        self.old = os.environ.get("ACTUALS_TOOL_CONFIG")
        os.environ["ACTUALS_TOOL_CONFIG"] = str(self.tool)
        self.pc1, self.pc2 = Paths(self.tmp / "pc1"), Paths(self.tmp / "pc2")

    def tearDown(self):
        os.environ["ACTUALS_TOOL_CONFIG"] = self.old or ""
        if self.old is None:
            os.environ.pop("ACTUALS_TOOL_CONFIG")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_on_one_pc_is_seen_on_another(self):
        s1 = Settings(self.pc1)
        rev, _ = s1.save({"shared_dir": r"\\srv\share\actuals", "rne_path": r"\\srv\rne\梱包実績.RNE"}, s1.revision())
        saved = json.loads((self.tool / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["shared_dir"], r"\\srv\share\actuals")
        self.assertIn("symnavi_exe", saved)                                    # ファイルを見れば全部分かる
        s2 = Settings(self.pc2)
        self.assertEqual(s2.load()["rne_path"], r"\\srv\rne\梱包実績.RNE")     # 別の PC でも同じ
        self.assertFalse((self.pc1.config / "settings.json").exists())          # PC ごとには書かない
        self.assertEqual(Path(s2.resolved()["export_dir"]), self.pc2.base / "export")   # {DATA} は PC ごと

    def test_old_local_settings_are_not_used(self):
        self.pc1.config.mkdir(parents=True)
        (self.pc1.config / "settings.json").write_text(json.dumps({"shared_dir": "C:/old"}), encoding="utf-8")
        self.assertEqual(Settings(self.pc1).load()["shared_dir"], "")

    def test_login_registered_once_for_all_pcs(self):
        CR.save(self.pc1, "user1", "pw1")
        self.assertTrue((self.tool / "credentials.json").exists())
        st = CR.status(self.pc2)
        self.assertEqual((st["ready"], st["id"]), (True, "user1"))
        self.assertIn("全 PC 共通", st["source"])
        self.assertNotIn("pw1", json.dumps(st))

    def test_old_local_login_still_used_until_registered(self):
        self.pc1.config.mkdir(parents=True)
        (self.pc1.config / "credentials.json").write_text(json.dumps({"id": "old", "pass": "p"}), encoding="utf-8")
        self.assertEqual(CR.status(self.pc1)["id"], "old")
        CR.save(self.pc2, "new", "p2")
        self.assertEqual(CR.status(self.pc1)["id"], "new")                     # 共通のものが先

    def test_pc_without_symnavi_is_stopped(self):
        s = Settings(self.pc1)
        rne = self.tmp / "a.RNE"
        rne.write_text("x")
        s.save({"rne_path": str(rne), "symnavi_exe": str(self.tmp / "無い" / "SymNavi.exe")}, s.revision())
        CR.save(self.pc1, "u", "p")
        svc = Service(self.pc1)
        with self.assertRaises(UserError) as cm:
            svc.run_update()
        self.assertIn("この PC には SymNavi が入っていないため", str(cm.exception))
        self.assertFalse(svc.rne.running)
        r = P.check_all({"symnavi_exe": str(self.tmp / "無い" / "SymNavi.exe")}, self.pc1)["symnavi_exe"]
        self.assertEqual(r["state"], "warn")                                    # 設定画面では警告（集計は見られる）
        self.assertIn("この PC ではデータ更新は使えません", r["message"])


if __name__ == "__main__":
    unittest.main()
