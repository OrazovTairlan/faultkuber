"""Synthetic AITU Digital Student Services Platform - endterm version, one image, five roles.

SERVICE=gateway | auth | course | registration | db

Standard library only, so the container image needs no pip install.
All data is synthetic; the HMAC secret comes from a Kubernetes Secret.

Endterm additions over the midterm baseline (git commit a6b1daa):
  observability  /metrics (Prometheus text), request/correlation id, structured access log, audit log
  security       password login with account lockout, expiring signed tokens, ownership authorization,
                 strict input validation, per-client rate limiting, duplicate-request protection,
                 configuration checks
  resilience     circuit breaker per upstream at the gateway, stale-cache graceful degradation for
                 non-critical read features (courses, timetable)
"""
import base64
import collections
import hashlib
import hmac
import json
import os
import random
import re
import signal
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = os.environ.get("SERVICE", "course")
PORT = int(os.environ.get("PORT", "8080"))
POD = os.environ.get("POD_NAME", socket.gethostname())
ENV = os.environ.get("ENV", "development")
DEFAULT_SECRET = "synthetic-dev-secret"
SECRET = os.environ.get("AUTH_SECRET", DEFAULT_SECRET).encode()

AUTH_URL = os.environ.get("AUTH_URL", "auth:8080")
AUTH_HEADLESS = os.environ.get("AUTH_HEADLESS", "auth-headless:8080")
COURSE_URL = os.environ.get("COURSE_URL", "course:8080")
REGISTRATION_URL = os.environ.get("REGISTRATION_URL", "registration:8080")
DB_NODES = [n for n in os.environ.get("DB_NODES", "").split(",") if n]
DATA_DIR = os.environ.get("DATA_DIR", "/data")

UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "0.20"))
DB_TIMEOUT = float(os.environ.get("DB_TIMEOUT", "0.15"))
TOKEN_TTL_S = int(os.environ.get("TOKEN_TTL_S", "900"))
MAX_BODY = int(os.environ.get("MAX_BODY", "1024"))
RATE_SUBJECT_PER_S = float(os.environ.get("RATE_SUBJECT_PER_S", "20"))
RATE_SUBJECT_BURST = float(os.environ.get("RATE_SUBJECT_BURST", "40"))
RATE_IP_PER_S = float(os.environ.get("RATE_IP_PER_S", "400"))
RATE_IP_BURST = float(os.environ.get("RATE_IP_BURST", "800"))
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
LOGIN_WINDOW_S = float(os.environ.get("LOGIN_WINDOW_S", "60"))
LOGIN_LOCK_S = float(os.environ.get("LOGIN_LOCK_S", "30"))
IP_FAILURE_CAP = int(os.environ.get("IP_FAILURE_CAP", "40"))
STALE_MAX_AGE_S = float(os.environ.get("STALE_MAX_AGE_S", "600"))
TOKEN_CACHE_TTL_S = float(os.environ.get("TOKEN_CACHE_TTL_S", "5"))

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

STUDENT_RE = re.compile(r"\A[SA]\d{4,7}\Z")
REGISTRANT_RE = re.compile(r"\AS\d{4,7}\Z")
RID_RE = re.compile(r"[^A-Za-z0-9\-]")


def validate_config():
    """Returns a list of configuration problems; an empty list means the configuration is acceptable."""
    problems = []
    if SERVICE in ("gateway", "auth"):
        if len(SECRET) < 16:
            problems.append("AUTH_SECRET shorter than 16 bytes")
        if SECRET.decode() == DEFAULT_SECRET and ENV == "production":
            problems.append("default AUTH_SECRET in production")
    if UPSTREAM_TIMEOUT <= 0 or DB_TIMEOUT <= 0:
        problems.append("timeouts must be positive")
    if UPSTREAM_TIMEOUT * 2 > 0.5:
        problems.append("two upstream attempts exceed the 500 ms latency budget")
    return problems


class Metrics:
    """Thread-safe Prometheus-style registry: request counter, latency histogram, event counters, gauges."""

    BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)

    def __init__(self):
        self.lock = threading.Lock()
        self.requests = collections.defaultdict(int)
        self.hist = {}
        self.events = collections.defaultdict(int)
        self.gauges = {}
        self.started = time.time()

    def observe(self, route, status, seconds):
        with self.lock:
            self.requests[(route, str(status))] += 1
            h = self.hist.setdefault(route, {"b": [0] * len(self.BUCKETS), "sum": 0.0, "n": 0})
            h["sum"] += seconds
            h["n"] += 1
            for i, edge in enumerate(self.BUCKETS):
                if seconds <= edge:
                    h["b"][i] += 1

    def event(self, name, n=1):
        with self.lock:
            self.events[name] += n

    def gauge(self, name, labels, value):
        with self.lock:
            self.gauges[(name, labels)] = value

    def render(self):
        s = SERVICE
        out = ["# TYPE aitu_http_requests_total counter"]
        with self.lock:
            for (route, status), n in sorted(self.requests.items()):
                out.append(f'aitu_http_requests_total{{service="{s}",route="{route}",status="{status}"}} {n}')
            out.append("# TYPE aitu_http_request_duration_seconds histogram")
            for route, h in sorted(self.hist.items()):
                for edge, c in zip(self.BUCKETS, h["b"]):
                    out.append(f'aitu_http_request_duration_seconds_bucket{{service="{s}",route="{route}",le="{edge}"}} {c}')
                out.append(f'aitu_http_request_duration_seconds_bucket{{service="{s}",route="{route}",le="+Inf"}} {h["n"]}')
                out.append(f'aitu_http_request_duration_seconds_sum{{service="{s}",route="{route}"}} {h["sum"]:.6f}')
                out.append(f'aitu_http_request_duration_seconds_count{{service="{s}",route="{route}"}} {h["n"]}')
            out.append("# TYPE aitu_events_total counter")
            for name, n in sorted(self.events.items()):
                out.append(f'aitu_events_total{{service="{s}",event="{name}"}} {n}')
            out.append("# TYPE aitu_gauge gauge")
            for (name, labels), v in sorted(self.gauges.items()):
                out.append(f'aitu_{name}{{service="{s}"{("," + labels) if labels else ""}}} {v}')
            out.append(f'aitu_process_uptime_seconds{{service="{s}"}} {time.time() - self.started:.1f}')
        return "\n".join(out) + "\n"


METRICS = Metrics()


AUDIT_LAST = {}
LOG_LOCK = threading.Lock()


def emit(record):
    """Writes one complete JSON line with a single write call under a lock, so concurrent threads cannot interleave lines."""
    line = json.dumps(record) + "\n"
    with LOG_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def audit(event, rid="-", sample_key=None, sample_s=2.0, **fields):
    """One JSON line per security-relevant decision; every event is counted in the metrics, a flood of
    identical events (sample_key) is written to the log at most once per sample_s seconds."""
    METRICS.event(event.lower())
    if sample_key is not None:
        now = time.time()
        if now - AUDIT_LAST.get((event, sample_key), 0.0) < sample_s:
            return
        if len(AUDIT_LAST) > 10000:
            AUDIT_LAST.clear()
        AUDIT_LAST[(event, sample_key)] = now
    emit({"audit": True, "ts": round(time.time(), 3), "svc": SERVICE, "pod": POD, "rid": rid, "event": event, **fields})


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


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(sub, role="student", ttl=None, now=None):
    now = time.time() if now is None else now
    payload = b64(json.dumps({"sub": sub, "role": role, "iat": int(now), "exp": int(now + (TOKEN_TTL_S if ttl is None else ttl))},
                             separators=(",", ":")).encode())
    sig = b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def parse_token(token, now=None):
    """Returns the claims of a valid, unexpired token, otherwise None."""
    try:
        payload, sig = token.split(".")
        good = b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        claims = json.loads(unb64(payload))
        if claims["exp"] < (time.time() if now is None else now):
            return None
        return claims
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def password_hash(sid, password):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), b"aitu-synthetic-salt:" + sid.encode(), 2000)


def check_password(sid, password):
    """Synthetic credential store: the password of every account is 'pw-' + its id (hashed with PBKDF2)."""
    return hmac.compare_digest(password_hash(sid, password), password_hash(sid, "pw-" + sid))


class LoginGuard:
    """Per-account lockout and per-source failure cap against password guessing."""

    def __init__(self, max_failures=LOGIN_MAX_FAILURES, window=LOGIN_WINDOW_S, lock=LOGIN_LOCK_S, ip_cap=IP_FAILURE_CAP):
        self.max_failures, self.window, self.lock_s, self.ip_cap = max_failures, window, lock, ip_cap
        self.fail = collections.defaultdict(collections.deque)
        self.ip_fail = collections.defaultdict(collections.deque)
        self.locked_until = {}
        self.mutex = threading.Lock()

    def _trim(self, dq, now):
        while dq and now - dq[0] > self.window:
            dq.popleft()

    def is_locked(self, sid, now=None):
        now = time.time() if now is None else now
        with self.mutex:
            return self.locked_until.get(sid, 0) > now

    def ip_blocked(self, ip, now=None):
        now = time.time() if now is None else now
        with self.mutex:
            self._trim(self.ip_fail[ip], now)
            return len(self.ip_fail[ip]) >= self.ip_cap

    def failure(self, sid, ip, now=None):
        now = time.time() if now is None else now
        with self.mutex:
            for dq in (self.fail[sid], self.ip_fail[ip]):
                dq.append(now)
                self._trim(dq, now)
            if len(self.fail[sid]) >= self.max_failures:
                self.locked_until[sid] = now + self.lock_s
                self.fail[sid].clear()
                return True
            return False

    def success(self, sid):
        with self.mutex:
            self.fail.pop(sid, None)


class TokenBucket:
    """Classic token bucket per key; `allow` is False when the key exceeds rate/burst."""

    def __init__(self, rate, burst, max_keys=20000):
        self.rate, self.burst, self.max_keys = rate, burst, max_keys
        self.state = {}
        self.mutex = threading.Lock()

    def allow(self, key, now=None):
        now = time.monotonic() if now is None else now
        with self.mutex:
            tokens, last = self.state.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= 1.0
            self.state[key] = (tokens - 1.0 if ok else tokens, now)
            if len(self.state) > self.max_keys:
                self.state.clear()
            return ok


class CircuitBreaker:
    """Opens after `threshold` consecutive failures, half-opens after `reset` seconds."""

    def __init__(self, threshold=3, reset=3.0):
        self.threshold, self.reset = threshold, reset
        self.failures, self.opened_at = 0, 0.0
        self.lock = threading.Lock()

    def is_open(self):
        with self.lock:
            return self.failures >= self.threshold and time.time() - self.opened_at < self.reset

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


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def route_label(path):
    if path.startswith("/registrations/"):
        return "/registrations/{id}"
    if path.startswith("/courses/"):
        return "/courses/{code}"
    if path in ("/auth/login", "/auth/verify", "/courses", "/timetable", "/registrations",
                "/admin/count", "/db/write", "/db/replicate", "/db/records", "/db/dump", "/db/count"):
        return path
    return "other"


class Handler(BaseHTTPRequestHandler):
    server_version = "aitu-synthetic/2.0"
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt, *args):
        return

    def reply(self, status, body, extra=None):
        self._status = status
        data = json.dumps({**body, "served_by": POD}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Request-ID", self.rid)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def raw(self, status, text, content_type="text/plain; version=0.0.4"):
        self._status = status
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise HttpError(400, "invalid Content-Length")
        if n > MAX_BODY:
            self.rfile.read(min(n, 65536))
            raise HttpError(413, "request body too large")
        if not n:
            return {}
        try:
            body = json.loads(self.rfile.read(n))
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "body is not valid JSON")
        if not isinstance(body, dict):
            raise HttpError(400, "body must be a JSON object")
        return body

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        started = time.perf_counter()
        self._status = 0
        raw_rid = RID_RE.sub("", self.headers.get("X-Request-ID", ""))[:40]
        self.rid = raw_rid or uuid.uuid4().hex[:12]
        url = urllib.parse.urlparse(self.path)
        probe = url.path in ("/healthz", "/readyz", "/metrics")
        try:
            if url.path == "/healthz":
                self.reply(200, {"status": "alive"})
            elif url.path == "/readyz":
                ok, why = self.ready()
                self.reply(200 if ok else 503, {"status": "ready" if ok else why})
            elif url.path == "/metrics":
                self.raw(200, METRICS.render())
            else:
                self.handle_api(method, url.path, urllib.parse.parse_qs(url.query))
        except HttpError as exc:
            METRICS.event("validation_rejected" if exc.status in (400, 413) else "http_error")
            self.reply(exc.status, {"error": exc.message})
        except Exception as exc:
            self.reply(500, {"error": type(exc).__name__, "detail": str(exc)})
        finally:
            elapsed = time.perf_counter() - started
            if not probe:
                label = route_label(url.path)
                METRICS.observe(label, self._status, elapsed)
                emit({"ts": round(time.time(), 3), "svc": SERVICE, "pod": POD, "rid": self.rid, "method": method,
                      "route": label, "status": self._status, "ms": round(elapsed * 1000, 1), "client": self.client_address[0]})

    def ready(self):
        problems = validate_config()
        return (not problems), "; ".join(problems)

    def handle_api(self, method, path, query):
        self.reply(404, {"error": "not found"})


class AuthHandler(Handler):
    guard = LoginGuard()

    def handle_api(self, method, path, query):
        if method == "POST" and path == "/auth/login":
            return self.login()
        if method == "GET" and path == "/auth/verify":
            claims = parse_token(query.get("token", [""])[0])
            if not claims:
                audit("TOKEN_INVALID", self.rid)
                return self.reply(401, {"error": "invalid or expired token"})
            return self.reply(200, {"student_id": claims["sub"], "role": claims["role"], "exp": claims["exp"]})
        self.reply(404, {"error": "not found"})

    def login(self):
        body = self.read_json()
        sid, pw = body.get("student_id"), body.get("password")
        ip = self.headers.get("X-Client-IP", self.client_address[0])
        if not isinstance(sid, str) or not STUDENT_RE.match(sid) or not isinstance(pw, str) or not 0 < len(pw) <= 64:
            audit("LOGIN_REJECTED", self.rid, reason="invalid format", client=ip)
            raise HttpError(400, "student_id or password has an invalid format")
        if self.guard.is_locked(sid):
            audit("ACCOUNT_LOCKED", self.rid, subject=sid, client=ip)
            return self.reply(429, {"error": "account temporarily locked"}, {"Retry-After": str(int(LOGIN_LOCK_S))})
        if check_password(sid, pw):
            self.guard.success(sid)
            role = "admin" if sid.startswith("A") else "student"
            audit("LOGIN_OK", self.rid, subject=sid, client=ip)
            return self.reply(200, {"token": make_token(sid, role), "role": role, "expires_in": TOKEN_TTL_S})
        if self.guard.ip_blocked(ip):
            audit("LOGIN_BLOCKED_SOURCE", self.rid, subject=sid, client=ip)
            return self.reply(429, {"error": "too many failed logins from this source"}, {"Retry-After": str(int(LOGIN_LOCK_S))})
        locked_now = self.guard.failure(sid, ip)
        audit("LOGIN_FAILED", self.rid, subject=sid, client=ip, locked=locked_now)
        self.reply(401, {"error": "invalid credentials"})


class CourseHandler(Handler):
    def handle_api(self, method, path, query):
        if path == "/courses":
            return self.reply(200, {"courses": COURSES})
        if path.startswith("/courses/"):
            c = COURSES.get(path.rsplit("/", 1)[1])
            return self.reply(200 if c else 404, c or {"error": "no such course"})
        if path == "/timetable":
            group = query.get("group", ["SE-2301"])[0][:16]
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
        self.marker = os.path.join(DATA_DIR, ".initialized")
        expected = 0
        if os.path.exists(self.marker):
            try:
                with open(self.marker) as mf:
                    expected = int(mf.read() or 0)
            except ValueError:
                expected = 0
        if os.path.exists(self.path):
            with open(self.path) as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        self.records[rec["id"]] = rec
        self.damaged = len(self.records) < expected

    def put(self, rec):
        with self.lock:
            if self.damaged or rec["id"] in self.records:
                return False
            self.records[rec["id"]] = rec
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")
                f.flush()
                os.fsync(f.fileno())
            with open(self.marker, "w") as f:
                f.write(str(len(self.records)))
            return True


STORE = None
PEERS = [n for n in DB_NODES if not n.startswith(POD + ".")]


def valid_record(rec):
    return (isinstance(rec, dict) and isinstance(rec.get("student_id"), str) and REGISTRANT_RE.match(rec["student_id"])
            and rec.get("course_code") in COURSES and rec.get("id") == f"{rec['student_id']}:{rec['course_code']}")


def anti_entropy():
    """Pull missing records from the peer at start-up and every 5 s."""
    while True:
        for peer in PEERS:
            try:
                status, body = call(peer, "GET", "/db/dump", timeout=1.0)
                if status == 200:
                    for rec in body.get("records", []):
                        if valid_record(rec):
                            STORE.put(rec)
            except OSError:
                pass
        time.sleep(5)


class DbHandler(Handler):
    def ready(self):
        if STORE.damaged:
            return False, "data store missing: restore required"
        return True, ""

    def handle_api(self, method, path, query):
        if STORE.damaged:
            return self.reply(503, {"error": "data store missing: restore required"})
        if method == "POST" and path == "/db/write":
            rec = self.read_json()
            if not valid_record(rec):
                raise HttpError(400, "record failed schema validation")
            created = STORE.put(rec)
            replicated = 0
            for peer in PEERS:
                try:
                    if call(peer, "POST", "/db/replicate", rec, headers={"X-Request-ID": self.rid}, timeout=DB_TIMEOUT)[0] == 200:
                        replicated += 1
                except OSError:
                    pass
            return self.reply(200, {"id": rec["id"], "created": created, "replicas_acked": 1 + replicated})
        if method == "POST" and path == "/db/replicate":
            rec = self.read_json()
            if valid_record(rec):
                STORE.put(rec)
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


def db_request(method, path, body=None, rid="-"):
    """Try DB nodes in random order (bounded: one attempt per node); the breaker protects the latency budget."""
    if not BREAKER.allow():
        METRICS.event("db_circuit_open")
        raise ConnectionError("circuit open")
    nodes = random.sample(DB_NODES, len(DB_NODES))
    for attempt, node in enumerate(nodes):
        try:
            status, resp = call(node, method, path, body, headers={"X-Request-ID": rid}, timeout=DB_TIMEOUT)
            if status == 200:
                BREAKER.record(True)
                return resp
        except OSError:
            METRICS.event("db_retry")
            continue
    BREAKER.record(False)
    METRICS.event("db_unavailable")
    raise ConnectionError("no database node reachable")


class RegistrationHandler(Handler):
    def handle_api(self, method, path, query):
        sub = self.headers.get("X-Auth-Subject")
        role = self.headers.get("X-Auth-Role", "student")
        if not sub:
            audit("UNAUTHENTICATED_INTERNAL_CALL", self.rid, path=route_label(path))
            return self.reply(401, {"error": "missing authenticated subject"})
        METRICS.gauge("circuit_breaker_open", 'target="db"', 1 if BREAKER.is_open() else 0)
        try:
            if method == "POST" and path == "/registrations":
                return self.create(sub, role)
            if method == "GET" and path.startswith("/registrations/"):
                sid = path.rsplit("/", 1)[1]
                if not REGISTRANT_RE.match(sid):
                    raise HttpError(400, "invalid student id")
                if sid != sub and role != "admin":
                    audit("FORBIDDEN_ACCESS", self.rid, subject=sub, target=sid, action="read")
                    return self.reply(403, {"error": "not allowed to read another student's registrations"})
                resp = db_request("GET", f"/db/records?student_id={sid}", rid=self.rid)
                return self.reply(200, {"student_id": sid, "registrations": resp["records"]})
            if method == "GET" and path == "/admin/count":
                if role != "admin":
                    audit("FORBIDDEN_ACCESS", self.rid, subject=sub, action="admin-count")
                    return self.reply(403, {"error": "administrator role required"})
                return self.reply(200, {"count": db_request("GET", "/db/count", rid=self.rid)["count"]})
        except ConnectionError as exc:
            return self.reply(503, {"error": "registration database unavailable", "detail": str(exc)}, {"Retry-After": "5"})
        self.reply(404, {"error": "not found"})

    def create(self, sub, role):
        req = self.read_json()
        sid, course = req.get("student_id"), req.get("course_code")
        if not isinstance(sid, str) or not REGISTRANT_RE.match(sid):
            raise HttpError(400, "student_id has an invalid format")
        if not isinstance(course, str) or course not in COURSES:
            raise HttpError(400, "unknown course_code")
        if sid != sub and role != "admin":
            audit("FORBIDDEN_ACCESS", self.rid, subject=sub, target=sid, action="write")
            return self.reply(403, {"error": "not allowed to register on behalf of another student"})
        rec = {"id": f"{sid}:{course}", "student_id": sid, "course_code": course, "ts": round(time.time(), 3)}
        resp = db_request("POST", "/db/write", rec, rid=self.rid)
        if resp.get("created"):
            audit("REGISTRATION_CREATED", self.rid, subject=sub, record=rec["id"])
            return self.reply(201, {"registered": rec["id"], "replicas_acked": resp["replicas_acked"]})
        audit("DUPLICATE_REGISTRATION", self.rid, subject=sub, record=rec["id"])
        return self.reply(200, {"registered": rec["id"], "duplicate": True, "replicas_acked": resp["replicas_acked"]})


ROUTES = {"/auth": ("auth", AUTH_URL), "/courses": ("course", COURSE_URL), "/timetable": ("course", COURSE_URL),
          "/registrations": ("registration", REGISTRATION_URL), "/admin": ("registration", REGISTRATION_URL)}
UP_BREAKERS = {name: CircuitBreaker(5, 2.0) for name in ("auth", "course", "registration")}
RATE_IP = TokenBucket(RATE_IP_PER_S, RATE_IP_BURST)
RATE_SUBJECT = TokenBucket(RATE_SUBJECT_PER_S, RATE_SUBJECT_BURST)
STALE_CACHE = {}
CACHEABLE = ("/courses", "/timetable")
REPLICA_CACHE = {"t": 0.0, "ips": []}
TOKEN_CACHE = {}


def auth_replica_for(sid, now=None):
    """Deterministic Auth replica per account: the lockout counters of one account then live in exactly one pod."""
    now = time.time() if now is None else now
    host, _, port = AUTH_HEADLESS.partition(":")
    if now - REPLICA_CACHE["t"] > 2.0:
        try:
            infos = socket.getaddrinfo(host, int(port or 80), socket.AF_INET, socket.SOCK_STREAM)
            ips = sorted({i[4][0] for i in infos})
        except OSError:
            ips = []
        REPLICA_CACHE.update(t=now, ips=ips)
    ips = REPLICA_CACHE["ips"]
    if not ips:
        return None
    return f"{ips[int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16) % len(ips)]}:{port or 80}"


class GatewayHandler(Handler):
    def handle_api(self, method, path, query):
        for name, br in UP_BREAKERS.items():
            METRICS.gauge("circuit_breaker_open", f'target="{name}"', 1 if br.is_open() else 0)
        ip = self.client_address[0]
        if not RATE_IP.allow(ip):
            audit("RATE_LIMITED", self.rid, sample_key=("source", ip), scope="source", client=ip)
            return self.reply(429, {"error": "too many requests"}, {"Retry-After": "1"})
        if len(path) > 128:
            raise HttpError(400, "path too long")
        route = next(((n, u) for p, (n, u) in ROUTES.items() if path.startswith(p)), None)
        if not route:
            return self.reply(404, {"error": "no route"})
        name, target = route
        body = self.read_json() if method == "POST" else None
        headers = {"X-Request-ID": self.rid, "X-Client-IP": ip}
        if path.startswith("/registrations") or path.startswith("/admin"):
            claims = self.authenticate()
            if claims is None:
                return
            if not RATE_SUBJECT.allow(claims["student_id"]):
                audit("RATE_LIMITED", self.rid, sample_key=("subject", claims["student_id"]), scope="subject", subject=claims["student_id"], client=ip)
                return self.reply(429, {"error": "rate limit exceeded for this account"}, {"Retry-After": "1"})
            headers.update({"X-Auth-Subject": claims["student_id"], "X-Auth-Role": claims["role"]})
        full = path + (("?" + urllib.parse.urlencode(query, doseq=True)) if query else "")
        fallback = None
        if path == "/auth/login" and method == "POST" and isinstance(body.get("student_id"), str):
            preferred = auth_replica_for(body["student_id"])
            if preferred:
                target, fallback = preferred, target
        self.forward(name, target, method, full, body, headers, fallback)

    def authenticate(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            audit("UNAUTHORIZED_REQUEST", self.rid, reason="missing token", client=self.client_address[0])
            self.reply(401, {"error": "authentication required"}, {"WWW-Authenticate": "Bearer"})
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        hit = TOKEN_CACHE.get(key)
        now = time.time()
        if hit and now - hit[0] <= TOKEN_CACHE_TTL_S and hit[1]["exp"] > now:
            return hit[1]
        claims = None
        for _attempt in range(2):
            try:
                status, resp = call(AUTH_URL, "GET", "/auth/verify?token=" + urllib.parse.quote(token), headers={"X-Request-ID": self.rid})
                claims = resp if status == 200 else False
                break
            except OSError:
                continue
        if claims is None:
            self.reply(503, {"error": "authentication service unavailable"}, {"Retry-After": "2"})
            return None
        if not claims:
            audit("UNAUTHORIZED_REQUEST", self.rid, reason="invalid or expired token", client=self.client_address[0])
            self.reply(401, {"error": "invalid or expired token"}, {"WWW-Authenticate": "Bearer"})
            return None
        if len(TOKEN_CACHE) > 5000:
            TOKEN_CACHE.clear()
        TOKEN_CACHE[key] = (now, claims)
        return claims

    def forward(self, name, target, method, full, body, headers, fallback=None):
        breaker = UP_BREAKERS[name]
        last = "circuit open"
        if breaker.allow():
            for attempt in range(2):
                try:
                    status, resp = call(fallback if attempt and fallback else target, method, full, body, headers)
                    if status < 500 or status == 503:
                        breaker.record(True)
                        if status == 200 and method == "GET" and full.startswith(CACHEABLE):
                            STALE_CACHE[full] = (time.time(), resp)
                        return self.reply(status, resp, {"Retry-After": "5"} if status == 503 else None)
                    last = f"upstream {status}"
                except OSError as exc:
                    last = type(exc).__name__
                if attempt == 0:
                    METRICS.event("upstream_retry")
            breaker.record(False)
        if method == "GET" and full.startswith(CACHEABLE):
            cached = STALE_CACHE.get(full)
            if cached and time.time() - cached[0] <= STALE_MAX_AGE_S:
                METRICS.event("degraded_response")
                age = int(time.time() - cached[0])
                return self.reply(200, {**cached[1], "degraded": True, "stale_age_s": age},
                                  {"X-Degraded": "stale-cache", "Warning": '110 - "Response is Stale"'})
            return self.reply(503, {"error": f"{name} service unavailable and no cached copy", "detail": last}, {"Retry-After": "3"})
        if last == "circuit open":
            return self.reply(503, {"error": f"{name} service temporarily unavailable", "detail": last}, {"Retry-After": "2"})
        self.reply(502, {"error": "bad gateway", "detail": last})


HANDLERS = {"gateway": GatewayHandler, "auth": AuthHandler, "course": CourseHandler,
            "registration": RegistrationHandler, "db": DbHandler}


def main():
    global STORE
    problems = validate_config()
    print(json.dumps({"event": "config-check", "svc": SERVICE, "pod": POD, "ok": not problems, "problems": problems}), flush=True)
    if problems and ENV == "production":
        sys.exit("refusing to start with an unsafe configuration: " + "; ".join(problems))
    if SERVICE == "db":
        STORE = Store()
        threading.Thread(target=anti_entropy, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), HANDLERS[SERVICE])
    srv.daemon_threads = True
    srv.request_queue_size = 256
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    print(json.dumps({"event": "start", "svc": SERVICE, "pod": POD, "port": PORT, "env": ENV}), flush=True)
    srv.serve_forever()
    print(json.dumps({"event": "graceful-stop", "svc": SERVICE, "pod": POD}), flush=True)


if __name__ == "__main__":
    main()
