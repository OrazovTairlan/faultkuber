"""Open-loop HTTP load generator for the synthetic AITU platform.

Every request is logged as (send_time, latency_ms, status, kind). status 0 means
a transport error/timeout. Used by run_experiments.py and runnable on its own:

    python scripts/loadgen.py --rps 100 --seconds 30
"""
import argparse
import itertools
import json
import os
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection

BASE = (os.environ.get("LG_HOST", "127.0.0.1"), int(os.environ.get("LG_PORT", "18080")))
COURSES = ["CS401", "CS312", "CS205", "MA101"]
MIX = [("timetable", 40), ("courses", 20), ("login", 10), ("reg_read", 15), ("reg_write", 15)]


_local = threading.local()


def one_request(method, path, body=None, token=None, timeout=3.0):
    """One call over a per-thread keep-alive connection (a new TCP connection per request
    exhausts the Windows ephemeral-port range at 100+ req/s)."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = json.dumps(body) if body is not None else None
    t0 = time.time()
    for attempt in (0, 1):
        conn = getattr(_local, "conn", None)
        reused = conn is not None
        if conn is None:
            conn = _local.conn = HTTPConnection(*BASE, timeout=timeout)
        try:
            conn.request(method, path, payload, headers)
            resp = conn.getresponse()
            raw = resp.read()
            return t0, (time.time() - t0) * 1000, resp.status, raw
        except OSError:
            conn.close()
            _local.conn = None
            if reused and attempt == 0:
                continue
            return t0, (time.time() - t0) * 1000, 0, b""
        except Exception:
            conn.close()
            _local.conn = None
            if reused and attempt == 0:
                continue
            return t0, (time.time() - t0) * 1000, 0, b""


class LoadGen:
    def __init__(self, rps, workers=128):
        self.rps, self.records, self.acked = rps, [], []
        self.acked_t = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.counter = itertools.count(int(os.environ.get("LG_OFFSET", "1")))
        self.token = None
        self.thread = None

    def start(self):
        _, _, st, raw = one_request("POST", "/auth/login", {"student_id": "S0001"})
        assert st == 200, f"login failed: {st}"
        self.token = json.loads(raw)["token"]
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join()
        self.pool.shutdown(wait=True)

    def _loop(self):
        interval, nxt = 1.0 / self.rps, time.perf_counter()
        kinds = [k for k, w in MIX for _ in range(w)]
        while not self.stop.is_set():
            if self.paused.is_set():
                time.sleep(0.05)
                nxt = time.perf_counter()
                continue
            self.pool.submit(self._task, random.choice(kinds))
            nxt += interval
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)

    def _task(self, kind):
        n = next(self.counter)
        body = None
        if kind == "timetable":
            req = ("GET", "/timetable?group=SE-2301")
        elif kind == "courses":
            req = ("GET", "/courses")
        elif kind == "login":
            req = ("POST", "/auth/login"); body = {"student_id": f"S{n % 9000 + 1:04d}"}
        elif kind == "reg_read":
            req = ("GET", "/registrations/S0001")
        else:
            sid, course = f"S{n:07d}", COURSES[n % 4]
            req = ("POST", "/registrations"); body = {"student_id": sid, "course_code": course}
        t0, lat, st, _ = one_request(req[0], req[1], body, self.token if req[1].startswith("/reg") else None)
        with self.lock:
            self.records.append((t0, lat, st, kind))
            if kind == "reg_write" and st == 201:
                rid = f"{body['student_id']}:{body['course_code']}"
                self.acked.append(rid)
                self.acked_t[rid] = t0


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(len(v) * q))], 1)


def summarise(records, t_from=None, t_to=None):
    rs = [r for r in records if (t_from is None or r[0] >= t_from) and (t_to is None or r[0] <= t_to)]
    ok = [r for r in rs if 200 <= r[2] < 300]
    lat = [r[1] for r in ok]
    by_kind = {}
    for k in {r[3] for r in rs}:
        kr = [r for r in rs if r[3] == k]
        by_kind[k] = {"total": len(kr), "failed": sum(1 for r in kr if not 200 <= r[2] < 300)}
    return {"total": len(rs), "failed": len(rs) - len(ok),
            "success_pct": round(100 * len(ok) / len(rs), 3) if rs else None,
            "latency_ms": {"p50": pct(lat, .5), "p95": pct(lat, .95), "p99": pct(lat, .99),
                           "max": round(max(lat), 1) if lat else None,
                           "mean": round(statistics.mean(lat), 1) if lat else None},
            "by_kind": by_kind}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rps", type=int, default=100)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--dump", action="store_true", help="also print every request as a REC line")
    a = ap.parse_args()
    g = LoadGen(a.rps)
    g.start(); time.sleep(a.seconds); g.finish()
    if a.dump:
        for t, lat, st, kind in sorted(g.records):
            print(f"REC,{t:.3f},{lat:.1f},{st},{kind}")
    span = max(r[0] for r in g.records) - min(r[0] for r in g.records)
    print(json.dumps({"target_rps": a.rps, "achieved_rps": round(len(g.records) / span, 1),
                      **summarise(g.records)}))
