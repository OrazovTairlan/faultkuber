"""Capacity check for the 500 req/s peak: same load, different CPU limits / gateway replicas.

Usage: python scripts/capacity_test.py
The load generator is a single Python process running inside the cluster (Job), so 'achieved_rps' is a lower bound.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import loadgen
import run_experiments as rx

TARGET, SECONDS, PODS = 500, 25, 4
STATELESS = ["gateway", "auth", "course", "registration"]


def configure(cpu_limit, gw_replicas):
    for d in STATELESS:
        rx.kubectl("set", "resources", f"deploy/{d}", "--limits", f"cpu={cpu_limit}", "--requests", "cpu=100m")
    rx.kubectl("scale", "deploy/gateway", f"--replicas={gw_replicas}")
    for d in STATELESS:
        rx.kubectl("rollout", "status", f"deploy/{d}", "--timeout=120s")
    time.sleep(8)


JOB = """
apiVersion: batch/v1
kind: Job
metadata: {name: loadgen, namespace: aitu-sim}
spec:
  backoffLimit: 0
  completions: %d
  parallelism: %d
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: loadgen
          image: aitu-sim:1.2
          imagePullPolicy: IfNotPresent
          command: ["python", "/lg/loadgen.py", "--rps", "%d", "--seconds", "%d"]
          env: [{name: LG_HOST, value: gateway}, {name: LG_PORT, value: "8080"}]
          resources: {requests: {cpu: 500m}, limits: {cpu: "2"}}
          volumeMounts: [{name: lg, mountPath: /lg}]
      volumes: [{name: lg, configMap: {name: loadgen-script}}]
"""


def run(label):
    """Load generator runs as a Job inside the cluster -> measures the platform, not the Windows host path."""
    import subprocess
    rx.kubectl("delete", "job", "loadgen", "--ignore-not-found")
    rx.kubectl("delete", "configmap", "loadgen-script", "--ignore-not-found")
    rx.kubectl("create", "configmap", "loadgen-script", "--from-file=" + os.path.join(os.path.dirname(__file__), "loadgen.py"))
    subprocess.run(["kubectl", *rx.CTX, "apply", "-f", "-"], input=JOB % (PODS, PODS, TARGET // PODS, SECONDS), text=True, check=True,
                   capture_output=True)
    rx.kubectl("wait", "--for=condition=complete", "job/loadgen", f"--timeout={SECONDS + 120}s")
    outs = [json.loads(l) for l in rx.kubectl("logs", "-l", "job-name=loadgen", "--tail=1").splitlines()
            if l.strip().startswith("{")]
    total = sum(o["total"] for o in outs)
    failed = sum(o["failed"] for o in outs)
    worst = lambda k: max(o["latency_ms"][k] for o in outs)
    return {"config": label, "generator_pods": len(outs), "achieved_rps": round(sum(o["achieved_rps"] for o in outs), 1),
            "failed": failed, "success_pct": round(100 * (total - failed) / total, 3),
            "latency_ms": {"p50_worst_pod": worst("p50"), "p95_worst_pod": worst("p95"), "p99_worst_pod": worst("p99")}}


if __name__ == "__main__":
    results = []
    for label, cpu, gw in (("A: cpu limit 250m, gateway x2 (original)", "250m", 2),
                           ("C: cpu limit 1000m, gateway x4", "1000m", 4)):
        configure(cpu, gw)
        r = run(label); results.append(r); print(json.dumps(r), flush=True)
        time.sleep(15)
    rx.save("capacity_500rps.json", {"target_rps": TARGET, "seconds": SECONDS, "runs": results})
