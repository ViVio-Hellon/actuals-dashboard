"""画面の API（サーバーを実際に立てて呼ぶ）。"""
import json
import re
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from actuals import columns as C
from actuals.settings import Paths
from tests.helpers import golden, write_source

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        os.environ["ACTUALS_DASHBOARD_HOME"] = str(cls.tmp / "home")
        cls.paths = Paths.default()
        g = golden()
        cls.g = g
        cls.src = write_source(cls.tmp / "KURAIRE.sqlite3", g["header"], g["rows"])
        (cls.paths.config / "settings.json").write_text(json.dumps({
            "shared_dir": str(cls.tmp / "share"),
            "export_dir": str(cls.tmp / "export")}), encoding="utf-8")
        from actuals.web import server
        cls.holder = {}
        ready = threading.Event()
        cls.thread = threading.Thread(target=server.serve, args=(cls.paths,),
                                      kwargs={"open_browser": False, "port": 0, "ready": ready, "holder": cls.holder},
                                      daemon=True)
        cls.thread.start()
        ready.wait(10)
        cls.url = cls.holder["url"].rstrip("/")
        cls.token = cls.holder["app"].token

    @classmethod
    def tearDownClass(cls):
        cls.holder["app"].request_shutdown("test")
        cls.thread.join(5)
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("ACTUALS_DASHBOARD_HOME", None)

    def call(self, path, data=None, token=True, raw=None):
        headers = {"X-Tool-Token": self.token} if token else {}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if raw is not None:
            body = raw
            headers["Content-Type"] = "application/octet-stream"
        req = urllib.request.Request(self.url + path, data=body, headers=headers, method="POST" if body is not None else "GET")
        try:
            with OPENER.open(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def test_01_token_required(self):
        code, body = self.call("/api/state", token=False)
        self.assertEqual(code, 403)
        self.assertEqual(body["error"]["code"], "token")

    def test_02_import_and_month(self):
        code, st = self.call("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(st["history"]["lots"], 0)
        code, res = self.call("/api/import/sqlite", {"path": str(self.src)})
        self.assertEqual((code, res["rows"], res["added"]), (200, 987, 987))
        code, st = self.call("/api/state")
        self.assertEqual((st["default"]["year"], st["default"]["month"]), (2022, 6))   # 旧 VBA の集計月と同じ
        code, again = self.call("/api/import/sqlite", {"path": str(self.src)})
        self.assertEqual((again["added"], again["same"]), (0, 987))                    # 同じロットは増えない
        self.assertEqual(self.call("/api/import/sqlite", {"path": str(self.tmp / "none.sqlite3")})[0], 422)

    def test_03_vba_compatible_matches_golden(self):
        """判定方式を旧 VBA と同じにすると、月集計が受領ブックと一致する（手動訂正も移す）。"""
        code, cond = self.call("/api/conditions")
        c = cond["conditions"]
        c.update({"rule_not_material": "temper_val", "rule_not_hit": "stop"})
        code, _ = self.call("/api/conditions", c)
        self.assertEqual(code, 200)
        for lot, grp in self.g["overrides"].items():
            code, _ = self.call("/api/override", {"lot": lot, "grp": grp, "reason": "旧ブック", "seen": None})
            self.assertEqual(code, 200)
        code, v = self.call("/api/month?y=2022&m=6&cal=genba&sel=9")
        exp = self.g["expected"]["month"]
        for gi, gr in enumerate(v["groups"]):
            if gr["named"]:
                for d in range(31):
                    self.assertAlmostEqual(gr["weight"][d], exp["weight"][gi][d], places=6)
                    self.assertEqual(gr["count"][d], exp["count"][gi][d])
        self.assertEqual(len(v["detail"]), len(self.g["expected"]["graph"]["detail"]))
        self.assertEqual(v["unclassified"], 17)
        # 修正版に戻す（後の試験のため）
        code, cond = self.call("/api/conditions")
        c = cond["conditions"]
        c.update({"rule_not_material": "material", "rule_not_hit": "skip"})
        self.assertEqual(self.call("/api/conditions", c)[0], 200)
        self.assertEqual(self.call("/api/month?y=2022&m=6&cal=genba&sel=all")[1]["unclassified"], 0)

    def test_04_conflicts(self):
        code, cond = self.call("/api/conditions")
        c = cond["conditions"]
        self.assertEqual(self.call("/api/conditions", dict(c))[0], 200)
        code, body = self.call("/api/conditions", dict(c))          # 古い版のまま 2 回目
        self.assertEqual((code, body["error"]["code"]), (409, "stale"))
        lot = self.g["rows"][5][C.LOT]
        self.assertEqual(self.call("/api/override", {"lot": lot, "grp": 2, "seen": None})[0], 200)
        code, body = self.call("/api/override", {"lot": lot, "grp": 3, "seen": None})
        self.assertEqual(code, 409)
        self.assertEqual(self.call("/api/override", {"lot": lot, "grp": None, "seen": 2})[0], 200)
        code, body = self.call("/api/override", {"lot": "NO-SUCH", "grp": 1, "seen": None})
        self.assertEqual(code, 422)

    def test_05_invalid_conditions(self):
        code, cond = self.call("/api/conditions")
        c = cond["conditions"]
        c["rows"][0]["t_min"] = "abc"
        code, body = self.call("/api/conditions", c)
        self.assertEqual(code, 422)
        self.assertIn("1 行目", body["error"]["message"])

    def test_06_preview_lot_year_review(self):
        code, cond = self.call("/api/conditions")
        c = cond["conditions"]
        c["rows"][8]["t_min"] = 30                                   # L-1 を 30mm 以上に
        code, pv = self.call("/api/conditions/preview", {"conditions": c, "y": 2022, "m": 6, "cal": "genba"})
        self.assertEqual(code, 200)
        self.assertTrue(pv["changes"])
        lot = self.g["rows"][0][C.LOT]
        code, d = self.call(f"/api/lot?lot={lot}&cal=genba")
        self.assertEqual((code, d["auto"]), (200, 9))
        self.assertEqual(len(d["trace"]), 15)                        # 9 行目で決まり、VC 行での確定が無いので全行を見る
        code, y = self.call("/api/year?fy=2022&cal=genba")
        self.assertEqual(code, 200)
        self.assertGreater(y["months"][2]["weight_total"], 0)        # 6 月
        code, r = self.call("/api/review?y=2022&m=6&cal=genba")
        self.assertEqual(code, 200)
        code, cell = self.call("/api/cell?y=2022&m=6&cal=genba&g=9&d=22")
        self.assertEqual(code, 200)
        self.assertGreater(cell["count"], 0)

    def test_07_export_and_paste(self):
        code, r = self.call("/api/export", {"kind": "month", "y": 2022, "m": 6, "cal": "genba"})
        self.assertEqual(code, 200)
        text = Path(r["path"]).read_text(encoding="utf-8-sig")
        self.assertIn("丸徳ﾗｲﾝ", text)
        header = self.g["header"]
        rows = self.g["rows"][:3]
        tsv = "\t".join(header) + "\n" + "\n".join("\t".join("" if r.get(h) is None else str(r.get(h)) for h in header) for r in rows)
        code, res = self.call("/api/import/paste", {"text": tsv})
        self.assertEqual((code, res["rows"], res["same"] + res["changed"]), (200, 3, 3))
        code, res = self.call("/api/import/paste", {"text": "a\tb\n1\t2"})
        self.assertEqual(code, 422)

    def test_08_settings_revision(self):
        code, s = self.call("/api/settings")
        code, _ = self.call("/api/settings", {"values": {"auto_import": False}, "revision": s["revision"]})
        self.assertEqual(code, 200)
        code, body = self.call("/api/settings", {"values": {"auto_import": True}, "revision": s["revision"]})
        self.assertEqual(code, 409)
        code, s = self.call("/api/settings")
        self.assertEqual(self.call("/api/settings", {"values": {"auto_import": True}, "revision": s["revision"]})[0], 200)

    def test_09_update_not_configured(self):
        code, body = self.call("/api/rne/run", {})
        self.assertEqual(code, 422)
        msg = body["error"]["message"]
        self.assertIn("RNE ファイルが見つかりません", msg)
        self.assertIn("ID・パスワードが未登録", msg)

    def test_10_credentials_never_returned(self):
        self.assertEqual(self.call("/api/credentials", {"id": "", "password": "x"})[0], 422)
        code, r = self.call("/api/credentials", {"id": "user1", "password": "secret-pass"})
        self.assertEqual((code, r["id"], r["has_password"], r["ready"]), (200, "user1", True, True))
        code, s = self.call("/api/settings")
        self.assertNotIn("secret-pass", json.dumps(s, ensure_ascii=False))
        code, st = self.call("/api/state")
        self.assertNotIn("secret-pass", json.dumps(st, ensure_ascii=False))
        code, r = self.call("/api/credentials", {"id": "user2", "password": ""})        # 空ならパスワードはそのまま
        self.assertTrue(r["has_password"])

    def test_11_bad_seconds(self):
        code, s = self.call("/api/settings")
        code, body = self.call("/api/settings", {"values": {"result_wait_sec": "abc"}, "revision": s["revision"]})
        self.assertEqual(code, 422)

    def test_12_unexpected_error_ref_matches_log(self):
        from actuals import logsetup as L
        svc = self.holder["app"].service
        logs = self.tmp / "logs12"
        L.setup(str(logs), self.tmp / "local12", version="t")
        old = svc.history_summary

        def broken():
            raise KeyError("壊れた行")
        svc.history_summary = broken
        try:
            code, body = self.call("/api/history")
        finally:
            svc.history_summary = old
            L.flush()
            L.detach()
        self.assertEqual(code, 500)
        ref = re.search(r"E \d{6}-\d{6}-[0-9a-f]{2}", body["error"]["message"]).group(0)
        ev = [json.loads(x) for f in (logs / L.TOOL_ID).glob("*.jsonl")
              for x in f.read_text(encoding="utf-8").splitlines()]
        err = [e for e in ev if e["kind"] == "エラー"]
        self.assertEqual([e["ref"] for e in err], [ref])
        self.assertEqual(err[0]["process"], "画面の読み込み /api/history")
        self.assertEqual(err[0]["error_type"], "KeyError")

    def test_13_set_log_output_path(self):
        """ログの出力パスを画面から指定 → 保存するとすぐ、その下の actualsdashboard に書く。"""
        from actuals import logsetup as L
        try:
            code, s = self.call("/api/settings")
            self.assertEqual(s["values"]["log_dir"], "")                                  # 既定は空欄（この PC の logs）
            code, body = self.call("/api/settings", {"values": {"log_dir": "logs"}, "revision": s["revision"]})
            self.assertEqual(code, 422)
            self.assertIn("フルパス", body["error"]["message"])
            out = self.tmp / "共有" / "logs"
            code, r = self.call("/api/settings", {"values": {"log_dir": str(out)}, "revision": s["revision"]})
            self.assertEqual(code, 200)
            code, s2 = self.call("/api/settings")
            self.assertEqual(s2["values"]["log_dir"], str(out))
            self.assertEqual(s2["log"]["wanted"], str(out / "actualsdashboard"))
            self.call("/api/export", {"kind": "month", "y": 2022, "m": 6, "cal": "genba"})
            L.flush()
            names = sorted(p.name for p in (out / "actualsdashboard").iterdir())
            self.assertTrue(any(n.startswith("actualsdashboard_記録_") for n in names), names)
            ev = (out / "actualsdashboard" / L.event_name(L.pc_name(), __import__("datetime").date.today()))
            self.assertIn("CSV の書き出し", ev.read_text(encoding="utf-8"))
            code, s3 = self.call("/api/settings")
            code, r = self.call("/api/settings", {"values": {"log_dir": ""}, "revision": s3["revision"]})
            self.assertEqual(code, 200)
            code, s4 = self.call("/api/settings")
            self.assertEqual(s4["log"]["wanted"], str(self.paths.logs / "actualsdashboard"))
        finally:
            L.detach()

if __name__ == "__main__":
    unittest.main()
