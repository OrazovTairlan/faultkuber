"""Unit tests of the pure building blocks (no network)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import service as svc


class TokenTests(unittest.TestCase):
    def test_roundtrip(self):
        claims = svc.parse_token(svc.make_token("S0001", "student"))
        self.assertEqual((claims["sub"], claims["role"]), ("S0001", "student"))

    def test_tampered_payload_rejected(self):
        payload, sig = svc.make_token("S0001").split(".")
        forged = svc.b64(json.dumps({"sub": "A0001", "role": "admin", "iat": 0, "exp": 9999999999}).encode())
        self.assertIsNone(svc.parse_token(f"{forged}.{sig}"))

    def test_expired_rejected(self):
        self.assertIsNone(svc.parse_token(svc.make_token("S0001", ttl=-1)))

    def test_garbage_rejected(self):
        for bad in ("", "abc", "a.b.c", "....", "x." + "y" * 40):
            self.assertIsNone(svc.parse_token(bad), bad)


class PasswordTests(unittest.TestCase):
    def test_correct_and_wrong(self):
        self.assertTrue(svc.check_password("S0001", "pw-S0001"))
        self.assertFalse(svc.check_password("S0001", "pw-S0002"))
        self.assertFalse(svc.check_password("S0001", ""))


class LoginGuardTests(unittest.TestCase):
    def test_lockout_after_max_failures_then_expiry(self):
        g = svc.LoginGuard(max_failures=3, window=60, lock=30, ip_cap=100)
        self.assertFalse(g.failure("S1", "1.1.1.1", now=0))
        self.assertFalse(g.failure("S1", "1.1.1.1", now=1))
        self.assertTrue(g.failure("S1", "1.1.1.1", now=2))
        self.assertTrue(g.is_locked("S1", now=10))
        self.assertFalse(g.is_locked("S1", now=40))

    def test_failures_outside_window_do_not_count(self):
        g = svc.LoginGuard(max_failures=3, window=10, lock=30, ip_cap=100)
        g.failure("S1", "ip", now=0)
        g.failure("S1", "ip", now=20)
        self.assertFalse(g.failure("S1", "ip", now=41))

    def test_success_resets_counter(self):
        g = svc.LoginGuard(max_failures=2, window=60, lock=30, ip_cap=100)
        g.failure("S1", "ip", now=0)
        g.success("S1")
        self.assertFalse(g.failure("S1", "ip", now=1))

    def test_ip_cap(self):
        g = svc.LoginGuard(max_failures=100, window=60, lock=30, ip_cap=3)
        for i in range(3):
            g.failure(f"S{i}", "9.9.9.9", now=i)
        self.assertTrue(g.ip_blocked("9.9.9.9", now=5))
        self.assertFalse(g.ip_blocked("8.8.8.8", now=5))


class TokenBucketTests(unittest.TestCase):
    def test_burst_then_deny_then_refill(self):
        b = svc.TokenBucket(rate=2, burst=3)
        self.assertEqual([b.allow("k", now=0) for _ in range(4)], [True, True, True, False])
        self.assertTrue(b.allow("k", now=1.0))

    def test_keys_are_independent(self):
        b = svc.TokenBucket(rate=1, burst=1)
        self.assertTrue(b.allow("a", now=0))
        self.assertFalse(b.allow("a", now=0))
        self.assertTrue(b.allow("b", now=0))


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_and_half_opens(self):
        cb = svc.CircuitBreaker(threshold=2, reset=0.05)
        cb.record(False)
        self.assertTrue(cb.allow())
        cb.record(False)
        self.assertFalse(cb.allow())
        self.assertTrue(cb.is_open())
        import time
        time.sleep(0.06)
        self.assertTrue(cb.allow())
        cb.record(True)
        self.assertFalse(cb.is_open())


class ValidationTests(unittest.TestCase):
    def test_student_id_patterns(self):
        for good in ("S0001", "S1234567", "A0001"):
            self.assertTrue(svc.STUDENT_RE.match(good), good)
        for bad in ("", "s0001", "S1", "S12345678", "S0001; DROP TABLE", "S00 01", "../etc", "S0001\n"):
            self.assertFalse(svc.REGISTRANT_RE.match(bad), repr(bad))

    def test_valid_record(self):
        good = {"id": "S0001:CS401", "student_id": "S0001", "course_code": "CS401"}
        self.assertTrue(svc.valid_record(good))
        self.assertFalse(svc.valid_record({**good, "id": "S0002:CS401"}))
        self.assertFalse(svc.valid_record({**good, "course_code": "XX"}))
        self.assertFalse(svc.valid_record("not a dict"))

    def test_route_label_bounds_cardinality(self):
        self.assertEqual(svc.route_label("/registrations/S0001"), "/registrations/{id}")
        self.assertEqual(svc.route_label("/registrations/zzzzzzzzzzz"), "/registrations/{id}")
        self.assertEqual(svc.route_label("/random/" + "x" * 50), "other")

    def test_config_check_accepts_the_production_settings_and_flags_unsafe_ones(self):
        old = (svc.UPSTREAM_TIMEOUT, svc.SECRET)
        try:
            svc.UPSTREAM_TIMEOUT = 0.2
            self.assertEqual(svc.validate_config(), [])
            svc.UPSTREAM_TIMEOUT = 0.4
            self.assertTrue(any("latency budget" in p for p in svc.validate_config()))
            svc.UPSTREAM_TIMEOUT = 0.2
            svc.SECRET = b"short"
            self.assertTrue(any("shorter" in p for p in svc.validate_config()))
            svc.SECRET = svc.DEFAULT_SECRET.encode()
            self.assertTrue(any("default AUTH_SECRET" in p for p in svc.validate_config()))
            old_service = svc.SERVICE
            svc.SERVICE = "db"
            try:
                self.assertEqual(svc.validate_config(), [])
            finally:
                svc.SERVICE = old_service
        finally:
            svc.UPSTREAM_TIMEOUT, svc.SECRET = old


class AuthAffinityTests(unittest.TestCase):
    def test_same_account_always_maps_to_the_same_replica_and_spreads_over_all(self):
        import time
        svc.REPLICA_CACHE.update(t=time.time(), ips=["10.0.0.1", "10.0.0.2"])
        first = {sid: svc.auth_replica_for(sid) for sid in (f"S{n:04d}" for n in range(1000, 1060))}
        again = {sid: svc.auth_replica_for(sid) for sid in first}
        self.assertEqual(first, again)
        self.assertEqual({v.split(":")[0] for v in first.values()}, {"10.0.0.1", "10.0.0.2"})
        svc.REPLICA_CACHE.update(t=time.time(), ips=[])
        self.assertIsNone(svc.auth_replica_for("S1000"))
        svc.REPLICA_CACHE.update(t=0.0, ips=[])


class MetricsTests(unittest.TestCase):
    def test_histogram_and_counters_render(self):
        m = svc.Metrics()
        for s in (0.004, 0.02, 0.4):
            m.observe("/x", 200, s)
        m.observe("/x", 500, 0.01)
        m.event("rate_limited", 3)
        text = m.render()
        self.assertIn('aitu_http_requests_total{service="%s",route="/x",status="200"} 3' % svc.SERVICE, text)
        self.assertIn('le="0.005"} 1', text)
        self.assertIn('le="+Inf"} 4', text)
        self.assertIn('event="rate_limited"} 3', text)


class StoreTests(unittest.TestCase):
    def test_idempotent_put_and_damage_detection(self):
        old = svc.DATA_DIR
        with tempfile.TemporaryDirectory() as d:
            svc.DATA_DIR = d
            try:
                s = svc.Store()
                rec = {"id": "S0001:CS401", "student_id": "S0001", "course_code": "CS401"}
                self.assertTrue(s.put(rec))
                self.assertFalse(s.put(rec))
                os.remove(os.path.join(d, "records.jsonl"))
                self.assertTrue(svc.Store().damaged)
            finally:
                svc.DATA_DIR = old


if __name__ == "__main__":
    unittest.main()
