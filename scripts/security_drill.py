"""Assignment 4 - ransomware-style incident drill on the kind platform with a timed 7-step recovery.

Phases
  P0  wait for a vault backup that is not older than a minute, start background user load (30 req/s)
  P1  credential attack from outside (15 + 21 failed logins, then a successful login) - live detector measures MTTD
  P2  attacker on a compromised Registration pod tries lateral movement / outbound connection (segmentation test)
  P3  impact: records files on both database nodes are encrypted and the nodes restarted; backup deletion attempt
  P4  recovery steps 1-7, each timed:  detect, isolate, preserve evidence, activate backup, restore, verify, return to production
Time is compressed against the dataset (about 2 minutes of the dataset become about 15 s here); the compression is stated in the report.
Output: results/security/drill.json, results/security/drill_requests.csv, results/security/evidence/<stamp>/
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))
os.environ["LG_OFFSET"] = "9000001"
import detector
import loadgen
import run_experiments as rx
import security_checks as sc

OUT = os.path.join(rx.ROOT, "results", "security")
NODE = "ft-aitu-worker"
POST = "S9"


def node(cmd):
    r = subprocess.run(["docker", "exec", NODE, "sh", "-c", cmd], capture_output=True, text=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return r.returncode, (r.stdout + r.stderr).strip()


def newest_manifest(not_after=None):
    names = node("ls /vault | grep manifest.json | grep -v '^\\.'")[1].split()
    best = None
    for n in names:
        m = json.loads(node(f"cat /vault/{n}")[1])
        if not_after is not None and m["created_epoch"] > not_after:
            continue
        if best is None or m["created_epoch"] > best["created_epoch"]:
            best = m | {"manifest_file": n}
    return best


def db_dump(pod):
    raw = rx.kubectl("exec", pod, "--", "python", "-c",
                     "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/db/dump').read().decode())")
    return json.loads(raw)["records"]


def ids_hash(records):
    return hashlib.sha256("\n".join(sorted(r["id"] for r in records)).encode()).hexdigest()


def functional_check(registration=True, attempts=6):
    last = {}
    for n in range(1, attempts + 1):
        last = functional_check_once(registration)
        if all(v in (200, 201) for v in last.values()):
            return {**last, "attempts": n}
        time.sleep(2)
    return {**last, "attempts": attempts}


def functional_check_once(registration=True):
    st = {}
    st["timetable"] = loadgen.one_request("GET", "/timetable?group=SE-2301")[2]
    _, _, st["login"], raw = loadgen.one_request("POST", "/auth/login", {"student_id": "S0001"})
    tok = json.loads(raw)["token"] if st["login"] == 200 else None
    if registration:
        st["register"] = loadgen.one_request("POST", "/registrations", {"student_id": "S0001", "course_code": "CS205"}, tok)[2]
        st["read"] = loadgen.one_request("GET", "/registrations/S0001", token=tok)[2]
    return st


class ImpactMonitor(threading.Thread):
    """Black-box synthetic probe: 5 consecutive failures of a registration read = service-impact alert."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop, self.alert_at, self.ok_again_at = threading.Event(), None, None
        self.bad = 0

    def run(self):
        while not self.stop.is_set():
            st = loadgen.one_request("GET", "/registrations/S0001", token=TOKEN[0], timeout=1.5)[2]
            if st != 200:
                self.bad += 1
                if self.bad >= 5 and self.alert_at is None:
                    self.alert_at = time.time()
            else:
                self.bad = 0
                if self.alert_at is not None and self.ok_again_at is None:
                    self.ok_again_at = time.time()
            time.sleep(0.5)


TOKEN = [None]


def main():
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    evid = os.path.join(OUT, "evidence", stamp)
    os.makedirs(evid, exist_ok=True)
    res = {"started": stamp, "time_compression": "dataset minutes are shortened to seconds; intervals between phases are NOT real-time"}

    print("P0: waiting for a fresh vault backup ...", flush=True)
    while True:
        m = newest_manifest()
        if m and time.time() - m["created_epoch"] < 45:
            break
        time.sleep(3)
    TOKEN[0] = json.loads(loadgen.one_request("POST", "/auth/login", {"student_id": "S0001"})[3])["token"]
    gen = loadgen.LoadGen(30)
    gen.start()
    monitor = ImpactMonitor(); monitor.start()
    time.sleep(40)
    res["baseline_records"] = len(db_dump("db-0"))

    print("P1: credential attack with live detector", flush=True)
    detected = []
    det_thread = threading.Thread(target=lambda: detected.extend(detector.live(seconds=75, poll=0.7, stop_after_alert=True)), daemon=True)
    det_thread.start()
    t_first_fail = time.time()
    for _ in range(15):
        loadgen.one_request("POST", "/auth/login", {"student_id": "attacker-guess"})
        time.sleep(0.15)
    time.sleep(12)
    for _ in range(21):
        loadgen.one_request("POST", "/auth/login", {"student_id": "attacker-guess"})
        time.sleep(0.15)
    t_success = time.time()
    st = loadgen.one_request("POST", "/auth/login", {"student_id": "S0001"})[2]
    det_thread.join(timeout=60)
    alerts = [{**a, "after_first_failed_login_s": round(a["detected_at"] - t_first_fail, 1)} for a in detected]
    res["P1_credential_attack"] = {"failed_logins_sent": 36, "successful_login_status": st, "alerts": alerts,
                                   "seconds_from_first_failure_to_success": round(t_success - t_first_fail, 1)}
    print(json.dumps(res["P1_credential_attack"], indent=1), flush=True)

    print("P2: lateral movement attempts from the compromised Registration pod", flush=True)
    compromised = sc.pod("registration")
    lat = {}
    for label, host, port in (("auth", sc.ip_of("app=auth"), "8080"), ("course", sc.ip_of("app=course"), "8080"),
                              ("internet 1.1.1.1:443", "1.1.1.1", "443"), ("db (allowed by design)", sc.ip_of("app=db"), "8080")):
        lat[label] = sc.attempt(compromised, f"{host}:{port}")
    leaked = rx.kubectl("exec", compromised, "--", "python", "-c",
                        "import urllib.request,json;print(len(json.load(urllib.request.urlopen('http://db-0.db-headless:8080/db/dump'))['records']))").strip()
    res["P2_lateral_movement"] = {"compromised_pod": compromised, "attempts": lat, "records_readable_via_allowed_db_path": int(leaked)}
    print(json.dumps(res["P2_lateral_movement"], indent=1), flush=True)

    print("P3: IMPACT - ransomware on the database nodes", flush=True)
    before_epoch = time.time()
    last_good = newest_manifest(not_after=before_epoch)
    encrypt = ("import os\nd='/data/records.jsonl'\nk=0x5a\nb=open(d,'rb').read()\n"
               "open(d+'.locked','wb').write(bytes(x^k for x in b))\n"
               "open('/data/README_RANSOM.txt','w').write('SYNTHETIC EXERCISE: files encrypted. No real ransom.')\n"
               "os.remove(d)\nprint(len(b))")
    t_impact = time.time()
    monitor.bad, monitor.alert_at, monitor.ok_again_at = 0, None, None
    enc_bytes = [int(rx.kubectl("exec", p, "--", "python", "-c", encrypt).strip()) for p in ("db-0", "db-1")]
    rx.kubectl("delete", "pod", "db-0", "db-1", "--grace-period=0", "--force", check=False)
    t_impact_done = time.time()
    tamper = [node(c) for c in (f"rm -f /vault/{last_good['file']}", f"rm -f /vault/{last_good['manifest_file']}")]
    res["P3_impact"] = {"encrypted_bytes_per_node": enc_bytes, "encryption_and_restart_s": round(t_impact_done - t_impact, 1),
                        "backup_deletion_attempt_blocked": all(c != 0 for c, _ in tamper),
                        "backup_deletion_output": [o[:90] for _, o in tamper],
                        "backup_used_age_at_impact_s": round(t_impact - last_good["created_epoch"], 1)}
    print(json.dumps(res["P3_impact"], indent=1), flush=True)
    steps = {}

    def step(n, name, fn):
        t = time.time()
        out = fn()
        steps[n] = {"action": name, "started_after_impact_s": round(t - t_impact, 1), "duration_s": round(time.time() - t, 1), **(out or {})}
        print(f"step {n} {name}: {steps[n]['duration_s']}s", flush=True)

    while monitor.alert_at is None and time.time() - t_impact < 60:
        time.sleep(0.3)
    steps["1"] = {"action": "Detect incident", "started_after_impact_s": 0,
                  "duration_s": round((monitor.alert_at or time.time()) - t_impact, 1),
                  "how": "synthetic probe: 5 consecutive failed registration reads; credential attack was detected earlier by R1a/R1b"}

    def isolate():
        rx.kubectl("label", "pod", compromised, "app-", "quarantine=true", "--overwrite")
        rx.kubectl("patch", "cronjob", "vault-backup", "-n", "vault", "-p", '{"spec":{"suspend":true}}')
        rx.kubectl("scale", "deploy/registration", "--replicas=0")
        time.sleep(3)
        blocked = sc.attempt(compromised, f"{sc.ip_of('app=db')}:8080")
        return {"quarantined_pod": compromised, "db_reachable_from_quarantined_pod": blocked, "backup_cronjob_suspended": True, "registration_scaled_to_zero_maintenance_mode": True}

    def evidence():
        files = {}
        for name, args in (("auth.log", ["logs", "-l", "app=auth", "--tail=2000", "--prefix"]),
                           ("registration.log", ["logs", compromised, "--tail=2000"]),
                           ("gateway.log", ["logs", "-l", "app=gateway", "--tail=2000", "--prefix"]),
                           ("pods.yaml", ["get", "pods", "-o", "yaml"]), ("events.txt", ["get", "events", "--sort-by=.lastTimestamp"]),
                           ("networkpolicies.yaml", ["get", "networkpolicy", "-o", "yaml"])):
            files[name] = rx.kubectl(*args, check=False)
        for p in ("db-0", "db-1"):
            files[f"{p}_records.jsonl.locked"] = rx.kubectl("exec", p, "--", "python", "-c",
                                                              "import sys;sys.stdout.write(open('/data/records.jsonl.locked','rb').read().hex())", check=False)
            files[f"{p}_RANSOM_NOTE.txt"] = rx.kubectl("exec", p, "--", "cat", "/data/README_RANSOM.txt", check=False)
        sums = []
        for name, data in files.items():
            with open(os.path.join(evid, name), "w", encoding="utf-8") as f:
                f.write(data)
            sums.append(f"{hashlib.sha256(data.encode()).hexdigest()}  {name}")
        with open(os.path.join(evid, "SHA256SUMS.txt"), "w") as f:
            f.write("\n".join(sums) + "\n")
        return {"evidence_dir": os.path.relpath(evid, rx.ROOT).replace("\\", "/"), "files": len(files)}

    def activate():
        m = newest_manifest(not_after=t_impact)
        raw_hash = node(f"sha256sum /vault/{m['file']}")[1].split()[0]
        attr = node(f"lsattr /vault/{m['file']}")[1]
        return {"backup": m["file"], "manifest_sha256_matches_file": raw_hash == m["sha256"], "immutable_flag_set": "i" in attr.split()[0],
                "backup_records": m["records"], "backup_age_at_impact_s": round(t_impact - m["created_epoch"], 1), "_m": m}

    def restore():
        rx.kubectl("scale", "sts/db", "--replicas=0")
        end = time.time() + 90
        while time.time() < end and rx.kubectl("get", "pods", "-l", "app=db", "-o", "name").strip():
            time.sleep(1)
        rx.kubectl("delete", "pvc", "data-db-0", "data-db-1", "--wait=true")
        rx.kubectl("scale", "sts/db", "--replicas=2")
        rx.wait_ready("app=db", 2, 180)
        job = open(os.path.join(rx.ROOT, "dr", "vault-restore-job.yaml"), encoding="utf-8").read()
        job = job.replace("          command: [\"python\", \"/scripts/restore.py\"]",
                          f"          command: [\"python\", \"/scripts/restore.py\"]\n          env: [{{name: BACKUP_FILE, value: {steps['4']['_m']['manifest_file']}}}]")
        subprocess.run(["kubectl", "--context", "kind-ft-aitu", "delete", "job", "vault-restore", "-n", "vault", "--ignore-not-found"], capture_output=True)
        subprocess.run(["kubectl", "--context", "kind-ft-aitu", "apply", "-f", "-"], input=job, text=True, check=True, capture_output=True)
        subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "vault", "wait", "--for=condition=complete", "job/vault-restore",
                        "--timeout=900s"], check=True, capture_output=True)
        log = subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "vault", "logs", "job/vault-restore"], capture_output=True, text=True).stdout
        return {"restore_job": json.loads(log.strip().splitlines()[-1])}

    def verify():
        m = steps["4"]["_m"]
        gen.paused.set()
        time.sleep(3)
        deadline, ok = time.time() + 60, False
        while time.time() < deadline:
            d0, d1 = db_dump("db-0"), db_dump("db-1")
            if ids_hash(d0) == ids_hash(d1) and len(d0) >= m["records"]:
                ok = True
                break
            time.sleep(2)
        base_ids = {r["id"] for r in json.loads(node(f"cat /vault/{m['file']}")[1])}
        present = {r["id"] for r in d0}
        out = {"records_restored_db0": len(d0), "records_restored_db1": len(d1), "nodes_identical": ok,
               "all_backup_records_present": base_ids <= present, "backup_records": m["records"],
               "backup_ids_sha256": m["ids_sha256"], "ids_sha256_db0": ids_hash(d0), "ids_sha256_db1": ids_hash(d1),
               "traffic_held_during_verification": True}
        gen.paused.clear()
        out["functional_check"] = functional_check(registration=False)
        return out

    def production():
        new_secret = secrets.token_hex(16)
        yml = rx.kubectl("create", "secret", "generic", "auth-secret", f"--from-literal=AUTH_SECRET=rotated-{new_secret}", "--dry-run=client", "-o", "yaml")
        subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "aitu-sim", "apply", "-f", "-"], input=yml, text=True, check=True, capture_output=True)
        rx.kubectl("rollout", "restart", "deploy/auth", "deploy/gateway")
        rx.kubectl("rollout", "status", "deploy/auth", "--timeout=120s")
        rx.kubectl("rollout", "status", "deploy/gateway", "--timeout=120s")
        rx.kubectl("delete", "pod", compromised, "--grace-period=0", "--force", check=False)
        rx.kubectl("scale", "deploy/registration", "--replicas=2")
        rx.kubectl("patch", "cronjob", "vault-backup", "-n", "vault", "-p", '{"spec":{"suspend":false}}')
        for sel in ("app=registration", "app=gateway", "app=auth", "app=course", "app=db"):
            rx.wait_ready(sel, 2, 120)
        TOKEN[0] = json.loads(loadgen.one_request("POST", "/auth/login", {"student_id": "S0001"})[3])["token"]
        return {"auth_secret_rotated": True, "compromised_pod_deleted": True, "backup_cronjob_resumed": True,
                "functional_check": functional_check()}

    step("2", "Isolate affected system", isolate)
    step("3", "Preserve evidence", evidence)
    step("4", "Activate backup", activate)
    step("5", "Restore services", restore)
    t_service_back = time.time()
    step("6", "Verify integrity", verify)
    step("7", "Return to production", production)
    t_done = time.time()
    time.sleep(10)
    monitor.stop.set()
    gen.finish()
    steps["4"].pop("_m", None)

    m = newest_manifest(not_after=t_impact)
    after = {r["id"] for r in db_dump("db-0")}
    acked_after_backup = [i for i, t in gen.acked_t.items() if t > m["created_epoch"] and t < t_impact]
    acked_before_backup = [i for i, t in gen.acked_t.items() if t <= m["created_epoch"] - 1.0]
    lost_post_backup = [i for i in acked_after_backup if i not in after]
    lost_pre_backup = [i for i in acked_before_backup if i not in after]
    t_restore_done = t_service_back
    acked_while_data_missing = [i for i, t in gen.acked_t.items() if t_impact <= t < t_restore_done]
    lost_while_data_missing = [i for i in acked_while_data_missing if i not in after]
    acked_after_restore = [i for i, t in gen.acked_t.items() if t >= t_restore_done]
    lost_after_restore = [i for i in acked_after_restore if i not in after]
    first_ok_write = min((r[0] for r in gen.records if r[3] == "reg_write" and r[2] == 201 and r[0] > t_impact + 1), default=None)
    outage = [r for r in gen.records if r[0] >= t_impact and r[3] in ("reg_read", "reg_write") and not 200 <= r[2] < 300]
    other_fail = [r for r in gen.records if r[0] >= t_impact and r[3] in ("timetable", "courses", "login") and not 200 <= r[2] < 300]
    res.update({
        "steps": steps,
        "rpo": {"backup_created_before_impact_s": round(t_impact - m["created_epoch"], 1), "target_s": 300,
                "writes_acked_after_backup_before_impact": len(acked_after_backup), "lost_of_those": len(lost_post_backup),
                "acked_before_backup_lost": len(lost_pre_backup),
                "acked_between_impact_and_restore_done": len(acked_while_data_missing),
                "lost_of_those_accepted_while_data_missing": len(lost_while_data_missing),
                "acked_after_restore": len(acked_after_restore), "lost_after_restore": len(lost_after_restore)},
        "rto": {"impact_to_registration_service_restored_s": round(t_service_back - t_impact, 1),
                "impact_to_fully_back_in_production_s": round(t_done - t_impact, 1), "target_s": 1800,
                "first_successful_registration_write_after_impact_s": round(first_ok_write - t_impact, 1) if first_ok_write else None},
        "user_impact": {"registration_requests_failed": len(outage), "timetable_courses_login_failed": len(other_fail),
                        "total_requests": len(gen.records),
                        "monitor_alert_after_impact_s": round(monitor.alert_at - t_impact, 1) if monitor.alert_at else None},
        "final_audit_records": len(after)})
    with open(os.path.join(OUT, "drill.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    with open(os.path.join(OUT, "drill_requests.csv"), "w", encoding="utf-8") as f:
        f.write("t_rel_s,latency_ms,status,kind\n")
        for t, lat, st, kind in sorted(gen.records):
            f.write(f"{t - t_impact:.3f},{lat:.1f},{st},{kind}\n")
    print(json.dumps({k: v for k, v in res.items() if k in ("rpo", "rto", "user_impact")}, indent=1))


if __name__ == "__main__":
    main()
