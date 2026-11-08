"""Builds both images and deploys two stacks into the kind cluster `ft-aitu`:

  baseline  namespace `baseline`  - midterm configuration, unmodified files of git commit a6b1daa, NodePort 30081 (host 18081)
  endterm   namespace `aitu-sim`  - extended implementation, NodePort 30080 (host 18080), NetworkPolicies + Pod Security labels

Usage: python scripts/endterm_deploy.py [--rebuild]
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTX = "kind-ft-aitu"
ORDER = ["00-base", "10-db", "20-auth", "30-course", "40-registration", "50-gateway"]


def run(*cmd, input_text=None, check=True):
    r = subprocess.run(cmd, input=input_text, text=True, capture_output=True, encoding="utf-8")
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def kubectl(*args, **kw):
    return run("kubectl", "--context", CTX, *args, **kw)


def baseline_manifest(name):
    with open(os.path.join(ROOT, "baseline", "k8s", name + ".yaml"), encoding="utf-8") as f:
        text = f.read()
    text = text.replace("namespace: aitu-sim", "namespace: baseline")
    text = re.sub(r"(kind: Namespace\nmetadata:\n  name: )aitu-sim", r"\1baseline", text)
    text = text.replace("aitu-sim:1.2", "aitu-baseline:1.0").replace("nodePort: 30080", "nodePort: 30081")
    return text


def wait_stack(ns):
    for kind, name in (("sts", "db"), ("deploy", "auth"), ("deploy", "course"), ("deploy", "registration"), ("deploy", "gateway")):
        kubectl("-n", ns, "rollout", "status", f"{kind}/{name}", "--timeout=240s")


def main():
    if "--rebuild" in sys.argv or "aitu-sim:2.0" not in run("docker", "images", "--format", "{{.Repository}}:{{.Tag}}"):
        run("docker", "build", "-t", "aitu-sim:2.0", os.path.join(ROOT, "app"))
        run("docker", "build", "-t", "aitu-baseline:1.0", os.path.join(ROOT, "baseline", "app"))
    if "ft-aitu" not in run("kind", "get", "clusters"):
        run("kind", "create", "cluster", "--name", "ft-aitu", "--config", os.path.join(ROOT, "k8s", "kind-cluster.yaml"), "--wait", "180s")
    for image in ("aitu-sim:2.0", "aitu-baseline:1.0"):
        run("kind", "load", "docker-image", image, "--name", "ft-aitu")

    for name in ORDER:
        kubectl("apply", "-f", os.path.join(ROOT, "k8s", name + ".yaml"))
    kubectl("create", "namespace", "baseline", check=False)
    for name in ORDER:
        kubectl("apply", "-f", "-", input_text=baseline_manifest(name))
    wait_stack("aitu-sim")
    wait_stack("baseline")
    kubectl("apply", "-f", os.path.join(ROOT, "k8s", "security", "00-namespaces.yaml"))
    kubectl("apply", "-f", os.path.join(ROOT, "k8s", "security", "10-networkpolicies.yaml"))
    print(kubectl("get", "pods", "-A", "-l", "app").replace("\n", "\n"))


if __name__ == "__main__":
    main()
