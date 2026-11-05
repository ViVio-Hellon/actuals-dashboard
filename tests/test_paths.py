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

    def test_rne_file(self):
        self.assertEqual(P.check_rne("", self.paths)["state"], "missing")
        rne = self.tmp / "梱包実績(当月_倉入れ日付順).RNE"
        self.assertEqual(P.check_rne(str(rne), self.paths)["state"], "missing")
        rne.write_text("x", encoding="utf-8")
        self.assertEqual(P.check_rne(str(rne), self.paths)["state"], "ok")

    def test_check_all(self):
        exe = self.tmp / "SymNavi.exe"
        exe.write_text("x")
        res = P.check_all({"symnavi_exe": str(exe), "symnavi_server": "NlmFaNgyNavd", "work_dir": "{DATA}/work",
                           "cscript_path": str(self.tmp / "no.exe")}, self.paths)
        self.assertEqual(res["symnavi_exe"]["state"], "ok")
        self.assertEqual(res["symnavi_server"]["state"], "ok")
        self.assertEqual(res["work_dir"]["state"], "ok")
        self.assertEqual(res["cscript_path"]["state"], "warn")                         # 予備なので無くても警告だけ
        self.assertEqual(res["credentials"]["state"], "missing")
        self.assertEqual(P.check_all({"symnavi_server": "X"}, self.paths)["symnavi_server"]["state"], "warn")

    def test_log_output_path(self):
        empty = P.check_log("", self.paths)
        self.assertEqual(empty["state"], "ok")                                          # 空欄 = この PC の logs
        self.assertEqual(empty["folder"], str(self.paths.logs / "actualsdashboard"))
        self.assertEqual(P.check_log("logs", self.paths)["state"], "missing")           # フルパスでない
        share = self.tmp / "share" / "logs"
        r = P.check_log(str(share), self.paths)
        self.assertEqual((r["state"], r["folder"]), ("ok", str(share / "actualsdashboard")))
        self.assertIn("actualsdashboard_記録_", r["message"])
        self.assertTrue((share / "actualsdashboard").is_dir())
        bad = self.tmp / "file"
        bad.write_text("x")
        r = P.check_log(str(bad), self.paths)                                           # 書けなくても止まらない
        self.assertNotEqual(r["state"], "ok")
        self.assertIn("この PC の", r["message"])
        self.assertEqual(P.check_all({}, self.paths)["log_dir"]["state"], "ok")

    def test_work_dir_not_in_localappdata(self):
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(self.tmp / "AppData")
        try:
            self.assertEqual(P.check_work(str(self.tmp / "AppData" / "w"), self.paths)["state"], "missing")
        finally:
            if old is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old

    def test_credentials_fallback_to_rneconverter(self):
        from actuals import credentials as CR
        self.assertFalse(CR.status(self.paths)["ready"])
        d = self.tmp / "rnehome" / "config"
        d.mkdir(parents=True)
        (d / "credentials.json").write_text(json.dumps({"id": "rc", "pass": "p"}))
        st = CR.status(self.paths)
        self.assertEqual((st["ready"], st["id"]), (True, "rc"))
        self.assertIn("RNEConverter", st["source"])
        CR.save(self.paths, "own", "p2")
        self.assertEqual(CR.status(self.paths)["id"], "own")                           # このツールの登録が先


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

    # ---- 番号の使い回し（PC を終了・再起動した後に、別のプログラムが同じ番号を持つ） ----
    def _other(self):
        """ツールとは関係のない、いま動いているプログラム。"""
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    def test_is_ours_needs_pid_and_start_time(self):
        me = server.lock_record()
        self.assertTrue(server.is_ours(me))
        other = self._other()
        try:
            time.sleep(0.2)
            st = server._proc_started(other.pid)
            self.assertIsNotNone(st)
            self.assertTrue(server.is_ours({"pid": other.pid, "started": st}))
            self.assertFalse(server.is_ours({"pid": other.pid, "started": st - 3600}))      # 番号は同じでも別物
            old = "2020-01-01T00:00:00"                                                   # 前の版の記録（起動時刻なし）
            self.assertFalse(server.is_ours({"pid": other.pid, "time": old}))             # 書いた後に起動 → 本人ではない
            self.assertTrue(server.is_ours({"pid": other.pid, "time": "2999-01-01T00:00:00"}))
        finally:
            other.kill()
            other.wait()
        self.assertFalse(server.is_ours({"pid": 999999, "started": 0}))

    def test_reused_pid_lock_is_tidied_and_lock_taken(self):
        other = self._other()
        try:
            (self.paths.runtime / "web.lock").write_text(json.dumps({"pid": other.pid, "time": "2020-01-01T00:00:00"}))
            lock = server.LocalLock(self.paths.runtime / "web.lock")
            lock.__enter__()
            self.assertEqual(json.loads((self.paths.runtime / "web.lock").read_text())["pid"], os.getpid())
            lock.__exit__(None, None, None)
            self.assertIsNone(other.poll())                                               # 関係のないプログラムは終わらせない
        finally:
            other.kill()
            other.wait()

    def test_stop_does_not_kill_reused_pid(self):
        other = self._other()
        try:
            time.sleep(0.2)
            rec = {"pid": other.pid, "started": server._proc_started(other.pid) - 3600}
            (self.paths.runtime / "web.lock").write_text(json.dumps(rec))
            (self.paths.runtime / "server.json").write_text(json.dumps({"url": "http://127.0.0.1:1/", **rec}))
            self.assertEqual(server.stop_running(self.paths, force_wait=1), 0)
            time.sleep(0.3)
            self.assertIsNone(other.poll())                                               # 終わらせていない
            self.assertFalse((self.paths.runtime / "web.lock").exists())                  # 片付けた
            self.assertFalse((self.paths.runtime / "server.json").exists())
        finally:
            other.kill()
            other.wait()

    def test_start_after_reboot_with_reused_pid(self):
        """前の版の web.lock が残り、その番号を別のプログラムが使っていても、普通に起動できる。"""
        other = self._other()
        env = dict(os.environ, ACTUALS_DASHBOARD_HOME=str(self.paths.base))
        try:
            (self.paths.runtime / "web.lock").write_text(json.dumps({"pid": other.pid, "time": "2020-01-01T00:00:00"}))
            (self.paths.runtime / "server.json").write_text(json.dumps({"url": "http://127.0.0.1:1/", "pid": other.pid}))
            proc = subprocess.Popen([sys.executable, str(ROOT / "start_app.py"), "--no-browser", "--port", "0"], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if json.loads((self.paths.runtime / "server.json").read_text() or "{}").get("pid") == proc.pid:
                        break
                    time.sleep(0.1)
                info = json.loads((self.paths.runtime / "server.json").read_text())
                self.assertEqual(info["pid"], proc.pid)
                self.assertIsNotNone(info.get("started"))
                self.assertEqual(server.stop_running(self.paths), 0)
                proc.wait(10)
            finally:
                if proc.poll() is None:
                    proc.kill()
            self.assertIsNone(other.poll())
        finally:
            other.kill()
            other.wait()

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
    """「データ更新」: SymNavi で取り出した行を、そのまま共有の蓄積に取り込む（SymNavi の部分だけ差し替える）。"""

    def _service(self, tmp, cred=True):
        from actuals import credentials as CR
        from actuals.service import Service
        rne = tmp / "梱包実績(当月_倉入れ日付順).RNE"
        rne.write_text("x", encoding="utf-8")
        exe = tmp / "SymNavi.exe"
        exe.write_text("x")
        home = tmp / "home"
        (home / "config").mkdir(parents=True)
        (home / "config" / "settings.json").write_text(json.dumps({
            "shared_dir": str(tmp / "share"), "rne_path": str(rne), "symnavi_exe": str(exe),
            "work_dir": str(tmp / "work")}, ensure_ascii=False), encoding="utf-8")
        paths = Paths(home)
        if cred:
            CR.save(paths, "u", "p")
        return Service(paths), rne, exe

    def test_extracts_and_imports(self):
        from actuals import rne_runner
        from tests.helpers import golden
        tmp = Path(tempfile.mkdtemp())
        old = rne_runner.extract_rows
        seen = {}
        try:
            g = golden()

            def fake(rne_path, work_dir, exe, server, cred, startup, wait, cscript, report):
                seen.update(rne=rne_path, exe=exe, server=server, cred=dict(cred), work=work_dir)
                report(0.5, "SymNavi の結果を読み込んでいます")
                rows = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in g["rows"][:50]]
                return {"columns": g["header"], "rows": rows}
            rne_runner.extract_rows = fake
            os.environ["RNE_CONVERTER_HOME"] = str(tmp / "rnehome")
            svc, rne, exe = self._service(tmp)
            svc.run_update()
            for _ in range(100):
                if svc.rne.ok is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(svc.rne.ok, svc.rne.message)
            self.assertEqual((seen["rne"], seen["exe"], seen["server"], seen["cred"]),
                             (rne, str(exe), "NlmFaNgyNavd", {"id": "u", "pass": "p"}))
            self.assertEqual(svc.history.summary()["lots"], 50)
            self.assertEqual(svc.history.summary()["last"]["kind"], "symnavi")
            self.assertEqual(svc.rne.status()["progress"], 100)
        finally:
            rne_runner.extract_rows = old
            os.environ.pop("RNE_CONVERTER_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_failure_is_reported(self):
        from actuals import rne_runner
        tmp = Path(tempfile.mkdtemp())
        old = rne_runner.extract_rows
        try:
            def boom(*a, **k):
                raise RuntimeError("SymNavi の結果が届きません（時間切れ）")
            rne_runner.extract_rows = boom
            os.environ["RNE_CONVERTER_HOME"] = str(tmp / "rnehome")
            svc, _, _ = self._service(tmp)
            svc.run_update()
            for _ in range(100):
                if svc.rne.ok is not None:
                    break
                time.sleep(0.05)
            self.assertFalse(svc.rne.ok)
            self.assertIn("時間切れ", svc.rne.status()["message"])
            self.assertEqual(svc.history.summary()["lots"], 0)
        finally:
            rne_runner.extract_rows = old
            os.environ.pop("RNE_CONVERTER_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_refuses_without_settings(self):
        from actuals.service import UserError
        tmp = Path(tempfile.mkdtemp())
        try:
            os.environ["RNE_CONVERTER_HOME"] = str(tmp / "rnehome")
            svc, rne, _ = self._service(tmp, cred=False)
            rne.unlink()
            with self.assertRaises(UserError) as cm:
                svc.run_update()
            self.assertIn("RNE ファイルが見つかりません", str(cm.exception))
            self.assertIn("ID・パスワードが未登録", str(cm.exception))
        finally:
            os.environ.pop("RNE_CONVERTER_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_copied_symnavi_code_imports(self):
        """写した SymNavi のコードが読み込めて、元（RNEConverter）と同じ設定の既定値を持つ。"""
        from actuals.symnavi.config import SymNaviConfig
        from actuals.symnavi.extract.symnavi import SymNaviExtractor  # noqa: F401
        c = SymNaviConfig()
        self.assertEqual((c.exe, c.server, c.args), (r"C:\NAVICL\bin\SymNavi.exe", "NlmFaNgyNavd", "-d -u"))


if __name__ == "__main__":
    unittest.main()
