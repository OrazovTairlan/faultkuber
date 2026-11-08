"""Shared test support: loads app/service.py with a local configuration and runs all five roles in-process."""
import contextlib
import http.client
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTS = {"gateway": 19100, "auth": 19101, "course": 19102, "registration": 19103, "db": 19104}
DATA = tempfile.mkdtemp(prefix="aitu-test-")

os.environ.update({
    "SERVICE": "gateway", "POD_NAME": "test-pod", "ENV": "production", "AUTH_SECRET": "unit-test-secret-0123456789",
    "AUTH_URL": f"127.0.0.1:{PORTS['auth']}", "COURSE_URL": f"127.0.0.1:{PORTS['course']}",
    "REGISTRATION_URL": f"127.0.0.1:{PORTS['registration']}", "DB_NODES": f"127.0.0.1:{PORTS['db']}",
    "DATA_DIR": DATA, "AUTH_HEADLESS": f"127.0.0.1:{PORTS['auth']}", "UPSTREAM_TIMEOUT": "1.0", "DB_TIMEOUT": "0.2",
    "RATE_SUBJECT_PER_S": "1", "RATE_SUBJECT_BURST": "12", "LOGIN_MAX_FAILURES": "5", "LOGIN_LOCK_S": "30",
})

_spec = importlib.util.spec_from_file_location("service_under_test", os.path.join(ROOT, "app", "service.py"))
service = importlib.util.module_from_spec(_spec)
sys.modules["service_under_test"] = service
_spec.loader.exec_module(service)


class Platform:
    """Starts auth, course, registration, db and gateway as threads; individual roles can be stopped to inject faults."""

    def __init__(self):
        self.servers = {}
        self.log = io.StringIO()
        self._redirect = None

    def start(self):
        self._redirect = contextlib.redirect_stdout(self.log)
        self._redirect.__enter__()
        service.STORE = service.Store()
        for name in PORTS:
            self.start_role(name)
        return self

    def start_role(self, name):
        handler = service.HANDLERS[name]
        srv = ThreadingHTTPServer(("127.0.0.1", PORTS[name]), handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers[name] = srv

    def stop_role(self, name):
        srv = self.servers.pop(name, None)
        if srv:
            srv.shutdown()
            srv.server_close()

    def stop(self):
        for name in list(self.servers):
            self.stop_role(name)
        if self._redirect:
            self._redirect.__exit__(None, None, None)

    def lines(self):
        return [json.loads(l) for l in self.log.getvalue().splitlines() if l.startswith("{")]


def request(method, path, body=None, token=None, raw_body=None, headers=None, port=None):
    """Returns (status, parsed_json_or_text, response_headers)."""
    conn = http.client.HTTPConnection("127.0.0.1", port or PORTS["gateway"], timeout=5)
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    payload = raw_body if raw_body is not None else (json.dumps(body) if body is not None else None)
    if payload is not None:
        h.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, payload, h)
        resp = conn.getresponse()
        data = resp.read().decode()
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = data
        return resp.status, parsed, {k.lower(): v for k, v in resp.getheaders()}
    finally:
        conn.close()


def login(sid, password=None):
    status, body, _ = request("POST", "/auth/login", {"student_id": sid, "password": password or f"pw-{sid}"})
    return status, (body.get("token") if isinstance(body, dict) else None)
