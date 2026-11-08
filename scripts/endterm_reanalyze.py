"""Recomputes the E5 summary and criteria of stored runs from their raw per-attempt statuses.

Reason: the first definition counted every non-denied response (for example a 502 caused by a stall of the test bed) as a successful
attack; the refined definition counts only 2xx responses as success and reports transport errors / 5xx as inconclusive.
Raw statuses are never changed. Usage: python scripts/endterm_reanalyze.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import endterm_experiments as X

changed = 0
for path in sorted(glob.glob(os.path.join(X.OUT, "runs", "E5_*_rep*.json"))):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("reanalysed"):
        continue
    endterm = d["config"].startswith("endterm")
    d.update(X.e5_summary(d["attempts"]))
    d["criteria"] = X.e5_criteria(d, endterm)
    d["passed"] = all(c["pass"] for c in d["criteria"])
    d["reanalysed"] = "E5 criteria refined after the series: only 2xx counts as a successful attack, 5xx/transport errors are inconclusive"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    changed += 1
    print(os.path.basename(path), "passed" if d["passed"] else "FAILED", "inconclusive:", d["inconclusive_attempts"])
print("reanalysed runs:", changed)
