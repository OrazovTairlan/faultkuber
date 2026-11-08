"""Post-series checks: test run record, log secret scan, segmentation matrix on the endterm stack.

Usage: python scripts/endterm_post.py
Writes results/endterm/tests.txt, log_secret_scan.json, segmentation.json
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import endterm_experiments as X
import security_checks as sc

OUT = X.OUT


def run_tests():
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=X.ROOT, capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    text = r.stdout + r.stderr
    with open(os.path.join(OUT, "tests.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    ran = re.search(r"Ran (\d+) tests in ([\d.]+)s", text)
    print("tests:", ran.group(0) if ran else "?", "OK" if "\nOK" in text else "FAILED")
    return text


def scan_logs():
    st = X.STACKS["endterm"]
    forbidden = ["synthetic-educational-secret-not-real", "pw-S", "eyJ"]
    scanned, hits = 0, {k: 0 for k in forbidden}
    for app in ("gateway", "auth", "registration", "course", "db"):
        for pod in st.pods(app):
            text = st.k("logs", pod, "--tail=100000", check=False)
            scanned += len(text.splitlines())
            for k in forbidden:
                hits[k] += text.count(k)
    bad_json = 0
    total_json = 0
    for app in ("gateway", "auth", "registration"):
        for pod in st.pods(app):
            for line in st.k("logs", pod, "--tail=100000", check=False).splitlines():
                if line.startswith("{"):
                    total_json += 1
                    try:
                        json.loads(line)
                    except ValueError:
                        bad_json += 1
    with open(os.path.join(OUT, "log_integrity.json"), "w", encoding="utf-8") as f:
        json.dump({"json_lines": total_json, "unparsable_lines": bad_json, "share_pct": round(100 * bad_json / max(1, total_json), 3)}, f, indent=1)
    print("log integrity:", bad_json, "of", total_json, "lines unparsable")
    res = {"log_lines_scanned": scanned, "patterns": {"signing key": hits[forbidden[0]], "password literal 'pw-S...'": hits[forbidden[1]], "token payload prefix 'eyJ'": hits[forbidden[2]]},
           "total_hits": sum(hits.values())}
    with open(os.path.join(OUT, "log_secret_scan.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print("log scan:", res)


def segmentation():
    rows = sc.segmentation()
    res = {"checks": rows, "all_as_designed": all(r["as_designed"] for r in rows)}
    with open(os.path.join(OUT, "segmentation.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print("segmentation:", sum(1 for r in rows if r["as_designed"]), "/", len(rows))


if __name__ == "__main__":
    X.ensure_proxy()
    os.makedirs(OUT, exist_ok=True)
    scan_logs()
    segmentation()
    run_tests()
