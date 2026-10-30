"""Python 側の起動開始点（Start.vbs / start.bat / stop.bat から呼ぶ）。標準ライブラリだけで動く。"""
import os
import sys
from pathlib import Path

MIN_PY = (3, 8)


def main() -> int:
    if sys.version_info < MIN_PY:
        print(f"Python {MIN_PY[0]}.{MIN_PY[1]} 以上が必要です（現在 {sys.version.split()[0]}）")
        return 2
    root = Path(__file__).resolve().parent
    local = os.environ.get("LOCALAPPDATA")
    if local:
        sys.pycache_prefix = str(Path(local) / "ActualsDashboard" / "pycache")
    sys.path.insert(0, str(root))
    from actuals.main import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
