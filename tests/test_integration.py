"""Integration tests of the critical workflows: all five roles run in-process and are called over real HTTP."""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import Platform, login, request, service

PLATFORM = None


def setUpModule():
    global PLATFORM
    PLATFORM = Platform().start()


def tearDownModule():
    PLATFORM.stop()


class AuthenticationTests(unittest.TestCase):
    def test_login_returns_expiring_signed_token(self):
        status, token = login("S1001")
        self.assertEqual(status, 200)
        claims = service.parse_token(token)
        self.assertEqual(claims["sub"], "S1001")
        self.assertGreater(claims["exp"], time.time())

    def test_wrong_password_is_401_without_user_enumeration(self):
        s1, b1, _ = request("POST", "/auth/login", {"student_id": "S1002", "password": "wrong"})
        s2, b2, _ = request("POST", "/auth/login", {"student_id": "S9999", "password": "wrong"})
        self.assertEqual((s1, s2), (401, 401))
        self.assertEqual(b1["error"], b2["error"])

    def test_account_lockout_after_repeated_failures_even_for_correct_password(self):
        for _ in range(service.LOGIN_MAX_FAILURES):
            request("POST", "/auth/login", {"student_id": "S1003", "password": "bad"})
        status, _ = login("S1003")
        self.assertEqual(status, 429)
        audits = [l for l in PLATFORM.lines() if l.get("event") == "ACCOUNT_LOCKED" and l.get("subject") == "S1003"]
        self.assertTrue(audits)

    def test_missing_expired_and_forged_tokens_rejected(self):
        self.assertEqual(request("GET", "/registrations/S1004")[0], 401)
        self.assertEqual(request("GET", "/registrations/S1004", token="garbage")[0], 401)
        expired = service.make_token("S1004", ttl=-5)
        self.assertEqual(request("GET", "/registrations/S1004", token=expired)[0], 401)


class RegistrationWorkflowTests(unittest.TestCase):
    def test_register_then_read_back(self):
        _, token = login("S2001")
        status, body, _ = request("POST", "/registrations", {"student_id": "S2001", "course_code": "CS401"}, token)
        self.assertEqual(status, 201)
        status, body, _ = request("GET", "/registrations/S2001", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in body["registrations"]], ["S2001:CS401"])

    def test_duplicate_request_creates_exactly_one_record(self):
        _, token = login("S2002")
        first = request("POST", "/registrations", {"student_id": "S2002", "course_code": "CS312"}, token)
        second = request("POST", "/registrations", {"student_id": "S2002", "course_code": "CS312"}, token)
        self.assertEqual((first[0], second[0]), (201, 200))
        self.assertTrue(second[1]["duplicate"])
        _, body, _ = request("GET", "/registrations/S2002", token=token)
        self.assertEqual(len(body["registrations"]), 1)


class AuthorizationTests(unittest.TestCase):
    def test_student_cannot_write_or_read_for_another_student(self):
        _, attacker = login("S3001")
        _, victim = login("S3002")
        before = len(service.STORE.records)
        status, _, _ = request("POST", "/registrations", {"student_id": "S3002", "course_code": "CS401"}, attacker)
        self.assertEqual(status, 403)
        self.assertEqual(request("GET", "/registrations/S3002", token=attacker)[0], 403)
        self.assertEqual(len(service.STORE.records), before)
        self.assertEqual(request("GET", "/registrations/S3002", token=victim)[0], 200)
        self.assertTrue([l for l in PLATFORM.lines() if l.get("event") == "FORBIDDEN_ACCESS" and l.get("subject") == "S3001"])

    def test_admin_endpoint_requires_admin_role(self):
        _, student = login("S3003")
        _, admin = login("A0001")
        self.assertEqual(request("GET", "/admin/count", token=student)[0], 403)
        status, body, _ = request("GET", "/admin/count", token=admin)
        self.assertEqual(status, 200)
        self.assertIn("count", body)


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        _, self.token = login("S4001")
        self.before = len(service.STORE.records)

    def test_malformed_and_hostile_inputs_are_rejected_and_nothing_is_stored(self):
        cases = [
            ({"raw_body": "{not json"}, 400),
            ({"raw_body": "[1,2,3]"}, 400),
            ({"body": {"student_id": "S4001", "course_code": "NOPE"}}, 400),
            ({"body": {"student_id": "S4001; DROP TABLE x", "course_code": "CS401"}}, 400),
            ({"body": {"student_id": 12345, "course_code": "CS401"}}, 400),
            ({"body": {"student_id": "S4001"}}, 400),
            ({"raw_body": '{"student_id":"S4001","course_code":"CS401","pad":"' + "x" * 5000 + '"}'}, 413),
        ]
        for kwargs, expected in cases:
            status, _, _ = request("POST", "/registrations", token=self.token, **kwargs)
            self.assertEqual(status, expected, kwargs)
        self.assertEqual(len(service.STORE.records), self.before)

    def test_invalid_path_parameter_rejected(self):
        self.assertEqual(request("GET", "/registrations/not-an-id", token=self.token)[0], 400)

    def test_login_input_is_validated_and_rejections_are_audited(self):
        self.assertEqual(request("POST", "/auth/login", {"student_id": "x" * 500, "password": "p"})[0], 400)
        self.assertEqual(request("POST", "/auth/login", {"student_id": "S4001", "password": 5})[0], 400)
        self.assertEqual(request("POST", "/auth/login", {"student_id": "S4001"})[0], 400)
        rejected = [l for l in PLATFORM.lines() if l.get("event") == "LOGIN_REJECTED"]
        self.assertGreaterEqual(len(rejected), 3)


class RateLimitTests(unittest.TestCase):
    def test_burst_from_one_account_is_throttled_but_others_unaffected(self):
        _, noisy = login("S5001")
        _, calm = login("S5002")
        statuses = [request("GET", "/registrations/S5001", token=noisy)[0] for _ in range(40)]
        self.assertIn(429, statuses)
        self.assertIn(200, statuses)
        self.assertEqual(request("GET", "/registrations/S5002", token=calm)[0], 200)
        self.assertTrue([l for l in PLATFORM.lines() if l.get("event") == "RATE_LIMITED"])


class ObservabilityTests(unittest.TestCase):
    def test_request_id_is_echoed_and_correlated_across_services(self):
        _, token = login("S6001")
        rid = "trace-abc-123"
        status, _, headers = request("POST", "/registrations", {"student_id": "S6001", "course_code": "MA101"}, token,
                                     headers={"X-Request-ID": rid})
        self.assertEqual(status, 201)
        self.assertEqual(headers["x-request-id"], rid)
        hits = [l for l in PLATFORM.lines() if l.get("rid") == rid]
        routes = {l.get("route") for l in hits}
        self.assertIn("/registrations", routes)
        self.assertIn("/auth/verify", routes)
        self.assertIn("/db/write", routes)
        self.assertGreaterEqual(len(hits), 4)

    def test_hostile_request_id_is_sanitised(self):
        _, _, headers = request("GET", "/timetable", headers={"X-Request-ID": "x\"};evil"})
        self.assertNotIn('"', headers["x-request-id"])

    def test_metrics_endpoint_exposes_counters_and_histogram(self):
        request("GET", "/timetable")
        status, text, _ = request("GET", "/metrics")
        self.assertEqual(status, 200)
        self.assertIn("aitu_http_requests_total", text)
        self.assertIn("aitu_http_request_duration_seconds_bucket", text)
        self.assertIn("aitu_events_total", text)

    def test_structured_log_lines_are_valid_json_with_required_fields(self):
        request("GET", "/courses")
        access = [l for l in PLATFORM.lines() if "route" in l and l.get("route") == "/courses"]
        self.assertTrue(access)
        for key in ("ts", "svc", "rid", "status", "ms", "method"):
            self.assertIn(key, access[-1])


class ResilienceTests(unittest.TestCase):
    def test_graceful_degradation_serves_stale_courses_when_course_service_is_down(self):
        status, body, headers = request("GET", "/courses")
        self.assertEqual(status, 200)
        self.assertNotIn("x-degraded", headers)
        PLATFORM.stop_role("course")
        try:
            status, body, headers = request("GET", "/courses")
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("x-degraded"), "stale-cache")
            self.assertTrue(body["degraded"])
            status, body, headers = request("GET", "/timetable?group=SE-2302")
            self.assertEqual(status, 503)
            self.assertIn("retry-after", headers)
        finally:
            PLATFORM.start_role("course")
            service.UP_BREAKERS["course"] = service.CircuitBreaker(5, 2.0)

    def test_database_outage_gives_controlled_503_and_recovers(self):
        _, token = login("S7001")
        PLATFORM.stop_role("db")
        try:
            status, body, headers = request("POST", "/registrations", {"student_id": "S7001", "course_code": "CS205"}, token)
            self.assertEqual(status, 503)
            self.assertIn("retry-after", headers)
        finally:
            PLATFORM.start_role("db")
            service.BREAKER.record(True)
        time.sleep(0.1)
        status, _, _ = request("POST", "/registrations", {"student_id": "S7001", "course_code": "CS205"}, token)
        self.assertIn(status, (200, 201))

    def test_non_critical_failure_does_not_break_registration(self):
        _, token = login("S7002")
        PLATFORM.stop_role("course")
        try:
            status, _, _ = request("POST", "/registrations", {"student_id": "S7002", "course_code": "CS401"}, token)
            self.assertEqual(status, 201)
        finally:
            PLATFORM.start_role("course")
            service.UP_BREAKERS["course"] = service.CircuitBreaker(5, 2.0)


class GatewayEfficiencyTests(unittest.TestCase):
    def test_verified_token_is_cached_so_auth_is_called_once_for_repeated_requests(self):
        _, token = login("S8001")
        before = len([l for l in PLATFORM.lines() if l.get("route") == "/auth/verify"])
        for _ in range(5):
            self.assertEqual(request("GET", "/registrations/S8001", token=token)[0], 200)
        after = len([l for l in PLATFORM.lines() if l.get("route") == "/auth/verify"])
        self.assertEqual(after - before, 1)

    def test_rate_limit_audit_is_sampled_but_every_event_is_counted(self):
        _, token = login("S8002")
        statuses = [request("GET", "/registrations/S8002", token=token)[0] for _ in range(60)]
        limited = statuses.count(429)
        self.assertGreater(limited, 10)
        audit_lines = [l for l in PLATFORM.lines() if l.get("event") == "RATE_LIMITED" and l.get("subject") == "S8002"]
        self.assertTrue(0 < len(audit_lines) < limited)
        counted = service.METRICS.events["rate_limited"]
        self.assertGreaterEqual(counted, limited)


class LogIntegrityTests(unittest.TestCase):
    def test_concurrent_requests_never_interleave_json_log_lines(self):
        import threading
        _, token = login("S8101")

        def burst():
            for _ in range(15):
                request("GET", "/timetable")
                request("GET", "/registrations/S8101", token=token)
        threads = [threading.Thread(target=burst) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bad = 0
        for line in PLATFORM.log.getvalue().splitlines():
            if line.startswith("{") or line.startswith("}"):
                try:
                    json.loads(line)
                except ValueError:
                    bad += 1
        self.assertEqual(bad, 0)


class DataIntegrityTests(unittest.TestCase):
    def test_no_record_in_store_violates_the_schema(self):
        for rec in list(service.STORE.records.values()):
            self.assertTrue(service.valid_record(rec), rec)

    def test_ids_are_unique_by_construction(self):
        ids = [r["id"] for r in service.STORE.records.values()]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
