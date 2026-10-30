"""起動の入口。

    python start_app.py                 画面を開く（Start.vbs / start.bat）
    python start_app.py --no-browser    画面のサーバーだけ起動
    python start_app.py --stop          動いている画面を終了（stop.bat）
    python start_app.py --import-workbook <旧ブック.xlsm> [--with-conditions]
                                        旧ブックの DATA・修正シート（・設定シート）を取り込む（移行用）
"""
from __future__ import annotations

import argparse
import json
import sys

from . import logsetup
from .settings import Paths, Settings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="actuals-dashboard", description="倉入実績集計")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--port", type=int)
    ap.add_argument("--import-workbook")
    ap.add_argument("--with-conditions", action="store_true")
    args = ap.parse_args(argv)
    paths = Paths.default()
    s = Settings(paths).resolved()
    console = bool(sys.stdout and getattr(sys.stdout, "isatty", lambda: False)())
    logsetup.setup(s["log_dir"], paths.logs, console=console, keep_days=int(s.get("log_keep_days") or 90))
    if args.stop:
        from .web.server import stop_running
        return stop_running(paths)
    if args.import_workbook:
        from .service import Service
        res = Service(paths).import_workbook(args.import_workbook, args.with_conditions)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    from .web.server import serve
    return serve(paths, open_browser=not args.no_browser, port=args.port)
