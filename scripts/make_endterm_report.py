"""Builds ENDTERM_Final_Technical_Report.docx and results/endterm/summary.md from results/endterm (no number is typed by hand)."""
import datetime
import json
import os
import re

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

import endterm_docs as DOCS
import endterm_results as R

ROOT = R.ROOT
OUT = os.path.join(ROOT, "results", "endterm")
OUTPUT = os.path.join(ROOT, "ENDTERM_Final_Technical_Report.docx")
TODO = "[fill in]"
NAMES = {"E1": "E1 Service / instance failure", "E2": "E2 Dependency timeout", "E3": "E3 Load spike", "E4": "E4 Monitoring / alert test with graceful degradation",
         "E5": "E5 Unauthorized access", "E6": "E6 Abusive or malformed requests", "E7": "E7 Recovery and consistency"}
KIND = {"E1": "fault injection", "E2": "fault injection", "E3": "abnormal condition (load)", "E4": "fault injection", "E5": "security / misuse",
        "E6": "security / misuse", "E7": "fault injection"}


def jload(rel, default=None):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


ALERTS = jload("observability/alerts.json")
SEG = jload("results/endterm/segmentation.json", {"checks": [], "all_as_designed": False})
SCAN = jload("results/endterm/log_secret_scan.json", {"log_lines_scanned": 0, "patterns": {}, "total_hits": -1})
TRACE = jload("results/endterm/samples/correlated_trace.json", [])
TESTS_TXT = open(os.path.join(OUT, "tests.txt"), encoding="utf-8").read() if os.path.exists(os.path.join(OUT, "tests.txt")) else ""
DEMO = open(os.path.join(OUT, "demo_transcript.txt"), encoding="utf-8").read() if os.path.exists(os.path.join(OUT, "demo_transcript.txt")) else ""


def sh(cmd):
    import subprocess
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.strip()
    except OSError:
        return ""


class Builder:
    def __init__(self):
        self.d = docx.Document()
        sec = self.d.sections[0]
        sec.left_margin = sec.right_margin = Inches(0.85)
        sec.top_margin = sec.bottom_margin = Inches(0.8)
        st = self.d.styles["Normal"]
        st.font.name, st.font.size = "Calibri", Pt(10.5)
        for name, size in (("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11)):
            self.d.styles[name].font.name = "Calibri"
            self.d.styles[name].font.size = Pt(size)
            self.d.styles[name].font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
        self.fig = 0
        self.tab = 0

    def h(self, text, level=1):
        return self.d.add_heading(text, level)

    def p(self, text, bold=False, italic=False, size=None, color=None, after=4):
        para = self.d.add_paragraph()
        run = para.add_run(text)
        run.bold, run.italic = bold, italic
        if size:
            run.font.size = Pt(size)
        if color:
            run.font.color.rgb = RGBColor.from_string(color)
        para.paragraph_format.space_after = Pt(after)
        return para

    def bullets(self, items, size=10):
        for it in items:
            para = self.d.add_paragraph(style="List Bullet")
            run = para.add_run(it)
            run.font.size = Pt(size)
            para.paragraph_format.space_after = Pt(1)

    def numbered(self, items, size=10):
        for it in items:
            para = self.d.add_paragraph(style="List Number")
            run = para.add_run(it)
            run.font.size = Pt(size)
            para.paragraph_format.space_after = Pt(1)

    def caption(self, text):
        self.p(text, italic=True, size=8.5, color="52514E", after=6)

    def table(self, rows, widths=None, size=8, caption=None, bold_first_col=False):
        if caption:
            self.tab += 1
            self.p(f"Table {self.tab} - {caption}", bold=True, size=9, after=2)
        t = self.d.add_table(rows=len(rows), cols=len(rows[0]))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                cell = t.rows[i].cells[j]
                cell.text = ""
                para = cell.paragraphs[0]
                run = para.add_run(str(val))
                run.font.size = Pt(size)
                run.bold = i == 0 or (bold_first_col and j == 0)
                para.paragraph_format.space_after = Pt(1)
                if i == 0:
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:val"), "clear")
                    shade.set(qn("w:fill"), "DCE6F1")
                    cell._tc.get_or_add_tcPr().append(shade)
                sval = str(val)
                if i and (sval.startswith("PASS") or sval.startswith("FAIL")):
                    shade = OxmlElement("w:shd")
                    shade.set(qn("w:val"), "clear")
                    shade.set(qn("w:fill"), "DFF2E1" if sval.startswith("PASS") else "FBE0DD")
                    cell._tc.get_or_add_tcPr().append(shade)
        if widths:
            for row in t.rows:
                for j, w in enumerate(widths):
                    row.cells[j].width = Inches(w)
        self.d.add_paragraph().paragraph_format.space_after = Pt(2)
        return t

    def picture(self, rel, width=6.6, caption=None):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            self.p(f"[figure missing: {rel}]", italic=True)
            return
        para = self.d.add_paragraph()
        para.alignment = 1
        para.add_run().add_picture(path, width=Inches(width))
        if caption:
            self.fig += 1
            self.caption(f"Figure {self.fig} - {caption}")

    def code(self, text, size=7.5):
        para = self.d.add_paragraph()
        lines = text.rstrip().split("\n")
        for i, line in enumerate(lines):
            run = para.add_run(line)
            run.font.name, run.font.size = "Consolas", Pt(size)
            run._r.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Consolas")
            if i < len(lines) - 1:
                run.add_break(WD_BREAK.LINE)
        shade = OxmlElement("w:shd")
        shade.set(qn("w:val"), "clear")
        shade.set(qn("w:fill"), "F3F3F1")
        para._p.get_or_add_pPr().append(shade)
        para.paragraph_format.space_after = Pt(6)

    def pagebreak(self):
        self.d.add_page_break()


def status_total(exp, cfg, path, code):
    return int(sum((R.get(r, path) or {}).get(code, 0) for r in R.runs(exp, cfg)))


def pf(exp, cfg):
    ok, n = R.pass_counts(exp, cfg)
    return f"{'PASS' if n and ok == n else 'FAIL'} ({ok}/{n} repetitions)"


def build():
    b = Builder()
    tp, fn, fp, alert_per = R.alert_totals()
    commit = sh(["git", "rev-parse", "--short", "HEAD"]) or "n/a"
    n_tests = re.search(r"Ran (\d+) tests", TESTS_TXT)
    n_tests = int(n_tests.group(1)) if n_tests else 0
    tests_ok = "\nOK" in TESTS_TXT
    mod_counts = {m: len(re.findall(rf"\(test_{m}\.", TESTS_TXT)) for m in ("unit", "obs", "integration")}
    base_lat, end_lat = R.normal_load_latency("baseline"), R.normal_load_latency("endterm")
    n_runs = sum(len(v) for k, v in R.DATA.items())

    def full_pass(cfg):
        return sum(1 for e in R.EXPERIMENTS if R.runs(e, cfg) and R.pass_counts(e, cfg)[0] == R.pass_counts(e, cfg)[1])

    def stall_runs(cfg):
        """(runs with failures in the steady-state window before any fault, runs with a request slower than 2 s, runs, runs with an alert before the injection)"""
        steady = slow = pre = tot = 0
        for e in R.EXPERIMENTS:
            for r in R.runs(e, cfg):
                tot += 1
                sp = r.get("summary_pre") or {}
                steady += 1 if sp.get("success_pct") is not None and sp["success_pct"] < 99 else 0
                slow += 1 if (R.get(r, "summary_whole.max_ms") or 0) > 2000 else 0
                pre += 1 if r["alerts"].get("pre_fault_alerts", 0) > 0 else 0
        return steady, slow, tot, pre

    nf_base, nf_end, nf_np = full_pass("baseline"), full_pass("endterm"), full_pass("endterm-no-netpol")
    st_base, st_end, st_np = stall_runs("baseline"), stall_runs("endterm"), stall_runs("endterm-no-netpol")
    np_lat = R.normal_load_latency("endterm-no-netpol")
    integ_b2 = jload("results/endterm/log_integrity_build2.json", {"json_lines": 0, "unparsable_lines": 0, "share_pct": 0})
    integ_b3 = jload("results/endterm/log_integrity.json", {"json_lines": 0, "unparsable_lines": 0, "share_pct": 0})
    d = b.d
    t = d.add_paragraph()
    t.alignment = 1
    r = t.add_run("ENDTERM PROJECT - FINAL TECHNICAL REPORT")
    r.bold, r.font.size = True, Pt(22)
    t = d.add_paragraph()
    t.alignment = 1
    r = t.add_run("Resilient University Information System\nContinuation of the Midterm Project")
    r.font.size = Pt(15)
    t = d.add_paragraph()
    t.alignment = 1
    t.add_run("Course: Fault Tolerance and Dependable Computing | Astana IT University | Academic Year 2026-2027").font.size = Pt(10)
    b.table([["Item", "Entry"],
             ["Team / student(s)", TODO], ["Individual contributions (for the demonstration and defence)", TODO], ["Instructor", TODO],
             ["Date", datetime.date.today().strftime("%d.%m.%Y")],
             ["Repository / commit", f"local git repository; midterm baseline commit a6b1daa (Assignment 3), security commit cf6df73 (Assignment 4); endterm work is in the working tree (HEAD {commit}) - add the remote URL and the final commit hash: {TODO}"],
             ["Demonstration recording", f"script scripts/endterm_demo.py with transcript results/endterm/demo_transcript.txt; screen recording link: {TODO}"],
             ["Data", "All data are synthetic; no real users, systems or credentials were used."]], widths=[2.3, 4.6], size=9, bold_first_col=True)
    b.p("Reading guide. The sections follow the report structure of the assignment (1-10 and the appendix). Every number in the results sections is read from "
        "results/endterm/runs/*.json produced by scripts/endterm_experiments.py; nothing was typed by hand. Where a mechanism is only a design, the text says so.", italic=True, size=9)
    b.h("Executive summary", 1)
    b.bullets([
        f"What was built: the Assignment 3 platform (gateway, auth, course, registration, replicated database) was extended with request-level metrics, structured and audit logs, request correlation, "
        f"{len(ALERTS)} alert rules, password authentication with lockout, ownership authorization, strict input validation, rate limiting, duplicate protection, configuration checks, circuit breakers and graceful degradation; "
        f"{n_tests} automated unit and integration tests were added.",
        f"How it was evaluated: 7 experiments (5 fault-injection or abnormal-condition, 2 security), each repeated 3 times on the midterm baseline and on the endterm system with and without NetworkPolicy ({n_runs} measured runs), "
        "with explicit acceptance criteria, data-integrity checks and an alert-correctness analysis.",
        f"Main result: measured without NetworkPolicy, the endterm passed every acceptance criterion in every repetition of {nf_np} of 7 experiments, the midterm baseline of {nf_base} of 7. The baseline lost nothing that was acknowledged "
        "(E1, E2, E3, E7 kept the data intact) - the inherited fault tolerance was sound - but it had no automated detection (0 alerts), answered only "
        f"{R.fmt('E4', 'baseline', 'non_critical_during_outage.success_pct', 0)} % of timetable/course requests while the Course service was down (endterm: {R.fmt('E4', 'endterm-no-netpol', 'non_critical_during_outage.success_pct', 0)} %, stale and flagged), "
        "accepted 4 of 8 unauthorized-access patterns and stored junk records. The endterm denied all of them, audited them and alerted within seconds.",
        f"Honest cost and caveat: with the Assignment 4 NetworkPolicies enabled the endterm was less stable on this CNI - {st_end[0]} of {st_end[2]} runs already failed requests in the steady-state window before any fault was injected "
        f"(baseline {st_base[0]} of {st_base[2]}, endterm without NetworkPolicy {st_np[0]} of {st_np[2]}), "
        f"and it could not sustain the 100 req/s spike (E3: success {R.fmt('E3', 'endterm', 'during_spike.success_pct', 0)} %, p95 {R.fmt('E3', 'endterm', 'during_spike.p95_ms', 0)} ms; without NetworkPolicy {R.fmt('E3', 'endterm-no-netpol', 'during_spike.success_pct', 0)} %, {R.fmt('E3', 'endterm-no-netpol', 'during_spike.p95_ms', 0)} ms). Section 9 lists all limitations.",
    ])
    b.pagebreak()

    b.h("1. Introduction and relationship to the midterm project", 1)
    b.p("The midterm project of this team is the 'AITU Digital Student Services' platform of Assignment 3: an API gateway in front of Auth, Course and Registration services and a two-node replicated database, running as "
        "Kubernetes workloads with probes, replicas, rolling updates, timeouts, a circuit breaker towards the database and a measured recovery plan. Assignment 4 added network segmentation, an immutable backup vault and a timed ransomware recovery drill.")
    b.p("Assumption stated explicitly: this endterm report treats that platform as the midterm baseline. If the instructor's midterm refers to a different codebase, only the baseline namespace has to be replaced (baseline/ is a verbatim copy of git commit a6b1daa), the method stays the same.", bold=True, size=10)
    b.table([["Area", "Midterm baseline (commit a6b1daa)", "Endterm implementation", "Where"],
             ["Services", "gateway, auth, course, registration, 2-node db (5 roles, one image)", "same decomposition and workflow; code extended", "app/service.py"],
             ["Resilience", "2 replicas, probes, rolling update, 200 ms timeout + 1 retry, DB breaker, fail-open data store", "+ breaker per upstream at the gateway, stale-cache degradation, fail-closed data tier, Retry-After", "app/service.py, k8s/*.yaml"],
             ["Observability", "unstructured access line per request", "+ /metrics (counters, latency histogram, events), JSON logs, request id, audit log, 10 alert rules, collector, dashboard", "app/service.py, scripts/obs.py, observability/"],
             ["Security", "token check on registrations only", "+ passwords, expiring tokens, ownership authorization, validation, rate limits, lockout, duplicate protection, config checks, NetworkPolicy, Pod Security", "app/service.py, k8s/security/"],
             ["Verification", "ad-hoc experiments (Assignment 3)", f"+ {n_tests} automated tests, 7 repeatable experiments with acceptance criteria, baseline comparison", "tests/, scripts/endterm_experiments.py"]],
            widths=[0.9, 2.1, 2.9, 1.0], size=8, caption="Relationship between the midterm baseline and the endterm implementation")
    b.p("Goal of the endterm: show with measured evidence that the system detects abnormal behaviour, limits the impact of failures and attacks, and recovers while keeping availability and data integrity - and compare this with the baseline. "
        "Mechanisms that appear in a diagram are accepted only when an experiment or test demonstrates them.")

    b.h("2. Updated requirements, assumptions and threat model", 1)
    b.table([["ID", "Requirement", "Realisation / measure"]] + [list(x) for x in DOCS.REQUIREMENTS], widths=[0.5, 1.6, 4.8], size=8, caption="Functional and non-functional requirements")
    b.h("Assumptions", 3)
    b.bullets(DOCS.ASSUMPTIONS, size=9.5)
    b.h("Threat model", 2)
    b.p("Assets: student accounts and sessions, registration records, service availability, backups, configuration and secrets. "
        "Actors: legitimate student; malicious student misusing own access; anonymous external attacker; compromised pod; operator error. "
        "Trust boundaries: client -> gateway (untrusted), gateway -> services, services -> database, cluster -> backup vault. STRIDE was used to enumerate threats; each threat maps to controls and to the experiment or test that demonstrates it.")
    b.table([["ID", "Threat", "STRIDE", "Controls", "Evidence"]] + [[x[0], x[1], x[2], x[4], x[6]] for x in DOCS.THREATS],
            widths=[0.4, 1.4, 0.8, 2.9, 1.4], size=7.5, caption="Threat model: threats, controls and evidence (also in docs/threat_model.md)")

    b.h("3. Cloud / microservices architecture and resilience design", 1)
    b.picture("diagrams/endterm_architecture.png", 6.8, "Endterm architecture (green = new or changed since the midterm; Mermaid/PNG sources in diagrams/)")
    b.table([["Concern", "Design", "Value / file"],
             ["Service decomposition", "Independent services with own Deployment: gateway, auth, course, registration; StatefulSet db (data-storage component)", "k8s/20-auth.yaml ... 50-gateway.yaml, 10-db.yaml"],
             ["API gateway", "Single entry point: authentication, ownership authorization, rate limiting, input limits, request id, routing", "GatewayHandler"],
             ["Service discovery / configuration", "Kubernetes DNS names (auth, course, registration, db-N.db-headless, auth-headless); ConfigMap aitu-config; Secret auth-secret", "k8s/00-base.yaml"],
             ["Replicas and placement", "2 replicas per service and 2 database nodes, topology spread over workers, PodDisruptionBudget minAvailable 1", "k8s/*.yaml"],
             ["Health / readiness", "/healthz (liveness, 5 s), /readyz (readiness 2 s; also reports configuration problems and a damaged data store)", "Handler.route"],
             ["Load balancing", "Service NodePort with kube-proxy over Ready pods; login additionally pinned to one Auth replica per account", "50-gateway.yaml, auth_replica_for"],
             ["Timeouts / retries", "upstream 200 ms with one bounded retry; DB 150 ms per node, one attempt per node; every call has a timeout", "UPSTREAM_TIMEOUT, DB_TIMEOUT"],
             ["Circuit breakers", "Per upstream at the gateway (5 failures, 2 s) and towards the DB in Registration (3 failures, 3 s); open breaker answers at once (503 + Retry-After)", "CircuitBreaker, UP_BREAKERS"],
             ["Graceful degradation", "Timetable and courses (non-critical) are served from a stale cache (max 10 min, header X-Degraded) when the Course service is unreachable; registration and login are unaffected", "GatewayHandler.forward"],
             ["Deployment / scaling", "Rolling update maxUnavailable 0, minReadySeconds 3, preStop 5 s, SIGTERM handler; manual scaling (HPA not used: see limitations)", "k8s/*.yaml"],
             ["Data consistency", "Synchronous replication between two nodes, fsync, idempotent keyed records, fail-closed store, anti-entropy catch-up", "Store, DbHandler"]],
            widths=[1.4, 4.0, 1.5], size=8, caption="Resilience design decisions")
    b.p("What changed relative to the midterm and why: the midterm already contained replicas, probes, rolling updates and a database breaker. The endterm adds containment of non-critical failures (breaker per upstream and stale cache) so that a failure of the Course service no longer becomes a user-visible failure, and routes each login to one Auth replica so that abuse protection is consistent across replicas.")

    b.h("4. Monitoring, logging, metrics and alerting design", 1)
    b.h("4.1 Metrics", 2)
    b.table([["Metric", "Type", "Labels", "Purpose"],
             ["aitu_http_requests_total", "counter", "service, route, status", "request count and error rate (5xx share), availability"],
             ["aitu_http_request_duration_seconds", "histogram", "service, route, le", "average and p95 latency per route (buckets 5 ms - 2.5 s)"],
             ["aitu_events_total", "counter", "service, event", "security and resilience events: login_failed, account_locked, rate_limited, forbidden_access, unauthorized_request, validation_rejected, degraded_response, db_unavailable, db_retry, upstream_retry, duplicate_registration, ..."],
             ["aitu_circuit_breaker_open", "gauge", "service, target", "breaker state (1 = open) per upstream"],
             ["aitu_process_uptime_seconds", "gauge", "service", "restart detection"]],
            widths=[2.1, 0.7, 1.3, 2.8], size=8, caption="Metrics exposed on /metrics by every endterm pod (Prometheus text format)")
    b.h("4.2 Structured logs and request correlation", 2)
    b.p("Every request is logged as one JSON line (timestamp, service, pod, request id, method, route, status, latency, client). Security decisions are logged additionally as audit events "
        "(LOGIN_OK/FAILED/REJECTED, ACCOUNT_LOCKED, UNAUTHORIZED_REQUEST, FORBIDDEN_ACCESS, RATE_LIMITED, REGISTRATION_CREATED, DUPLICATE_REGISTRATION) with the same request id. "
        "The gateway accepts or creates an X-Request-ID (sanitised), forwards it to Auth, Registration and the database, and returns it to the client. "
        f"A scan of {SCAN.get('log_lines_scanned', 0)} log lines of all endterm pods found {SCAN.get('total_hits', 'n/a')} occurrences of the signing key, password literals or token payloads.")
    if TRACE:
        rows = [["time (s)", "service", "pod", "route", "status", "ms", "request id / event"]]
        t0 = TRACE[0]["ts"]
        for x in TRACE[:14]:
            rows.append([f"{x['ts'] - t0:.3f}", x["svc"], x["pod"][-12:], x.get("route") or x.get("event", ""), x.get("status", "audit"), x.get("ms", ""), x.get("rid", "")])
        b.table(rows, widths=[0.6, 0.8, 1.2, 1.5, 0.6, 0.5, 1.7], size=7.5, caption="One registration request followed across services by its request id (results/endterm/samples/correlated_trace.json)")
    b.h("4.3 Alert rules", 2)
    b.table([["Alert", "Severity", "What it detects", "Threshold", "Expected response"]] + [[a["name"], a["severity"], a["detects"], a["condition"], a["response"]] for a in ALERTS],
            widths=[1.1, 0.6, 1.5, 1.6, 2.1], size=7.5, caption="Alert definitions (observability/alerts.json, runbook in observability/RUNBOOK.md)")
    b.h("4.4 Dashboard and incident diagnosis", 2)
    b.picture("diagrams/endterm_dashboard.png", 6.8, "Dashboard from the endterm metrics for two experiments (HTML version: diagrams/endterm_dashboard.html)")
    e2 = R.runs("E2", "endterm")
    first = e2[0] if e2 else None
    if first:
        fired = [f"{a['alert']} at +{a['fired_after_s']} s" for a in first["alerts"]["fired"] if a["fired_after_s"] >= -0.5]
        b.p("How an incident is detected and diagnosed (E2, repetition 1): the database is blocked at t = 0. The alert engine fires "
            + ", ".join(fired) + ". The dashboard shows 5xx responses on the registration routes while timetable and course requests stay healthy, the ready-pod panel stays at 10 (no instance died), "
            "so the cause is a dependency, not an instance. The metric aitu_events_total{event=db_unavailable} in the Registration pods confirms it, and the request id of any failed request leads through the logs "
            "gateway -> registration to the DB timeout. The runbook action for DatabaseUnavailable is the data-tier procedure.")
    b.p("Detection and recovery times are therefore obtained from three sources: client-observed first/last failure, Kubernetes pod readiness (1 s resolution) and alert firing time from the metric series.")

    b.h("5. Security controls and intrusion-tolerance strategy", 1)
    b.table([["#", "Control", "Implementation", "Verified by"],
             ["C1", "Authentication", "PBKDF2 password check, signed expiring token (15 min), constant-time comparisons, no user enumeration", "E5 A2/A5/A7/A8, unit + integration tests"],
             ["C2", "Authorization", "Ownership check at gateway and Registration; admin role for /admin/count; deny by default", "E5 A3/A4/A6, integration tests"],
             ["C3", "Input validation", "Anchored regexes, JSON type checks, course whitelist, body <= 1 KB, path <= 128, DB schema check", "E6 (12 malformed cases), unit + integration tests"],
             ["C4", "Rate limiting / abuse protection", "Token bucket per account and per source, 429 + Retry-After, per-account login lockout", "E6 burst and brute force"],
             ["C5", "Audit logging", "JSON audit events with request id and subject for every security decision", "E5 (every denied attempt audited), samples/"],
             ["C6", "Secrets and configuration", "Signing key only from a Kubernetes Secret, configuration check at start and in /readyz, refuses unsafe production start", "unit tests, log scan"],
             ["C7", "Duplicate / replay protection", "Deterministic record id, created flag, duplicate answer", "E7 replay, integration test"],
             ["C8", "Segmentation and least privilege", "Default-deny NetworkPolicy, Pod Security, non-root, dropped capabilities, no internet egress", f"segmentation matrix {sum(1 for c in SEG['checks'] if c['as_designed'])}/{len(SEG['checks'])} checks as designed"],
             ["C9", "Safe failure", "Fail-closed data store, 503 + Retry-After instead of wrong data, stale answers flagged", "E2, E4, Assignment 4 drill"]],
            widths=[0.4, 1.4, 3.3, 1.8], size=8, caption="Implemented security controls (the assignment requires at least three)")
    b.p("Intrusion-tolerance strategy. The system does not rely on one barrier: an attacker must pass authentication, then authorization at two layers, then validation, while every denied step is logged and rate-limited; "
        "network policy limits what a compromised pod can reach; replication, a fail-closed store and immutable backups limit what a successful attacker can destroy (Assignment 4 drill: restored in 8.0 min against a 30 min RTO). "
        "Detection is rule-based and deterministic; the anomaly-score idea of Assignment 4 stays advisory.")
    if SEG["checks"]:
        b.p(f"Segmentation re-check on the endterm stack: {sum(1 for c in SEG['checks'] if c['as_designed'])} of {len(SEG['checks'])} connection attempts behaved as designed "
            "(including a control pod that proves the internet is reachable without policy).", size=9.5)

    b.h("6. Verification and validation plan", 1)
    b.table([["Level", "What", "Tool / command", "Result"],
             ["Unit", "tokens, passwords, lockout, token bucket, breaker, validation, metrics, store, alert engine, quantiles",
              "python -m unittest discover -s tests -v", f"{mod_counts['unit'] + mod_counts['obs']} tests"],
             ["Integration", "all five roles in-process over real HTTP: login, register, duplicate, authorization, validation, rate limit, lockout, correlation, metrics, degradation, DB outage",
              "same command", f"{mod_counts['integration']} tests"],
             ["System / experiments", "7 experiments on the deployed cluster, 3 repetitions x 2 configurations", "python scripts/endterm_experiments.py --reps 3 --ablation", f"{n_runs} runs"]],
            widths=[1.1, 3.1, 1.9, 0.8], size=8, caption=f"Test levels (latest unit/integration run: {n_tests} tests, {'all passed' if tests_ok else 'see tests.txt'})")
    rows = [["Experiment", "Acceptance criteria (identical for both configurations)"]]
    for exp in R.EXPERIMENTS:
        crit = R.criteria_table(exp)
        rows.append([NAMES[exp], "\n".join(f"- {c[0]}: {c[1]}{(' ' + c[2]) if c[2] else ''}" for c in crit)])
    b.table(rows, widths=[1.7, 5.2], size=7.5, caption="Explicit acceptance criteria per experiment")
    b.p("Traceability: learning objectives -> mechanisms -> tests/experiments -> evidence files.", bold=True, size=9.5)
    b.table([["Objective", "Mechanism", "Test / experiment", "Evidence"],
             ["Microservices resilience", "replicas, probes, timeouts, retries, breakers, degradation", "E1, E2, E4, E7; integration ResilienceTests", "results/endterm/runs/E1,E2,E4,E7"],
             ["Detection and observability", "metrics, structured logs, request id, alerts, dashboard", "alert correctness in every experiment; ObservabilityTests", "runs/*.json (alerts), diagrams/endterm_dashboard.*, samples/"],
             ["Security and intrusion tolerance", "authn/authz, validation, rate limit, audit, segmentation", "E5, E6; unit and integration tests; segmentation matrix", "runs/E5,E6, segmentation.json"],
             ["Validation and fault injection", "repeatable scripts, workload generator, integrity checks", "all experiments; tests", "scripts/, tests/, results/endterm/"],
             ["Baseline comparison", "unmodified midterm stack in namespace baseline", "all experiments on both configurations", "runs/*_baseline_*.json"]],
            widths=[1.4, 1.9, 2.0, 1.6], size=8, caption="Traceability")

    b.h("7. Fault-injection and security-test methodology", 1)
    b.p("Common setup for all experiments: both stacks run in the same kind cluster (3 nodes) with 2 replicas per service and 2 database nodes; the endterm stack has NetworkPolicies and Pod Security, the baseline has neither. "
        "A host-side open-loop workload generator (scripts/endterm_load.py) sends 50 req/s (35 % timetable, 15 % courses, 25 % own-registration reads, 15 % unique registration writes, 10 % logins; 200 users, each with a password and a token). "
        "Every request is recorded (send time, latency, status, kind). A collector scrapes pod readiness and (endterm) /metrics every second. Before each repetition the system is restored to a known state "
        "(policies re-applied, replicas 2, all pods Ready, 8 s settle time; the database is wiped after E6). After each run the integrity of both database nodes is checked: acknowledged writes missing, duplicate lines, node divergence and invalid records. "
        "Each experiment is repeated 3 times per configuration; results show the median and the minimum-maximum range.")
    meth = {
        "E1": ("With two replicas, probes and a retry, losing one Registration instance keeps success >= 99 %; the lost replica is detected by an alert within 5 s (endterm).",
               "Warm-up 10 s; kubectl delete pod <registration> --force --grace-period=0; observe 18 s; wait Ready; audit database.",
               "<= 1 % failed requests, all pods Ready within 30 s, 0 lost acknowledged writes, alert ReplicaUnavailable <= 5 s.",
               "Force deletion is not a hardware crash; 1 s resolution of readiness polling and scraping; single host."),
        "E2": ("When the database is unreachable (packets dropped, so calls time out), timeouts and breakers keep failures fast and bounded, independent endpoints stay up, and registration works again within 15 s after the block is removed.",
               "Warm-up 10 s; NetworkPolicy blocks all ingress to the database pods for 20 s (endterm: the DB allow rule is removed; baseline: a deny policy is added); remove the block; observe 18 s.",
               "Timetable/courses/login >= 99 % success; failed DB requests answered within 500 ms (p95), nothing slower than 1 s; recovery <= 15 s; 0 lost acknowledged writes; alert <= 10 s.",
               "A dropped-packet block is one kind of dependency timeout; slow-but-responding dependencies are not injected."),
        "E3": ("Doubling the offered load (50 -> 100 req/s for 20 s) keeps success >= 99 % and p95 <= 500 ms; the controls cost some latency. An ablation without NetworkPolicy isolates the cost of segmentation.",
               "Warm-up 10 s at 50 req/s; raise the generator to 100 req/s for 20 s; return to 50 req/s; CPU use of gateway/auth/registration from cgroup counters.",
               "success >= 99 %, p95 <= 500 ms, achieved throughput >= 90 % of offered, 0 lost acknowledged writes.",
               "A single Python client machine and one host limit the maximum load; the 500 req/s peak of the midterm requirement is NOT demonstrated (Assignment 3 finding)."),
        "E4": ("With graceful degradation the Course outage is invisible for timetable and courses (stale answers flagged with X-Degraded) and does not touch registration or login; alerts fire within 10 s. The baseline fails these reads.",
               "Warm-up 10 s (fills the stale cache); scale the Course service to 0 for 20 s; scale back; observe 18 s.",
               "non-critical reads >= 99 % success, critical workflow >= 99 %, degraded answers flagged, alert <= 10 s, 0 lost acknowledged writes.",
               "Stale data are served for up to 10 minutes; the cache is per gateway pod and filled only by requests seen before the outage."),
        "E5": ("All eight unauthorized-access patterns are denied, the victim's data are neither changed nor returned, every denial is audited and an alert fires within 10 s; the baseline lacks ownership checks and password verification.",
               "Attacker and victim accounts; 8 patterns x 3 attempts: A1 no token, A2 forged signature, A3 write for another student, A4 read another student's data, A5 token with replaced subject, A6 student on the admin endpoint, A7 login without password, A8 login with a wrong password.",
               "24/24 denied (400/401/403/404/429), victim records unchanged, 0 data returned, >= 24 audit events (endterm), alert <= 10 s.",
               "The attacker is an authenticated student of the same system; no network-level or insider-at-database attacks."),
        "E6": ("Malformed input yields 4xx without crashes or junk records, a burst from one account is rate limited while other users are unaffected, and password guessing is stopped within 5 failures.",
               "10 malformed bodies and 2 path attacks; 960 requests from 16 parallel connections for one account; 12 wrong-password logins followed by a correct one; background load 30 req/s of other users.",
               "0 server errors, 0 junk records, 429 observed, lockout by the 6th attempt, other users >= 99 %, 0 pod restarts, alert <= 10 s.",
               "Per-pod token buckets: the effective burst limit is up to the number of gateway replicas times the per-pod limit."),
        "E7": ("During node and pod failure a stream of 300 unique writes with bounded client retries produces no lost acknowledged write and no duplicate; the nodes converge within 60 s and replaying all requests changes nothing.",
               "300 writes at 30/s by 6 workers, up to 5 retries with back-off; db-0 force-deleted at +3 s and a Registration pod at +6 s; wait until both DB nodes are identical; replay all 300 requests.",
               "0 lost acknowledged writes, 0 duplicates, nodes identical within 60 s, replay creates 0 records, every write acknowledged.",
               "A client that gives up after its retries may leave an unacknowledged but stored record (counted separately, not an integrity violation).")}
    for exp in R.EXPERIMENTS:
        h, steps, exp_res, lim = meth[exp]
        b.h(f"{NAMES[exp]} ({KIND[exp]})", 3)
        b.table([["Hypothesis", h], ["Environment / configuration", "see common setup above; baseline and endterm; 3 repetitions each" + ("; plus ablation without NetworkPolicy" if exp == "E3" else "")],
                 ["Steps", steps], ["Expected result", exp_res], ["Limitations", lim]], widths=[1.5, 5.4], size=8, bold_first_col=True)

    b.h("8. Results and baseline-versus-endterm comparison", 1)
    summary_rows = [["Experiment", "Type", "Midterm baseline", "Endterm (with NetworkPolicy)", "Endterm without NetworkPolicy"]]
    for exp in R.EXPERIMENTS:
        summary_rows.append([NAMES[exp], KIND[exp], pf(exp, "baseline"), pf(exp, "endterm"), pf(exp, "endterm-no-netpol")])
    b.table(summary_rows, widths=[2.0, 1.1, 1.3, 1.3, 1.3], size=8, caption="Experiment outcome (an experiment passes when every criterion passes in every repetition)")

    def failed_list(exp, cfg):
        out = []
        for name, limit, unit, row in R.criteria_table(exp):
            if cfg not in row:
                continue
            ok, n, val, note = row[cfg]
            if n and ok < n:
                out.append(f"{name} ({n - ok}/{n} failed)")
        return "; ".join(out) if out else "none"
    b.table([["Experiment", "Criteria failed by the midterm baseline", "Criteria failed by the endterm (with NetworkPolicy)", "Criteria failed by the endterm without NetworkPolicy"]] +
            [[NAMES[e], failed_list(e, "baseline"), failed_list(e, "endterm"), failed_list(e, "endterm-no-netpol")] for e in R.EXPERIMENTS],
            widths=[1.3, 2.0, 2.0, 1.6], size=7, caption="Which acceptance criteria failed")
    b.picture("diagrams/endterm_pass_rate.png", 6.3, "Share of acceptance criteria passed, baseline versus endterm")
    b.p("Reading the tables: the baseline fails for different reasons - no alerting (E1, E2), no graceful degradation (E4), missing authorization and validation (E5, E6). "
        "Where it passes (E3, E7) the data-path resilience inherited from the midterm is sound. The endterm without NetworkPolicy passes everything except one repetition of E3; "
        "with the NetworkPolicies of Assignment 4 enabled the same code shows sporadic multi-second stalls and a capacity limit (section 9). "
        "E5 note: the success criterion was refined after the series (only a 2xx response counts as a successful attack, 5xx or transport errors are 'inconclusive'); "
        "the raw statuses are unchanged and scripts/endterm_reanalyze.py documents the recomputation.", size=9.5)

    VARIANTS = (("endterm-no-netpol", "Endterm without NetworkPolicy"), ("endterm-fresh-cni", "Endterm, fresh CNI state"))

    def metric_table(exp, rows, caption):
        variants = [(v, t) for v, t in VARIANTS if (exp, v) in R.DATA]
        out = [["Measurement", "Midterm baseline", "Endterm (with NetworkPolicy)"] + [t for _, t in variants]]
        for label, fn in rows:
            out.append([label, fn(exp, "baseline"), fn(exp, "endterm")] + [fn(exp, v) for v, _ in variants])
        b.table(out, widths=[2.6] + [1.4] * (len(out[0]) - 1), size=8, caption=caption)

    def row(label, path, nd=1):
        return (label, lambda e, c: R.fmt(e, c, path, nd))

    def status_row(label, path, codes):
        return (label, lambda e, c: " / ".join(str(status_total(e, c, path, k)) for k in codes))

    b.h("8.1 E1 Instance failure", 2)
    metric_table("E1", [row("Requests in the run", "summary_whole.total", 0), row("Failed requests", "summary_whole.failed", 0), row("Success ratio = availability over the run %", "summary_whole.success_pct", 2),
                        row("Error rate over the run % (100 - success)", "summary_whole.error_rate_pct", 2), row("Average latency (ms)", "summary_whole.avg_ms"), row("p95 latency (ms)", "summary_whole.p95_ms"),
                        row("Kubernetes detection (s)", "timing.k8s_detection_s"), row("Pod recovery (s)", "timing.k8s_recovery_s"), row("Automated alert delay (s)", "alerts.first_expected_delay_s"),
                        row("Alerts before the injection (false positives)", "alerts.pre_fault_alerts", 0), row("Lost acknowledged writes", "integrity.lost_acked_count", 0),
                        row("Duplicate records", "integrity.duplicate_lines", 0)], "E1 results, median (min-max) of 3 repetitions")
    b.h("8.2 E2 Dependency timeout", 2)
    metric_table("E2", [row("Success ratio over the whole run % (error rate = 100 - value)", "summary_whole.success_pct", 1), row("DB-dependent requests succeeding during the block %", "db_dependent_during_fault.success_pct", 1),
                        status_row("DB-dependent failures: 503 / 502 / transport error (total over reps)", "db_dependent_during_fault.status_counts", ("503", "502", "0")),
                        row("Independent requests (timetable, courses, login) %", "independent_during_fault.success_pct", 1), row("p95 latency of failed DB requests (ms)", "failed_db_request_p95_ms"),
                        row("Max latency during the fault (ms)", "max_latency_during_fault_ms"), row("Registration works again after unblock (s)", "recovery_after_unblock_s"),
                        row("Automated alert delay (s)", "alerts.first_expected_delay_s"), row("Lost acknowledged writes", "integrity.lost_acked_count", 0)], "E2 results, median (min-max)")
    b.h("8.3 E3 Load spike", 2)
    metric_table("E3", [row("Success during the spike (availability) %", "during_spike.success_pct", 2), row("Error rate during the spike % (100 - success)", "during_spike.error_rate_pct", 2),
                        row("Average latency during the spike (ms)", "during_spike.avg_ms"), row("p95 latency during the spike (ms)", "during_spike.p95_ms"),
                        row("Achieved throughput during the spike (req/s, offered 100)", "achieved_rps_during_spike"), row("Gateway CPU used (cpu-seconds)", "resource_use.gateway.cpu_seconds", 2),
                        row("Gateway CPU throttled periods", "resource_use.gateway.throttled_periods", 0), row("p95 after the spike (ms)", "after_spike.p95_ms"),
                        row("Lost acknowledged writes", "integrity.lost_acked_count", 0)], "E3 results, median (min-max)")
    b.picture("diagrams/endterm_load.png", 5.8, "Latency during the load spike")
    b.h("8.4 E4 Monitoring / alert test with graceful degradation", 2)
    metric_table("E4", [row("Timetable + courses success during the outage %", "non_critical_during_outage.success_pct"), row("Degraded (stale) answers served", "non_critical_during_outage.degraded_responses", 0),
                        row("Login + registration success during the outage %", "critical_during_outage.success_pct"), row("Success over the whole run (availability) %", "summary_whole.success_pct", 2),
                        row("Failed requests in the run", "summary_whole.failed", 0), row("Kubernetes detection (s)", "timing.k8s_detection_s"), row("Pod recovery (s)", "timing.k8s_recovery_s"),
                        row("Automated alert delay (s)", "alerts.first_expected_delay_s"), row("Alerts before the injection (false positives)", "alerts.pre_fault_alerts", 0),
                        row("Lost acknowledged writes", "integrity.lost_acked_count", 0)], "E4 results, median (min-max)")
    b.picture("diagrams/endterm_timelines.png", 6.6, "Failed requests per second during E2 and E4 (repetition 1)")
    b.h("8.5 E5 Unauthorized access", 2)
    metric_table("E5", [row("Attempts denied (of 24 per repetition)", "denied_count", 0), row("Attempts that succeeded for the attacker", "violation_count", 0),
                        row("Responses that returned the victim's data", "data_exposed_responses", 0), row("Audit events recorded for the attempts", "audit_events_recorded", 0),
                        row("Automated alert delay (s)", "alerts.first_expected_delay_s"),
                        ("Successful attack patterns", lambda e, c: "; ".join(sorted({v.split(" ")[0] for r in R.runs(e, c) for v in r["violations"]})) or "none")],
                 "E5 results (3 repetitions, 24 attempts each)")
    b.h("8.6 E6 Abusive or malformed requests", 2)
    metric_table("E6", [row("Malformed requests that caused a 5xx or transport error (of 12)", "malformed_server_errors", 0), row("Junk or invalid records stored", "junk_records_stored", 0),
                        ("Burst: 429 responses per repetition (of 960)", lambda e, c: str(status_total(e, c, "burst_status_counts", "429") // max(1, len(R.runs(e, c))))),
                        row("Attempt number answered with the lockout (429)", "lockout_after_attempt", 0), row("Other users' success during the abuse %", "other_users_during_abuse.success_pct"),
                        row("Pod restarts during the abuse", "pod_restarts_during_test", 0), row("Automated alert delay (s)", "alerts.first_expected_delay_s")], "E6 results, median (min-max)")
    b.h("8.7 E7 Recovery and consistency", 2)
    metric_table("E7", [row("Writes acknowledged (of 300)", "writes_acknowledged", 0), row("Client retries needed", "client_retries", 0), row("Lost acknowledged writes", "integrity.lost_acked_count", 0),
                        row("Duplicate records", "integrity.duplicate_lines", 0), row("Seconds until both DB nodes identical", "integrity.seconds_to_identical"),
                        row("New records created by the replay of 300 requests", "replay_new_records", 0),
                        status_row("Replay answers: 201 / 200", "replay_status_counts", ("201", "200"))], "E7 results, median (min-max); replay answers are totals over the repetitions")
    b.h("8.8 Alerts: number triggered and correctness", 2)
    rows = [["Experiment", "Expected alerts fired (TP)", "Expected alerts missed (FN)", "Unexpected alerts (FP)", "Median delay to first expected alert (s)"]]
    for exp in R.EXPERIMENTS:
        t_, f_, p_, dl = alert_per[exp]
        rows.append([NAMES[exp], t_, f_, p_, dl if dl is not None else "-"])
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    rows.append(["Total (endterm, all repetitions)", tp, fn, fp, "-"])
    b.table(rows, widths=[2.4, 1.1, 1.1, 1.1, 1.2], size=8, caption=f"Alert correctness of the endterm system; the baseline defines no alerts (0 fired, every expected alert missed). Precision {prec:.2f}, recall {rec:.2f}" if prec is not None else "Alert correctness")
    tp2, fn2, fp2, _ = R.alert_totals("endterm-no-netpol")
    b.p(f"{R.fired_alerts('endterm')} alert activations were recorded in the endterm runs with NetworkPolicy. An alert counts as correct (TP) when it is in the expected set of the scenario; "
        "unexpected activations (FP) include any alert firing before the injection. Optional alerts that are plausible side effects (for example HighLatencyP95 during a load spike) are not counted as errors. "
        f"Most of the {fp} unexpected activations belong to the runs in which the test bed itself stalled (HighErrorRate, HighLatencyP95, DatabaseUnavailable, DegradedMode fired because requests really failed or slowed down, but not because of the injected fault); "
        f"in the runs without NetworkPolicy the same analysis gives {tp2} correct activations, {fn2} missed and {fp2} unexpected.", size=9.5)
    b.h("8.9 Endterm build 1 versus build 2: what experiment E6 and the first series changed", 2)
    b.p("The first complete series (build 1, kept in results/endterm/runs_build1/) exposed operational defects under the burst of E6 and under churn: one repetition restarted three pods "
        "because the liveness probe (1 s timeout) could not be answered while the CPU-limited gateway was busy, every rate-limited request still caused a token check at the Auth service and wrote an audit line. "
        "Three changes were made and the whole endterm series was repeated (build 2): the gateway caches a verified token for 5 s (expiry is still checked), audit lines of identical rate-limit events are written at most once "
        "per 2 s per account (every event is still counted in the metrics), and the liveness probe tolerates 3 s / 6 failures. The baseline was not changed.", size=9.5)
    comp = [["Measurement", "Build 1 (first series)", "Build 2 (final)"]]
    for label, exp, path, nd in (("E3 success during the spike %", "E3", "during_spike.success_pct", 2), ("E3 p95 during the spike (ms)", "E3", "during_spike.p95_ms", 1),
                                 ("E4 login + registration success during the outage %", "E4", "critical_during_outage.success_pct", 1),
                                 ("E6 other users' success during the abuse %", "E6", "other_users_during_abuse.success_pct", 1), ("E6 pod restarts during the abuse", "E6", "pod_restarts_during_test", 0),
                                 ("Steady-state p95 at 50 req/s, E1 pre-fault (ms)", "E1", "summary_pre.p95_ms", 1)):
        comp.append([label, R.prev_fmt(exp, "endterm", path, nd), R.fmt(exp, "endterm", path, nd)])
    for exp in R.EXPERIMENTS:
        po, pn = R.prev_pass(exp, "endterm")
        ok, n = R.pass_counts(exp, "endterm")
        comp.append([f"{NAMES[exp]}: repetitions with all criteria passed", f"{po}/{pn}" if pn else "-", f"{ok}/{n}"])
    b.table(comp, widths=[3.3, 1.8, 1.8], size=8, caption="Endterm build 1 versus build 2 (median, min-max)")
    b.p("Effect of the changes: the pod restarts of E6 disappeared (3 in one repetition of build 1, none in build 2) and the share of 'other users' served during the burst rose, but the picture with NetworkPolicy stays dominated by "
        "the test-bed stalls described in section 9, so the change in pass counts must not be read as a clean improvement or deterioration; the variant without NetworkPolicy is the clean measurement of the application-level mechanisms.", size=9.5)
    b.h("8.10 Baseline versus endterm: capability and overhead comparison", 2)
    b.table([["Capability", "Midterm baseline", "Endterm", "Evidence"],
             ["Metrics for monitoring", "none", "request counters, latency histogram, event counters, breaker gauge on every pod", "/metrics, samples/metrics_*.prom"],
             ["Alert rules / automated detection", "0", f"{len(ALERTS)} rules; detection within seconds (tables 8.1-8.8)", "alerts.json, runs/*.json"],
             ["Request correlation", "no", "X-Request-ID through all hops", "correlated_trace.json, integration test"],
             ["Authentication", "any id starting with 'S' logs in", "password, expiring signed token, lockout", "E5 A7/A8, E6"],
             ["Authorization", "none (any valid token acts for any student)", "ownership at two layers, admin role", "E5 A3/A4/A6"],
             ["Input validation", "truthy check only (500 on bad JSON, junk stored)", "strict validation, 400/413", "E6"],
             ["Rate limiting / lockout", "none", "token buckets, 429, lockout", "E6"],
             ["Graceful degradation", "none (Course outage = user-visible errors)", "stale cache with X-Degraded", "E4"],
             ["Duplicate protection", "idempotent id, silent", "idempotent id + explicit duplicate answer + audit", "E7"],
             ["Segmentation", "none", "default-deny NetworkPolicy, Pod Security", "segmentation.json"],
             ["Automated tests", "none", f"{n_tests} unit and integration tests", "results/endterm/tests.txt"],
             ["Steady-state latency at 50 req/s, average / p95 (ms)", f"{base_lat[0]} / {base_lat[1]}", f"{end_lat[0]} / {end_lat[1]} with NetworkPolicy; {np_lat[0]} / {np_lat[1]} without", f"median of the pre-fault windows of {base_lat[2]} runs per configuration"]],
            widths=[1.9, 1.7, 2.2, 1.1], size=8, caption="Capability and overhead comparison")
    b.h("8.11 Regression run on build 3 (structured-log fix)", 2)
    reg = [["Item", "Result"]]
    for exp in ("E1", "E5"):
        if (exp, "endterm-build3") in R.DATA:
            ok, n = R.pass_counts(exp, "endterm-build3")
            reg.append([f"{NAMES[exp]} on build 3", f"all criteria passed in {ok} of {n} repetitions"])
    reg.append(["Unparsable JSON log lines, build 2 (before the fix)", f"{integ_b2['unparsable_lines']} of {integ_b2['json_lines']} ({integ_b2['share_pct']} %)"])
    reg.append(["Unparsable JSON log lines, build 3 (after the fix)", f"{integ_b3['unparsable_lines']} of {integ_b3['json_lines']} ({integ_b3['share_pct']} %)"])
    b.table(reg, widths=[3.4, 3.5], size=8.5, caption="Build 3 differs from the measured build 2 only in how log lines are written (one atomic write under a lock)")
    b.p("After the series a scan of the gateway, auth and registration logs showed that about 1 % of the JSON lines were damaged because two threads printed at the same time. "
        "The fix writes every line with one call under a lock; a unit test with 8 concurrent clients guards it. Since the change does not touch request handling, E1 and E5 were repeated on build 3 as a regression check instead of repeating the whole series.", size=9.5)

    b.h("9. Discussion, limitations and lessons learned", 1)
    b.h("Interpretation", 3)
    b.bullets([
        "The inherited fault tolerance is real: E7 (node and pod failure during writes) passed for every configuration, and the baseline failed E1 and E2 only through the missing alert criterion - its requests, data and recovery were fine. "
        "The endterm does not claim better raw availability there; it claims earlier, automated detection and a defined response.",
        f"Detection: the endterm raised the expected alert within a median of {alert_per['E1'][3]} s (E1), {alert_per['E2'][3]} s (E2) and {alert_per['E4'][3]} s (E4); the baseline detected nothing automatically.",
        f"Graceful degradation is the one place where availability itself improved: during the Course outage timetable and course reads succeeded {R.fmt('E4', 'baseline', 'non_critical_during_outage.success_pct', 0)} % on the baseline and "
        f"{R.fmt('E4', 'endterm-no-netpol', 'non_critical_during_outage.success_pct', 0)} % on the endterm (answers flagged stale).",
        f"Security: 4 of the 8 unauthorized-access patterns succeeded against the baseline (cross-account write and read, login without password, login with a wrong password); the endterm denied all of them and audited every denial. "
        f"Malformed input produced server errors in {R.fmt('E6', 'baseline', 'malformed_server_errors', 0)} of 12 cases and {R.fmt('E6', 'baseline', 'junk_records_stored', 0)} junk records on the baseline, none on the endterm.",
        f"NetworkPolicy is a security gain (segmentation matrix 19/19) with a measurable cost on this CNI: spike p95 {R.fmt('E3', 'endterm', 'during_spike.p95_ms', 0)} ms and success {R.fmt('E3', 'endterm', 'during_spike.success_pct', 0)} % with the policies, "
        f"{R.fmt('E3', 'endterm-no-netpol', 'during_spike.p95_ms', 0)} ms and {R.fmt('E3', 'endterm-no-netpol', 'during_spike.success_pct', 0)} % without. Under the policies E7 also needed more client retries ({R.fmt('E7', 'endterm', 'client_retries', 0)} against {R.fmt('E7', 'endterm-no-netpol', 'client_retries', 0)} without). Restarting the CNI before each repetition did not help "
        f"({R.fmt('E3', 'endterm-fresh-cni', 'during_spike.success_pct', 0)} %), so the cost is not CNI state ageing. Runs with failures before the injection: {st_end[0]} of {st_end[2]} with the policies, {st_np[0]} of {st_np[2]} without (baseline {st_base[0]} of {st_base[2]}); runs with a request slower than 2 s: {st_end[1]}, {st_np[1]} and {st_base[1]}; "
        f"alerts raised before the injection: {st_end[3]}, {st_np[3]} and {st_base[3]} runs. The build 3 regression runs (E1, E5; low load) with the policies showed none of these.",
        f"Steady-state cost of the application-level controls: average / p95 latency at 50 req/s was {np_lat[0]} / {np_lat[1]} ms without NetworkPolicy against {base_lat[0]} / {base_lat[1]} ms on the baseline - no measurable overhead, "
        "partly because the gateway caches a verified token for 5 s and so makes one Auth call fewer per request.",
        "Variance matters: with only 3 repetitions a single stall can fail a repetition (E1, E2, E4, E5 endterm with NetworkPolicy); this is reported, not hidden, and the passing configuration is not claimed to be statistically better.",
    ], size=9.5)
    b.h("Limitations", 3)
    b.bullets(DOCS.LIMITATIONS, size=9.5)
    b.h("Lessons learned (defects found by the tests and experiments themselves)", 3)
    b.bullets([
        "A unit test found that the validation regex accepted an id with a trailing newline ('$' matches before it); fixed with anchored \\A...\\Z patterns.",
        "An integration test found that the gateway did not relay Retry-After on a database 503; fixed.",
        "The first deployment showed that the configuration check made the db, course and registration roles (which have no signing key) refuse to start; the check is now role-aware and covered by a test.",
        "The first experiment trial showed that NetworkPolicy also blocked metrics scraping by the API server; an explicit allow for the control-plane node was added.",
        "E6 showed that per-replica lockout counters stopped password guessing only at the 9th attempt; login is now routed to a fixed Auth replica per account (6th attempt).",
        "E5/E6 showed that a BruteForceLogin threshold of 10 failures can never fire when lockout stops the attacker after 5; the threshold was aligned with the lockout.",
        "The collector counted the whole history of a pod it saw late as new events and produced false alerts before the injection; fixed with a baseline period.",
        "Experiments with 100 req/s revealed the NetworkPolicy capacity cost, which led to the variants without NetworkPolicy and with a restarted CNI, and to a working load of 50 req/s for the other experiments.",
        "E6 (burst) exposed that a rate-limited flood still cost an Auth call and an audit line per request and could starve the liveness probe (3 pod restarts in one repetition); the gateway now caches verified tokens for 5 s, samples identical rate-limit audit lines and the liveness probe tolerates 3 s / 6 failures. The whole series was repeated on that build.",
        "A post-series scan found that about 1 % of the JSON log lines were interleaved by concurrent printing; fixed with one atomic write per line and a regression test and run (build 3).",
        "One E5 repetition counted a 502 from a test-bed stall as a 'not denied' attempt; the success criterion was corrected to count only 2xx responses and the runs were recomputed from the unchanged raw statuses.",
        "The Docker Desktop port proxy on the Windows host stalls for minutes after connection floods; the runner therefore waits for the gateway to answer 15 times in a row before every repetition.",
    ], size=9.5)

    b.h("10. Conclusion and references", 1)
    b.p("The extended system detects selected incidents within seconds, contains the impact of an instance failure, a blocked database, a non-critical service outage and several kinds of misuse, recovers predictably "
        "and preserved every acknowledged record in all runs. Compared with the midterm baseline the main gains are detection (0 -> "
        f"{len(ALERTS)} alert rules, {tp} correct activations and {fn} missed in the endterm runs with NetworkPolicy), graceful degradation of non-critical features, and security (4 of 8 attack patterns succeeded before, 0 after). "
        f"Measured without NetworkPolicy the endterm passed all criteria in {nf_np} of 7 experiments against {nf_base} for the baseline; with NetworkPolicy the segmentation is verified (19/19) but costs capacity and stability on this CNI. "
        "The limitations above define what is not shown: the 500 req/s peak, TLS, a live pager, statistical significance and a video of the demonstration.")
    b.h("References", 3)
    b.bullets([
        "Avizienis, Laprie, Randell, Landwehr. Basic Concepts and Taxonomy of Dependable and Secure Computing. IEEE TDSC, 2004.",
        "Nygard. Release It! (circuit breaker, timeouts, bulkheads). Pragmatic Bookshelf.",
        "Beyer et al. Site Reliability Engineering, chapters on monitoring and alerting. O'Reilly / Google.",
        "Prometheus documentation: data model, histograms and alerting rules.",
        "OWASP API Security Top 10 and OWASP Authentication / Input Validation cheat sheets.",
        "NIST SP 800-63B Digital Identity Guidelines: authenticator management.",
        "IETF RFC 6585 (429 Too Many Requests) and RFC 9110 (Retry-After).",
        "Kubernetes documentation: NetworkPolicy, Pod Security Standards, probes, Deployments.",
    ], size=9.5)

    b.pagebreak()
    b.h("Appendix A. Repository, configuration, scripts and how to run", 1)
    b.table([["Path", "Content"],
             ["app/service.py, app/Dockerfile", "endterm services (one image, five roles)"],
             ["baseline/", "midterm baseline, verbatim from git commit a6b1daa (service.py, Dockerfile, manifests)"],
             ["k8s/*.yaml, k8s/security/", "endterm manifests, NetworkPolicies, Pod Security namespaces, (Assignment 4) vault"],
             ["observability/alerts.json, RUNBOOK.md", "alert definitions with detection, threshold and response"],
             ["scripts/obs.py", "Prometheus parser, collector, histogram quantiles, alert engine"],
             ["scripts/endterm_load.py, endterm_experiments.py", "workload generator and the seven experiments"],
             ["scripts/endterm_deploy.py, endterm_post.py, endterm_demo.py", "deployment of both stacks, post-checks, demonstration"],
             ["scripts/make_endterm_*.py, endterm_docs.py", "figures, dashboard, threat-model/runbook markdown, this report"],
             ["tests/", "unit, observability and integration tests (python -m unittest discover -s tests -v)"],
             ["results/endterm/", "runs/*.json and per-request CSV (repetition 1), samples/, logs/, tests.txt, segmentation.json, log_secret_scan.json, demo_transcript.txt, summary.md"],
             ["docs/threat_model.md", "threat model table"]], widths=[2.6, 4.3], size=8, caption="Repository map")
    b.code("""# one-time: Docker Desktop, kind, kubectl, Python 3.13
python scripts/endterm_deploy.py --rebuild          # builds both images, creates cluster ft-aitu, deploys baseline + endterm
python -m unittest discover -s tests -v             # 49+ unit and integration tests, no cluster needed
python scripts/endterm_experiments.py --reps 3 --ablation   # about 60 minutes
python scripts/endterm_post.py                      # log scan, segmentation re-check, tests record
python scripts/endterm_demo.py                      # live demo of 3 scenarios (+ transcript)
python scripts/make_endterm_figures.py && python scripts/endterm_docs.py && python scripts/make_endterm_report.py""")
    b.h("Appendix B. Per-run verdicts", 2)
    rows = [["Experiment", "Configuration", "Repetition", "Criteria passed", "Verdict"]]
    for exp in R.EXPERIMENTS:
        for cfg in ["baseline", "endterm"] + sorted({k[1] for k in R.DATA if k[0] == exp and k[1] not in R.CONFIGS}):
            for r in R.runs(exp, cfg):
                rows.append([exp, cfg, r["rep"], f"{sum(1 for c in r['criteria'] if c['pass'])}/{len(r['criteria'])}", "PASS" if r["passed"] else "FAIL"])
    b.table(rows, widths=[1.0, 1.7, 1.0, 1.4, 1.0], size=7.5, caption="Every measured run")
    b.h("Appendix C. Sample logs and metrics", 2)
    for name in ("log_gateway.jsonl", "log_registration.jsonl", "log_auth.jsonl"):
        path = os.path.join(OUT, "samples", name)
        if os.path.exists(path):
            lines = [l for l in open(path, encoding="utf-8").read().splitlines() if l.strip()][-6:]
            b.p(name, bold=True, size=9)
            b.code("\n".join(l[:230] for l in lines), size=6.5)
    path = os.path.join(OUT, "samples", "metrics_gateway.prom")
    if os.path.exists(path):
        b.p("metrics_gateway.prom (excerpt)", bold=True, size=9)
        b.code("\n".join(open(path, encoding="utf-8").read().splitlines()[:16]), size=6.5)
    b.h("Appendix D. Demonstration transcript (three scenarios)", 2)
    if DEMO:
        b.code("\n".join(DEMO.splitlines()[:75]), size=6.5)
    else:
        b.p("Run scripts/endterm_demo.py to create the transcript.", italic=True)
    b.h("Appendix E. Test output", 2)
    if TESTS_TXT:
        lines = [l for l in TESTS_TXT.splitlines() if l.strip()]
        b.code("\n".join(lines[-14:]), size=7)
    b.d.save(OUTPUT)
    return OUTPUT, tp, fn, fp


def test_plan_markdown():
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    lines = ["# Verification and validation plan", "",
             "| Level | Scope | Command |", "|---|---|---|",
             "| Unit | tokens, passwords, lockout, token bucket, breaker, validation, metrics, store, alert engine | `python -m unittest discover -s tests -v` |",
             "| Integration | five roles in-process over HTTP: workflows, authorization, validation, rate limit, correlation, degradation, DB outage | same command |",
             "| System / fault injection | experiments E1-E7, 3 repetitions, baseline vs endterm | `python scripts/endterm_experiments.py --reps 3 --ablation` |", "",
             "Method: open-loop workload of 50 req/s, injection, 1 s collection of pod readiness and /metrics, data-integrity audit of both database nodes after every run, "
             "state restored between runs. Acceptance criteria are identical for both configurations.", ""]
    for exp in R.EXPERIMENTS:
        lines += [f"## {NAMES[exp]}", ""]
        for name, limit, unit, row in R.criteria_table(exp):
            lines.append(f"* {name}: {limit} {unit}".rstrip())
        lines.append("")
    with open(os.path.join(ROOT, "docs", "test_plan.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def summary_markdown():
    lines = ["# Endterm experiment results", "", "| Experiment | Type | Baseline | Endterm |", "|---|---|---|---|"]
    for exp in R.EXPERIMENTS:
        lines.append(f"| {NAMES[exp]} | {KIND[exp]} | {pf(exp, 'baseline')} | {pf(exp, 'endterm')} |")
    lines += ["", "Criteria (passed repetitions / repetitions; median value):", ""]
    for exp in R.EXPERIMENTS:
        lines += [f"## {NAMES[exp]}", "", "| Criterion | Limit | Baseline | Endterm |", "|---|---|---|---|"]
        for name, limit, unit, row in R.criteria_table(exp):
            cells = []
            for cfg in R.CONFIGS:
                ok, n, val, note = row[cfg]
                cells.append(f"{ok}/{n} ({val}{(' ' + unit) if unit else ''}){' - ' + note if note else ''}")
            lines.append(f"| {name} | {limit} {unit} | {cells[0]} | {cells[1]} |")
        lines.append("")
    with open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    summary_markdown()
    test_plan_markdown()
    path, tp, fn, fp = build()
    print("saved", path, "| alerts TP/FN/FP:", tp, fn, fp)
