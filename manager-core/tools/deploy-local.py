"""Explicit Windows deployment for the user's approved local Manager migration."""
import argparse
import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent
RUN = ROOT / "run"
STATE = RUN / "deployment.local.json"
SOURCE = Path.home() / ".antigravity-agent"
USER_DATA = Path(os.environ["APPDATA"]) / "Antigravity Manager"
MANAGER_EXE = Path(os.environ["LOCALAPPDATA"]) / "antigravity_manager/app-0.20.0/antigravity-manager.exe"
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def save(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def wait_for(check, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    raise RuntimeError("Local service readiness timed out")


def occupied(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def control(action=None):
    manifest = json.loads((RUN / "control.json").read_text(encoding="utf-8"))
    data = None if action is None else json.dumps({"action": action, "confirm": True}).encode()
    request = urllib.request.Request(manifest["url"] + ("/status" if data is None else "/action"),
                                     data=data, headers={"Authorization": "Bearer " + manifest["token"],
                                                         "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


def prepare():
    if STATE.exists():
        raise RuntimeError("Deployment already prepared")
    RUN.mkdir(exist_ok=True)
    # Windows chmod does not implement a private ACL. Restrict before copying secrets.
    identity = subprocess.check_output(["whoami"], text=True, creationflags=NO_WINDOW).strip()
    subprocess.run(["icacls", str(RUN), "/inheritance:r", "/grant:r",
                    identity + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], check=True,
                   stdout=subprocess.DEVNULL, creationflags=NO_WINDOW)
    backup = RUN / ("backup-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir()
    data = RUN / "data"
    user = RUN / "electron-user-data"
    data.mkdir()
    user.mkdir()
    config = SOURCE / "gui_config.json"
    shutil.copy2(config, backup / config.name)
    shutil.copy2(BASE / "config.json", backup / "relay-config.json")
    shutil.copy2(BASE / "cc_relay.py", backup / "relay-current.py")
    # SQLite backup includes committed WAL contents while the old process is still live.
    with sqlite3.connect((SOURCE / "cloud_accounts.db").as_uri() + "?mode=ro", uri=True) as source_db:
        with sqlite3.connect(backup / "cloud_accounts.db") as destination:
            source_db.backup(destination)
            count = destination.execute("select count(*) from accounts").fetchone()[0]
    for name in (".mk", "Local State"):
        original = USER_DATA / name
        if original.exists():
            shutil.copy2(original, backup / name)
            shutil.copy2(original, user / name)
    if not (user / ".mk").exists():
        raise RuntimeError("Existing encrypted master-key file is required")
    shutil.copy2(backup / "cloud_accounts.db", data / "cloud_accounts.db")
    shutil.copy2(backup / config.name, data / config.name)
    port = json.loads(config.read_text(encoding="utf-8"))["proxy"]["port"]
    save(STATE, {"backup": str(backup), "port": port, "expected_accounts": count,
                 "data": str(data), "user_data": str(user), "stage": "prepared"})
    print(json.dumps({"stage": "prepared", "accounts": count, "port": port,
                      "backup": str(backup)}))


def start(port):
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if occupied(port) or occupied(18446):
        raise RuntimeError("Target gateway or control port is already occupied")
    if (RUN / "control.json").exists():
        raise RuntimeError("Existing control manifest requires explicit inspection")
    config = json.loads((Path(state["data"]) / "gui_config.json").read_text(encoding="utf-8"))
    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.update(AGM_CORE_DATA_DIR=state["data"], AGM_CORE_USER_DATA=state["user_data"],
               AGM_CORE_API_KEY=config["proxy"]["api_key"], AGM_CORE_CONTROL_TOKEN=secrets.token_urlsafe(32),
               AGM_CORE_PORT=str(port), AGM_CORE_CONTROL_PORT="18446",
               AGM_CORE_CONTROL_FILE=str(RUN / "control.json"), AGM_CORE_HEADER_PROFILE="official")
    with (RUN / "runtime.stdout.log").open("ab") as out, (RUN / "runtime.stderr.log").open("ab") as err:
        child = subprocess.Popen([str(ROOT / "node_modules/electron/dist/electron.exe"), str(ROOT)],
                                 cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                 creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
    try:
        status = wait_for(control)
        if status["accounts_count"] != state["expected_accounts"] or not status["gateway"]["running"]:
            raise RuntimeError("Account pool or gateway did not survive migration")
        state.update(stage="running", pid=child.pid, active_port=port)
        save(STATE, state)
        print(json.dumps({"stage": "running", "pid": child.pid, "port": port,
                          "accounts": status["accounts_count"], "mode": status["scheduling_mode"],
                          "header_profile": status["header_profile"]}))
    except Exception:
        child.terminate()
        child.wait(timeout=10)
        raise


def stop():
    control("stop")
    wait_for(lambda: not (RUN / "control.json").exists())
    print("Core stopped")


def probe():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    config = json.loads((Path(state["data"]) / "gui_config.json").read_text(encoding="utf-8"))
    payload = {"model": "gemini-3.8-flash-high", "max_tokens": 128,
               "metadata": {"user_id": "manager-core-deployment-check-" + secrets.token_hex(8)},
               "messages": [{"role": "user", "content": "Reply with exactly OK."}]}
    request = urllib.request.Request("http://127.0.0.1:" + str(state["active_port"]) + "/v1/messages",
                                     data=json.dumps(payload).encode(),
                                     headers={"x-api-key": config["proxy"]["api_key"],
                                              "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=150) as response:
        result = json.load(response)
        text = "".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")
        print(json.dumps({"http_status": response.status, "has_text": bool(text), "usage": result.get("usage"),
                          "stop_reason": result.get("stop_reason")}))
        if not text:
            raise RuntimeError("Inference returned no text")


def port_processes(ports):
    script = "$ports = @(" + ",".join(map(str, ports)) + "); " + """
    @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -in $ports |
      Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Get-CimInstance Win32_Process -Filter "ProcessId = $_" |
          Select-Object ProcessId,ExecutablePath,CommandLine
      }) | ConvertTo-Json -Compress
    """
    output = subprocess.check_output(["powershell", "-NoProfile", "-Command", script],
                                     text=True, creationflags=NO_WINDOW).strip()
    value = json.loads(output) if output else []
    return value if isinstance(value, list) else [value]


def terminate_pid(pid):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)


def launch_relay():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    with (RUN / "relay.stdout.log").open("ab") as out, (RUN / "relay.stderr.log").open("ab") as err:
        subprocess.Popen([sys.executable, str(BASE / "cc_relay.py"), "serve"], cwd=BASE, env=env,
                         stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                         creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
    wait_for(lambda: occupied(8400) and occupied(8610))


def activate():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    original = port_processes([state["port"]])
    relays = port_processes([8400, 8610])
    if len(original) != 1 or Path(original[0]["ExecutablePath"]).resolve() != MANAGER_EXE.resolve():
        raise RuntimeError("Unexpected production Manager process")
    for relay in relays:
        if not any(str(BASE / name).lower() in relay["CommandLine"].lower() or
                   (".\\" + name + " serve") in relay["CommandLine"]
                   for name in ("cc_relay.py", "cc_relay.original.py")):
            raise RuntimeError("Unexpected relay process")
    stop()
    terminate_pid(original[0]["ProcessId"])
    wait_for(lambda: not occupied(state["port"]))
    try:
        # Take a final consistent snapshot after the old writer has stopped.
        backup = Path(state["backup"]) / "cutover-cloud_accounts.db"
        with sqlite3.connect((SOURCE / "cloud_accounts.db").as_uri() + "?mode=ro", uri=True) as source_db:
            with sqlite3.connect(backup) as destination:
                source_db.backup(destination)
        shutil.copy2(backup, Path(state["data"]) / "cloud_accounts.db")
        shutil.copy2(SOURCE / "gui_config.json", Path(state["data"]) / "gui_config.json")
        start(state["port"])
        probe()
        for relay in relays:
            terminate_pid(relay["ProcessId"])
        wait_for(lambda: not occupied(8400) and not occupied(8610))
        launch_relay()
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                "http://127.0.0.1:8610/api/manager/status", timeout=5) as response:
            if json.load(response).get("available") is not True:
                raise RuntimeError("Web Manager bridge did not start")
        state = json.loads(STATE.read_text(encoding="utf-8"))
        state.update(stage="active", activated_at=datetime.datetime.now().isoformat())
        save(STATE, state)
        print("Activated original-core gateway and integrated web UI")
    except Exception:
        try:
            stop()
        except Exception:
            pass
        if not occupied(state["port"]):
            subprocess.Popen([str(MANAGER_EXE)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW | subprocess.DETACHED_PROCESS)
        if not occupied(8400) and not occupied(8610):
            launch_relay()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "start", "stop", "status", "probe", "activate"))
    parser.add_argument("--port", type=int, default=8045)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare()
        elif args.action == "start":
            start(args.port)
        elif args.action == "stop":
            stop()
        elif args.action == "probe":
            probe()
        elif args.action == "activate":
            activate()
        else:
            status = control()
            print(json.dumps({key: status[key] for key in ("accounts_count", "gateway", "scheduling_mode", "header_profile", "body_limit_bytes")}))
    except Exception as error:
        print("Deployment failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
