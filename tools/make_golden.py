"""受領ブックから照合用の正解データ（tests/fixtures/golden_*.json）を作る。

    python tools/make_golden.py <旧ブック.xlsm> tests/fixtures/golden_202206.json

旧 VBA が保存した結果（修正・月集計・ｸﾞﾗﾌ シートの値）をそのまま書き出す。
Python 側の判定・集計がこれと 1 件もずれないことを tests/test_golden.py が確かめる。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actuals import xlsm  # noqa: E402


def _j(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


def main(src: str, dst: str) -> int:
    with xlsm.Workbook(Path(src)) as wb:
        header, rows = xlsm.read_data_rows(wb)
        cs = xlsm.read_conditions(wb)
        overrides = xlsm.read_overrides(wb)
        n = len(rows)
        fix = {f"{r}": {"auto": wb.value("修正", r, 2), "year": wb.value("修正", r, 5),
                        "month": wb.value("修正", r, 6), "day": wb.value("修正", r, 7),
                        "unit_w": wb.value("修正", r, 8), "count": wb.value("修正", r, 9),
                        "lot": wb.value("修正", r, 11), "choku": wb.value("修正", r, 23)}
               for r in range(2, n + 2)}
        month = {"label": wb.value("月集計", 2, 4), "calendar": wb.value("月集計", 2, 2),
                 "names": [wb.value("月集計", r, 4) for r in range(4, 19)],
                 "weight": [[wb.value("月集計", r, c) for c in range(6, 37)] for r in range(4, 19)],
                 "count": [[wb.value("月集計", r, c) for c in range(6, 37)] for r in range(20, 35)],
                 "day_weight": [wb.value("月集計", 3, c) for c in range(6, 37)],
                 "day_count": [wb.value("月集計", 19, c) for c in range(6, 37)]}
        sel_row = next(r for r in range(3, 19) if wb.value("ｸﾞﾗﾌ", r, 2) == "レ")
        last = wb.max_row("ｸﾞﾗﾌ", 27)
        graph = {"selected_row": sel_row,
                 "detail": [[_j(wb.value("ｸﾞﾗﾌ", r, c)) for c in range(23, 48)] for r in range(3, last + 1)],
                 "shift": [[wb.value("ｸﾞﾗﾌ", r, c) or 0 for c in range(10, 16)] for r in range(3, 34)],
                 "total": [[wb.value("ｸﾞﾗﾌ", r, c) or 0 for c in (8, 9)] for r in range(3, 34)],
                 "hits": [wb.value("ｸﾞﾗﾌ", r, 5) for r in range(3, 19)]}
    out = {"source": Path(src).name, "header": header,
           "rows": [{k: _j(v) for k, v in r.items()} for r in rows],
           "conditions": cs.to_dict(), "overrides": overrides,
           "expected": {"fix": fix, "month": month, "graph": graph}}
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{dst}: rows={len(rows)} overrides={len(overrides)} detail={len(graph['detail'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
