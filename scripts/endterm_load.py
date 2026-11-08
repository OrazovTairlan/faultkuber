"""Open-loop workload generator for the endterm experiments (works against baseline and endterm gateways).

Every user logs in with a password (the baseline ignores it), requests use the user's own token, and every
registration write uses a unique (student, course) pair so that acknowledged writes can be audited afterwards.
"""
import http.client
import itertools
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

COURSES = ["CS401", "CS312", "CS205", "MA101"]
MIX = [("timetable", 35), ("courses", 15), ("reg_read", 25), ("reg_write", 15), ("login", 10)]
_local = threading.local()


def http_call(port, method, path, body=None, token=None, timeout=3.0, headers=None, raw_body=None, keepalive=True):
    """Returns (t_send, latency_ms, status, parsed_or_text, response_headers). status 0 = transport failure."""
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    payload = raw_body if raw_body is not None else (json.dumps(body) if body is not None else None)
    if payload is not None:
        h.setdefault("Content-Type", "application/json")
    t0 = time.time()
    for attempt in (0, 1):
        conn = getattr(_local, "conn", None) if keepalive else None
        reused = conn is not None and getattr(_local, "port", None) == port
        if not reused:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request(method, path, payload, h)
            resp = conn.getresponse()
            data = resp.read().decode(errors="replace")
            if keepalive:
                _local.conn, _local.port = conn, port
            else:
                conn.close()
            try:
                parsed = json.loads(data)
            except ValueError:
                parsed = data
            return t0, (time.time() - t0) * 1000, resp.status, parsed, {k.lower(): v for k, v in resp.getheaders()}
        except (OSError, http.client.HTTPException):
            conn.close()
            _local.conn = None
            if reused and attempt == 0:
                continue
            return t0, (time.time() - t0) * 1000, 0, "", {}
    return t0, (time.time() - t0) * 1000, 0, "", {}


def login(port, sid):
    t0, lat, status, body, _ = http_call(port, "POST", "/auth/login", {"student_id": sid, "password": f"pw-{sid}"})
    return body["token"] if status == 200 and isinstance(body, dict) else None


class LoadGen:
    def __init__(self, port, rps, first_user, users=300, workers=96, mix=None):
        self.port, self.rps = port, rps
        self.user_ids = [f"S{first_user + i:04d}" for i in range(users)]
        self.tokens = {}
        self.records = []
        self.acked = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.counter = itertools.count(0)
        self.users_n = users
        kinds = mix or MIX
        self.kinds = [k for k, w in kinds for _ in range(w)]
        self.thread = None
        self.rng = random.Random(first_user)

    def start(self):
        for round_no in range(4):
            missing = [u for u in self.user_ids if u not in self.tokens]
            if not missing:
                break
            with ThreadPoolExecutor(max_workers=8) as ex:
                for sid, tok in zip(missing, ex.map(lambda u: login(self.port, u), missing)):
                    if tok:
                        self.tokens[sid] = tok
            if len(self.tokens) < self.users_n:
                time.sleep(2)
        if len(self.tokens) < self.users_n * 0.95:
            raise RuntimeError(f"only {len(self.tokens)} of {self.users_n} users could log in")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def finish(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self.pool.shutdown(wait=True)

    def _loop(self):
        nxt = time.perf_counter()
        while not self.stop_event.is_set():
            self.pool.submit(self._task, self.rng.choice(self.kinds))
            nxt += 1.0 / self.rps
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -1.0:
                nxt = time.perf_counter()

    def _task(self, kind):
        n = next(self.counter)
        sid = self.user_ids[n % self.users_n]
        tok = self.tokens.get(sid)
        body, path, method, rid = None, "", "GET", None
        if kind == "timetable":
            path = "/timetable?group=SE-2301"
        elif kind == "courses":
            path = "/courses"
        elif kind == "login":
            method, path, body = "POST", "/auth/login", {"student_id": sid, "password": f"pw-{sid}"}
        elif kind == "reg_read":
            path = f"/registrations/{sid}"
        else:
            method, path = "POST", "/registrations"
            course = COURSES[(n // self.users_n) % len(COURSES)]
            body = {"student_id": sid, "course_code": course}
            rid = f"{sid}:{course}"
        use_token = tok if path.startswith("/registrations") else None
        t0, lat, status, _, headers = http_call(self.port, method, path, body, use_token)
        degraded = headers.get("x-degraded") == "stale-cache"
        with self.lock:
            self.records.append((t0, lat, status, kind, degraded))
            if rid and status in (200, 201):
                self.acked[rid] = t0


def summarize(records, t_from=None, t_to=None, kinds=None):
    rs = [r for r in records if (t_from is None or r[0] >= t_from) and (t_to is None or r[0] <= t_to) and (kinds is None or r[3] in kinds)]
    ok = [r for r in rs if 200 <= r[2] < 300]
    lat = sorted(r[1] for r in ok)

    def pct(q):
        return round(lat[min(len(lat) - 1, int(q * len(lat)))], 1) if lat else None
    by_status = {}
    for r in rs:
        by_status[str(r[2])] = by_status.get(str(r[2]), 0) + 1
    return {"total": len(rs), "ok": len(ok), "failed": len(rs) - len(ok),
            "success_pct": round(100 * len(ok) / len(rs), 3) if rs else None,
            "avg_ms": round(sum(lat) / len(lat), 1) if lat else None, "p95_ms": pct(0.95), "p99_ms": pct(0.99),
            "max_ms": round(lat[-1], 1) if lat else None, "degraded_responses": sum(1 for r in rs if r[4]),
            "status_counts": by_status}


def per_second(records, t0, lo, hi):
    """Bins of one second: total, failed and p95 latency of successful requests."""
    bins = {}
    for t, lat, status, kind, degraded in records:
        k = int((t - t0) // 1)
        if lo <= k <= hi:
            b = bins.setdefault(k, {"total": 0, "failed": 0, "lat": []})
            b["total"] += 1
            if not 200 <= status < 300:
                b["failed"] += 1
            else:
                b["lat"].append(lat)
    out = []
    for k in sorted(bins):
        lat = sorted(bins[k]["lat"])
        out.append({"t": k, "total": bins[k]["total"], "failed": bins[k]["failed"],
                    "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None})
    return out
