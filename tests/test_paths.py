"""パスの設定（選ぶ…・確かめる・RNEConverter の KURAIRE）と stop.bat（停止）。"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from actuals import paths_check as P
from actuals.settings import Paths
from actuals.web import server

ROOT = Path(__file__).resolve().parents[1]


class PathsCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = Paths(self.tmp / "home")
        self._env = os.environ.get("RNE_CONVERTER_HOME")
        os.environ["RNE_CONVERTER_HOME"] = str(self.tmp / "rnehome")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._env is None:
            os.environ.pop("RNE_CONVERTER_HOME", None)
        else:
            os.environ["RNE_CONVERTER_HOME"] = self._env

    def test_list_dir(self):
        (self.tmp / "a").mkdir()
        (self.tmp / "b.sqlite3").write_text("x")
        (self.tmp / "c.txt").write_text("x")
        r = P.list_dir(str(self.tmp), (".sqlite3",))
        self.assertEqual((r["dirs"][:1], r["files"]), (["a"], ["b.sqlite3"]))
        self.assertEqual(P.list_dir(str(self.tmp / "b.sqlite3"))["path"], str(self.tmp))   # ファイルならその親
        with self.assertRaises(P.PathInputError):
            P.list_dir(str(self.tmp / "nothing"))

    def test_shared_and_folders(self):
        self.assertEqual(P.check_shared("", self.paths)["state"], "warn")                # 未設定 = この PC だけ
        r = P.check_shared(str(self.tmp / "share"), self.paths)
        self.assertEqual(r["state"], "ok")                                               # 無ければ作る
        self.assertTrue((self.tmp / "share").is_dir())
        self.assertEqual(P.check_all({"export_dir": "{DATA}/export"}, self.paths)["export_dir"]["state"], "ok")

    def test_source(self):
        src = self.tmp / "KURAIRE.sqlite3"
        self.assertEqual(P.check_source(str(src), self.paths)["state"], "warn")         # フォルダはあるがまだ無い
        src.write_text("not sqlite")
        self.assertEqual(P.check_source(str(src), self.paths)["state"], "missing")
        src.unlink()
        sqlite3.connect(str(src)).execute("CREATE TABLE t (a)").connection.close()
        self.assertEqual(P.check_source(str(src), self.paths)["state"], "ok")

    def _rneconverter(self, enabled=True, with_rne=True, save_dir=None):
        root = self.tmp / "RNEConverter"
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "rne").mkdir(exist_ok=True)
        (root / "start_app.py").write_text("")
        (root / "config" / "settings.json").write_text(json.dumps(
            {"extract": {"rne_dir": "{APP_ROOT}/rne"}, "output": {"save_dir": str(save_dir or self.tmp / "out")}}),
            encoding="utf-8")
        (root / "config" / "rne_list.json").write_text(json.dumps(
            [{"enabled": enabled, "rne": "梱包実績(当月_倉入れ日付順).RNE", "output": "KURAIRE"}], ensure_ascii=False),
            encoding="utf-8")
        if with_rne:
            (root / "rne" / "梱包実績(当月_倉入れ日付順).RNE").write_text("x", encoding="utf-8")
        return root

    def test_rneconverter_ready(self):
        root = self._rneconverter()
        r = P.rneconverter_status(str(root), "KURAIRE", str(self.tmp / "out" / "KURAIRE.sqlite3"), self.paths)
        self.assertEqual(r["state"], "ok", r)

    def test_rneconverter_problems(self):
        root = self._rneconverter(enabled=False, with_rne=False)
        r = P.rneconverter_status(str(root), "KURAIRE", str(self.tmp / "elsewhere.sqlite3"), self.paths)
        self.assertEqual(r["state"], "missing")
        states = {x["label"]: x["state"] for x in r["rows"]}
        self.assertEqual(states, {"RNE 一覧の行": "warn", "RNE ファイル": "missing", "RNEConverter の出力先": "warn"})
        sug = [x for x in r["rows"] if x.get("suggest_source")][0]["suggest_source"]
        self.assertEqual(Path(sug), self.tmp / "out" / "KURAIRE.sqlite3")             # 取り込み元の候補

    def test_rneconverter_user_list_without_kuraire(self):
        """RNEConverter の画面で RNE 一覧を保存済み（データの場所にある）なら、そちらを読む。"""
        root = self._rneconverter()
        user = self.tmp / "rnehome" / "config"
        user.mkdir(parents=True)
        (user / "rne_list.json").write_text(json.dumps([{"rne": "A.RNE", "output": "SIKALOT"}]), encoding="utf-8")
        r = P.rneconverter_status(str(root), "KURAIRE", "", self.paths)
        self.assertEqual(r["state"], "missing")
        self.assertIn("KURAIRE がありません", r["message"])

    def test_rneconverter_not_set(self):
        self.assertEqual(P.rneconverter_status("", "KURAIRE", "", self.paths)["state"], "warn")
        self.assertEqual(P.rneconverter_status(str(self.tmp), "KURAIRE", "", self.paths)["state"], "missing")


class Stop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.paths = Paths(self.tmp / "home")
        self.paths.runtime.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_not_running(self):
        self.assertEqual(server.stop_running(self.paths), 0)

    def test_stale_files_are_cleaned(self):
        (self.paths.runtime / "web.lock").write_text(json.dumps({"pid": 999999}))
        (self.paths.runtime / "server.json").write_text(json.dumps({"url": "http://127.0.0.1:1/", "pid": 999999}))
        self.assertEqual(server.stop_running(self.paths), 0)
        self.assertFalse((self.paths.runtime / "web.lock").exists())

    def test_graceful_stop(self):
        env = dict(os.environ, ACTUALS_DASHBOARD_HOME=str(self.paths.base))
        proc = subprocess.Popen([sys.executable, str(ROOT / "start_app.py"), "--no-browser", "--port", "0"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if (self.paths.runtime / "server.json").exists():
                    break
                time.sleep(0.1)
            self.assertEqual(server.stop_running(self.paths), 0)
            proc.wait(10)
            self.assertFalse((self.paths.runtime / "web.lock").exists())
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_hung_process_is_killed(self):
        """画面が応答しない（固まった）時は、記録の pid を強制終了してロックを片付ける。"""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            (self.paths.runtime / "web.lock").write_text(json.dumps({"pid": proc.pid}))
            (self.paths.runtime / "server.json").write_text(json.dumps({"url": "http://127.0.0.1:1/", "pid": proc.pid}))
            self.assertEqual(server.stop_running(self.paths, force_wait=1), 0)
            proc.wait(5)
            self.assertFalse((self.paths.runtime / "web.lock").exists())
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
