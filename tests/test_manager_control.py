import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cc_relay
import manager_control


SAFE_STATUS = {
    "available": True,
    "running": True,
    "current_account": {"id": "account-1", "email": "user@example.test", "models": [
        {"name": "gemini-test", "percentage": 72, "reset_time": "2026-09-20T12:00:00Z"}]},
    "accounts_count": 2,
    "gateway": {"running": True, "port": 8402, "active_accounts": 1},
    "scheduling_mode": "round-robin",
    "header_profile": "official",
    "body_limit_bytes": 33554432,
}


class SyntheticManager(BaseHTTPRequestHandler):
    requests = []
    reply = SAFE_STATUS
    action_reply = {"ok": True, "status": SAFE_STATUS}

    def log_message(self, *args):
        pass

    def _reply(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests.append((self.command, self.path, self.headers.get("Authorization"), None))
        self._reply(type(self).reply)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).requests.append((self.command, self.path, self.headers.get("Authorization"), json.loads(raw)))
        self._reply(type(self).action_reply)


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticManager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        SyntheticManager.requests = []
        SyntheticManager.reply = SAFE_STATUS
        SyntheticManager.action_reply = {"ok": True, "status": SAFE_STATUS}

    def manifest(self, directory, url=None):
        manifest_path = os.path.join(directory, "control.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump({"url": url or "http://127.0.0.1:%s/" % self.server.server_port,
                       "token": "synthetic-control-token", "pid": 123}, handle)
        return manifest_path


class ManagerControlTests(ServerCase):
    def test_status_uses_manifest_bearer_and_whitelists_response(self):
        with tempfile.TemporaryDirectory() as directory:
            status = manager_control.ManagerControl(self.manifest(directory)).status()
        self.assertEqual(status, SAFE_STATUS)
        self.assertEqual(SyntheticManager.requests, [
            ("GET", "/status", "Bearer synthetic-control-token", None)])

    def test_action_is_whitelisted_and_stop_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = manager_control.ManagerControl(self.manifest(directory))
            self.assertEqual(bridge.action("stop"), {"ok": False, "error": "Stop requires confirmation"})
            self.assertEqual(bridge.action("not-an-action"), {"ok": False, "error": "Invalid Manager action"})
            self.assertTrue(bridge.action("next-account")["ok"])
            self.assertTrue(bridge.action("stop", True)["ok"])
        self.assertEqual(SyntheticManager.requests[0][3], {"action": "next-account", "confirm": False})
        self.assertEqual(SyntheticManager.requests[1][3], {"action": "stop", "confirm": True})

    def test_external_manifest_url_is_never_contacted(self):
        with tempfile.TemporaryDirectory() as directory:
            status = manager_control.ManagerControl(self.manifest(directory, "http://example.test/")).status()
        self.assertEqual(status, manager_control.unavailable_status())
        self.assertEqual(SyntheticManager.requests, [])

    def test_unexpected_control_fields_are_not_forwarded_to_browser(self):
        SyntheticManager.reply = dict(SAFE_STATUS, oauth_token="secret")
        with tempfile.TemporaryDirectory() as directory:
            status = manager_control.ManagerControl(self.manifest(directory)).status()
        self.assertNotIn("oauth_token", status)
        self.assertEqual(status["current_account"]["email"], "user@example.test")

    def test_oversized_control_reply_becomes_static_unavailable_state(self):
        SyntheticManager.reply = dict(SAFE_STATUS, padding="x" * (manager_control.MAX_RESPONSE_BYTES + 1))
        with tempfile.TemporaryDirectory() as directory:
            status = manager_control.ManagerControl(self.manifest(directory)).status()
        self.assertEqual(status, manager_control.unavailable_status())

    def test_non_finite_or_out_of_range_quota_becomes_static_unavailable_state(self):
        bad_account = dict(SAFE_STATUS["current_account"], models=[
            {"name": "gemini-test", "percentage": float("nan"), "reset_time": None}])
        SyntheticManager.reply = dict(SAFE_STATUS, current_account=bad_account)
        with tempfile.TemporaryDirectory() as directory:
            status = manager_control.ManagerControl(self.manifest(directory)).status()
        self.assertEqual(status, manager_control.unavailable_status())

    def test_manifest_token_with_header_newline_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.manifest(directory)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"url": "http://127.0.0.1:%s/" % self.server.server_port,
                           "token": "bad\r\ntoken", "pid": 123}, handle)
            status = manager_control.ManagerControl(path).status()
        self.assertEqual(status, manager_control.unavailable_status())
        self.assertEqual(SyntheticManager.requests, [])

    def test_malformed_or_unsuccessful_action_reply_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = manager_control.ManagerControl(self.manifest(directory))
            for reply in (None, [], {"ok": False}, {"ok": 1}, {"error": "upstream secret"}):
                SyntheticManager.action_reply = reply
                self.assertEqual(bridge.action("refresh-current"),
                                 {"ok": False, "error": manager_control.UNAVAILABLE_MESSAGE})

    def test_action_reply_strips_extra_fields_and_rejects_bad_embedded_status(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = manager_control.ManagerControl(self.manifest(directory))
            SyntheticManager.action_reply = {"ok": True, "oauth_token": "secret"}
            self.assertEqual(bridge.action("refresh-current"), {"ok": True})
            SyntheticManager.action_reply = {"ok": True, "status": {"available": True}}
            self.assertEqual(bridge.action("refresh-current"),
                             {"ok": False, "error": manager_control.UNAVAILABLE_MESSAGE})


class FakeControl:
    def __init__(self):
        self.calls = []

    def status(self):
        return SAFE_STATUS

    def action(self, action, confirm):
        self.calls.append((action, confirm))
        return {"ok": True}


class ManagerUiRouteTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), cc_relay.UIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.fake_control = FakeControl()
        self.bridge = patch.object(cc_relay.manager_control, "control_from_config", return_value=self.fake_control)
        self.bridge.start()

    def tearDown(self):
        self.bridge.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        status = response.status
        connection.close()
        return status, payload

    def same_origin_headers(self):
        origin = "http://127.0.0.1:%s" % self.server.server_port
        return {"Content-Type": "application/json", "Origin": origin, "Host": origin.removeprefix("http://"),
                "Sec-Fetch-Site": "same-origin"}

    def test_status_route_returns_safe_manager_state(self):
        status, payload = self.request("GET", "/api/manager/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload, SAFE_STATUS)

    def test_status_route_rejects_rebound_or_non_loopback_host(self):
        status, _ = self.request("GET", "/api/manager/status", headers={
            "Host": "rebound.example:%s" % self.server.server_port})
        self.assertEqual(status, 403)

    def test_action_route_requires_same_origin_and_only_accepted_fields(self):
        body = json.dumps({"action": "next-account"})
        status, _ = self.request("POST", "/api/manager/action", body, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        headers = self.same_origin_headers()
        status, _ = self.request("POST", "/api/manager/action", json.dumps({"action": "next-account", "extra": 1}), headers)
        self.assertEqual(status, 400)
        status, payload = self.request("POST", "/api/manager/action", body, headers)
        self.assertEqual((status, payload), (200, {"ok": True}))
        self.assertEqual(self.fake_control.calls, [("next-account", False)])

    def test_action_route_rejects_same_origin_rebound_host(self):
        host = "rebound.example:%s" % self.server.server_port
        headers = {"Content-Type": "application/json", "Origin": "http://" + host,
                   "Host": host, "Sec-Fetch-Site": "same-origin"}
        status, _ = self.request("POST", "/api/manager/action", json.dumps({"action": "next-account"}), headers)
        self.assertEqual(status, 403)
        self.assertEqual(self.fake_control.calls, [])

    def test_stop_action_requires_browser_confirmation_flag(self):
        headers = self.same_origin_headers()
        status, _ = self.request("POST", "/api/manager/action", json.dumps({"action": "stop"}), headers)
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/manager/action", json.dumps({"action": "stop", "confirm": True}), headers)
        self.assertEqual(status, 200)
        self.assertEqual(self.fake_control.calls, [("stop", True)])


if __name__ == "__main__":
    unittest.main()
