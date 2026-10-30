"""共有フォルダへの保存（複数 PC・同時保存・古いロック・届かない時）。

PC ごとに手元の写しの場所（cache）を分けて、2 台の PC を再現する。
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from actuals import columns as C
from actuals.conditions import default_conditions
from actuals.sharedfile import SharedBusy, SharedFile
from actuals.stores import Conflict, HistoryStore, ImportError_, MasterStore
from tests.helpers import golden


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.share = self.tmp / "share"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pc(self, name, cls=MasterStore, **kw):
        return cls(self.share, self.tmp / name / "cache", **kw)


class MasterTests(Base):
    def test_initial_conditions_and_rev(self):
        a = self.pc("A")
        a.ensure()
        cs = a.conditions()
        self.assertEqual(cs.rev, 1)
        self.assertEqual(cs.rows, default_conditions().rows)

    def test_conflict_between_pcs(self):
        a, b = self.pc("A"), self.pc("B")
        a.ensure()
        ca, cb = a.conditions(), b.conditions()
        ca.rows[0]["name"] = "丸徳A"
        a.save_conditions(ca, ca.rev)
        cb.rows[0]["name"] = "丸徳B"
        with self.assertRaises(Conflict):        # B は古い版を見たまま → 上書きしない
            b.save_conditions(cb, cb.rev)
        self.assertEqual(b.conditions().rows[0]["name"], "丸徳A")   # B も最新が見える
        self.assertEqual(b.conditions().rev, 2)

    def test_override_seen_check(self):
        a, b = self.pc("A"), self.pc("B")
        a.set_override("L1", 3, "理由", None)
        with self.assertRaises(Conflict):
            b.set_override("L1", 5, "", None)     # B は「訂正なし」を見ていた
        b.set_override("L1", 5, "", 3)
        self.assertEqual(a.overrides()["L1"]["grp"], 5)
        a.set_override("L1", None, "", 5)
        self.assertNotIn("L1", b.overrides())
        self.assertEqual(len(a.audit(10, "override")), 3)

    def test_override_validation(self):
        a = self.pc("A")
        with self.assertRaises(ValueError):
            a.set_override("L1", 16, "", None)
        with self.assertRaises(ValueError):
            a.set_override("", 1, "", None)

    def test_concurrent_writes_do_not_lose_updates(self):
        """2 台が同時に別々のロットを訂正しても、両方とも残る（ロックで 1 台ずつ）。"""
        a, b = self.pc("A", lock_wait=20), self.pc("B", lock_wait=20)
        a.ensure()
        errors = []

        def work(store, prefix):
            try:
                for i in range(15):
                    store.set_override(f"{prefix}{i}", 1, "", None)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        ts = [threading.Thread(target=work, args=(a, "A")), threading.Thread(target=work, args=(b, "B"))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(a.overrides()), 30)

    def test_busy_lock_and_stale_lock(self):
        a = self.pc("A", lock_wait=0.5, lock_stale_sec=60)
        a.ensure()
        lock = a.file.lock_path
        lock.write_text(json.dumps({"pc": "PC-X", "user": "u", "at": "now"}), encoding="utf-8")
        with self.assertRaises(SharedBusy) as cm:
            a.set_override("L1", 1, "", None)
        self.assertIn("PC-X", str(cm.exception))
        old = time.time() - 600
        os.utime(lock, (old, old))                # 落ちた PC が残したロック → 時間が過ぎたら外して進む
        a.set_override("L1", 1, "", None)
        self.assertFalse(lock.exists())

    def test_backup_created(self):
        a = self.pc("A")
        a.ensure()
        a.set_override("L1", 1, "", None)
        self.assertTrue(list((self.share / "backup").glob("actuals_master_*.sqlite3")))

    def test_unreachable_share_reads_last_copy(self):
        a = self.pc("A")
        a.ensure()
        cs = a.conditions()
        cs.rows[0]["name"] = "控えの名前"
        a.save_conditions(cs, cs.rev)
        shutil.rmtree(self.share)                 # 共有が消えた（届かない）
        self.assertEqual(a.conditions().rows[0]["name"], "控えの名前")


class HistoryTests(Base):
    def test_merge_accumulates_by_lot(self):
        g = golden()
        h = self.pc("A", HistoryStore)
        r1 = h.merge(g["header"], g["rows"][:500], kind="test", source="s")
        self.assertEqual((r1["added"], r1["changed"]), (500, 0))
        changed = [dict(r) for r in g["rows"][400:600]]
        changed[0][C.WEIGHT] = 9999
        r2 = h.merge(g["header"], changed, kind="test", source="s")
        self.assertEqual((r2["added"], r2["changed"], r2["same"]), (100, 1, 99))
        self.assertEqual(h.summary()["lots"], 600)            # 消しはしない
        self.assertEqual(h.lot(changed[0][C.LOT])["data"][C.WEIGHT], 9999)

    def test_months_and_order(self):
        g = golden()
        h = self.pc("A", HistoryStore)
        h.merge(g["header"], g["rows"], kind="test", source="s")
        rows = h.lots_in_months("genba", ["2022-06"])
        self.assertEqual(len(rows), 973)
        expect = [r[C.LOT] for r in g["rows"] if r[C.BOX_DATE].startswith("2022-06")]
        self.assertEqual([r[C.LOT] for r in rows], expect)     # 取り込んだ順（旧 DATA の並び）

    def test_missing_columns_rejected(self):
        h = self.pc("A", HistoryStore)
        with self.assertRaises(ImportError_) as cm:
            h.merge([C.LOT, C.WEIGHT], [{C.LOT: "A", C.WEIGHT: 1}], kind="test", source="s")
        self.assertIn(C.BOX_DATE, str(cm.exception))

    def test_duplicates_in_one_import(self):
        g = golden()
        rows = [g["rows"][0], dict(g["rows"][0], **{C.WEIGHT: 1})]
        h = self.pc("A", HistoryStore)
        r = h.merge(g["header"], rows, kind="test", source="s")
        self.assertEqual((r["rows"], r["duplicates"]), (1, 1))
        self.assertEqual(h.lot(g["rows"][0][C.LOT])["data"][C.WEIGHT], 1)   # 後のものが残る

    def test_second_pc_sees_new_data(self):
        g = golden()
        a, b = self.pc("A", HistoryStore), self.pc("B", HistoryStore)
        a.merge(g["header"], g["rows"][:10], kind="test", source="s")
        self.assertEqual(b.summary()["lots"], 10)
        a.merge(g["header"], g["rows"][10:20], kind="test", source="s")
        self.assertEqual(b.summary()["lots"], 20)              # 長く開いていても、次に読む時に新しくなる


if __name__ == "__main__":
    unittest.main()
