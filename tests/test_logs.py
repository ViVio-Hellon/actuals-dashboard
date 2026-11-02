"""ログの置き場所・名前・中身（all-tools 日報複合ツールと同じ決まり）。"""
import json
import logging
import os
import shutil
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

from actuals import logsetup as L

log = logging.getLogger("actuals.test")


def _events(base: Path):
    p = base / L.TOOL_ID / L.event_name(L.pc_name(), date.today())
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _text(base: Path) -> str:
    p = base / L.TOOL_ID / L.text_name(L.pc_name(), date.today())
    return p.read_text(encoding="utf-8") if p.exists() else ""


class Logs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "share" / "logs"
        self.local = self.tmp / "local" / "logs"
        L.setup(str(self.out), self.local, keep_days=90, version="9.9.9")

    def tearDown(self):
        L.detach()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_place_and_names(self):
        log.info("こんにちは")
        L.operation("画面を開く")
        L.flush()
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [L.TOOL_ID])     # 出力パスの直下には置かない
        names = sorted(p.name for p in (self.out / L.TOOL_ID).iterdir())
        pc = L.pc_name()
        self.assertIn(f"actualsdashboard_{pc}_{date.today():%Y%m%d}.log", names)
        self.assertIn(f"actualsdashboard_記録_{pc}_{date.today():%Y%m%d}.jsonl", names)
        self.assertTrue(all(n.startswith("actualsdashboard_") for n in names))
        self.assertRegex(L.TOOL_ID, r"^[a-z]+$")
        self.assertIn("こんにちは", _text(self.out))
        self.assertFalse(self.local.exists())

    def test_fallback_when_unreachable_and_not_set(self):
        bad = self.tmp / "not_a_folder"
        bad.write_text("x")                     # フォルダが作れない = 届かない
        L.setup(str(bad), self.local)
        log.warning("共有に届かない時の記録")
        L.flush()
        self.assertIn("共有に届かない時の記録", _text(self.local))   # 止めずにこの PC の logs\actualsdashboard へ
        self.assertIn("書けません", L.status()["problem"])
        self.assertEqual(L.status()["dir"], str(self.local / L.TOOL_ID))
        L.setup("", self.local)                 # 決めていない
        log.info("未設定の時の記録")
        L.flush()
        self.assertIn("未設定の時の記録", _text(self.local))

    def test_start_stop_and_operation(self):
        L.started("画面", data_dir="D")
        L.operation("CSV の書き出し", kind="month", y=2022, m=6)
        L.stopped(0, "画面の「終了」")
        L.flush()
        ev = _events(self.out)
        kinds = [e["kind"] for e in ev]
        self.assertEqual(kinds, ["起動", "操作", "終了"])
        self.assertEqual((ev[0]["tool"], ev[0]["tool_name"], ev[0]["version"], ev[0]["pc"]),
                         ("actualsdashboard", "倉入実績集計", "9.9.9", L.pc_name()))
        self.assertEqual((ev[1]["action"], ev[1]["y"]), ("CSV の書き出し", 2022))
        self.assertEqual(ev[2]["reason"], "画面の「終了」")
        self.assertIn("uptime", ev[2])

    def test_start_and_end_are_paired_with_duration(self):
        with L.timed("月の集計", y=2022) as fr:
            fr.note(rows=987)
        L.flush()
        ev = [e for e in _events(self.out) if e["kind"].startswith("処理")]
        self.assertEqual([e["kind"] for e in ev], ["処理開始", "処理終了"])
        self.assertEqual((ev[1]["process"], ev[1]["result"], ev[1]["rows"], ev[1]["level"]), ("月の集計", "正常", 987, "INFO"))
        self.assertIn("seconds", ev[1])
        self.assertIn("▶ 月の集計 開始", _text(self.out))
        self.assertIn("■ 月の集計 終了", _text(self.out))

    def test_slow_levels(self):
        self.assertEqual([L.slow_label(s) for s in (2.99, 3, 9.9, 10, 29, 30, 700)],
                         ["", "3秒以上", "3秒以上", "10秒以上", "10秒以上", "30秒以上", "30秒以上"])
        old = L.SLOW_STEPS
        try:
            L.SLOW_STEPS = ((0.05, "3秒以上"),)
            with L.timed("遅い処理"):
                time.sleep(0.08)
            with L.timed("画面の読み込み", quiet=True):     # 速い quiet は残さない
                pass
        finally:
            L.SLOW_STEPS = old
        L.flush()
        ev = [e for e in _events(self.out) if e["kind"] == "処理終了"]
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["level"], ev[0]["slow"]), ("WARNING", "3秒以上"))

    def test_error_record_has_everything(self):
        L.new_operation_id()
        with self.assertRaises(ZeroDivisionError):
            with L.timed("データ更新"):
                log.info("SymNavi を起動しました")
                with L.timed("蓄積へ取り込み", files=[r"\\srv\share\actuals_history.sqlite3"]):
                    L.using(r"C:\work\KURAIRE.tsv")
                    1 / 0
        L.flush()
        ev = _events(self.out)
        err = [e for e in ev if e["kind"] == "エラー"]
        self.assertEqual(len(err), 1)                                   # 入れ子でも 1 回だけ
        e = err[0]
        for k in ("at", "tool", "version", "pc", "user", "process", "error_type", "error_message", "traceback",
                  "files", "recent", "ref", "where", "op"):
            self.assertIn(k, e, k)
        self.assertEqual(e["process"], "データ更新 > 蓄積へ取り込み")
        self.assertEqual(e["error_type"], "ZeroDivisionError")
        self.assertIn("1 / 0", e["traceback"])
        self.assertIn(r"C:\work\KURAIRE.tsv", e["files"])
        self.assertIn(r"\\srv\share\actuals_history.sqlite3", e["files"])
        self.assertTrue(any("SymNavi を起動しました" in r for r in e["recent"]))
        self.assertEqual(e["level"], "ERROR")
        ends = [x for x in ev if x["kind"] == "処理終了"]
        self.assertEqual([(x["process"], x["result"], x["ref"]) for x in ends],
                         [("蓄積へ取り込み", "エラー", e["ref"]), ("データ更新", "エラー", e["ref"])])
        text = _text(self.out)
        self.assertIn(f"エラー {e['ref']}", text)
        self.assertIn("実行していた処理: データ更新 > 蓄積へ取り込み", text)
        self.assertIn("Traceback", text)

    def test_expected_rejection_is_warning(self):
        class Rejected(ValueError):
            pass
        L.expected_errors(Rejected)
        with self.assertRaises(Rejected):
            with L.timed("条件表の保存"):
                raise Rejected("入力に誤りがあります")
        L.flush()
        ev = _events(self.out)
        self.assertFalse([e for e in ev if e["kind"] == "エラー"])
        end = [e for e in ev if e["kind"] == "処理終了"][0]
        self.assertEqual((end["result"], end["level"]), ("中断", "WARNING"))

    def test_read_save_and_warning(self):
        L.loaded("集計に使うロット", r"\\srv\share\actuals_history.sqlite3", 987, 0.12)
        L.saved("CSV（月の集計）", r"C:\out\月集計.csv", 34, 0.01)
        log.warning("古いロックを外します（続けます）")
        L.flush()
        ev = _events(self.out)
        self.assertEqual([(e["kind"], e.get("count")) for e in ev], [("読込", 987), ("保存", 34), ("警告", None)])
        self.assertEqual(ev[0]["target"], r"\\srv\share\actuals_history.sqlite3")

    def test_password_never_written(self):
        L.secret("Pw-Secret-123")
        log.info("cmd -u ID,Pw-Secret-123,SRV")
        try:
            raise RuntimeError("login failed for Pw-Secret-123")
        except RuntimeError as e:
            L.record_error(e)
        L.flush()
        both = _text(self.out) + json.dumps(_events(self.out), ensure_ascii=False)
        self.assertNotIn("Pw-Secret-123", both)
        self.assertIn("****", both)

    def test_long_running_is_reported_while_running(self):
        old = (L.LONG_FIRST, L.WATCH_INTERVAL)
        try:
            L.LONG_FIRST, L.WATCH_INTERVAL = 0.2, 0.05
            time.sleep(L.WATCH_INTERVAL if old[1] < 1 else 0)
            with L.timed("SymNavi で RNE を実行して取り出す"):
                t0 = time.time()
                while time.time() - t0 < 7:              # 見張りが前の 5 秒の眠りから覚めるまで待つこともある
                    time.sleep(0.1)
                    L.flush()
                    if any(e["kind"] == "長引き" for e in _events(self.out)):
                        break
        finally:
            L.LONG_FIRST, L.WATCH_INTERVAL = old
        ev = [e for e in _events(self.out) if e["kind"] == "長引き"]
        self.assertTrue(ev)
        self.assertEqual((ev[0]["process"], ev[0]["level"]), ("SymNavi で RNE を実行して取り出す", "WARNING"))

    def test_cleanup_only_own_old_files(self):
        L.detach()
        pc = L.pc_name()
        old = (date.today() - timedelta(days=40)).strftime("%Y%m%d")
        new = date.today().strftime("%Y%m%d")
        files = {
            f"actualsdashboard/actualsdashboard_{pc}_{old}.log": False,           # 自分・古い → 消す
            f"actualsdashboard/actualsdashboard_記録_{pc}_{old}.jsonl": False,
            f"actuals-dashboard/actuals-dashboard_{pc}_{old}.log": False,          # 前の形の自分・古い → 消す
            f"actualsdashboard/actualsdashboard_{pc}_{new}.log": True,             # 新しい → 残す
            f"actuals-dashboard/actuals-dashboard_{pc}_{new}.log": True,           # 前の形は移さない・消さない
            f"actualsdashboard/actualsdashboard_OTHER-PC_{old}.log": True,         # ほかの PC → 触らない
            f"nippou/nippou_動作記録_{pc}.log": True,                               # ほかのツール → 触らない
            f"nippou/nippou_出来事_2020-01-01_{pc}.jsonl": True,
            f"記録_{pc}_{old}.jsonl": True,                                         # 直下の他人のもの → 触らない
        }
        for rel in files:
            p = self.out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
            os.utime(p, (time.time() - 50 * 86400,) * 2)
        L.setup(str(self.out), self.local, keep_days=30)
        log.info("書く")
        L.flush()
        for rel, keep in files.items():
            self.assertEqual((self.out / rel).exists(), keep, rel)
        self.assertNotIn("書く", (self.out / f"actuals-dashboard/actuals-dashboard_{pc}_{new}.log").read_text(encoding="utf-8"))
        self.assertEqual(L.status()["legacy"], [str(self.out / "actuals-dashboard")])


class Wiring(unittest.TestCase):
    """業務の処理から、読込・保存・処理の開始と終了が残る。"""

    def test_import_and_export_are_logged(self):
        from actuals.service import Service
        from actuals.settings import Paths
        from tests.helpers import golden
        tmp = Path(tempfile.mkdtemp())
        try:
            home = tmp / "home"
            (home / "config").mkdir(parents=True)
            (home / "config" / "settings.json").write_text(json.dumps({
                "shared_dir": str(tmp / "share"), "export_dir": str(tmp / "out")}), encoding="utf-8")
            L.setup(str(tmp / "logs"), tmp / "local", version="0.1.0")
            svc = Service(Paths(home))
            g = golden()
            rows = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in g["rows"][:30]]
            svc.import_rows(g["header"], rows, "paste", "試験")
            st = svc.state()
            svc.month(st["default"]["year"], st["default"]["month"], "genba", "all")
            svc.export("month", st["default"]["year"], st["default"]["month"], "genba", "all", 0)
            L.flush()
            ev = _events(tmp / "logs")
            kinds = [(e["kind"], e.get("process") or e.get("what")) for e in ev]
            self.assertIn(("処理開始", "蓄積へ取り込み"), kinds)
            self.assertIn(("処理終了", "蓄積へ取り込み"), kinds)
            saved = [e for e in ev if e["kind"] == "保存"]
            self.assertIn(("蓄積した実績", 30), [(e["what"], e["count"]) for e in saved])
            self.assertTrue(any(e["what"] == "CSV（月の集計）" and e["count"] > 0 for e in saved))
            self.assertTrue(any(e["kind"] == "読込" and e["what"].startswith("集計に使うロット") and e["count"] == 30
                                for e in ev))
        finally:
            L.detach()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
