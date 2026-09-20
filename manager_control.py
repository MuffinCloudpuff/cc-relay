"""Local-only bridge to the Antigravity Manager control service.

The control token is deliberately confined to this module.  Browser callers
receive only the small, safe status shape defined by manager-core/CONTRACT.md.
"""
import http.client
import ipaddress
import json
import math
import os
from urllib.parse import urlsplit


DEFAULT_CONTROL_RELATIVE_PATH = os.path.join("manager-core", "run", "control.json")
MAX_MANIFEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 2
ACTION_TIMEOUT_SECONDS = 65
UNAVAILABLE_MESSAGE = "Manager unavailable"
ALLOWED_ACTIONS = frozenset(("next-account", "refresh-current", "stop"))


class ManagerControlError(Exception):
    """An expected local-control failure that must not expose internals."""


def unavailable_status():
    return {"available": False, "running": False, "error": UNAVAILABLE_MESSAGE}


def _loopback_host(host):
    """Return a numeric loopback host without consulting a configurable resolver."""
    if not host:
        raise ManagerControlError()
    host = host.strip().lower()
    # Avoid resolving localhost through an altered hosts/DNS configuration.
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ManagerControlError()
    if not address.is_loopback:
        raise ManagerControlError()
    return str(address)


def _control_target(url):
    """Return a validated loopback target.  Manifest URLs are base URLs only."""
    if not isinstance(url, str) or len(url) > 2048:
        raise ManagerControlError()
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ("", "/") or
            not parsed.hostname):
        raise ManagerControlError()
    try:
        port = parsed.port or 80
    except ValueError:
        raise ManagerControlError()
    if not 1 <= port <= 65535:
        raise ManagerControlError()
    return _loopback_host(parsed.hostname), port


def _read_manifest(path):
    try:
        if not isinstance(path, str) or not path:
            raise ManagerControlError()
        if os.path.getsize(path) > MAX_MANIFEST_BYTES:
            raise ManagerControlError()
        with open(path, "rb") as manifest_file:
            raw = manifest_file.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ManagerControlError()
        manifest = json.loads(raw.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ManagerControlError()
        token = manifest.get("token")
        if (not isinstance(token, str) or not token or len(token) > 4096 or
                "\r" in token or "\n" in token):
            raise ManagerControlError()
        host, port = _control_target(manifest.get("url"))
        return host, port, token
    except (OSError, UnicodeDecodeError, ValueError, TypeError, ManagerControlError):
        raise ManagerControlError()


def _read_limited(response):
    data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ManagerControlError()
    return data


def _safe_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 <= value <= 100 else None


def _safe_status(payload):
    """Whitelist the public control schema instead of forwarding arbitrary JSON."""
    if (not isinstance(payload, dict) or payload.get("available") is not True or
            not isinstance(payload.get("running"), bool)):
        raise ManagerControlError()

    account = payload.get("current_account")
    if account is None:
        safe_account = None
    elif isinstance(account, dict):
        account_id, email, models = account.get("id"), account.get("email"), account.get("models")
        if not isinstance(account_id, str) or not isinstance(email, str) or not isinstance(models, list):
            raise ManagerControlError()
        safe_models = []
        for model in models:
            if not isinstance(model, dict):
                raise ManagerControlError()
            name, percentage, reset_time = model.get("name"), _safe_number(model.get("percentage")), model.get("reset_time")
            if not isinstance(name, str) or percentage is None or not (reset_time is None or isinstance(reset_time, str)):
                raise ManagerControlError()
            safe_models.append({"name": name, "percentage": percentage, "reset_time": reset_time})
        safe_account = {"id": account_id, "email": email, "models": safe_models}
    else:
        raise ManagerControlError()

    gateway = payload.get("gateway")
    if not isinstance(gateway, dict):
        raise ManagerControlError()
    gateway_running, gateway_port, active_accounts = gateway.get("running"), gateway.get("port"), gateway.get("active_accounts")
    if (not isinstance(gateway_running, bool) or not isinstance(gateway_port, int) or isinstance(gateway_port, bool) or
            not isinstance(active_accounts, int) or isinstance(active_accounts, bool)):
        raise ManagerControlError()
    accounts_count, scheduling_mode, header_profile, body_limit_bytes = (
        payload.get("accounts_count"), payload.get("scheduling_mode"),
        payload.get("header_profile"), payload.get("body_limit_bytes"))
    if (not isinstance(accounts_count, int) or isinstance(accounts_count, bool) or
            not isinstance(scheduling_mode, str) or not isinstance(header_profile, str) or
            not isinstance(body_limit_bytes, int) or isinstance(body_limit_bytes, bool)):
        raise ManagerControlError()
    return {
        "available": True,
        "running": payload["running"],
        "current_account": safe_account,
        "accounts_count": accounts_count,
        "gateway": {"running": gateway_running, "port": gateway_port, "active_accounts": active_accounts},
        "scheduling_mode": scheduling_mode,
        "header_profile": header_profile,
        "body_limit_bytes": body_limit_bytes,
    }


class ManagerControl:
    def __init__(self, control_file):
        self.control_file = control_file

    def _request(self, method, path, payload=None, timeout=REQUEST_TIMEOUT_SECONDS):
        host, port, token = _read_manifest(self.control_file)
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        connection = None
        try:
            # HTTPConnection is used directly: it neither consults proxy settings nor follows redirects.
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = _read_limited(response)
            if response.status < 200 or response.status >= 300:
                raise ManagerControlError()
            return json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError, http.client.HTTPException, ManagerControlError):
            raise ManagerControlError()
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def status(self):
        try:
            return _safe_status(self._request("GET", "/status"))
        except ManagerControlError:
            return unavailable_status()

    def action(self, action, confirm=False):
        if action not in ALLOWED_ACTIONS or not isinstance(confirm, bool):
            return {"ok": False, "error": "Invalid Manager action"}
        if action == "stop" and not confirm:
            return {"ok": False, "error": "Stop requires confirmation"}
        try:
            reply = self._request("POST", "/action", {"action": action, "confirm": confirm},
                                  timeout=ACTION_TIMEOUT_SECONDS)
            if not isinstance(reply, dict) or reply.get("ok") is not True:
                raise ManagerControlError()
            result = {"ok": True}
            if isinstance(reply.get("status"), dict):
                result["status"] = _safe_status(reply["status"])
            return result
        except ManagerControlError:
            return {"ok": False, "error": UNAVAILABLE_MESSAGE}


def control_from_config(conf, base_dir):
    configured = (conf or {}).get("manager_core_control_file")
    control_file = configured or os.path.join(base_dir, DEFAULT_CONTROL_RELATIVE_PATH)
    return ManagerControl(control_file)
