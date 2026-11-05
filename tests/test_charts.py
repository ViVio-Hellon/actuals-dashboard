"""グラフの描画計画（all-tools 日報と同じ見た目の決まり）。色は印（mark）で渡し、数字は変えない。"""
import unittest

from actuals import charts as CH


def _shift(w1, w2, w3, c=(0,) * 31):
    return {1: {"weight": list(w1), "count": list(c)}, 2: {"weight": list(w2), "count": list(c)},
            3: {"weight": list(w3), "count": list(c)}}


class Charts(unittest.TestCase):
    def setUp(self):
        z = [0.0] * 31
        self.w1, self.w2, self.w3 = z[:], z[:], z[:]
        self.w1[0], self.w2[0], self.w3[0] = 10.0, 20.0, 30.0
        self.w1[2] = 5.0
        self.total = [a + b + c for a, b, c in zip(self.w1, self.w2, self.w3)]
        self.counts = [0] * 31
        self.counts[0] = 7

    def test_total_is_stacked_by_shift_in_excel_colors(self):
        p = CH.month_charts("全", self.total, self.counts, _shift(self.w1, self.w2, self.w3), 30, 2022, 6, True)["total"]
        self.assertTrue(p["stacked"])
        self.assertEqual([(g["label"], g["mark"], g["shape"]) for g in p["legend"]],
                         [("1直", "shift-1", "bar"), ("2直", "shift-2", "bar"), ("3直", "shift-3", "bar"),
                          ("枚数", "count", "line")])
        day1 = [it for it in p["items"] if it["t"] == "rect" and "bar" in it.get("cls", "") and it["x"] < 80]
        self.assertEqual([it["cls"].split()[-1] for it in day1], ["mark-shift-1", "mark-shift-2", "mark-shift-3"])
        ys = [it["y"] for it in day1]
        self.assertEqual(ys, sorted(ys, reverse=True))                      # 下から 1→2→3 直
        heights = [it["h"] for it in day1]
        self.assertAlmostEqual(heights[2] / heights[0], 3.0, places=6)     # 高さは重量どおり
        self.assertEqual((p["weight_total"], p["count_total"]), (65.0, 7))
        tip = [it["tip"] for it in p["items"] if it.get("cls") == "hit"][0]
        self.assertIn("6/1(水)", tip)
        self.assertIn("1直 10.0 kg・2直 20.0 kg・3直 30.0 kg", tip)

    def test_not_stacked_when_no_shift_or_sums_differ(self):
        sh = _shift(self.w1, self.w2, self.w3)
        taiyo = CH.month_charts("全", self.total, self.counts, sh, 30, 2022, 6, False)["total"]
        self.assertFalse(taiyo["stacked"])
        self.assertEqual([g["mark"] for g in taiyo["legend"]], ["total", "count"])
        bad = list(self.total)
        bad[0] += 1                                                         # 積んでも合計にならない → 積まない
        p = CH.month_charts("全", bad, self.counts, sh, 30, 2022, 6, True)["total"]
        self.assertFalse(p["stacked"])

    def test_count_line_has_halo_and_marks(self):
        p = CH.month_charts("全", self.total, self.counts, _shift(self.w1, self.w2, self.w3), 30, 2022, 6, True)["total"]
        lines = [it["cls"] for it in p["items"] if it["t"] == "polyline"]
        self.assertEqual(lines, ["line line--halo", "line mark-count"])       # 縁取りが先（下）
        self.assertEqual(len([it for it in p["items"] if it["t"] == "dot"]), 30)

    def test_days_and_sundays(self):
        p = CH.month_charts("全", self.total, self.counts, _shift(self.w1, self.w2, self.w3), 30, 2022, 6, True)["total"]
        days = [it["text"] for it in p["items"] if it["t"] == "text" and "axis--day" in it["cls"]]
        self.assertEqual(days, [str(d) for d in range(1, 31)])               # 1〜末日すべて
        suns = [it["text"] for it in p["items"] if it["t"] == "text" and "axis--sun" in it["cls"]]
        self.assertEqual(suns, ["5", "12", "19", "26"])                     # 2022/6 の日曜
        self.assertEqual(len([it for it in p["items"] if it.get("cls") == "sun"]), 4)

    def test_count_scale_is_per_shift(self):
        """枚数の目盛りは直ごと（ほかの直の多い日に引っぱられて線が平らにならない）。"""
        c1 = [0] * 31
        c1[0] = 40000
        c3 = [0] * 31
        c3[0] = 120
        sh = _shift(self.w1, self.w2, self.w3)
        sh[1]["count"], sh[3]["count"] = c1, c3
        out = CH.month_charts("全", self.total, self.counts, sh, 30, 2022, 6, True)["shift"]
        tops = [[it["text"] for it in p["items"] if it["t"] == "text" and it["cls"] == "axis axis--count"][-1]
                for p in out]
        self.assertEqual(tops, ["40,000枚", "4枚", "120枚"])

    def test_shift_charts_share_scale(self):
        out = CH.month_charts("全", self.total, self.counts, _shift(self.w1, self.w2, self.w3), 30, 2022, 6, True)
        heads = [[it["text"] for it in p["items"] if it["t"] == "text" and it["cls"] == "axis"][-1] for p in out["shift"]]
        self.assertEqual(len(set(heads)), 1)                                # 3 枚とも同じ物差し
        self.assertEqual([p["legend"][0]["mark"] for p in out["shift"]], ["shift-1", "shift-2", "shift-3"])



class YearChart(unittest.TestCase):
    def _months(self, rows):
        out = []
        for k in range(12):
            y, m = (2022, 4 + k) if k < 9 else (2023, k - 8)
            w = rows.get(k, [0.0] * len(self.groups))
            out.append({"year": y, "month": m, "weight": w, "count": [int(x) for x in w]})
        return out

    def setUp(self):
        self.groups = [{"no": i, "name": f"G{i}"} for i in range(1, 15)]

    def test_top_nine_get_colors_rest_is_other(self):
        w = [float(100 * i) for i in range(1, 15)]                # G14 が一番多い
        w[0] = 0.0                                                # G1 は実績なし
        out = CH.year_charts(2022, self._months({2: w}), self.groups)
        marks = {int(k): v for k, v in out["marks"].items()}
        self.assertNotIn(1, marks)                                # 実績の無いグループは凡例にも出さない
        top = [no for no, m in marks.items() if m != "cat-10"]
        self.assertEqual(sorted(top), list(range(6, 15)))         # 多い 9 つ
        self.assertEqual([marks[no] for no in sorted(top)], [f"cat-{k}" for k in range(1, 10)])  # 番号の順に 1〜9 色目
        self.assertEqual({no for no, m in marks.items() if m == "cat-10"}, {2, 3, 4, 5})
        leg = out["weight"]["legend"]
        self.assertEqual(leg[-1]["mark"], "cat-10")
        self.assertTrue(leg[-1]["label"].startswith("その他（G2・G3・G4・G5"))
        self.assertEqual([g["mark"] for g in out["count"]["legend"]], [g["mark"] for g in leg])   # 枚数でも同じ色

    def test_bars_add_up_and_click_target(self):
        w = [0.0] * 14
        w[0], w[5] = 1200.0, 800.0
        p = CH.year_charts(2022, self._months({2: w, 9: [5.0] + [0.0] * 13}), self.groups)["weight"]
        self.assertEqual(p["total"], 2005.0)
        june = [it for it in p["items"] if it["t"] == "rect" and "bar" in it.get("cls", "")
                and LEFT_OF(it) == 2]
        self.assertEqual([it["cls"].split()[-1] for it in june], ["mark-cat-1", "mark-cat-2"])   # 下から番号の順
        vals = [it["text"] for it in p["items"] if it.get("cls") == "value"]
        self.assertEqual(vals, ["2.0t", "5kg"])
        hits = [it for it in p["items"] if it.get("cls") == "hit"]
        self.assertEqual(len(hits), 12)
        self.assertEqual(hits[2]["ym"], [2022, 6])
        self.assertIn("G1 1,200.0 kg", hits[2]["tip"])
        self.assertEqual(hits[9]["ym"], [2023, 1])
        months = [it["text"] for it in p["items"] if it["t"] == "text" and "axis--month" in it["cls"]]
        self.assertEqual(months, ["4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月", "1月", "2月", "3月"])

    def test_empty_year(self):
        p = CH.year_charts(2030, self._months({}), self.groups)
        self.assertEqual((p["weight"]["total"], p["weight"]["legend"], p["marks"]), (0, [], {}))


def LEFT_OF(it):
    step = (CH.W - CH.LEFT - 20.0) / 12
    return int((it["x"] - CH.LEFT) // step)


if __name__ == "__main__":
    unittest.main()
