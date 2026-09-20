"""Isolated cc-relay UI preview with synthetic Manager state; no real accounts."""
import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import cc_relay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8611)
    args = parser.parse_args()
    token = str(uuid4())
    state = {'index': 0, 'running': True, 'refreshes': 0}
    accounts = ['preview-alpha@example.invalid', 'preview-beta@example.invalid']

    class Control(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get('Authorization') != 'Bearer ' + token:
                return self.reply({}, 401)
            if not state['running']:
                return self.reply({}, 503)
            self.reply({'available': True, 'running': True, 'accounts_count': 2,
                        'current_account': {'id': str(state['index']), 'email': accounts[state['index']],
                            'models': [{'name': 'gemini-3.8-flash-high', 'percentage': 82 + state['refreshes'],
                                        'reset_time': '2026-09-21T08:30:00Z'},
                                       {'name': 'gemini-3.1-pro-high', 'percentage': 64, 'reset_time': None},
                                       {'name': 'claude-sonnet-4-6', 'percentage': 91, 'reset_time': None}]},
                        'gateway': {'running': True, 'port': 8402, 'active_accounts': 2},
                        'scheduling_mode': 'balance', 'header_profile': 'official',
                        'body_limit_bytes': 33554432})

        def do_POST(self):
            if self.headers.get('Authorization') != 'Bearer ' + token:
                return self.reply({}, 401)
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if body['action'] == 'next-account':
                state['index'] = (state['index'] + 1) % 2
            elif body['action'] == 'refresh-current':
                state['refreshes'] = min(state['refreshes'] + 1, 10)
            elif body['action'] == 'stop' and body.get('confirm'):
                state['running'] = False
            self.reply({'ok': True})

    control = ThreadingHTTPServer(('127.0.0.1', 0), Control)
    threading.Thread(target=control.serve_forever, daemon=True).start()
    result_dir = ROOT / 'manager-core' / 'results'
    result_dir.mkdir(exist_ok=True)
    manifest = result_dir / ('preview-' + str(uuid4()) + '.json')
    manifest.write_text(json.dumps({'url': 'http://127.0.0.1:%d' % control.server_port, 'token': token}), encoding='utf-8')
    conf = {'router': {'route': 'hybrid'}, 'manager_core_control_file': str(manifest)}
    cc_relay.load_conf = lambda: conf
    cc_relay.codex_up = lambda: False
    cc_relay._iter_records_tail = lambda **_: []

    class Preview(cc_relay.UIHandler):
        def do_POST(self):
            if self.path != '/api/manager/action':
                return self._json({'error': 'preview-read-only'}, 403)
            super().do_POST()

        def do_GET(self):
            if self.path == '/api/prompts':
                return self._json({})
            super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Preview)
    print('Synthetic preview: http://127.0.0.1:%d/#manager' % server.server_port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        control.shutdown()
        control.server_close()
        manifest.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
