#!/usr/bin/env python3
"""デスクトップ版（exe）を本当に起動して確かめる（GitHub Actions の Windows でも流す。python-web-tools と同じ）

確かめること:
    1. 窓の画面（WebView）が Python から届く
       ── 動いた記録に「画面を開きました（デスクトップ版）」が出る（WebView → 外枠 → Python → 外枠 → WebView が 1 周した証拠）
    2. **どのプロセスもポートで待ち受けていない**（exe・Python・WebView の子プロセス）
    3. 2 回目に起動した exe は窓を出さずにすぐ終わり、1 つ目は動いたまま
    4. デスクトップ版が動いている間、ブラウザ版（start_app.py）は起動せずに止まる（戻り値 5）。デスクトップ版はそのまま
    5. Windows: stop.bat（STOP_NOPAUSE=1）で止めると、窓が終了の確かめを通って自分で終わる
    6. exe を止めると Python も終わる（取り残さない）

使い方:
    python scripts/desktop_smoke.py --exe src-tauri/target/release/ActualsDashboard.exe
    （Linux では DISPLAY が要る。例: xvfb-run python scripts/desktop_smoke.py --exe ...）

作業用のフォルダ（設定・共有・ログ）は一時フォルダに作る。本番の領域は触らない。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"


def children(pid: int) -> List[int]:
    """pid の子孫(孫も)。

    **親より先に生まれたプロセスは子として数えない。** Windows は終わったプロセスの番号を
    使い回し、子の側の「親の番号」は書き換えない。親が先に終わった無関係なプロセス
    (CI では exe を組み立てたときの vctip)の「親の番号」が、たまたまこの exe の子孫の
    番号と同じになると、子孫に見えてしまう(「止めても残ったプロセスがあります: [vctip]」で落ちた)。
    """
    if WINDOWS:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object"
             " { \"$($_.ProcessId) $($_.ParentProcessId) $($_.CreationDate.Ticks)\" }"],
            capture_output=True, text=True).stdout
        rows = []
        for line in out.splitlines():
            cols = line.split()
            if len(cols) >= 2 and all(c.isdigit() for c in cols[:2]):
                born = int(cols[2]) if len(cols) > 2 and cols[2].isdigit() else 0
                rows.append((int(cols[0]), int(cols[1]), born))
    else:
        rows = []
        for name in os.listdir("/proc"):
            if name.isdigit():
                try:
                    fields = Path(f"/proc/{name}/stat").read_text().rsplit(")", 1)[1].split()
                    rows.append((int(name), int(fields[1]), int(fields[19])))
                except (OSError, ValueError, IndexError):
                    pass
    born_at = {child: born for child, _, born in rows}
    found, frontier = [], [pid]
    while frontier:
        parent = frontier.pop()
        for child, ppid, born in rows:
            if ppid == parent and child not in found and born >= born_at.get(parent, 0):
                found.append(child)
                frontier.append(child)
    return found


def listening_ports(pids: Set[int]) -> dict[int, List[int]]:
    """その pid たちが待ち受けている TCP ポート。"""
    result: dict[int, List[int]] = {}
    if WINDOWS:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        out += subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            cols = line.split()
            if len(cols) >= 5 and cols[3].upper() == "LISTENING" and cols[4].isdigit():
                pid = int(cols[4])
                if pid in pids:
                    result.setdefault(pid, []).append(int(cols[1].rsplit(":", 1)[1]))
        return result
    inodes: dict[str, int] = {}
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        if os.path.exists(name):
            for line in Path(name).read_text().splitlines()[1:]:
                cols = line.split()
                if cols[3] == "0A":
                    inodes[cols[9]] = int(cols[1].split(":")[1], 16)
    for pid in pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                result.setdefault(pid, []).append(inodes[target[8:-1]])
    return result


def alive(pid: int) -> bool:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    return os.path.exists(f"/proc/{pid}")


def visible_windows(pids: Set[int]) -> List[int]:
    """それらのプロセスが持つ、見えている最上位の窓(持ち主のいる窓は除く)。

    Windows だけ。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    found: List[int] = []

    def visit(hwnd, _param):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):   # 4 = GW_OWNER
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in pids:
                found.append(hwnd)
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return found


def ask_to_close(pids: Set[int]) -> int:
    """窓に WM_CLOSE を送る(×ボタンと同じ)。送った窓の数。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return sum(1 for hwnd in visible_windows(pids) if user32.PostMessageW(hwnd, 0x0010, 0, 0))


def second_launch_exits(exe: str, env: dict, first: subprocess.Popen) -> bool:
    """2回目の起動は窓を出さずに終わり、1つ目は動いたままか。"""
    second = subprocess.Popen([exe], env=env, cwd=str(ROOT))
    try:
        code = second.wait(timeout=30)
    except subprocess.TimeoutExpired:
        second.kill()
        print("[NG] 2回目に起動した exe が30秒たっても終わりません(2つ動いています)")
        return False
    if code != 0:
        print(f"[NG] 2回目に起動した exe の戻り値が {code} でした(0 のはず)")
        return False
    if first.poll() is not None:
        print(f"[NG] 2回目を起動したら1つ目が終わりました(戻り値 {first.returncode})")
        return False
    print("[OK] 2回目に起動した exe はすぐ終わり(戻り値 0)、1つ目は動いたままです")
    return True


def second_launch_exits(exe: str, env: dict, first: subprocess.Popen) -> bool:
    second = subprocess.Popen([exe], env=env, cwd=str(ROOT))
    try:
        code = second.wait(timeout=30)
    except subprocess.TimeoutExpired:
        second.kill()
        print("[NG] 2 回目に起動した exe が 30 秒たっても終わりません（2 つ動いています）")
        return False
    if first.poll() is not None:
        print(f"[NG] 2 回目を起動したら 1 つ目が終わりました（戻り値 {first.returncode}）")
        return False
    print(f"[OK] 2 回目に起動した exe はすぐ終わり（戻り値 {code}）、1 つ目は動いたままです")
    return True


def browser_refused(env: dict, first: subprocess.Popen) -> bool:
    r = subprocess.run([sys.executable, str(ROOT / "start_app.py"), "--no-browser", "--port", "0"], env=env,
                       cwd=str(ROOT), capture_output=True, timeout=120)
    if r.returncode != 5:
        print(f"[NG] デスクトップ版が動いているのに、ブラウザ版の戻り値が {r.returncode} でした（5 のはず）")
        return False
    if first.poll() is not None:
        print("[NG] ブラウザ版を起動したら、デスクトップ版が終わりました")
        return False
    print("[OK] デスクトップ版が動いている間、ブラウザ版は起動せずに止まりました（デスクトップ版はそのまま）")
    return True


def text_log(work: Path) -> str:
    logs = sorted((work / "home" / "logs" / "actualsdashboard").glob("actualsdashboard_*.log"))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in logs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="actuals_desktop_smoke_"))
    env = dict(os.environ, ACTUALS_ROOT=str(ROOT), ACTUALS_DASHBOARD_HOME=str(work / "home"))
    if os.environ.get("SMOKE_PYTHON"):
        env["ACTUALS_PYTHON"] = os.environ["SMOKE_PYTHON"]
    started = time.monotonic()
    app = subprocess.Popen([args.exe], env=env, cwd=str(ROOT))
    print(f"起動しました: pid={app.pid} 作業={work}", flush=True)

    ok, seen = True, False
    while time.monotonic() - started < args.timeout:
        if app.poll() is not None:
            print(f"[NG] exe が先に終わりました（終了コード {app.returncode}）")
            ok = False
            break
        if "画面を開きました（デスクトップ版）" in text_log(work):
            seen = True
            break
        time.sleep(0.5)
    if seen:
        print(f"[OK] 画面が Python から届きました（{time.monotonic() - started:.1f} 秒）")
    elif ok:
        print("[NG] 時間内に画面が届きませんでした。ログの末尾:")
        for log in sorted((work / "home" / "logs").rglob("*.log")):
            print(f"--- {log.name}")
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]))
        ok = False

    if app.poll() is None:
        time.sleep(2)
        tree = {app.pid, *children(app.pid)}
        ports = listening_ports(tree)
        if ports:
            print(f"[NG] 待ち受けているプロセスがあります: {ports}")
            ok = False
        else:
            print(f"[OK] 待ち受けはありません（調べたプロセス {len(tree)} 個）")
        pythons = [pid for pid in tree if pid != app.pid]
        ok = second_launch_exits(args.exe, env, app) and ok
        ok = browser_refused(env, app) and ok
        closed = False
        if WINDOWS:
            stop = subprocess.run(["cmd", "/c", str(ROOT / "stop.bat")], env=dict(env, STOP_NOPAUSE="1"),
                                  cwd=str(ROOT), capture_output=True, timeout=120)
            try:
                code = app.wait(timeout=30)
            except subprocess.TimeoutExpired:
                code = None
            if stop.returncode == 0 and code == 0:
                closed = True
                print("[OK] stop.bat（STOP_NOPAUSE=1）で、窓が終了の確かめを通って自分で終わりました（戻り値 0）")
            else:
                print(f"[NG] stop.bat で止まりませんでした（stop.bat の戻り値 {stop.returncode}・exe の戻り値 {code}）")
                print(stop.stdout.decode("cp932", "replace")[-2000:])
                ok = False
        if not closed and app.poll() is None:
            app.terminate()
        app.wait(timeout=30)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and any(alive(p) for p in pythons):
            time.sleep(0.5)
        left = [p for p in pythons if alive(p)]
        if left:
            print(f"[NG] exe を止めても残ったプロセスがあります: {left}")
            ok = False
        else:
            print("[OK] exe を止めたら子プロセス（Python など）も終わりました")
    print("結果:", "OK" if ok else "NG")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
