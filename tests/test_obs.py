"""Tests of the observability toolkit: Prometheus parser, histogram quantiles, alert evaluation."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import obs


RULES = json.load(open(os.path.join(ROOT, "observability", "alerts.json"), encoding="utf-8"))


def snap(gw_ok=0, gw_5xx=0, lat_fast=0, lat_slow=0, events=None):
    d = {("req", "200"): gw_ok, ("req", "503"): gw_5xx}
    total = gw_ok + gw_5xx
    for le in ("0.005", "0.01", "0.025", "0.05", "0.1", "0.25"):
        d[("lat", le)] = lat_fast
    for le in ("0.5", "1.0", "2.5"):
        d[("lat", le)] = lat_fast + lat_slow
    d[("lat", "+Inf")] = total
    for k, v in (events or {}).items():
        d[("event", k)] = v
    return d


class ParserTests(unittest.TestCase):
    def test_parses_labels_and_values(self):
        text = '# TYPE x counter\naitu_http_requests_total{service="gw",route="/a",status="200"} 12\naitu_gauge{service="gw"} 1.5\n'
        rows = obs.parse_prometheus(text)
        self.assertEqual(rows[0], ("aitu_http_requests_total", {"service": "gw", "route": "/a", "status": "200"}, 12.0))
        self.assertEqual(rows[1][2], 1.5)


class QuantileTests(unittest.TestCase):
    def test_quantile_interpolates(self):
        buckets = [(0.1, 50), (0.5, 90), (float("inf"), 100)]
        self.assertAlmostEqual(obs.histogram_quantile(0.5, buckets), 0.1, places=6)
        self.assertGreater(obs.histogram_quantile(0.95, buckets), 0.1)

    def test_empty_histogram(self):
        self.assertIsNone(obs.histogram_quantile(0.95, [(0.1, 0), (float("inf"), 0)]))

    def test_percentile_of_values(self):
        self.assertEqual(obs.percentile(list(range(1, 101)), 0.95), 96)
        self.assertIsNone(obs.percentile([], 0.95))


class AlertTests(unittest.TestCase):
    def engine(self, names):
        return obs.AlertEngine([r for r in RULES if r["name"] in names])

    def test_error_ratio_fires_only_above_threshold(self):
        series = [(0, {"gateway": snap(100, 0)}), (5, {"gateway": snap(200, 0)}), (10, {"gateway": snap(260, 40)})]
        fired = self.engine(["HighErrorRate"]).evaluate(series, [], 0, 10)
        self.assertTrue(fired)
        self.assertGreaterEqual(fired[0]["fired_at"], 8)

    def test_no_alert_on_healthy_traffic(self):
        series = [(t, {"gateway": snap(100 * t, 0, lat_fast=100 * t)}) for t in range(0, 20)]
        self.assertEqual(self.engine(["HighErrorRate", "HighLatencyP95"]).evaluate(series, [], 0, 19), [])

    def test_latency_alert_when_most_requests_are_slow(self):
        series = [(t, {"gateway": snap(100 * t, 0, lat_fast=10 * t, lat_slow=90 * t)}) for t in range(0, 20)]
        fired = self.engine(["HighLatencyP95"]).evaluate(series, [], 0, 19)
        self.assertTrue(fired)

    def test_event_rate_rule(self):
        series = [(0, {"auth": {("event", "login_failed"): 0}}), (10, {"auth": {("event", "login_failed"): 12}})]
        fired = self.engine(["BruteForceLogin"]).evaluate(series, [], 0, 10)
        self.assertEqual([a["alert"] for a in fired], ["BruteForceLogin"])

    def test_replica_alert_fires_and_resolves(self):
        ready = [(t, {"registration": {"ready": 1 if 3 <= t < 6 else 2, "total": 2}, "gateway": {"ready": 2, "total": 2},
                      "auth": {"ready": 2, "total": 2}, "course": {"ready": 2, "total": 2}, "db": {"ready": 2, "total": 2}})
                 for t in range(0, 10)]
        fired = self.engine(["ReplicaUnavailable"]).evaluate([], ready, 0, 9)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["fired_at"], 4)
        self.assertEqual(fired[0]["resolved_at"], 6)

    def test_every_rule_is_documented(self):
        for rule in RULES:
            for key in ("name", "detects", "condition", "response", "expr", "severity"):
                self.assertIn(key, rule, rule["name"])
        self.assertGreaterEqual(len(RULES), 3)


if __name__ == "__main__":
    unittest.main()
