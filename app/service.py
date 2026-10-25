"""Synthetic AITU Digital Student Services Platform - one image, five roles.

SERVICE=gateway | auth | course | registration | db

Standard library only, so the container image needs no pip install.
All data is synthetic; the HMAC secret comes from a Kubernetes Secret.
"""
import hashlib
import hmac
import json
import os
import random
import signal
import socket
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = os.environ.get("SERVICE", "course")
PORT = int(os.environ.get("PORT", "8080"))
POD = os.environ.get("POD_NAME", socket.gethostname())
SECRET = os.environ.get("AUTH_SECRET", "synthetic-dev-secret").encode()

AUTH_URL = os.environ.get("AUTH_URL", "auth:8080")
COURSE_URL = os.environ.get("COURSE_URL", "course:8080")
REGISTRATION_URL = os.environ.get("REGISTRATION_URL", "registration:8080")
DB_NODES = [n for n in os.environ.get("DB_NODES", "").split(",") if n]
DATA_DIR = os.environ.get("DATA_DIR", "/data")

UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "0.20"))
DB_TIMEOUT = float(os.environ.get("DB_TIMEOUT", "0.15"))

COURSES = {
    "CS401": {"title": "Fault Tolerance", "credits": 5},
    "CS312": {"title": "Cloud Computing", "credits": 5},
    "CS205": {"title": "Distributed Systems", "credits": 4},
    "MA101": {"title": "Discrete Mathematics", "credits": 3},
}
TIMETABLE = {
    "SE-2301": [{"day": "Mon", "time": "09:00", "course": "CS401", "room": "C1.1.254"}],
    "SE-2302": [{"day": "Tue", "time": "11:00", "course": "CS312", "room": "C1.2.101"}],
}


def call(target, method, path, body=None, headers=None, timeout=UPSTREAM_TIMEOUT):
    """Single HTTP call. Returns (status, parsed_json). Raises OSError on failure."""
    host, _, port = target.partition(":")
    conn = HTTPConnection(host, int(port or 80), timeout=timeout)
    try:
        payload = json.dumps(body) if body is not None else None
        h = {"Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=payload, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})
    finally:
        conn.close()


def sign(student_id):
    return hmac.new(SECRET, student_id.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(student_id):
    return f"{student_id}.{sign(student_id)}"


def token_student(token):
    sid, _, sig = token.partition(".")
    if sid and hmac.compare_digest(sig, sign(sid)):
        return sid
    return None


class CircuitBreaker:
    """Opens after `threshold` consecutive failures, half-opens after `reset` seconds."""

    def __init__(self, threshold=3, reset=3.0):
        self.threshold, self.reset = threshold, reset
        self.failures, self.opened_at = 0, 0.0
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            if self.failures < self.threshold:
                return True
            return time.time() - self.opened_at >= self.reset

    def record(self, ok):
        with self.lock:
            if ok:
                self.failures = 0
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.opened_at = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "aitu-synthetic/1.0"
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt, *args):
        print(json.dumps({"ts": round(time.time(), 3), "svc": SERVICE, "pod": POD,
                          "req": self.requestline, "msg": fmt % args}), flush=True)

    def reply(self, status, body, extra=None):
        data = json.dumps({**body, "served_by": POD}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/healthz":
            return self.reply(200, {"status": "alive"})
        if url.path == "/readyz":
            ok, why = self.ready()
            return self.reply(200 if ok else 503, {"status": "ready" if ok else why})
        try:
            self.handle_api(method, url.path, urllib.parse.parse_qs(url.query))
        except Exception as exc:
            self.reply(500, {"error": type(exc).__name__, "detail": str(exc)})

    def ready(self):
        return True, ""

    def handle_api(self, method, path, query):
        self.reply(404, {"error": "not found"})


class AuthHandler(Handler):
    def handle_api(self, method, path, query):
        if method == "POST" and path == "/auth/login":
            sid = self.read_json().get("student_id", "")
            if not sid.startswith("S"):
                return self.reply(401, {"error": "unknown synthetic student"})
            return self.reply(200, {"token": make_token(sid)})
        if method == "GET" and path == "/auth/verify":
            sid = token_student(query.get("token", [""])[0])
            return self.reply(200 if sid else 401, {"student_id": sid})
        self.reply(404, {"error": "not found"})


class CourseHandler(Handler):
    def handle_api(self, method, path, query):
        if path == "/courses":
            return self.reply(200, {"courses": COURSES})
        if path.startswith("/courses/"):
            c = COURSES.get(path.rsplit("/", 1)[1])
            return self.reply(200 if c else 404, c or {"error": "no such course"})
        if path == "/timetable":
            group = query.get("group", ["SE-2301"])[0]
            return self.reply(200, {"group": group, "lessons": TIMETABLE.get(group, [])})
        self.reply(404, {"error": "not found"})


class Store:
    """Append-only record set persisted as JSON lines on a PersistentVolume.

    Records are keyed by a deterministic id, so merging two nodes is a set union
    and writes are idempotent (safe to retry).
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.records = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self.path = os.path.join(DATA_DIR, "records.jsonl")
        if os.path.exists(self.path):
            with open(self.path) as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        self.records[rec["id"]] = rec

    def put(self, rec):
        with self.lock:
            if rec["id"] in self.records:
                return False
            self.records[rec["id"]] = rec
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return True


STORE = None
PEERS = [n for n in DB_NODES if not n.startswith(POD + ".")]


def anti_entropy():
    """Pull missing records from the peer at start-up and every 5 s."""
    while True:
        for peer in PEERS:
            try:
                status, body = call(peer, "GET", "/db/dump", timeout=1.0)
                if status == 200:
                    for rec in body.get("records", []):
                        STORE.put(rec)
            except OSError:
                pass
        time.sleep(5)


class DbHandler(Handler):
    def handle_api(self, method, path, query):
        if method == "POST" and path == "/db/write":
            rec = self.read_json()
            STORE.put(rec)
            replicated = 0
            for peer in PEERS:
                try:
                    if call(peer, "POST", "/db/replicate", rec, timeout=DB_TIMEOUT)[0] == 200:
                        replicated += 1
                except OSError:
                    pass
            return self.reply(200, {"id": rec["id"], "replicas_acked": 1 + replicated})
        if method == "POST" and path == "/db/replicate":
            STORE.put(self.read_json())
            return self.reply(200, {"ok": True})
        if method == "GET" and path == "/db/records":
            sid = query.get("student_id", [""])[0]
            recs = [r for r in STORE.records.values() if r["student_id"] == sid]
            return self.reply(200, {"records": recs})
        if method == "GET" and path == "/db/dump":
            return self.reply(200, {"records": list(STORE.records.values())})
        if method == "GET" and path == "/db/count":
            return self.reply(200, {"count": len(STORE.records)})
        self.reply(404, {"error": "not found"})


BREAKER = CircuitBreaker()


def db_request(method, path, body=None):
    """Try DB nodes in random order; circuit breaker protects the latency budget."""
    if not BREAKER.allow():
        raise ConnectionError("circuit open")
    nodes = random.sample(DB_NODES, len(DB_NODES))
    for node in nodes:
        try:
            status, resp = call(node, method, path, body, timeout=DB_TIMEOUT)
            if status == 200:
                BREAKER.record(True)
                return resp
        except OSError:
            continue
    BREAKER.record(False)
    raise ConnectionError("no database node reachable")


class RegistrationHandler(Handler):
    def handle_api(self, method, path, query):
        try:
            if method == "POST" and path == "/registrations":
                req = self.read_json()
                sid, course = req.get("student_id"), req.get("course_code")
                if not sid or course not in COURSES:
                    return self.reply(400, {"error": "bad request"})
                rec = {"id": f"{sid}:{course}", "student_id": sid, "course_code": course,
                       "ts": round(time.time(), 3)}
                resp = db_request("POST", "/db/write", rec)
                return self.reply(201, {"registered": rec["id"],
                                        "replicas_acked": resp["replicas_acked"]})
            if method == "GET" and path.startswith("/registrations/"):
                sid = path.rsplit("/", 1)[1]
                resp = db_request("GET", f"/db/records?student_id={sid}")
                return self.reply(200, {"student_id": sid, "registrations": resp["records"]})
        except ConnectionError as exc:
            return self.reply(503, {"error": "registration database unavailable",
                                    "detail": str(exc)}, {"Retry-After": "5"})
        self.reply(404, {"error": "not found"})


ROUTES = {"/auth": AUTH_URL, "/courses": COURSE_URL, "/timetable": COURSE_URL,
          "/registrations": REGISTRATION_URL}


class GatewayHandler(Handler):
    def handle_api(self, method, path, query):
        target = next((u for p, u in ROUTES.items() if path.startswith(p)), None)
        if not target:
            return self.reply(404, {"error": "no route"})
        body = self.read_json() if method == "POST" else None
        if path.startswith("/registrations"):
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not self.verify(token):
                return self.reply(401, {"error": "invalid or missing token"})
        full = path + (("?" + urllib.parse.urlencode(query, doseq=True)) if query else "")
        last = None
        for _attempt in range(2):
            try:
                status, resp = call(target, method, full, body)
                if status < 500 or status == 503:
                    return self.reply(status, resp)
                last = f"upstream {status}"
            except OSError as exc:
                last = f"{type(exc).__name__}"
        self.reply(502, {"error": "bad gateway", "detail": last})

    def verify(self, token):
        for _attempt in range(2):
            try:
                status, _ = call(AUTH_URL, "GET", "/auth/verify?token=" + urllib.parse.quote(token))
                return status == 200
            except OSError:
                continue
        return False


HANDLERS = {"gateway": GatewayHandler, "auth": AuthHandler, "course": CourseHandler,
            "registration": RegistrationHandler, "db": DbHandler}


def main():
    global STORE
    if SERVICE == "db":
        STORE = Store()
        threading.Thread(target=anti_entropy, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), HANDLERS[SERVICE])
    srv.daemon_threads = True
    srv.request_queue_size = 256
    signal.signal(signal.SIGTERM,
                  lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    print(json.dumps({"event": "start", "svc": SERVICE, "pod": POD, "port": PORT}), flush=True)
    srv.serve_forever()
    print(json.dumps({"event": "graceful-stop", "svc": SERVICE, "pod": POD}), flush=True)


if __name__ == "__main__":
    main()
