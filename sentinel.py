# -*- coding: utf-8 -*-
"""盯梢: 每 10 秒记录 serve/watch 存活状态与 PID 到 sentinel.log (侦查用, python.exe 跑)"""
import os, time, socket, subprocess, datetime

BASE = os.environ.get("CC_RELAY_DIR") or os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "sentinel.log")
WATCH_PID = os.path.join(BASE, ".watch.pid")


def tcp(port, t=0.5):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=t); s.close(); return True
    except Exception:
        return False


def alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True,
                             text=True, timeout=10, encoding="utf-8", errors="replace",
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return str(pid) in out
    except Exception:
        return False


def pid_of(needle):
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
             "Where-Object { $_.CommandLine -like '*%s*' } | ForEach-Object { $_.ProcessId }" % needle],
            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def main():
    t0 = time.time()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("=== sentinel start %s ===\n" % datetime.datetime.now().isoformat())
    while time.time() - t0 < 5400:  # 90 分钟
        relay = tcp(8400)
        ui = tcp(8610)
        serve_pids = pid_of("cc_relay.py") if relay else []
        watch_pid = None
        try:
            watch_pid = int(open(WATCH_PID).read().strip())
        except Exception:
            pass
        watch_ok = watch_pid and alive(watch_pid)
        st = "%s relay=%s ui=%s serve_pids=%s watch=%s(%s)" % (
            datetime.datetime.now().strftime("%H:%M:%S"), relay, ui,
            serve_pids if serve_pids else "-", watch_pid or "-",
            "alive" if watch_ok else "DEAD" if watch_pid else "nofile")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(st + "\n")
        time.sleep(10)


if __name__ == "__main__":
    main()
