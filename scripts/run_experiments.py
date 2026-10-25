"""Runs the failure-injection experiments of Assignment 3, Part D (+ rolling update, + DR drill).

Prerequisites: kind cluster `ft-aitu` with the manifests in k8s/ applied (see README.md).
Usage:  python scripts/run_experiments.py [baseline|rolling|exp1|exp1b|exp2|exp3|dr|all]

Timing method
  * t0 = wall-clock just before the injecting kubectl/docker command is started.
  * "k8s detection"  = t0 -> first poll in which the number of Ready, non-terminating pods dropped.
  * "k8s recovery"   = t0 -> first poll in which the desired number of Ready pods is back.
  * "client detection" = t0 -> first failed client request (a request is failed if HTTP status
    is not 2xx or the call errored/timed out). None means the user never noticed.
  * "client recovery" = t0 -> completion of the last failed request.
  The pod poller needs one `kubectl get pods -o json` per cycle, so k8s timings have ~0.3 s resolution.
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
import loadgen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results")
CTX = ["--context", "kind-ft-aitu", "-n", "aitu-sim"]
RPS = int(os.environ.get("RPS", "100"))
NEW_TAG = os.environ.get("NEW_TAG", "1.2")


def kubectl(*args, check=True):
    r = subprocess.run(["kubectl", *CTX, *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout


def ready_pods(selector):
    items = json.loads(kubectl("get", "pods", "-l", selector, "-o", "json"))["items"]
    n = 0
    for p in items:
        if p["metadata"].get("deletionTimestamp"):
            continue
        conds = {c["type"]: c["status"] for c in p["status"].get("conditions", [])}
        if conds.get("Ready") == "True":
            n += 1
    return n


class PodPoller(threading.Thread):
    def __init__(self, selector):
        super().__init__(daemon=True)
        self.selector, self.samples, self.stop = selector, [], threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                self.samples.append((time.time(), ready_pods(self.selector)))
            except Exception:
                pass


def k8s_times(samples, t0, desired):
    det = next((t - t0 for t, n in samples if t >= t0 and n < desired), None)
    rec = None
    if det is not None:
        rec = next((t - t0 for t, n in samples if t >= t0 + det and n >= desired), None)
    return det, rec


def client_times(records, t0, kinds=None):
    bad = [r for r in records if r[0] >= t0 and not 200 <= r[2] < 300 and (kinds is None or r[3] in kinds)]
    if not bad:
        return None, None, 0
    first = min(r[0] for r in bad) - t0
    last = max(r[0] + r[1] / 1000 for r in bad) - t0
    return round(first, 2), round(last, 2), len(bad)


def wait_ready(selector, n, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if ready_pods(selector) >= n:
            return
        time.sleep(0.5)
    raise TimeoutError(selector)


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"== {name}\n{json.dumps(data, indent=2)}\n", flush=True)


def dump_requests(name, gen, t0):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write("t_rel_s,latency_ms,status,kind\n")
        for t, lat, st, kind in sorted(gen.records):
            f.write(f"{t - t0:.3f},{lat:.1f},{st},{kind}\n")


def db_audit(acked):
    """Every acknowledged write must exist on both database nodes (RPO = 0 evidence)."""
    res = {}
    for node in ("db-0", "db-1"):
        raw = kubectl("exec", node, "--", "python", "-c",
                      "import urllib.request,json;"
                      "print(json.dumps([r['id'] for r in json.load(urllib.request.urlopen("
                      "'http://localhost:8080/db/dump'))['records']]))")
        ids = set(json.loads(raw))
        res[node] = {"records": len(ids), "acked_missing": len(set(acked) - ids)}
    return res


def experiment(name, inject, selector, desired, kinds, pre=10, post=20, recover=None):
    """Generic harness: warm-up -> inject -> observe until recovered -> cool-down."""
    gen = loadgen.LoadGen(RPS)
    poller = PodPoller(selector)
    gen.start(); poller.start()
    time.sleep(pre)
    t0 = time.time()
    extra = inject() or {}
    time.sleep(1)
    wait_ready(selector, desired, 180)
    t_ready = time.time()
    time.sleep(post)
    poller.stop.set(); gen.finish()
    det, rec = k8s_times(poller.samples, t0, desired)
    first, last, nfail = client_times(gen.records, t0)
    rfirst, rlast, rfail = client_times(gen.records, t0, kinds)
    res = {"experiment": name, "load_rps": RPS, "injected_at_epoch": round(t0, 2),
           "k8s_detection_s": round(det, 2) if det is not None else None,
           "k8s_recovery_s": round(rec, 2) if rec is not None else None,
           "client_first_failure_s": first, "client_last_failure_done_s": last,
           "failed_requests_total": nfail,
           "failed_requests_in_affected_kinds": rfail,
           "during_window": loadgen.summarise(gen.records, t0, t0 + (last or 0) + 0.001) if last else None,
           "whole_run": loadgen.summarise(gen.records), **extra}
    res["audit"] = db_audit(gen.acked)
    res["acked_writes"] = len(gen.acked)
    save(f"{name}.json", res)
    dump_requests(f"{name}_requests.csv", gen, t0)
    return res


def baseline():
    g = loadgen.LoadGen(RPS); g.start(); time.sleep(60); g.finish()
    res = {"experiment": "baseline", "load_rps": RPS, "seconds": 60, **loadgen.summarise(g.records)}
    res["audit"] = db_audit(g.acked); res["acked_writes"] = len(g.acked)
    save("baseline.json", res)


def peak():
    """Best-effort 500 req/s run; the single Python client may be the limiting factor."""
    g = loadgen.LoadGen(500, workers=400); g.start(); time.sleep(20); g.finish()
    span = max(r[0] for r in g.records) - min(r[0] for r in g.records)
    res = {"experiment": "peak_500rps", "target_rps": 500, "achieved_rps": round(len(g.records) / span, 1),
           **loadgen.summarise(g.records)}
    save("peak_500rps.json", res)


def rolling():
    def inject():
        kubectl("set", "image", "deploy/registration", f"registration=aitu-sim:{NEW_TAG}")
        kubectl("rollout", "status", "deploy/registration", "--timeout=120s")
    for d in ("auth", "course", "gateway"):
        kubectl("set", "image", f"deploy/{d}", f"{d}=aitu-sim:{NEW_TAG}")
    kubectl("rollout", "status", "deploy/gateway", "--timeout=120s")
    experiment("rolling_update", inject, "app=registration", 2, None, pre=10, post=10)


def exp1():
    victim = {}

    def inject():
        pods = kubectl("get", "pods", "-l", "app=registration", "-o", "name").split()
        victim["pod"] = pods[0]
        kubectl("delete", victim["pod"], "--grace-period=0", "--force", check=False)
        return {"injected": "kubectl delete --force --grace-period=0 " + victim["pod"]}
    experiment("exp1_one_registration_replica", inject, "app=registration", 2, {"reg_read", "reg_write"})


def exp1b():
    """Container crash: kubelet restarts the container in the same Pod (automatic restart)."""
    info = {}

    def inject():
        pod = kubectl("get", "pods", "-l", "app=registration", "-o", "name").split()[0].split("/")[1]
        node = json.loads(kubectl("get", "pod", pod, "-o", "json"))["spec"]["nodeName"]
        cid = subprocess.run(["docker", "exec", node, "crictl", "ps", "--name", "registration",
                              "--label", f"io.kubernetes.pod.name={pod}", "-q"],
                             capture_output=True, text=True).stdout.split()[0]
        subprocess.run(["docker", "exec", node, "crictl", "stop", "--timeout", "0", cid], check=True,
                       capture_output=True)
        info["pod"] = pod
        return {"injected": f"crictl stop {cid[:12]} (container of {pod} on {node})"}

    res = experiment("exp1b_container_crash_restart", inject, "app=registration", 2, {"reg_read", "reg_write"})
    res["restart_count"] = kubectl("get", "pod", info["pod"], "-o",
                                   "jsonpath={.status.containerStatuses[0].restartCount}")
    save("exp1b_container_crash_restart.json", res)


def exp2():
    def inject():
        pods = kubectl("get", "pods", "-l", "app=registration", "-o", "name").split()
        kubectl("delete", *pods, "--grace-period=0", "--force", check=False)
        return {"injected": "kubectl delete --force --grace-period=0 " + " ".join(pods)}
    experiment("exp2_all_registration_replicas", inject, "app=registration", 2, {"reg_read", "reg_write"})


def exp3():
    """Database unavailable for 30 s (both nodes), then restored."""
    marks = {}

    def inject():
        kubectl("scale", "sts/db", "--replicas=0")
        marks["db_down_at"] = time.time()
        time.sleep(30)
        marks["restore_at"] = time.time()
        kubectl("scale", "sts/db", "--replicas=2")
        wait_ready("app=db", 2, 120)
        marks["db_ready_at"] = time.time()
        return {"injected": "kubectl scale sts/db --replicas=0 for 30 s, then --replicas=2"}

    gen = loadgen.LoadGen(RPS)
    poller = PodPoller("app=db")
    gen.start(); poller.start(); time.sleep(10)
    t0 = time.time()
    extra = inject()
    t_ok = None
    end = time.time() + 60
    while time.time() < end and t_ok is None:
        t_ok = next((r[0] for r in sorted(gen.records) if r[0] >= marks["restore_at"]
                     and r[3] == "reg_write" and r[2] == 201), None)
        time.sleep(0.2)
    time.sleep(15)
    poller.stop.set(); gen.finish()
    det, _ = k8s_times(poller.samples, t0, 2)
    per_kind = loadgen.summarise(gen.records, t0, marks["restore_at"])["by_kind"]
    first_w = min((r[0] for r in gen.records if r[0] >= t0 and r[3] == "reg_write" and r[2] != 201), default=None)
    lat_db_down = [r[1] for r in gen.records if t0 + 2 <= r[0] <= marks["restore_at"]]
    res = {"experiment": "exp3_database_unavailable", "load_rps": RPS, **extra,
           "db_outage_s": round(marks["restore_at"] - t0, 1),
           "k8s_detection_s": round(det, 2) if det is not None else None,
           "client_first_failure_s": round(first_w - t0, 2) if first_w else None,
           "first_successful_write_after_restore_s": round(t_ok - marks["restore_at"], 2) if t_ok else None,
           "db_pods_ready_after_restore_s": round(marks["db_ready_at"] - marks["restore_at"], 2),
           "client_recovery_s_from_scale_up": round(t_ok - marks["restore_at"], 2) if t_ok else None,
           "during_outage_by_kind": per_kind,
           "max_latency_during_outage_ms": round(max(lat_db_down), 1) if lat_db_down else None,
           "whole_run": loadgen.summarise(gen.records), "acked_writes": len(gen.acked)}
    res["audit"] = db_audit(gen.acked)
    save("exp3_database_unavailable.json", res)
    dump_requests("exp3_database_unavailable_requests.csv", gen, t0)


def exp3b():
    """One of the two database nodes fails: the replica must keep serving with RPO = 0."""
    def inject():
        kubectl("delete", "pod", "db-0", "--grace-period=0", "--force", check=False)
        return {"injected": "kubectl delete --force --grace-period=0 pod/db-0"}
    experiment("exp3b_one_database_node", inject, "app=db", 2, {"reg_read", "reg_write"}, post=25)


JOB_LG = """
apiVersion: batch/v1
kind: Job
metadata: {name: loadgen, namespace: aitu-sim}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: loadgen
          image: aitu-sim:1.2
          imagePullPolicy: IfNotPresent
          command: ["python", "/lg/loadgen.py", "--rps", "%d", "--seconds", "%d", "--dump"]
          env: [{name: LG_HOST, value: gateway}, {name: LG_PORT, value: "8080"}]
          resources: {requests: {cpu: 200m}, limits: {cpu: "1"}}
          volumeMounts: [{name: lg, mountPath: /lg}]
      volumes: [{name: lg, configMap: {name: loadgen-script}}]
"""


def clock_offset():
    """node clock - host clock (seconds). The in-cluster generator stamps requests with the node clock."""
    offs = []
    for _ in range(5):
        a = time.time()
        n = float(subprocess.run(["docker", "exec", "ft-aitu-worker", "date", "+%s.%N"], capture_output=True,
                                 text=True).stdout)
        b = time.time()
        offs.append(n - (a + b) / 2)
    return sorted(offs)[len(offs) // 2]


def incluster_experiment(name, inject, selector, desired, seconds=50, warm=15):
    """Same as experiment(), but the load comes from a Job inside the cluster (no Docker Desktop host path)."""
    kubectl("delete", "job", "loadgen", "--ignore-not-found")
    kubectl("delete", "configmap", "loadgen-script", "--ignore-not-found")
    kubectl("create", "configmap", "loadgen-script", "--from-file=" + os.path.join(ROOT, "scripts", "loadgen.py"))
    subprocess.run(["kubectl", *CTX, "apply", "-f", "-"], input=JOB_LG % (RPS, seconds), text=True, check=True,
                   capture_output=True)
    kubectl("wait", "--for=condition=Ready", "pod", "-l", "job-name=loadgen", "--timeout=60s")
    poller = PodPoller(selector); poller.start()
    time.sleep(warm)
    off = clock_offset()
    t0 = time.time()
    extra = inject() or {}
    kubectl("wait", "--for=condition=complete", "job/loadgen", f"--timeout={seconds + 60}s")
    poller.stop.set()
    recs = []
    for line in kubectl("logs", "job/loadgen").splitlines():
        if line.startswith("REC,"):
            _, t, lat, st, kind = line.split(",")
            recs.append((float(t) - off, float(lat), int(st), kind))
    kubectl("delete", "job", "loadgen", "--ignore-not-found")
    det, rec = k8s_times(poller.samples, t0, desired)
    first, last, nfail = client_times(recs, t0)
    res = {"experiment": name, "load_source": "in-cluster Job -> Service gateway:8080", "load_rps": RPS,
           "clock_offset_s": round(off, 3), "injected_at_epoch": round(t0, 2),
           "k8s_detection_s": round(det, 2) if det is not None else None,
           "k8s_recovery_s": round(rec, 2) if rec is not None else None,
           "client_first_failure_s": first, "client_last_failure_done_s": last,
           "failed_requests_total": nfail, "whole_run": loadgen.summarise(recs), **extra}
    save(f"{name}.json", res)
    with open(os.path.join(OUT, f"{name}_requests.csv"), "w", encoding="utf-8") as f:
        f.write("t_rel_s,latency_ms,status,kind\n")
        for t, lat, st, kind in sorted(recs):
            f.write(f"{t - t0:.3f},{lat:.1f},{st},{kind}\n")
    return res


def gateway_before_after():
    """Before/after of the key design decision: a single API gateway vs two replicas."""
    def kill_one():
        pod = kubectl("get", "pods", "-l", "app=gateway", "-o", "name").split()[0]
        kubectl("delete", pod, "--grace-period=0", "--force", check=False)
        return {"injected": "kubectl delete --force --grace-period=0 " + pod}
    kubectl("scale", "deploy/gateway", "--replicas=1")
    kubectl("rollout", "status", "deploy/gateway", "--timeout=60s")
    time.sleep(8)
    incluster_experiment("gw_before_single_replica", kill_one, "app=gateway", 1)
    kubectl("scale", "deploy/gateway", "--replicas=2")
    kubectl("rollout", "status", "deploy/gateway", "--timeout=60s")
    time.sleep(8)
    incluster_experiment("gw_after_two_replicas", kill_one, "app=gateway", 2)


def dr():
    """Disaster-recovery drill: lose the whole namespace (pods + PVC data), rebuild, restore from backup."""
    import glob
    kubectl("create", "job", "dr-pre-backup", "--from=cronjob/db-backup")
    kubectl("wait", "--for=condition=complete", "job/dr-pre-backup", "--timeout=90s")
    before = json.loads(kubectl("exec", "db-0", "--", "python", "-c",
                                "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/db/count').read().decode())"))
    newest = sorted(glob.glob(os.path.join(OUT, "backups", "db-*.json")))[-1]
    t0 = time.time()
    subprocess.run(["kubectl", "--context", "kind-ft-aitu", "delete", "namespace", "aitu-sim", "--wait=true"],
                   check=True, capture_output=True)
    t_lost = time.time()
    steps = {}
    for f in ("00-base", "10-db", "20-auth", "30-course", "40-registration", "50-gateway", "60-backup"):
        subprocess.run(["kubectl", "--context", "kind-ft-aitu", "apply", "-f", os.path.join(ROOT, "k8s", f + ".yaml")],
                       check=True, capture_output=True)
    wait_ready("app=db", 2); steps["db_ready_s"] = round(time.time() - t_lost, 1)
    subprocess.run(["kubectl", *CTX, "apply", "-f", os.path.join(ROOT, "dr", "restore-job.yaml")],
                   check=True, capture_output=True)
    kubectl("wait", "--for=condition=complete", "job/db-restore", "--timeout=180s")
    steps["restore_done_s"] = round(time.time() - t_lost, 1)
    for sel in ("app=auth", "app=course", "app=registration", "app=gateway"):
        wait_ready(sel, 2)
    t_ready = time.time()
    _, _, st, _ = loadgen.one_request("GET", "/timetable?group=SE-2301")
    tok = None
    _, _, lst, raw = loadgen.one_request("POST", "/auth/login", {"student_id": "S0001"})
    tok = json.loads(raw)["token"]
    _, _, rst, rraw = loadgen.one_request("GET", "/registrations/S0001", token=tok)
    after = json.loads(kubectl("exec", "db-1", "--", "python", "-c",
                               "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/db/count').read().decode())"))
    res = {"experiment": "dr_full_recovery", "backup_used": os.path.basename(newest),
           "records_before_disaster": before["count"], "records_after_restore_db1": after["count"],
           "namespace_delete_s": round(t_lost - t0, 1), **steps,
           "full_recovery_RTO_s": round(t_ready - t0, 1),
           "functional_check": {"timetable": st, "login": lst, "registrations_read": rst}}
    save("dr_full_recovery.json", res)


SCENARIOS = {"baseline": baseline, "peak": peak, "rolling": rolling, "exp1": exp1, "exp1b": exp1b,
             "exp2": exp2, "exp3": exp3, "exp3b": exp3b, "gw": gateway_before_after, "dr": dr}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name in (SCENARIOS if which == "all" else [which]):
        print(f"##### {name}", flush=True)
        SCENARIOS[name]()
        time.sleep(5)
