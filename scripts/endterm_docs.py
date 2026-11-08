"""Static documentation content shared by the report, docs/threat_model.md and observability/RUNBOOK.md."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

THREATS = [
    ("T1", "Password guessing / credential stuffing", "Spoofing", "Accounts",
     "PBKDF2 password check (no user enumeration), lockout after 5 failures per account (30 s), per-source failure cap, login routed to a fixed Auth replica, audit events LOGIN_FAILED / ACCOUNT_LOCKED, alert BruteForceLogin",
     "service.py: AuthHandler.login, LoginGuard, auth_replica_for", "E5 (A7, A8), E6 (lockout at attempt 6); unit LoginGuardTests; integration lockout test"),
    ("T2", "Token forgery, tampering, reuse of expired tokens", "Spoofing / Tampering", "Sessions",
     "HMAC-SHA256 signed tokens with exp (15 min) and role; constant-time comparison; signing key only from a Kubernetes Secret",
     "service.py: make_token, parse_token", "E5 (A2, A5); unit TokenTests (tamper, expiry, garbage)"),
    ("T3", "Broken authorization: reading or writing another student's data, admin access by a student", "Elevation of privilege / Information disclosure", "Registration records",
     "Ownership check at the gateway and again in Registration (defence in depth), administrator role for /admin/count, audit FORBIDDEN_ACCESS, alerts ForbiddenAccessSpike / UnauthorizedAccessSpike",
     "service.py: GatewayHandler.authenticate, RegistrationHandler", "E5 (A3, A4, A6): baseline violated, endterm denied; integration AuthorizationTests"),
    ("T4", "Malformed or hostile input: injection strings, wrong types, oversized bodies, path attacks", "Tampering / Denial of service", "Data integrity, availability",
     "Strict validation (anchored regex, JSON types, course whitelist), body limit 1 KB (413), path limit, record schema check in the DB, 4xx instead of 5xx, alert MalformedRequestBurst",
     "service.py: read_json, STUDENT_RE, valid_record", "E6 (12 malformed cases, junk records = 0); integration InputValidationTests; unit ValidationTests"),
    ("T5", "Request flooding by one client", "Denial of service", "Availability",
     "Token bucket per account (20 req/s, burst 40) and per source (400 req/s), 429 + Retry-After, timeouts and circuit breakers, alert RateLimiting",
     "service.py: TokenBucket, GatewayHandler", "E6 (960-request burst), E3; unit TokenBucketTests; integration RateLimitTests"),
    ("T6", "Duplicate or replayed requests", "Tampering", "Registration records",
     "Deterministic record id (student:course), created flag, 200 duplicate answer, audit DUPLICATE_REGISTRATION",
     "service.py: RegistrationHandler.create, Store.put", "E7 (replay of 300 requests creates 0 records); integration duplicate test"),
    ("T7", "Data destruction or encryption (ransomware), deletion of backups", "Tampering / Denial of service", "Database, backups",
     "Fail-closed data tier, synchronous replica, immutable off-namespace backups and timed recovery drill (Assignment 4)",
     "service.py: Store; k8s/security/20-vault.yaml; security/vault/", "Assignment 4 drill (RTO 8.0 min); E7 (node kill during writes)"),
    ("T8", "Lateral movement from a compromised pod", "Elevation of privilege", "Cluster, data tier",
     "Default-deny NetworkPolicy with explicit allows, Pod Security (non-root, dropped capabilities, seccomp), egress to the internet denied",
     "k8s/security/10-networkpolicies.yaml, k8s/*.yaml", "Segmentation matrix re-run on the endterm stack (results/endterm/segmentation.json)"),
    ("T9", "Information disclosure through logs and errors", "Information disclosure", "Secrets, passwords, tokens",
     "Logs carry ids and decisions only, never passwords, tokens or the signing key; generic error text for authentication failures",
     "service.py: audit, access log", "Log scan of all endterm pods (results/endterm/log_secret_scan.json)"),
    ("T10", "Dependency failure or outage (instance, database, non-critical service)", "Denial of service", "Availability",
     "2 replicas, probes, timeouts (200 ms) with one bounded retry, circuit breakers, 503 + Retry-After, stale-cache graceful degradation for courses and timetable, alerts ReplicaUnavailable / DatabaseUnavailable / DegradedMode / HighErrorRate",
     "service.py: forward, db_request, STALE_CACHE", "E1, E2, E4, E7"),
    ("T11", "Unsafe configuration (default secret, bad timeouts)", "Spoofing", "Platform",
     "Configuration check at start and in /readyz; refuses to start in production with a default or short secret; Pod Security enforced on the namespace",
     "service.py: validate_config; k8s/security/00-namespaces.yaml", "Unit ValidationTests; found a real defect during the first deployment (see lessons learned)"),
    ("T12", "Vulnerable third-party dependencies", "Tampering", "Supply chain",
     "Services use the Python standard library only; image runs as non-root with a read-only root filesystem",
     "app/Dockerfile", "Not tested (design measure); base image digest is not pinned - listed as a limitation"),
]

REQUIREMENTS = [
    ("FR1", "Student authentication", "Login with student id and password, signed expiring token, roles student/admin"),
    ("FR2", "Timetable and course information", "Read-only, non-critical features, available even when the Course service is down (degraded)"),
    ("FR3", "Course registration", "Create and read own registrations; idempotent, ownership-protected, strongly consistent across two DB nodes"),
    ("FR4", "Administration", "Administrator can read the number of registrations"),
    ("NFR1", "Availability", "99.9 % target (Assignment 3 model); measured as successful-request ratio during each experiment"),
    ("NFR2", "Latency", "p95 <= 500 ms at the gateway (requirement carried from the midterm)"),
    ("NFR3", "Recoverability", "RPO <= 5 min, RTO <= 30 min (Assignment 4 drill); no lost acknowledged writes in any experiment"),
    ("NFR4", "Observability", "At least three metrics, structured logs, request correlation, at least three alert rules with response"),
    ("NFR5", "Security", "Authentication, authorization, input validation, rate limiting, audit logging, secrets from the platform, configuration checks"),
    ("NFR6", "Testability", "Automated unit and integration tests; repeatable fault-injection scripts; explicit acceptance criteria"),
]

ASSUMPTIONS = [
    "All data are synthetic; no real users, production systems or credentials are involved.",
    "The midterm baseline is the Assignment 3 platform (git commit a6b1daa); it is deployed unmodified in namespace 'baseline' for a fair comparison.",
    "The cluster is a single-host 3-node kind cluster with the kindnet CNI; absolute numbers are lab numbers, relative comparisons are the result.",
    "Clients reach the gateway through one NodePort, so all clients share one source address; per-account limits therefore carry the abuse protection.",
    "TLS termination, a real identity provider and external log shipping are out of scope and listed as limitations.",
]

LIMITATIONS = [
    "Three repetitions per experiment and configuration: medians and ranges are reported, no statistical significance test is claimed.",
    "The alert engine evaluates the rules over the 1-second scrape series after each run; it is an equivalent of Prometheus alerting rules, not a continuously running pager.",
    "Rate-limit buckets and the stale cache live in the memory of each gateway pod, so the effective limit can reach (number of replicas) x (per-pod limit); login lockout state is kept consistent by routing each account to one Auth replica.",
    "Unexpected server errors (HTTP 500) still return the exception text; this information leak was found late and is listed, not fixed (build 3 changed only how log lines are written).",
    "NetworkPolicy enforcement in kindnet is evaluated in user space and costs latency under load (see E3 ablation); an eBPF CNI would reduce this.",
    "No distributed tracing backend: correlation is by request id in the logs; no Prometheus/Grafana (a custom collector, alert engine and HTML dashboard are used instead).",
    "The demonstration is provided as a script with a recorded transcript; a screen recording has to be made by the team.",
]


def write_markdown():
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    lines = ["# Threat model (synthetic AITU platform, endterm)", "",
             "Assets: student accounts and sessions, registration records, availability of the service, backups, configuration and secrets.",
             "Actors: legitimate student, malicious student (misuse of own access), anonymous external attacker, compromised pod, operator error.",
             "Trust boundaries: internet -> gateway; gateway -> services; services -> database; cluster nodes -> backup vault.", "",
             "| ID | Threat | STRIDE | Control | Implemented in | Evidence |", "|---|---|---|---|---|---|"]
    for t in THREATS:
        lines.append(f"| {t[0]} | {t[1]} | {t[2]} | {t[4]} | {t[5]} | {t[6]} |")
    with open(os.path.join(ROOT, "docs", "threat_model.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(ROOT, "observability", "alerts.json"), encoding="utf-8") as f:
        rules = json.load(f)
    run = ["# Alert runbook", "", "Rules are defined in `observability/alerts.json` and evaluated by `scripts/obs.py` (AlertEngine).", "",
           "| Alert | Severity | What it detects | Threshold | Expected response |", "|---|---|---|---|---|"]
    for r in rules:
        run.append(f"| {r['name']} | {r['severity']} | {r['detects']} | {r['condition']} | {r['response']} |")
    with open(os.path.join(ROOT, "observability", "RUNBOOK.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(run) + "\n")


if __name__ == "__main__":
    write_markdown()
    print("docs written")
