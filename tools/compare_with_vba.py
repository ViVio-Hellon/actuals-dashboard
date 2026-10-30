"""旧ツールで集計したブック（.xlsm）と、Web 版の判定・集計を突き合わせる（並行運用の確認用）。

    python tools/compare_with_vba.py <旧ツールで集計したブック.xlsm>

1. 旧 VBA と同じ判定方式で、修正シート（自動グループ・単重・枚数・直・年月日）と月集計が一致するか
2. 修正版の判定方式（Web 版の既定）で、どのロットの振り分けが変わるか
を表示する。終了コード: 0 = 1 と 2 の結果が想定どおり（1 が全件一致）、1 = 1 に差がある。
標準ライブラリだけで動く（ラインPCでそのまま実行できる）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actuals import aggregate as A  # noqa: E402
from actuals import xlsm  # noqa: E402
from actuals.engine import GENBA, TAIYO  # noqa: E402


def main(path: str) -> int:
    with xlsm.Workbook(Path(path)) as wb:
        header, rows = xlsm.read_data_rows(wb)
        cs = xlsm.read_conditions(wb)
        overrides = xlsm.read_overrides(wb)
        cal = TAIYO if (wb.value("月集計", 2, 2) or "").startswith("太陽") else GENBA
        label = str(wb.value("月集計", 2, 4) or "")
        fix = [{"auto": wb.value("修正", r, 2), "unit_w": wb.value("修正", r, 8), "count": wb.value("修正", r, 9),
                "choku": wb.value("修正", r, 23), "lot": wb.value("修正", r, 11),
                "ymd": (wb.value("修正", r, 5), wb.value("修正", r, 6), wb.value("修正", r, 7))}
               for r in range(2, len(rows) + 2)]
        mw = [[wb.value("月集計", r, c) for c in range(6, 37)] for r in range(4, 19)]
        mc = [[wb.value("月集計", r, c) for c in range(6, 37)] for r in range(20, 35)]
    print(f"ブック: {Path(path).name}  行: {len(rows)}  手動訂正: {len(overrides)}  暦: {cal}  集計月: {label}")
    legacy = cs.vba_compatible()
    got = A.make_rows(rows, legacy, overrides)
    diffs = []
    for r, e in zip(got, fix):
        checks = [("ロット", r.lot.lot, e["lot"]), ("自動グループ", r.auto, e["auto"]), ("計算単重", r.lot.unit_w, e["unit_w"]),
                  ("枚数", r.lot.count, e["count"]), ("年月日", r.lot.ymd(cal), e["ymd"])]
        if cal == GENBA:
            checks.append(("直", r.lot.choku, e["choku"]))
        for name, a, b in checks:
            if isinstance(a, float) and isinstance(b, (int, float)):
                same = abs(a - b) < 1e-9
            else:
                same = a == b
            if not same:
                diffs.append(f"  {r.lot.lot} {name}: Web版={a!r} 旧ツール={b!r}")
    print(f"[1] 修正シート（旧 VBA と同じ判定方式）: {'全件一致' if not diffs else f'{len(diffs)} 件の差'}")
    for d in diffs[:50]:
        print(d)
    mdiff = 0
    if "/" in label:
        y, m = map(int, label.split("/"))
        mat = A.month_matrix(got, legacy.names(), cal, y, m)
        for gi, g in enumerate(mat["groups"]):
            if not g["named"]:
                continue
            for d in range(31):
                if abs(g["weight"][d] - (mw[gi][d] or 0)) > 1e-6 or g["count"][d] != (mc[gi][d] or 0):
                    mdiff += 1
                    if mdiff <= 20:
                        print(f"  月集計 {g['name']} {d + 1}日: Web版 {g['weight'][d]} / {g['count'][d]}  "
                              f"旧ツール {mw[gi][d]} / {mc[gi][d]}")
        print(f"[2] 月集計（{label}）: {'全セル一致' if not mdiff else f'{mdiff} セルの差'}")
    fixed = A.make_rows(rows, cs, overrides)
    names = cs.names()
    changed = [(o, n) for o, n in zip(got, fixed) if o.auto != n.auto]
    print(f"[3] 修正版の判定方式で振り分けが変わるロット: {len(changed)} 件")
    for o, n in changed[:100]:
        a = f"{o.auto} {names[o.auto - 1]}" if o.auto else "未分類"
        b = f"{n.auto} {names[n.auto - 1]}" if n.auto else "未分類"
        print(f"  {o.lot.lot}: {a} → {b}（重量 {o.lot.weight}）")
    return 0 if not diffs and not mdiff else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
