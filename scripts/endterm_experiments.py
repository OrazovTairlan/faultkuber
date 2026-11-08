"""Endterm experiment suite: baseline (midterm, commit a6b1daa) versus endterm implementation.

Seven experiments (five fault-injection / abnormal-condition, two security), each repeated REPS times per configuration:
  E1 instance failure        E2 dependency timeout       E3 load spike (+ ablation without NetworkPolicy)
  E4 monitoring + degradation (Course service down)      E5 unauthorized access
  E6 abusive / malformed requests                        E7 recovery and consistency

Usage: python scripts/endterm_experiments.py [--only E1,E2] [--reps 3] [--configs baseline,endterm]
Results: results/endterm/runs/<experiment>_<config>_rep<n>.json (+ per-request CSV for rep 1, samples/).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
import endterm_load as L
import obs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "endterm")
CTX = "kind-ft-aitu"
DESIRED = {"gateway": 2, "auth": 2, "course": 2, "registration": 2, "db": 2}
NORMAL_RPS, SPIKE_RPS = 50, 100
VALID_ID = re.compile(r"^S\d{4,7}:(CS401|CS312|CS205|MA101)$")
RULES = json.load(open(os.path.join(ROOT, "observability", "alerts.json"), encoding="utf-8"))
ENGINE = obs.AlertEngine(RULES)
USER_COUNTER = [1000]


def sh(*cmd, check=True, input_text=None, timeout=120):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", input=input_text, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()[:300]}")
    return r.stdout


def next_users(n=200):
    USER_COUNTER[0] += n + 20
    if USER_COUNTER[0] > 9000:
        USER_COUNTER[0] = 1000
    return USER_COUNTER[0]


def ensure_proxy():
    try:
        obs.kube_get("/api/v1/namespaces/default", timeout=1.0)
        return
    except OSError:
        pass
    subprocess.Popen(["kubectl", "--context", CTX, "proxy", "--port=8001"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.5)
        try:
            obs.kube_get("/api/v1/namespaces/default", timeout=1.0)
            return
        except OSError:
            continue
    raise RuntimeError("kubectl proxy did not start")


class Stack:
    def __init__(self, name, ns, port, endterm, netpol=True):
        self.name, self.ns, self.port, self.endterm, self.netpol = name, ns, port, endterm, netpol

    def k(self, *args, **kw):
        return sh("kubectl", "--context", CTX, "-n", self.ns, *args, **kw)

    def ready_counts(self):
        _, data = obs.kube_get(f"/api/v1/namespaces/{self.ns}/pods")
        counts = {}
        for item in json.loads(data)["items"]:
            if item["metadata"].get("deletionTimestamp"):
                continue
            app = item["metadata"].get("labels", {}).get("app")
            c = counts.setdefault(app, 0)
            ok = any(x["type"] == "Ready" and x["status"] == "True" for x in item["status"].get("conditions", []))
            counts[app] = c + (1 if ok else 0)
        return counts

    def all_ready(self):
        counts = self.ready_counts()
        return all(counts.get(app, 0) >= n for app, n in DESIRED.items())

    def wait_ready(self, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            if self.all_ready():
                return True
            time.sleep(1)
        raise TimeoutError(f"{self.name} did not become ready")

    def block_db(self):
        if self.endterm and self.netpol:
            self.k("delete", "networkpolicy", "db-ingress-registration-peer-vault", "--ignore-not-found")
        else:
            policy = ("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: fi-block-db, namespace: %s}\n"
                      "spec:\n  podSelector: {matchLabels: {app: db}}\n  policyTypes: [Ingress]\n" % self.ns)
            sh("kubectl", "--context", CTX, "apply", "-f", "-", input_text=policy)

    def unblock_db(self):
        if self.endterm and self.netpol:
            sh("kubectl", "--context", CTX, "apply", "-f", os.path.join(ROOT, "k8s", "security", "10-networkpolicies.yaml"))
        else:
            self.k("delete", "networkpolicy", "fi-block-db", "--ignore-not-found")

    def set_netpol(self, enabled):
        path = os.path.join(ROOT, "k8s", "security", "10-networkpolicies.yaml")
        sh("kubectl", "--context", CTX, "apply" if enabled else "delete", "-f", path, check=False)
        self.netpol = enabled
        time.sleep(5)

    def restore_state(self):
        if self.endterm:
            sh("kubectl", "--context", CTX, "apply", "-f", os.path.join(ROOT, "k8s", "security", "10-networkpolicies.yaml"), check=False)
            self.netpol = True
        self.k("delete", "networkpolicy", "fi-block-db", "--ignore-not-found", check=False)
        for app in ("auth", "course", "registration", "gateway"):
            self.k("scale", f"deploy/{app}", "--replicas=2")
        self.k("scale", "sts/db", "--replicas=2")
        self.wait_ready()
        self.wait_gateway()
        time.sleep(8)

    def wait_gateway(self, timeout=600):
        """The Docker Desktop port proxy can stall after an overload run; wait until 15 consecutive requests succeed."""
        end, good = time.time() + timeout, 0
        while time.time() < end:
            ok = L.http_call(self.port, "GET", "/timetable?group=SE-2301", timeout=2.0, keepalive=False)[2] == 200
            good = good + 1 if ok else 0
            if good >= 15:
                return
            time.sleep(0.3 if ok else 3)
        raise TimeoutError(f"gateway of {self.name} not reachable from the host")

    def wipe_db(self):
        self.k("scale", "sts/db", "--replicas=0")
        end = time.time() + 90
        while time.time() < end and self.ready_counts().get("db", 0):
            time.sleep(1)
        time.sleep(3)
        self.k("delete", "pvc", "data-db-0", "data-db-1", "--wait=true")
        self.k("scale", "sts/db", "--replicas=2")
        self.wait_ready()
        time.sleep(8)

    def pods(self, app):
        _, data = obs.kube_get(f"/api/v1/namespaces/{self.ns}/pods")
        return [i["metadata"]["name"] for i in json.loads(data)["items"]
                if i["metadata"].get("labels", {}).get("app") == app and not i["metadata"].get("deletionTimestamp")]

    def restart_total(self):
        _, data = obs.kube_get(f"/api/v1/namespaces/{self.ns}/pods")
        return sum(cs.get("restartCount", 0) for i in json.loads(data)["items"] for cs in i["status"].get("containerStatuses", []))

    def cpu(self, app):
        out = {}
        for pod in self.pods(app):
            r = self.k("exec", pod, "--", "cat", "/sys/fs/cgroup/cpu.stat", check=False)
            d = dict(l.split() for l in r.splitlines() if l.strip())
            out[pod] = (int(d.get("usage_usec", 0)), int(d.get("nr_throttled", 0)), int(d.get("nr_periods", 0)))
        return out

    def events_total(self):
        """Sum of aitu_events_total per (service, event) over all ready pods (endterm only)."""
        totals = {}
        for app in ("gateway", "auth", "registration", "course"):
            for pod in self.pods(app):
                try:
                    _, text = obs.kube_get(f"/api/v1/namespaces/{self.ns}/pods/{pod}:8080/proxy/metrics", timeout=2.0)
                except OSError:
                    continue
                for name, labels, value in obs.parse_prometheus(text):
                    if name == "aitu_events_total":
                        key = (app, labels["event"])
                        totals[key] = totals.get(key, 0) + value
        return totals

    def db_state(self, pod):
        code = ("import json,urllib.request\nids=[r['id'] for r in json.load(urllib.request.urlopen('http://localhost:8080/db/dump'))['records']]\n"
                "lines=0\nuniq=set()\ntry:\n    for l in open('/data/records.jsonl'):\n        if l.strip():\n            lines+=1\n            uniq.add(json.loads(l)['id'])\n"
                "except Exception:\n    pass\nprint(json.dumps({'ids':ids,'file_lines':lines,'file_unique':len(uniq)}))")
        return json.loads(self.k("exec", pod, "--", "python", "-c", code, timeout=60))

    def integrity(self, acked, wait_identical_s=60, before_ids=None):
        deadline, st0, st1, identical = time.time() + wait_identical_s, None, None, False
        started = time.time()
        while True:
            st0, st1 = self.db_state("db-0"), self.db_state("db-1")
            identical = set(st0["ids"]) == set(st1["ids"])
            if identical or time.time() > deadline:
                break
            time.sleep(3)
        ids0, ids1 = set(st0["ids"]), set(st1["ids"])
        junk = sorted(i for i in ids0 | ids1 if not VALID_ID.match(i) and (before_ids is None or i not in before_ids))
        return {"records_db0": len(ids0), "records_db1": len(ids1), "nodes_identical": identical,
                "seconds_to_identical": round(time.time() - started, 1) if identical else None,
                "acked_writes": len(acked), "lost_acked": sorted(a for a in acked if a not in ids0 or a not in ids1)[:20],
                "lost_acked_count": sum(1 for a in acked if a not in ids0 or a not in ids1),
                "duplicate_lines": (st0["file_lines"] - st0["file_unique"]) + (st1["file_lines"] - st1["file_unique"]),
                "invalid_or_junk_records": len(junk), "junk_examples": junk[:5], "_ids": sorted(ids0)}


STACKS = {"baseline": Stack("baseline", "baseline", 18081, False), "endterm": Stack("endterm", "aitu-sim", 18080, True)}


class Observed:
    """Everything recorded around one fault scenario."""

    def __init__(self, stack, rps, scenario, pre_s=10, post_s=12, users=200, mix=None):
        self.stack, self.rps, self.scenario, self.pre_s, self.post_s, self.users, self.mix = stack, rps, scenario, pre_s, post_s, users, mix
        self.marks, self.gen, self.collector = {}, None, None

    def run(self):
        st = self.stack
        self.collector = obs.Collector(st.ns, interval=1.0, scrape_metrics=st.endterm)
        self.collector.start()
        self.t_start = time.time()
        if self.rps:
            self.gen = L.LoadGen(st.port, self.rps, next_users(self.users), users=self.users, mix=self.mix).start()
        self.t_load = time.time()
        try:
            time.sleep(self.pre_s)
            self.marks = self.scenario(self) or {}
            time.sleep(self.post_s)
        finally:
            self.t_end = time.time()
            if self.gen:
                self.gen.finish()
            self.collector.stop()
        return self

    @property
    def records(self):
        return self.gen.records if self.gen else []

    @property
    def acked(self):
        return self.gen.acked if self.gen else {}


def detection_times(obs_run, t0):
    """Kubernetes-level detection/recovery (pod readiness, 1 s resolution) and client-visible failure window."""
    det = rec = None
    for ts, counts in obs_run.collector.ready_series:
        if ts < t0:
            continue
        short = any(counts.get(app, {"ready": 0})["ready"] < n for app, n in DESIRED.items())
        if det is None and short:
            det = ts - t0
        if det is not None and rec is None and not short and ts - t0 > det:
            rec = ts - t0
    bad = [r for r in obs_run.records if r[0] >= t0 and not 200 <= r[2] < 300]
    first = round(min(r[0] for r in bad) - t0, 2) if bad else None
    last = round(max(r[0] + r[1] / 1000 for r in bad) - t0, 2) if bad else None
    return {"k8s_detection_s": None if det is None else round(det, 1), "k8s_recovery_s": None if rec is None else round(rec, 1),
            "client_first_failure_s": first, "client_last_failure_s": last, "client_failed_requests": len(bad)}


def alert_report(obs_run, t0, expected, optional):
    if not obs_run.stack.endterm:
        return {"available": False, "note": "baseline exposes no metrics and defines no alerts", "fired": [], "expected": sorted(expected),
                "true_positive": [], "false_negative": sorted(expected), "false_positive": [], "pre_fault_alerts": 0, "first_expected_delay_s": None}
    series, ready = obs_run.collector.series, obs_run.collector.ready_series
    fired = ENGINE.evaluate(series, ready, obs_run.t_load + 3, obs_run.t_end)
    names = {a["alert"] for a in fired if a["fired_at"] >= t0 - 0.5}
    pre = [a for a in fired if a["fired_at"] < t0 - 0.5]
    tp = sorted(names & expected)
    fp = sorted(n for n in names if n not in expected and n not in optional) + sorted(a["alert"] for a in pre)
    delays = [a["fired_at"] - t0 for a in fired if a["alert"] in expected and a["fired_at"] >= t0 - 0.5]
    return {"available": True, "fired": [{**a, "fired_after_s": round(a["fired_at"] - t0, 1),
                                          "resolved_after_s": None if a["resolved_at"] is None else round(a["resolved_at"] - t0, 1)} for a in fired],
            "expected": sorted(expected), "optional": sorted(optional), "true_positive": tp, "false_negative": sorted(expected - names),
            "false_positive": fp, "pre_fault_alerts": len(pre), "first_expected_delay_s": round(min(delays), 1) if delays else None}


def dashboard_series(obs_run, t0):
    """Per-second derived series for the dashboard (endterm only)."""
    if not obs_run.stack.endterm:
        return []
    series, ready = obs_run.collector.series, obs_run.collector.ready_series
    out, t = [], obs_run.t_load
    while t <= obs_run.t_end:
        d1 = obs.window_delta(series, t, 1.0).get("gateway", {})
        d5 = obs.window_delta(series, t, 5.0)
        total = sum(v for (k, _), v in d1.items() if k == "req")
        bad = sum(v for (k, s), v in d1.items() if k == "req" and s.startswith("5"))
        p95 = obs.AlertEngine._p95_ms(d5, "gateway")
        events = obs.window_delta(series, t, 5.0)
        snap = None
        for ts, counts in ready:
            if ts <= t:
                snap = counts
        out.append({"t": round(t - t0, 1), "rps": total, "errors_5xx": bad, "p95_ms": None if p95 is None else round(p95, 1),
                    "ready_pods": sum(c["ready"] for c in (snap or {}).values()),
                    "degraded": events.get("gateway", {}).get(("event", "degraded_response"), 0)})
        t += 1.0
    return out


def criterion(name, value, limit, passed, unit="", note=""):
    return {"name": name, "value": value, "limit": limit, "unit": unit, "pass": bool(passed), "note": note}


def save_run(exp, stack, rep, data, run):
    os.makedirs(os.path.join(OUT, "runs"), exist_ok=True)
    data.update({"experiment": exp, "config": stack.name, "rep": rep, "netpol_enabled": stack.netpol})
    for c in data.get("criteria", []):
        c["pass"] = bool(c["pass"])
    data["passed"] = all(c["pass"] for c in data.get("criteria", []))
    if "integrity" in data:
        data["integrity"].pop("_ids", None)
    with open(os.path.join(OUT, "runs", f"{exp}_{stack.name}_rep{rep}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    if rep == 1 and run is not None and run.records:
        with open(os.path.join(OUT, "runs", f"{exp}_{stack.name}_rep1_requests.csv"), "w", encoding="utf-8") as f:
            f.write("t_rel_s,latency_ms,status,kind,degraded\n")
            t0 = data["t0_epoch"]
            for t, lat, st, kind, dg in sorted(run.records):
                f.write(f"{t - t0:.3f},{lat:.1f},{st},{kind},{int(dg)}\n")
    return data


def base_data(run, t0, expected, optional):
    summary = L.summarize(run.records)
    return {"rps_normal": run.rps, "t0_epoch": t0, "summary_whole": summary,
            "summary_pre": L.summarize(run.records, run.t_load + 2, t0), "timing": detection_times(run, t0),
            "alerts": alert_report(run, t0, expected, optional), "per_second": L.per_second(run.records, t0, -run.pre_s, int(run.t_end - t0)),
            "dashboard": dashboard_series(run, t0)}


def e1(stack, rep):
    def scenario(o):
        pod = stack.pods("registration")[0]
        t0 = time.time()
        stack.k("delete", "pod", pod, "--grace-period=0", "--force", check=False)
        return {"t0": t0, "injected": f"kubectl delete pod {pod} --force --grace-period=0"}
    run = Observed(stack, NORMAL_RPS, scenario, pre_s=10, post_s=18).run()
    t0 = run.marks["t0"]
    data = base_data(run, t0, {"ReplicaUnavailable"}, {"HighErrorRate", "HighLatencyP95"})
    stack.wait_ready()
    data["integrity"] = stack.integrity(run.acked)
    data["injected"] = run.marks["injected"]
    s, tm, al = data["summary_whole"], data["timing"], data["alerts"]
    data["criteria"] = [
        criterion("success ratio during the run", s["success_pct"], ">= 99.0", s["success_pct"] is not None and s["success_pct"] >= 99.0, "%"),
        criterion("all pods Ready again", tm["k8s_recovery_s"], "<= 30", tm["k8s_recovery_s"] is not None and tm["k8s_recovery_s"] <= 30, "s"),
        criterion("lost acknowledged writes", data["integrity"]["lost_acked_count"], "== 0", data["integrity"]["lost_acked_count"] == 0),
        criterion("automated alert for the lost replica", al["first_expected_delay_s"], "<= 5", al["first_expected_delay_s"] is not None and al["first_expected_delay_s"] <= 5, "s",
                  "baseline has no alerting" if not al["available"] else ""),
    ]
    return save_run("E1", stack, rep, data, run)


def e2(stack, rep):
    def scenario(o):
        t0 = time.time()
        stack.block_db()
        time.sleep(20)
        t_unblock = time.time()
        stack.unblock_db()
        return {"t0": t0, "t_unblock": t_unblock, "injected": "NetworkPolicy blocks all ingress to the database pods for 20 s (packets dropped -> timeouts)"}
    run = Observed(stack, NORMAL_RPS, scenario, pre_s=10, post_s=18).run()
    t0, tu = run.marks["t0"], run.marks["t_unblock"]
    data = base_data(run, t0, {"DatabaseUnavailable", "HighErrorRate"}, {"HighLatencyP95", "ReplicaUnavailable"})
    stack.restore_state()
    data["integrity"] = stack.integrity(run.acked)
    during = L.summarize(run.records, t0 + 1, tu)
    dep = L.summarize(run.records, t0 + 1, tu, {"reg_read", "reg_write"})
    other = L.summarize(run.records, t0 + 1, tu, {"timetable", "courses", "login"})
    failed_reg = [r[1] for r in run.records if t0 + 1 <= r[0] <= tu and r[3] in ("reg_read", "reg_write") and not 200 <= r[2] < 300]
    after_ok = [r[0] for r in run.records if r[0] >= tu and r[3] == "reg_write" and r[2] in (200, 201)]
    recovery = round(min(after_ok) - tu, 1) if after_ok else None
    f_sorted = sorted(failed_reg)
    data.update({"during_fault": during, "db_dependent_during_fault": dep, "independent_during_fault": other, "recovery_after_unblock_s": recovery,
                 "failed_db_request_p95_ms": round(f_sorted[int(0.95 * len(f_sorted))], 1) if f_sorted else None,
                 "max_latency_during_fault_ms": round(max((r[1] for r in run.records if t0 <= r[0] <= tu), default=0), 1),
                 "injected": run.marks["injected"]})
    al = data["alerts"]
    data["criteria"] = [
        criterion("requests independent of the DB (timetable, courses, login) succeed", other["success_pct"], ">= 99.0", other["success_pct"] is not None and other["success_pct"] >= 99.0, "%"),
        criterion("failed DB-dependent requests fail fast (p95 latency)", data["failed_db_request_p95_ms"], "<= 500", data["failed_db_request_p95_ms"] is not None and data["failed_db_request_p95_ms"] <= 500, "ms"),
        criterion("no request slower than 1 s during the fault", data["max_latency_during_fault_ms"], "<= 1000", data["max_latency_during_fault_ms"] <= 1000, "ms"),
        criterion("registration works again after the block is removed", recovery, "<= 15", recovery is not None and recovery <= 15, "s"),
        criterion("lost acknowledged writes", data["integrity"]["lost_acked_count"], "== 0", data["integrity"]["lost_acked_count"] == 0),
        criterion("automated alert (DatabaseUnavailable / HighErrorRate)", al["first_expected_delay_s"], "<= 10", al["first_expected_delay_s"] is not None and al["first_expected_delay_s"] <= 10, "s",
                  "baseline has no alerting" if not al["available"] else ""),
    ]
    return save_run("E2", stack, rep, data, run)


def e3(stack, rep):
    def scenario(o):
        before = {a: stack.cpu(a) for a in ("gateway", "auth", "registration")}
        t0 = time.time()
        o.gen.rps = SPIKE_RPS
        time.sleep(20)
        t_end = time.time()
        o.gen.rps = NORMAL_RPS
        after = {a: stack.cpu(a) for a in ("gateway", "auth", "registration")}
        cpu = {}
        for app in before:
            cpu[app] = {"cpu_seconds": round(sum(after[app][p][0] - before[app].get(p, (0, 0, 0))[0] for p in after[app]) / 1e6, 2),
                        "throttled_periods": sum(after[app][p][1] - before[app].get(p, (0, 0, 0))[1] for p in after[app]),
                        "periods": sum(after[app][p][2] - before[app].get(p, (0, 0, 0))[2] for p in after[app])}
        return {"t0": t0, "t_spike_end": t_end, "cpu": cpu, "injected": f"request rate raised from {NORMAL_RPS} to {SPIKE_RPS} req/s for 20 s"}
    run = Observed(stack, NORMAL_RPS, scenario, pre_s=10, post_s=12).run()
    t0, te = run.marks["t0"], run.marks["t_spike_end"]
    data = base_data(run, t0, set(), {"HighLatencyP95", "HighErrorRate"})
    spike = L.summarize(run.records, t0 + 1, te)
    elapsed = te - t0
    data.update({"during_spike": spike, "achieved_rps_during_spike": round(spike["total"] / elapsed, 1), "resource_use": run.marks["cpu"],
                 "after_spike": L.summarize(run.records, te + 3, run.t_end), "injected": run.marks["injected"]})
    stack.wait_ready()
    data["integrity"] = stack.integrity(run.acked)
    data["criteria"] = [
        criterion("success ratio during the spike", spike["success_pct"], ">= 99.0", spike["success_pct"] is not None and spike["success_pct"] >= 99.0, "%"),
        criterion("p95 latency during the spike", spike["p95_ms"], "<= 500", spike["p95_ms"] is not None and spike["p95_ms"] <= 500, "ms"),
        criterion("throughput reaches the offered load (>= 90 %)", data["achieved_rps_during_spike"], f">= {0.9 * SPIKE_RPS:.0f}", data["achieved_rps_during_spike"] >= 0.9 * SPIKE_RPS, "req/s"),
        criterion("lost acknowledged writes", data["integrity"]["lost_acked_count"], "== 0", data["integrity"]["lost_acked_count"] == 0),
    ]
    return save_run("E3", stack, rep, data, run)


def e4(stack, rep):
    def scenario(o):
        t0 = time.time()
        stack.k("scale", "deploy/course", "--replicas=0")
        time.sleep(20)
        t_restore = time.time()
        stack.k("scale", "deploy/course", "--replicas=2")
        return {"t0": t0, "t_restore": t_restore, "injected": "Course service scaled to 0 replicas for 20 s (non-critical feature)"}
    run = Observed(stack, NORMAL_RPS, scenario, pre_s=10, post_s=18).run()
    t0, tr = run.marks["t0"], run.marks["t_restore"]
    data = base_data(run, t0, {"ReplicaUnavailable", "DegradedMode"}, {"HighErrorRate"})
    stack.wait_ready()
    data["integrity"] = stack.integrity(run.acked)
    window = (t0 + 2, tr)
    noncrit = L.summarize(run.records, *window, {"timetable", "courses"})
    critical = L.summarize(run.records, *window, {"reg_read", "reg_write", "login"})
    data.update({"non_critical_during_outage": noncrit, "critical_during_outage": critical, "injected": run.marks["injected"]})
    al = data["alerts"]
    data["criteria"] = [
        criterion("non-critical reads (timetable, courses) keep succeeding (graceful degradation)", noncrit["success_pct"], ">= 99.0", noncrit["success_pct"] is not None and noncrit["success_pct"] >= 99.0, "%"),
        criterion("critical workflow (login, registration) unaffected", critical["success_pct"], ">= 99.0", critical["success_pct"] is not None and critical["success_pct"] >= 99.0, "%"),
        criterion("degraded answers are flagged to the client (X-Degraded)", noncrit["degraded_responses"], "> 0", noncrit["degraded_responses"] > 0),
        criterion("automated alert for the outage", al["first_expected_delay_s"], "<= 10", al["first_expected_delay_s"] is not None and al["first_expected_delay_s"] <= 10, "s",
                  "baseline has no alerting" if not al["available"] else ""),
        criterion("lost acknowledged writes", data["integrity"]["lost_acked_count"], "== 0", data["integrity"]["lost_acked_count"] == 0),
    ]
    return save_run("E4", stack, rep, data, run)


def forge_signature(token):
    return token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")


def forge_subject(token, victim, endterm):
    if not endterm:
        sid, _, sig = token.partition(".")
        return f"{victim}.{sig}"
    payload, sig = token.split(".")
    claims = json.loads(obs_unb64(payload))
    claims["sub"] = victim
    return f"{obs_b64(json.dumps(claims, separators=(',', ':')).encode())}.{sig}"


def obs_b64(data):
    import base64
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def obs_unb64(text):
    import base64
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def e5_summary(results):
    """Attack outcome per attempt: succeeded = 2xx for the attacker, denied = 400/401/403/404/429, inconclusive = transport error or 5xx."""
    succeeded = [r for r in results if 200 <= r["status"] < 300]
    denied = [r for r in results if r["status"] in (400, 401, 403, 404, 429)]
    inconclusive = [r for r in results if r not in succeeded and r not in denied]
    return {"attempt_count": len(results), "denied_count": len(denied), "violation_count": len(succeeded), "inconclusive_attempts": len(inconclusive),
            "violations": sorted({r["attempt"] for r in succeeded}), "data_exposed_responses": sum(1 for r in results if r["exposed_victim_data"]),
            "attacker_writes_succeeded": sum(1 for r in succeeded if r["attempt"].startswith("A3"))}


def e5_criteria(data, endterm):
    n = data["attempt_count"]
    al = data["alerts"]
    return [
        criterion("unauthorized attempts that succeeded for the attacker (2xx)", f"{data['violation_count']}/{n}", "0/" + str(n), data["violation_count"] == 0, "",
                  f"{data['inconclusive_attempts']} attempt(s) inconclusive (transport error or 5xx), not counted as success" if data["inconclusive_attempts"] else ""),
        criterion("victim's data changed by the attacker's write attempts", data["attacker_writes_succeeded"] > 0, "False", data["attacker_writes_succeeded"] == 0),
        criterion("victim's data returned to the attacker", data["data_exposed_responses"], "== 0", data["data_exposed_responses"] == 0),
        criterion("every denied attempt left an audit event", data["audit_events_recorded"], f">= {data['denied_count']}", endterm and data["audit_events_recorded"] >= data["denied_count"], "",
                  "baseline has no audit log" if not endterm else ""),
        criterion("automated alert for the attack", al["first_expected_delay_s"], "<= 10", al["first_expected_delay_s"] is not None and al["first_expected_delay_s"] <= 10, "s",
                  "baseline has no alerting" if not al["available"] else ""),
    ]


def e5(stack, rep):
    port, results = stack.port, []
    att, vic = f"S{9100 + rep * 10:04d}", f"S{9101 + rep * 10:04d}"

    def scenario(o):
        tok_a, tok_v = L.login(port, att), L.login(port, vic)
        L.http_call(port, "POST", "/registrations", {"student_id": vic, "course_code": "CS401"}, tok_v)
        victim_before = L.http_call(port, "GET", f"/registrations/{vic}", token=tok_v)[3]
        events_before = stack.events_total() if stack.endterm else {}
        t0 = time.time()
        attempts = [
            ("A1 write without any token", "POST", "/registrations", {"student_id": vic, "course_code": "CS312"}, None, None),
            ("A2 write with a forged signature", "POST", "/registrations", {"student_id": vic, "course_code": "CS312"}, forge_signature(tok_a), None),
            ("A3 write on behalf of another student (valid token of the attacker)", "POST", "/registrations", {"student_id": vic, "course_code": "CS312"}, tok_a, None),
            ("A4 read another student's registrations", "GET", f"/registrations/{vic}", None, tok_a, None),
            ("A5 token with the subject replaced by the victim", "GET", f"/registrations/{vic}", None, forge_subject(tok_a, vic, stack.endterm), None),
            ("A6 student calls the administrator endpoint", "GET", "/admin/count", None, tok_a, None),
            ("A7 login without a password", "POST", "/auth/login", {"student_id": vic}, None, None),
            ("A8 login with a wrong password", "POST", "/auth/login", {"student_id": vic, "password": "guess-1"}, None, None),
        ]
        for name, method, path, body, token, raw in attempts:
            for _ in range(3):
                t, lat, status, resp, headers = L.http_call(port, method, path, body, token)
                results.append({"attempt": name, "status": status, "denied": status in (400, 401, 403, 404, 429),
                                "exposed_victim_data": bool(isinstance(resp, dict) and any(vic in json.dumps(r) for r in resp.get("registrations", []))),
                                "token_issued": bool(isinstance(resp, dict) and resp.get("token"))})
        time.sleep(2)
        events_after = stack.events_total() if stack.endterm else {}
        victim_after = L.http_call(port, "GET", f"/registrations/{vic}", token=tok_v)[3]
        return {"t0": t0, "events_before": {f"{k[0]}.{k[1]}": v for k, v in events_before.items()},
                "events_after": {f"{k[0]}.{k[1]}": v for k, v in events_after.items()},
                "victim_before": victim_before, "victim_after": victim_after, "injected": "8 unauthorized-access patterns x 3 attempts"}
    run = Observed(stack, 30, scenario, pre_s=8, post_s=12).run()
    t0 = run.marks["t0"]
    data = base_data(run, t0, {"UnauthorizedAccessSpike", "ForbiddenAccessSpike"}, {"MalformedRequestBurst", "BruteForceLogin"})
    stack.wait_ready()
    data["integrity"] = stack.integrity(run.acked)
    n_att = len(results)
    records_before = len((run.marks["victim_before"] or {}).get("registrations", []))
    records_after = len((run.marks["victim_after"] or {}).get("registrations", []))
    eb, ea = run.marks["events_before"], run.marks["events_after"]
    audited = sum(ea.get(k, 0) - eb.get(k, 0) for k in ea if k.split(".")[1] in ("unauthorized_request", "forbidden_access", "login_failed", "login_rejected"))
    data.update({"attempts": results, "events_before": eb, "events_after": ea, "audit_events_recorded": audited, "victim_records_before": records_before,
                 "victim_records_after": records_after, "injected": run.marks["injected"]})
    data.update(e5_summary(results))
    data["criteria"] = e5_criteria(data, stack.endterm)
    return save_run("E5", stack, rep, data, run)


MALFORMED = [
    ("invalid JSON", {"raw_body": "{not json"}),
    ("JSON array instead of object", {"raw_body": "[1,2,3]"}),
    ("missing fields", {"body": {}}),
    ("unknown course", {"body": {"student_id": "ME", "course_code": "NOPE"}}),
    ("injection-like student id", {"body": {"student_id": "ME'; DROP TABLE registrations;--", "course_code": "CS401"}}),
    ("numeric student id", {"body": {"student_id": 12345, "course_code": "CS401"}}),
    ("5000-character student id", {"body": {"student_id": "S" + "9" * 5000, "course_code": "CS401"}}),
    ("20 KB request body", {"raw_body": json.dumps({"student_id": "ME", "course_code": "CS401", "pad": "x" * 20000})}),
    ("control characters in student id", {"body": {"student_id": "ME\u0000\r\n", "course_code": "CS401"}}),
    ("list as student id", {"body": {"student_id": ["S1"], "course_code": "CS401"}}),
]


def e6(stack, rep):
    port, out = stack.port, {}
    att, vic = f"S{9300 + rep * 10:04d}", f"S{9301 + rep * 10:04d}"

    def scenario(o):
        tok = L.login(port, att)
        before_ids = set(stack.db_state("db-0")["ids"])
        restarts_before = stack.restart_total()
        t0 = time.time()
        m1 = []
        for name, kw in MALFORMED:
            body = kw.get("body")
            if body is not None and body.get("student_id") == "ME":
                body = {**body, "student_id": att}
            t, lat, status, resp, _ = L.http_call(port, "POST", "/registrations", body, tok, raw_body=kw.get("raw_body"))
            m1.append({"case": name, "status": status, "server_error": status >= 500 or status == 0})
        for path in ("/registrations/../../etc/passwd", "/registrations/" + "A" * 3000):
            t, lat, status, resp, _ = L.http_call(port, "GET", path, token=tok)
            m1.append({"case": "path attack " + path[:30], "status": status, "server_error": status >= 500 or status == 0})
        statuses, lock = [], threading.Lock()

        def burst(n):
            for _ in range(n):
                s = L.http_call(port, "GET", f"/registrations/{att}", token=tok)[2]
                with lock:
                    statuses.append(s)
        threads = [threading.Thread(target=burst, args=(60,)) for _ in range(16)]
        tb = time.time()
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        burst_s = time.time() - tb
        bf = [L.http_call(port, "POST", "/auth/login", {"student_id": vic, "password": f"wrong-{i}"})[2] for i in range(12)]
        correct_after = L.http_call(port, "POST", "/auth/login", {"student_id": vic, "password": f"pw-{vic}"})[2]
        time.sleep(2)
        after_ids = set(stack.db_state("db-0")["ids"])
        out.update({"malformed": m1, "burst_statuses": statuses, "burst_seconds": round(burst_s, 2), "brute_force": bf, "correct_login_after_bruteforce": correct_after,
                    "restarts": stack.restart_total() - restarts_before})
        return {"t0": t0, "new_ids": sorted(after_ids - before_ids), "injected": "10 malformed bodies + 2 path attacks, 960-request burst from one account, 12 wrong-password logins"}
    run = Observed(stack, 30, scenario, pre_s=8, post_s=15).run()
    t0 = run.marks["t0"]
    data = base_data(run, t0, {"MalformedRequestBurst", "RateLimiting", "BruteForceLogin"}, {"UnauthorizedAccessSpike", "HighLatencyP95"})
    stack.wait_ready()
    data["integrity"] = stack.integrity(run.acked)
    junk = [i for i in run.marks["new_ids"] if not VALID_ID.match(i)]
    server_errors = [m for m in out["malformed"] if m["server_error"]]
    codes = {}
    for s in out["burst_statuses"]:
        codes[str(s)] = codes.get(str(s), 0) + 1
    locked_after = next((i + 1 for i, s in enumerate(out["brute_force"]) if s == 429), None)
    victims = L.summarize(run.records, t0, t0 + 20)
    data.update({"malformed": out["malformed"], "malformed_server_errors": len(server_errors), "junk_records_stored": len(junk), "junk_examples": junk[:3],
                 "burst_status_counts": codes, "burst_seconds": out["burst_seconds"], "brute_force_statuses": out["brute_force"], "lockout_after_attempt": locked_after,
                 "correct_login_status_after_bruteforce": out["correct_login_after_bruteforce"], "pod_restarts_during_test": out["restarts"],
                 "other_users_during_abuse": victims, "injected": run.marks["injected"]})
    al = data["alerts"]
    data["criteria"] = [
        criterion("malformed requests answered with 4xx (no 5xx, no crash)", len(server_errors), "== 0", not server_errors),
        criterion("junk/invalid records stored", len(junk), "== 0", not junk),
        criterion("burst from one account is rate limited (429 seen)", codes.get("429", 0), ">= 1", codes.get("429", 0) >= 1),
        criterion("password guessing is stopped within 5 failures", locked_after, "<= 6", locked_after is not None and locked_after <= 6),
        criterion("other users unaffected during the abuse (success)", victims["success_pct"], ">= 99.0", victims["success_pct"] is not None and victims["success_pct"] >= 99.0, "%"),
        criterion("pods restarted by the abuse", out["restarts"], "== 0", out["restarts"] == 0),
        criterion("automated alert for the abuse", al["first_expected_delay_s"], "<= 10", al["first_expected_delay_s"] is not None and al["first_expected_delay_s"] <= 10, "s",
                  "baseline has no alerting" if not al["available"] else ""),
    ]
    saved = save_run("E6", stack, rep, data, run)
    stack.wipe_db()
    return saved


def e7(stack, rep):
    port = stack.port
    first = next_users(120)
    users = [f"S{first + i:04d}" for i in range(75)]
    payloads = [(users[i % 75], L.COURSES[(i // 75) % 4]) for i in range(300)]
    state = {"acked": {}, "failed": [], "attempts": 0, "retried": 0}
    lock = threading.Lock()
    tokens = {}

    def writer(idx):
        sid, course = payloads[idx]
        for attempt in range(6):
            t, lat, status, resp, _ = L.http_call(port, "POST", "/registrations", {"student_id": sid, "course_code": course}, tokens[sid])
            with lock:
                state["attempts"] += 1
                state["retried"] += 1 if attempt else 0
            if status in (200, 201):
                with lock:
                    state["acked"][f"{sid}:{course}"] = t
                return
            if status in (0, 502, 503):
                time.sleep(0.3 * (attempt + 1))
                continue
            break
        with lock:
            state["failed"].append(f"{sid}:{course}")

    def scenario(o):
        for sid in users:
            tokens[sid] = L.login(port, sid)
        t0 = time.time()

        def disruptor():
            time.sleep(3)
            state["db_killed_at"] = time.time()
            stack.k("delete", "pod", "db-0", "--grace-period=0", "--force", check=False)
            time.sleep(3)
            state["reg_killed_at"] = time.time()
            stack.k("delete", "pod", stack.pods("registration")[0], "--grace-period=0", "--force", check=False)
        d = threading.Thread(target=disruptor)
        d.start()
        stamps = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            for i in range(len(payloads)):
                stamps.append(ex.submit(writer, i))
                time.sleep(1 / 30)
            for f in stamps:
                f.result()
        d.join()
        return {"t0": t0, "t_writes_done": time.time(), "injected": "300 unique registrations (30/s, client retries up to 5x); db-0 killed at +3 s, a Registration pod at +6 s"}
    run = Observed(stack, 0, scenario, pre_s=5, post_s=5).run()
    t0 = run.marks["t0"]
    data = base_data(run, t0, {"ReplicaUnavailable"}, {"HighErrorRate", "DatabaseUnavailable", "HighLatencyP95"})
    stack.wait_ready()
    time.sleep(5)
    data["integrity"] = stack.integrity(state["acked"], wait_identical_s=60)
    before_count = data["integrity"]["records_db0"]
    replay_status = []
    for sid, course in payloads:
        replay_status.append(L.http_call(port, "POST", "/registrations", {"student_id": sid, "course_code": course}, tokens[sid])[2])
    after = stack.integrity(state["acked"], wait_identical_s=30)
    new_after_replay = after["records_db0"] - before_count
    ghosts = sorted(i for i in (f"{s}:{c}" for s, c in payloads) if i in set(data["integrity"]["_ids"]) and i not in state["acked"])
    data.update({"writes_attempted": len(payloads), "writes_acknowledged": len(state["acked"]), "writes_failed_after_retries": len(state["failed"]), "client_attempts": state["attempts"],
                 "client_retries": state["retried"], "unacknowledged_but_stored": len(ghosts), "replay_new_records": new_after_replay,
                 "replay_status_counts": {str(s): replay_status.count(s) for s in set(replay_status)}, "injected": run.marks["injected"]})
    data["summary_whole"] = {"total": state["attempts"], "ok": len(state["acked"]), "failed": state["attempts"] - len(state["acked"]),
                             "success_pct": round(100 * len(state["acked"]) / len(payloads), 2), "avg_ms": None, "p95_ms": None, "max_ms": None}
    ident = data["integrity"]
    data["criteria"] = [
        criterion("lost acknowledged writes", ident["lost_acked_count"], "== 0", ident["lost_acked_count"] == 0),
        criterion("duplicate records", ident["duplicate_lines"], "== 0", ident["duplicate_lines"] == 0),
        criterion("both database nodes identical after recovery", ident["seconds_to_identical"], "<= 60", ident["nodes_identical"], "s"),
        criterion("replaying all 300 requests creates no new records", new_after_replay, "== 0", new_after_replay == 0),
        criterion("every write acknowledged after bounded retries", len(state["failed"]), "== 0", not state["failed"]),
    ]
    return save_run("E7", stack, rep, data, run)


EXPERIMENTS = {"E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6, "E7": e7}


def collect_samples():
    """Sample logs, a metrics dump and one correlated trace of the endterm stack."""
    st = STACKS["endterm"]
    os.makedirs(os.path.join(OUT, "samples"), exist_ok=True)
    port = st.port
    tok = L.login(port, "S9901")
    rid = "demo-trace-0001"
    L.http_call(port, "POST", "/registrations", {"student_id": "S9901", "course_code": "MA101"}, tok, headers={"X-Request-ID": rid})
    L.http_call(port, "POST", "/auth/login", {"student_id": "S9901", "password": "wrong"}, headers={"X-Request-ID": "demo-failed-login"})
    L.http_call(port, "GET", "/registrations/S9902", token=tok, headers={"X-Request-ID": "demo-forbidden"})
    time.sleep(2)
    trace = []
    for app in ("gateway", "auth", "registration", "db"):
        for pod in st.pods(app):
            for line in st.k("logs", pod, "--tail=400", check=False).splitlines():
                if rid in line or "demo-failed-login" in line or "demo-forbidden" in line:
                    trace.append(json.loads(line))
    trace.sort(key=lambda x: x["ts"])
    with open(os.path.join(OUT, "samples", "correlated_trace.json"), "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=1)
    for app in ("gateway", "registration", "auth"):
        pod = st.pods(app)[0]
        _, text = obs.kube_get(f"/api/v1/namespaces/{st.ns}/pods/{pod}:8080/proxy/metrics")
        with open(os.path.join(OUT, "samples", f"metrics_{app}.prom"), "w", encoding="utf-8") as f:
            f.write(text)
        lines = st.k("logs", pod, "--tail=30", check=False)
        with open(os.path.join(OUT, "samples", f"log_{app}.jsonl"), "w", encoding="utf-8") as f:
            f.write(lines)


def restart_cni():
    """Restart the CNI daemon set so that the NetworkPolicy controller starts from a clean state."""
    sh("kubectl", "--context", CTX, "-n", "kube-system", "rollout", "restart", "ds/kindnet")
    sh("kubectl", "--context", CTX, "-n", "kube-system", "rollout", "status", "ds/kindnet", "--timeout=180s")
    time.sleep(10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--rep-from", type=int, default=1)
    ap.add_argument("--configs", default="baseline,endterm")
    ap.add_argument("--ablation", action="store_true", help="alias of --variants no-netpol")
    ap.add_argument("--variants", default="", help="comma list for E3/E4 on the endterm stack: no-netpol, fresh-cni")
    ap.add_argument("--no-samples", action="store_true")
    ap.add_argument("--variants-all", action="store_true", help="run the variants for every selected experiment, not only E3 and E4")
    a = ap.parse_args()
    ensure_proxy()
    os.makedirs(os.path.join(OUT, "runs"), exist_ok=True)
    names = [n for n in (a.only.split(",") if a.only else EXPERIMENTS)]
    for name in names:
        for cfg in [c for c in a.configs.split(",") if c]:
            stack = STACKS[cfg]
            for rep in range(a.rep_from, a.reps + 1):
                stack.restore_state()
                t = time.time()
                res = EXPERIMENTS[name](stack, rep)
                print(f"{name} {cfg} rep{rep}: {'PASS' if res['passed'] else 'FAIL'} "
                      f"({sum(1 for c in res['criteria'] if c['pass'])}/{len(res['criteria'])}) {time.time() - t:.0f}s", flush=True)
    variants = [v for v in a.variants.split(",") if v] + (["no-netpol"] if a.ablation else [])
    for variant in dict.fromkeys(variants):
        for name in [n for n in names if a.variants_all or n in ("E3", "E4")]:
            for rep in range(1, a.reps + 1):
                stack = Stack(f"endterm-{variant}", "aitu-sim", 18080, True)
                stack.restore_state()
                if variant == "no-netpol":
                    stack.set_netpol(False)
                if variant == "fresh-cni":
                    restart_cni()
                    stack.restore_state()
                t = time.time()
                try:
                    res = EXPERIMENTS[name](stack, rep)
                finally:
                    if variant == "no-netpol":
                        stack.set_netpol(True)
                print(f"{name} {stack.name} rep{rep}: {'PASS' if res['passed'] else 'FAIL'} "
                      f"({sum(1 for c in res['criteria'] if c['pass'])}/{len(res['criteria'])}) {time.time() - t:.0f}s", flush=True)
    STACKS["endterm"].restore_state()
    if not a.no_samples:
        collect_samples()


if __name__ == "__main__":
    main()
