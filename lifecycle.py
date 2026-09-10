#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cc-relay 生命周期管理 (被 claude wrapper 调用)
    autostart : 确保中转+UI 运行 (未跑则拉起); 退出码 0 = 刚启动(可开浏览器)
    watch     : 单实例看门狗; 观察到 claude 运行过→归零 → 停中转+codex上游
    stopall   : 立即停中转+codex
    status    : 打印状态
"""
import os, sys, time, json, socket, subprocess

BASE = os.environ.get("CC_RELAY_DIR") or os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(BASE, "config.json")
RELAY = os.path.join(BASE, "cc_relay.py")
PYW = os.environ.get("CC_RELAY_PYTHONW") or sys.executable.replace("python.exe","pythonw.exe")
PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
CODEX_PORT = 8317
RELAY_PORT = 8400
UI_PORT = 8610
WATCH_PID = os.path.join(BASE, ".watch.pid")


def tcp(port, host="127.0.0.1", t=0.6):
    try:
        s = socket.create_connection((host, port), timeout=t); s.close(); return True
    except Exception:
        return False


def relay_up():
    return tcp(RELAY_PORT)


def _pythonw_pids_like(needle):
    try:
        out = subprocess.run(
            [PS, "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
             "Where-Object { $_.CommandLine -like '*%s*' } | ForEach-Object { $_.ProcessId }" % needle],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def _pids_on_ports(ports):
    """按端口占用反查 PID (比命令行匹配可靠)"""
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace",
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        for line in out.splitlines():
            if "LISTENING" not in line:
                continue
            for p in ports:
                if (":%d " % p) in line or line.rstrip().endswith(":%d" % p):
                    toks = line.split()
                    if toks and toks[-1].isdigit():
                        pids.add(int(toks[-1]))
    except Exception:
        pass
    return pids


def relay_start():
    """启动 cc-relay (含 UI); 返回 True=刚启动"""
    if relay_up():
        return False
    # 按端口 + 命令行双重清掉僵尸实例
    for pid in _pids_on_ports([RELAY_PORT, UI_PORT]):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    for pid in _pythonw_pids_like("cc_relay"):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(1.2)
    if tcp(RELAY_PORT):
        return False  # 端口仍被占, 让现有的用
    subprocess.Popen([PYW, RELAY, "serve"], cwd=BASE, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(30):
        time.sleep(0.4)
        if relay_up():
            return True
    return False


def relay_stop():
    for pid in _pids_on_ports([RELAY_PORT, UI_PORT]) | set(_pythonw_pids_like("cc_relay")):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return True


def codex_stop():
    subprocess.run(["taskkill", "/F", "/IM", "cli-proxy-api.exe"], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def claude_count():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe"],
                             capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace",
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return sum(1 for l in out.splitlines() if l.strip().lower().startswith("claude.exe"))
    except Exception:
        return 0


def _watch_alive():
    try:
        pid = int(open(WATCH_PID).read().strip())
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True,
                             text=True, timeout=15, encoding="utf-8", errors="replace",
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return str(pid) in out
    except Exception:
        return False


def cmd_autostart():
    started = relay_start()
    return 0 if started else 3


def cmd_watch():
    if _watch_alive():
        return
    with open(WATCH_PID, "w") as f:
        f.write(str(os.getpid()))
    was_running = False
    idle_since = time.time()
    _last_resurrect = 0.0
    try:
        while True:
            c = claude_count()
            # 保活: claude 在跑而中转不在 -> 自动拉起 (防抖 15s, 避免反复拉起风暴)
            if c > 0 and not relay_up() and time.time() - _last_resurrect > 15:
                relay_start()
                _last_resurrect = time.time()
            if was_running and c == 0:
                time.sleep(6)
                if claude_count() == 0:
                    relay_stop()
                    codex_stop()
                    break
                was_running = False
            if c > 0:
                was_running = True
                idle_since = time.time()
            elif not was_running and time.time() - idle_since > 90:
                relay_stop()
                codex_stop()
                break
            time.sleep(3)
    finally:
        try:
            os.remove(WATCH_PID)
        except Exception:
            pass


def cmd_stopall():
    relay_stop()
    codex_stop()


def cmd_status():
    print(json.dumps({"relay_up": relay_up(), "ui_up": tcp(UI_PORT),
                      "codex_up": tcp(CODEX_PORT), "claude_procs": claude_count()}, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "autostart":
        sys.exit(cmd_autostart())
    elif cmd == "watch":
        cmd_watch()
    elif cmd == "stopall":
        cmd_stopall()
    else:
        cmd_status()
