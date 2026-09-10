# -*- coding: utf-8 -*-
"""
cc-relay 兜底守护 (由 Windows 计划任务每分钟调用, 完成即退出)
规则: 只要 claude 在跑, 中转(8400)不在就拉起 serve; watch 不在也补拉起。
claude 全退时不干预(正常收尾由 lifecycle watch 负责)。
"""
import os, sys, time, socket, subprocess

BASE = os.environ.get("CC_RELAY_DIR") or os.path.dirname(os.path.abspath(__file__))
PYW = os.environ.get("CC_RELAY_PYTHONW") or sys.executable.replace("python.exe","pythonw.exe")
RELAY_PORT = 8400
WATCH_PID = os.path.join(BASE, ".watch.pid")


def tcp(port, t=0.6):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=t); s.close(); return True
    except Exception:
        return False


def claude_count():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe"],
                             capture_output=True, text=True, timeout=15,
                             encoding="utf-8", errors="replace",
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return sum(1 for l in out.splitlines() if l.strip().lower().startswith("claude.exe"))
    except Exception:
        return 0


def watch_alive():
    try:
        pid = int(open(WATCH_PID).read().strip())
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True,
                             text=True, timeout=15, encoding="utf-8", errors="replace",
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return str(pid) in out
    except Exception:
        return False


def start_serve():
    subprocess.Popen([PYW, "cc_relay.py", "serve"], cwd=BASE,
                     creationflags=subprocess.CREATE_NO_WINDOW)


def start_watch():
    subprocess.Popen([PYW, "lifecycle.py", "watch"], cwd=BASE,
                     creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    c = claude_count()
    if c <= 0:
        return  # claude 不在, 不干预(收尾由 watch 管)
    if not tcp(RELAY_PORT):
        start_serve()
        # 等待最多 15s 确认起来
        for _ in range(30):
            time.sleep(0.5)
            if tcp(RELAY_PORT):
                break
    if not watch_alive():
        start_watch()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
