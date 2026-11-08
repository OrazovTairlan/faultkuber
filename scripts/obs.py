"""Observability toolkit: Prometheus-text parser, metrics collector (kubectl proxy), histogram maths, alert engine.

The collector scrapes /metrics of every pod once per second and keeps cumulative per-service counters; the alert
engine evaluates the rules of observability/alerts.json over those series, so detection times are computed from
the same evidence a dashboard would show.
"""
import http.client
import json
import re
import threading
import time

ROOT_PROXY = ("127.0.0.1", 8001)
LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+eE0-9.naif]+)$')
LABEL = re.compile(r'(\w+)="([^"]*)"')


def parse_prometheus(text):
    """Returns a list of (metric_name, labels_dict, value)."""
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line.strip())
        if m:
            out.append((m.group(1), dict(LABEL.findall(m.group(2) or "")), float(m.group(3))))
    return out


def histogram_quantile(q, buckets):
    """buckets: list of (upper_bound_float, cumulative_count); returns the q-quantile in the unit of the bounds."""
    buckets = sorted(buckets)
    total = buckets[-1][1] if buckets else 0
    if total <= 0:
        return None
    rank = q * total
    prev_bound, prev_count = 0.0, 0.0
    for bound, count in buckets:
        if count >= rank:
            if bound == float("inf"):
                return prev_bound
            if count == prev_count:
                return bound
            return prev_bound + (bound - prev_bound) * (rank - prev_count) / (count - prev_count)
        prev_bound, prev_count = bound, count
    return buckets[-1][0]


def percentile(values, q):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def kube_get(path, timeout=3.0):
    conn = http.client.HTTPConnection(*ROOT_PROXY, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read().decode()
        return resp.status, data
    finally:
        conn.close()


class Collector(threading.Thread):
    """Scrapes pod metrics and pod readiness once per `interval` seconds."""

    def __init__(self, namespace, interval=1.0, scrape_metrics=True, desired=None):
        super().__init__(daemon=True)
        self.ns, self.interval, self.scrape_metrics = namespace, interval, scrape_metrics
        self.desired = desired or {}
        self.series = []
        self.ready_series = []
        self.stop_flag = threading.Event()
        self.last = {}
        self.acc = {}
        self.errors = 0
        self.initialized = False
        self.cycles = 0

    def _pods(self):
        status, data = kube_get(f"/api/v1/namespaces/{self.ns}/pods")
        pods = []
        for item in json.loads(data)["items"]:
            meta = item["metadata"]
            if meta.get("deletionTimestamp") or item["status"].get("phase") != "Running":
                ready = False
            else:
                ready = any(c["type"] == "Ready" and c["status"] == "True" for c in item["status"].get("conditions", []))
            labels = meta.get("labels", {})
            pods.append({"name": meta["name"], "app": labels.get("app"), "ready": ready,
                         "terminating": bool(meta.get("deletionTimestamp"))})
        return pods

    def _scrape(self, pod, out, lock):
        try:
            status, text = kube_get(f"/api/v1/namespaces/{self.ns}/pods/{pod['name']}:8080/proxy/metrics", timeout=1.5)
            if status == 200:
                with lock:
                    out[pod["name"]] = (pod["app"], parse_prometheus(text))
        except (OSError, http.client.HTTPException):
            self.errors += 1

    def _accumulate(self, scraped):
        for pod, (app, samples) in scraped.items():
            cur = {}
            for name, labels, value in samples:
                if name == "aitu_http_requests_total":
                    cur[("req", labels["status"])] = cur.get(("req", labels["status"]), 0) + value
                elif name == "aitu_http_request_duration_seconds_bucket":
                    key = ("lat", labels["le"])
                    cur[key] = cur.get(key, 0) + value
                elif name == "aitu_http_request_duration_seconds_sum":
                    cur[("latsum", "")] = cur.get(("latsum", ""), 0) + value
                elif name == "aitu_events_total":
                    cur[("event", labels["event"])] = value
            bucket = self.acc.setdefault(app, {})
            if pod not in self.last and not self.initialized:
                self.last[pod] = cur
                continue
            prev = self.last.get(pod, {})
            for key, value in cur.items():
                delta = value - prev.get(key, 0.0)
                if delta < 0:
                    delta = value
                bucket[key] = bucket.get(key, 0.0) + delta
            self.last[pod] = cur

    def run(self):
        while not self.stop_flag.is_set():
            t0 = time.time()
            try:
                pods = self._pods()
                counts = {}
                for p in pods:
                    c = counts.setdefault(p["app"], {"ready": 0, "total": 0})
                    c["total"] += 1
                    c["ready"] += 1 if p["ready"] else 0
                self.ready_series.append((t0, counts))
                if self.scrape_metrics:
                    scraped, lock, threads = {}, threading.Lock(), []
                    for p in pods:
                        if p["ready"] and p["app"] in ("gateway", "auth", "course", "registration", "db"):
                            th = threading.Thread(target=self._scrape, args=(p, scraped, lock))
                            th.start()
                            threads.append(th)
                    for th in threads:
                        th.join(2.0)
                    self._accumulate(scraped)
                    self.cycles += 1
                    self.initialized = self.cycles >= 5
                    self.series.append((t0, {app: dict(v) for app, v in self.acc.items()}))
            except (OSError, ValueError, http.client.HTTPException):
                self.errors += 1
            time.sleep(max(0.0, self.interval - (time.time() - t0)))

    def stop(self):
        self.stop_flag.set()
        self.join(5)


def snapshot_at(series, t):
    """Latest cumulative snapshot not after time t (or None)."""
    best = None
    for ts, snap in series:
        if ts <= t:
            best = snap
        else:
            break
    return best


def window_delta(series, t, window):
    """Counter increase per service between t-window and t."""
    now, before = snapshot_at(series, t), snapshot_at(series, t - window)
    out = {}
    for app, cur in (now or {}).items():
        prev = (before or {}).get(app, {})
        out[app] = {k: v - prev.get(k, 0.0) for k, v in cur.items()}
    return out


class AlertEngine:
    """Evaluates the rules of observability/alerts.json over collector series (one evaluation per second)."""

    def __init__(self, rules):
        self.rules = rules

    @staticmethod
    def _ratio(delta, service):
        d = delta.get(service, {})
        total = sum(v for (kind, _), v in d.items() if kind == "req")
        bad = sum(v for (kind, status), v in d.items() if kind == "req" and status.startswith("5"))
        return (bad / total if total else 0.0), total

    @staticmethod
    def _p95_ms(delta, service):
        d = delta.get(service, {})
        buckets = [(float("inf") if le == "+Inf" else float(le), v) for (kind, le), v in d.items() if kind == "lat"]
        if not buckets:
            return None
        finite = {b: v for b, v in buckets}
        total = sum(v for (kind, _), v in d.items() if kind == "req")
        if total <= 0:
            return None
        finite[float("inf")] = total
        q = histogram_quantile(0.95, list(finite.items()))
        return None if q is None else q * 1000.0

    def condition(self, rule, series, ready_series, t):
        e = rule["expr"]
        kind = e["type"]
        if kind == "error_ratio":
            ratio, total = self._ratio(window_delta(series, t, e["window_s"]), e["service"])
            return total >= e["min_requests"] and ratio > e["threshold"]
        if kind == "latency_p95":
            delta = window_delta(series, t, e["window_s"])
            ratio_total = sum(v for (k, _), v in delta.get(e["service"], {}).items() if k == "req")
            p95 = self._p95_ms(delta, e["service"])
            return ratio_total >= e.get("min_requests", 20) and p95 is not None and p95 > e["threshold_ms"]
        if kind == "event_rate":
            delta = window_delta(series, t, e["window_s"])
            services = e["service"] if isinstance(e["service"], list) else [e["service"]]
            n = sum(delta.get(s, {}).get(("event", e["event"]), 0.0) for s in services)
            return n >= e["threshold"]
        if kind == "ready_replicas":
            snap = None
            for ts, counts in ready_series:
                if ts <= t:
                    snap = counts
                else:
                    break
            if not snap:
                return False
            for app, want in e["desired"].items():
                if snap.get(app, {"ready": 0})["ready"] < want:
                    return True
            return False
        raise ValueError(kind)

    def evaluate(self, series, ready_series, t_from, t_to, step=1.0):
        """Returns [{'alert', 'severity', 'fired_at', 'resolved_at'}] for the interval."""
        fired = []
        for rule in self.rules:
            holding_since, active = None, None
            t = t_from
            while t <= t_to:
                on = self.condition(rule, series, ready_series, t)
                if on and holding_since is None:
                    holding_since = t
                if not on:
                    holding_since = None
                    if active:
                        active["resolved_at"] = t
                        active = None
                if on and active is None and t - holding_since >= rule.get("for_s", 0):
                    active = {"alert": rule["name"], "severity": rule["severity"], "fired_at": t, "resolved_at": None}
                    fired.append(active)
                t += step
        return sorted(fired, key=lambda a: a["fired_at"])
