"""デスクトップ版の入口（bridge.py）: ポートを使わない・ブラウザ版と同じ画面と守り・同時に動かさない。"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ソケットを開くと落ちるようにしてから bridge.py を動かす（ポートを使っていないことの確かめ）
NO_SOCKET = ("import socket, runpy, sys\n"
             "class _No(socket.socket):\n"
             "    def __init__(self, *a, **k):\n"
             "        raise RuntimeError('ソケットを開こうとしました')\n"
             "socket.socket = _No\n"
             "sys.argv = ['bridge.py']\n"
             "runpy.run_path('bridge.py', run_name='__main__')\n")


class Bridge:
    def __init__(self, home: Path, token: str = "tok-test", exe_pid: int = 0):
        env = dict(os.environ, ACTUALS_DASHBOARD_HOME=str(home), ACTUALS_TOKEN=token, PYTHONIOENCODING="utf-8")
        if exe_pid:
            env["ACTUALS_EXE_PID"] = str(exe_pid)
        self.token = token
        self.p = subprocess.Popen([sys.executable, "-c", NO_SOCKET], cwd=str(ROOT), env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.next = 1

    def read(self):
        line = self.p.stdout.readline()
        if not line:
            return None, b""
        h = json.loads(line)
        return h, (self.p.stdout.read(h["len"]) if h.get("len") else b"")

    def call(self, method, path, body=b"", query="", headers=None, token=True, host="app.localhost"):
        hd = [["Host", host]] + (headers or [])
        if token:
            hd.append(["X-Tool-Token", self.token])
        i, self.next = self.next, self.next + 1
        self.p.stdin.write(json.dumps({"id": i, "method": method, "path": path, "query": query, "headers": hd,
                                       "len": len(body)}).encode() + b"\n" + body)
        self.p.stdin.flush()
        while True:
            h, b = self.read()
            if h is None or h.get("id") == i:
                return h, b

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            return self.p.wait(15)
        finally:
            if self.p.poll() is None:
                self.p.kill()
                self.p.wait()
            self.p.stdout.close()


class DesktopBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_works_without_any_socket(self):
        b = Bridge(self.home)
        try:
            h, _ = b.read()
            self.assertEqual(h["event"], "started")
            h, body = b.call("GET", "/")
            self.assertEqual(h["status"], 200)
            self.assertIn(b'name="app-token" content="tok-test"', body)            # 画面はブラウザ版と同じ
            csp = dict(h["headers"]).get("Content-Security-Policy", "")
            self.assertIn("http://ipc.localhost", csp)                            # 外枠への呼び出し（選ぶ…）を通す
            h, body = b.call("GET", "/api/state")
            self.assertEqual(h["status"], 200)
            self.assertIn("app", json.loads(body))
            h, body = b.call("GET", "/js/app.js")
            self.assertEqual(h["status"], 200)
            # 守りもブラウザ版と同じ
            self.assertEqual(b.call("GET", "/api/state", token=False)[0]["status"], 403)
            self.assertEqual(b.call("GET", "/api/state", host="127.0.0.1:8751")[0]["status"], 403)
            h, body = b.call("POST", "/api/settings", json.dumps({"values": {"result_wait_sec": "x"},
                                                                  "revision": ""}).encode(),
                             headers=[["Content-Type", "application/json"]])
            self.assertIn(h["status"], (409, 422))
            # 終了 → 外枠へ「終わってよい」
            h, _ = b.call("POST", "/api/shutdown", b"{}", headers=[["Content-Type", "application/json"]])
            self.assertEqual(h["status"], 200)
            ev, _ = b.read()
            self.assertEqual(ev, {"event": "quit"})
        finally:
            self.assertEqual(b.close(), 0)
        self.assertFalse((self.home / "runtime" / "web.lock").exists())             # 片付けて終わる
        logs = list((self.home / "logs" / "actualsdashboard").glob("actualsdashboard_記録_*.jsonl"))
        text = logs[0].read_text(encoding="utf-8")
        self.assertIn('"mode": "デスクトップ版"', text)

    def test_big_body_is_refused_and_bridge_continues(self):
        env_max = os.environ.get("ACTUALS_BRIDGE_MAX_BODY")
        os.environ["ACTUALS_BRIDGE_MAX_BODY"] = "1000"
        try:
            b = Bridge(self.home)
        finally:
            if env_max is None:
                os.environ.pop("ACTUALS_BRIDGE_MAX_BODY", None)
            else:
                os.environ["ACTUALS_BRIDGE_MAX_BODY"] = env_max
        try:
            b.read()
            h, body = b.call("POST", "/api/import/paste", b"x" * 5000)
            self.assertEqual(h["status"], 413)
            self.assertEqual(b.call("GET", "/api/state")[0]["status"], 200)       # 続けて使える
        finally:
            b.close()


class NotTogether(unittest.TestCase):
    """ブラウザ版とデスクトップ版は同時に動かない。後から開いた方が止まり、先の方には触らない。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.env = dict(os.environ, ACTUALS_DASHBOARD_HOME=str(self.home))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _browser(self):
        p = subprocess.Popen([sys.executable, str(ROOT / "start_app.py"), "--no-browser", "--port", "0"], env=self.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if json.loads((self.home / "runtime" / "server.json").read_text()).get("pid") == p.pid:
                    return p
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        p.kill()
        self.fail("ブラウザ版が起動しません")

    def test_desktop_after_browser_stops(self):
        browser = self._browser()
        try:
            b = Bridge(self.home)
            h, _ = b.read()
            self.assertEqual(h["event"], "fatal")
            self.assertIn("ブラウザ版が動いています", h["message"])
            self.assertEqual(b.close(), 5)
            self.assertIsNone(browser.poll())                                       # ブラウザ版はそのまま
            self.assertEqual(json.loads((self.home / "runtime" / "web.lock").read_text())["mode"], "browser")
        finally:
            browser.terminate()
            browser.wait(10)

    def test_browser_after_desktop_stops(self):
        b = Bridge(self.home)
        try:
            self.assertEqual(b.read()[0]["event"], "started")
            r = subprocess.run([sys.executable, str(ROOT / "start_app.py"), "--no-browser", "--port", "0"],
                               env=self.env, capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 5)
            self.assertIsNone(b.p.poll())                                           # デスクトップ版はそのまま（終わらせない）
            self.assertEqual(b.call("GET", "/api/state")[0]["status"], 200)
            self.assertEqual(json.loads((self.home / "runtime" / "web.lock").read_text())["mode"], "desktop")
        finally:
            b.close()

    def test_stop_bat_stops_desktop(self):
        from actuals.settings import Paths
        from actuals.web import server
        fake_exe = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])   # 窓（exe）の代わり
        b = Bridge(self.home, exe_pid=fake_exe.pid)
        try:
            self.assertEqual(b.read()[0]["event"], "started")
            held = json.loads((self.home / "runtime" / "web.lock").read_text())
            self.assertEqual((held["mode"], held["exe_pid"]), ("desktop", fake_exe.pid))
            rc = server.stop_running(Paths(self.home), force_wait=2)
            self.assertEqual(rc, 0)
            b.p.wait(10)
            self.assertIsNotNone(b.p.poll())
            self.assertFalse((self.home / "runtime" / "web.lock").exists())
        finally:
            b.close()
            if fake_exe.poll() is None:
                fake_exe.kill()
            fake_exe.wait()


class Versions(unittest.TestCase):
    def test_versions_agree(self):
        app = json.loads((ROOT / "config" / "app.json").read_text(encoding="utf-8"))["version"]
        tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
        cargo = re.search(r'^version = "([^"]+)"', (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"), re.M)
        self.assertEqual((app, tauri, cargo.group(1)), (app, app, app))


if __name__ == "__main__":
    unittest.main()
