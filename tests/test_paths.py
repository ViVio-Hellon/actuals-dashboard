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

    def test_source_is_folder_plus_name(self):
        res = P.check_all({"rne_output_dir": str(self.tmp), "rne_output": "KURAIRE.sqlite3"}, self.paths)
        self.assertEqual(Path(res["source"]["path"]), self.tmp / "KURAIRE.sqlite3")      # .sqlite3 を付けても同じ
        self.assertEqual(res["source"]["state"], "warn")                                 # まだ無い = データ更新の前
        self.assertEqual(res["rne_output_dir"]["state"], "ok")
        sqlite3.connect(str(self.tmp / "KURAIRE.sqlite3")).execute("CREATE TABLE t (a)").connection.close()
        res = P.check_all({"rne_output_dir": str(self.tmp), "rne_output": "KURAIRE"}, self.paths)
        self.assertEqual(res["source"]["state"], "ok")
        bad = P.check_all({"rne_output_dir": str(self.tmp), "rne_output": "A:B"}, self.paths)
        self.assertEqual(bad["rne_output"]["state"], "missing")
        none = P.check_all({}, self.paths)
        self.assertEqual((none["source"]["path"], none["source"]["state"]), ("", "missing"))
        self.assertEqual(none["rne_output_dir"]["state"], "missing")
        self.assertEqual(P.check_all({"rne_output_dir": str(self.tmp / "no")}, self.paths)["rne_output_dir"]["state"],
                         "missing")                                                      # 置くフォルダは作らない

    def test_rne_file(self):
        self.assertEqual(P.check_rne("", self.paths)["state"], "missing")
        rne = self.tmp / "梱包実績(当月_倉入れ日付順).RNE"
        self.assertEqual(P.check_rne(str(rne), self.paths)["state"], "missing")
        rne.write_text("x", encoding="utf-8")
        self.assertEqual(P.check_rne(str(rne), self.paths)["state"], "ok")

    def _rneconverter(self, new=True, cred=True):
        root = self.tmp / "RNEConverter"
        (root / "app").mkdir(parents=True, exist_ok=True)
        (root / "start_app.py").write_text("")
        (root / "app" / "main.py").write_text('ap.add_argument("--rne")' if new else 'ap.add_argument("--only")')
        if cred:
            d = self.tmp / "rnehome" / "config"
            d.mkdir(parents=True, exist_ok=True)
            (d / "credentials.json").write_text(json.dumps({"id": "u", "pass": "p"}))
        return root

    def test_rneconverter_ready(self):
        self.assertEqual(P.rneconverter_status(str(self._rneconverter()), self.paths)["state"], "ok")

    def test_rneconverter_old_version_and_no_login(self):
        r = P.rneconverter_status(str(self._rneconverter(new=False, cred=False)), self.paths)
        self.assertEqual(r["state"], "missing")
        self.assertEqual({x["label"]: x["state"] for x in r["rows"]},
                         {"RNEConverter の版": "missing", "SymNavi のログイン": "missing"})

    def test_rneconverter_not_set(self):
        self.assertEqual(P.rneconverter_status("", self.paths)["state"], "missing")
        self.assertEqual(P.rneconverter_status(str(self.tmp), self.paths)["state"], "missing")


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


class DataUpdate(unittest.TestCase):
    """「データ更新」: RNE ファイル・置くフォルダ・名前を RNEConverter に渡し、できたファイルを取り込む。"""

    def test_runs_rneconverter_and_imports(self):
        from actuals.service import Service
        from tests.helpers import golden, write_source
        tmp = Path(tempfile.mkdtemp())
        try:
            g = golden()
            made = write_source(tmp / "made.sqlite3", g["header"], g["rows"][:50])
            fake = tmp / "RNEConverter"
            fake.mkdir()
            # 偽の RNEConverter: 受け取った引数を記録し、--save-dir に --output.sqlite3 を置く
            (fake / "start_app.py").write_text(
                "import json, shutil, sys\n"
                "a = sys.argv[1:]\n"
                "v = {a[i]: a[i + 1] for i in range(0, len(a) - 1) if a[i].startswith('--')}\n"
                f"json.dump(a, open(r'{tmp / 'args.json'}', 'w', encoding='utf-8'), ensure_ascii=False)\n"
                f"shutil.copyfile(r'{made}', v['--save-dir'] + '/' + v['--output'] + '.sqlite3')\n"
                "print('ok')\n", encoding="utf-8")
            rne = tmp / "梱包実績(当月_倉入れ日付順).RNE"
            rne.write_text("x", encoding="utf-8")
            out = tmp / "out"
            out.mkdir()
            home = tmp / "home"
            (home / "config").mkdir(parents=True)
            (home / "config" / "settings.json").write_text(json.dumps({
                "shared_dir": str(tmp / "share"), "rne_path": str(rne), "rne_output_dir": str(out),
                "rne_output": "KURAIRE", "rneconverter_dir": str(fake), "python_exe": sys.executable},
                ensure_ascii=False), encoding="utf-8")
            svc = Service(Paths(home))
            svc.run_rne()
            for _ in range(100):
                if svc.rne.rc is not None:
                    break
                time.sleep(0.1)
            self.assertEqual(svc.rne.rc, 0)
            args = json.loads((tmp / "args.json").read_text(encoding="utf-8"))
            self.assertEqual(args[:6], ["--rne", str(rne), "--output", "KURAIRE", "--save-dir", str(out)])
            self.assertIn("--table", args)
            self.assertEqual(svc.rne.after.get("rows"), 50)                    # 終わったら取り込んでいる
            self.assertEqual(svc.history.summary()["lots"], 50)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_refuses_without_rne(self):
        from actuals.service import Service, UserError
        tmp = Path(tempfile.mkdtemp())
        try:
            home = tmp / "home"
            (home / "config").mkdir(parents=True)
            (home / "config" / "settings.json").write_text(json.dumps({"shared_dir": str(tmp / "s")}), encoding="utf-8")
            with self.assertRaises(UserError) as cm:
                Service(Paths(home)).run_rne()
            self.assertIn("パスの設定 ①", str(cm.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
