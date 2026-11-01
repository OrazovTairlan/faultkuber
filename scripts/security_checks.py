"""Assignment 4 - live verification of the preventive controls on the kind cluster.

1. Network segmentation matrix: every (source -> destination) pair is really attempted from inside a pod.
2. Immutable vault: delete / rename / overwrite / un-seal attempts as root on the node and from a pod.
Output: results/security/segmentation.json and results/security/immutability.json
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import run_experiments as rx

OUT = os.path.join(rx.ROOT, "results", "security")
NODE = "ft-aitu-worker"


def ip_of(selector, ns="aitu-sim"):
    return rx.kubectl("get", "pods", "-n", ns, "-l", selector, "-o", "jsonpath={.items[0].status.podIP}", check=True).strip()


def attempt(src, target, ns="aitu-sim", path="/healthz"):
    """Try an HTTP GET from `src` pod to host:port. Returns 'ALLOWED' or 'BLOCKED'."""
    host, port = target.rsplit(":", 1)
    code = ("import socket,sys\n"
            "s=socket.socket(); s.settimeout(2.5)\n"
            "try:\n s.connect((sys.argv[1],int(sys.argv[2]))); print('ALLOWED')\n"
            "except Exception as e:\n print('BLOCKED')\n")
    r = subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", ns, "exec", src, "--", "python", "-c", code, host, port],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip() or ("ERROR " + r.stderr.strip()[:80])


def pod(app, ns="aitu-sim"):
    return rx.kubectl("get", "pods", "-n", ns, "-l", f"app={app}", "-o", "jsonpath={.items[0].metadata.name}").strip()


def segmentation():
    rx.kubectl("delete", "pod", "probe", "-n", "default", "--ignore-not-found", "--now", check=False)
    subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "default", "run", "probe", "--image=aitu-sim:1.2",
                    "--image-pull-policy=IfNotPresent", "--restart=Never", "--command", "--", "sleep", "900"], capture_output=True)
    subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "default", "wait", "--for=condition=Ready", "pod/probe", "--timeout=60s"],
                   capture_output=True)
    gw, auth, course, reg = pod("gateway"), pod("auth"), pod("course"), pod("registration")
    db0 = "db-0"
    ips = {"gateway": ip_of("app=gateway"), "auth": ip_of("app=auth"), "course": ip_of("app=course"),
           "registration": ip_of("app=registration"), "db": ip_of("app=db")}
    cases = [
        ("external pod (default ns)", "probe", "default", "gateway:8080", ips["gateway"], True),
        ("external pod (default ns)", "probe", "default", "db:8080", ips["db"], False),
        ("external pod (default ns)", "probe", "default", "registration:8080", ips["registration"], False),
        ("gateway", gw, "aitu-sim", "auth:8080", ips["auth"], True),
        ("gateway", gw, "aitu-sim", "course:8080", ips["course"], True),
        ("gateway", gw, "aitu-sim", "registration:8080", ips["registration"], True),
        ("gateway", gw, "aitu-sim", "db:8080", ips["db"], False),
        ("registration", reg, "aitu-sim", "db:8080", ips["db"], True),
        ("registration", reg, "aitu-sim", "auth:8080", ips["auth"], False),
        ("registration", reg, "aitu-sim", "course:8080", ips["course"], False),
        ("registration", reg, "aitu-sim", "internet 1.1.1.1:443", "1.1.1.1", False),
        ("auth", auth, "aitu-sim", "db:8080", ips["db"], False),
        ("course", course, "aitu-sim", "db:8080", ips["db"], False),
        ("course", course, "aitu-sim", "registration:8080", ips["registration"], False),
        ("course", course, "aitu-sim", "internet 1.1.1.1:443", "1.1.1.1", False),
        ("db-0", db0, "aitu-sim", "db-1:8080 (replication)", ip_of("statefulset.kubernetes.io/pod-name=db-1"), True),
        ("db-0", db0, "aitu-sim", "auth:8080", ips["auth"], False),
        ("db-0", db0, "aitu-sim", "internet 1.1.1.1:443", "1.1.1.1", False),
    ]
    rows = []
    for name, src, ns, dst, host, expect_allowed in cases:
        port = "443" if "443" in dst else "8080"
        t = time.time()
        got = attempt(src, f"{host}:{port}", ns)
        ok = (got == "ALLOWED") == expect_allowed
        rows.append({"from": name, "to": dst, "expected": "ALLOWED" if expect_allowed else "BLOCKED", "observed": got,
                     "as_designed": ok, "seconds": round(time.time() - t, 1)})
        print(f"{name:26s} -> {dst:26s} expected {rows[-1]['expected']:8s} observed {got:8s} {'OK' if ok else 'MISMATCH'}", flush=True)
    rx.kubectl("delete", "pod", "probe", "-n", "default", "--now", check=False)
    subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "default", "run", "ctl", "--image=aitu-sim:1.2",
                    "--image-pull-policy=IfNotPresent", "--restart=Never", "--command", "--", "sleep", "120"], capture_output=True)
    subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "default", "wait", "--for=condition=Ready", "pod/ctl", "--timeout=60s"],
                   capture_output=True)
    got = attempt("ctl", "1.1.1.1:443", "default")
    rows.append({"from": "control pod (default ns, no policy)", "to": "internet 1.1.1.1:443", "expected": "ALLOWED",
                 "observed": got, "as_designed": got == "ALLOWED", "seconds": 0})
    print("control pod -> internet:", got, flush=True)
    rx.kubectl("delete", "pod", "ctl", "-n", "default", "--now", check=False)
    return rows


def node(cmd):
    r = subprocess.run(["docker", "exec", NODE, "sh", "-c", cmd], capture_output=True, text=True,
                       env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return {"cmd": cmd, "exit": r.returncode, "output": (r.stdout + r.stderr).strip()}


def immutability():
    listing = node("ls /vault | grep -v '^\\.' | grep manifest | tail -1")["output"]
    manifest = listing.strip()
    data = manifest.replace(".manifest", "")
    res = {"target_file": data, "lsattr": node(f"lsattr /vault/{data}")["output"], "attempts": []}
    for cmd in (f"rm -f /vault/{data}", f"mv /vault/{data} /vault/renamed.json", f"echo tampered > /vault/{data}",
                f"echo x >> /vault/{data}", f"truncate -s 0 /vault/{data}", f"rm -f /vault/{manifest}", "rm -rf /vault"):
        a = node(cmd)
        a["blocked"] = a["exit"] != 0
        res["attempts"].append(a)
        print(f"root on node: {cmd:60s} exit={a['exit']} {a['output'][:60]}", flush=True)
    res["intact_after_attempts"] = node(f"ls -l /vault/{data} && sha256sum /vault/{data}")["output"]
    res["all_blocked"] = all(a["blocked"] for a in res["attempts"])
    return res


def pod_security():
    cases = {
        "privileged container": {"securityContext": {"privileged": True}},
        "hostPath volume": {"volumeMounts": [{"name": "h", "mountPath": "/h"}]},
        "host network": None,
    }
    rows = []
    for name, extra in cases.items():
        spec = {"containers": [{"name": "x", "image": "aitu-sim:1.4", "command": ["sleep", "5"], **(extra or {})}]}
        if name == "hostPath volume":
            spec["volumes"] = [{"name": "h", "hostPath": {"path": "/"}}]
        if name == "host network":
            spec["hostNetwork"] = True
        override = json.dumps({"spec": spec})
        r = subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "aitu-sim", "run", "pss-test", "--image=aitu-sim:1.4",
                            "--restart=Never", "--overrides", override], capture_output=True, text=True)
        rejected = r.returncode != 0 and "violate" in r.stderr.lower()
        rows.append({"attempt": name, "rejected_by_pod_security": rejected, "message": (r.stderr or r.stdout).strip().replace("\n", " ")[:200]})
        subprocess.run(["kubectl", "--context", "kind-ft-aitu", "-n", "aitu-sim", "delete", "pod", "pss-test", "--ignore-not-found", "--now"],
                       capture_output=True)
        print(f"pod security: {name:22s} rejected={rejected}", flush=True)
    return rows


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    seg = segmentation()
    with open(os.path.join(OUT, "segmentation.json"), "w", encoding="utf-8") as f:
        json.dump({"checks": seg, "all_as_designed": all(r["as_designed"] for r in seg)}, f, indent=2)
    imm = immutability()
    with open(os.path.join(OUT, "immutability.json"), "w", encoding="utf-8") as f:
        json.dump(imm, f, indent=2)
    pss = pod_security()
    with open(os.path.join(OUT, "pod_security.json"), "w", encoding="utf-8") as f:
        json.dump({"attempts": pss, "all_rejected": all(r["rejected_by_pod_security"] for r in pss)}, f, indent=2)
    print("segmentation all as designed:", all(r["as_designed"] for r in seg), "| immutability all blocked:", imm["all_blocked"])
