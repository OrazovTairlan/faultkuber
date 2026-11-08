"""Loads results/endterm/runs/*.json and offers medians, ranges and pass counts per experiment and configuration."""
import glob
import json
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "results", "endterm", "runs")
EXPERIMENTS = ["E1", "E2", "E3", "E4", "E5", "E6", "E7"]
CONFIGS = ["baseline", "endterm"]


def load_all(directory=None):
    data = {}
    for path in sorted(glob.glob(os.path.join(directory or RUNS, "E*_rep*.json"))):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        for key in ("summary_whole", "during_spike", "summary_pre", "during_fault"):
            sw = d.get(key)
            if isinstance(sw, dict) and sw.get("success_pct") is not None:
                sw["error_rate_pct"] = round(100 - sw["success_pct"], 3)
        data.setdefault((d["experiment"], d["config"]), []).append(d)
    for key in data:
        data[key].sort(key=lambda d: d["rep"])
    return data


DATA = load_all()
PREV = load_all(os.path.join(ROOT, "results", "endterm", "runs_build1"))


def prev_values(exp, cfg, path):
    out = []
    for r in PREV.get((exp, cfg), []):
        v = get(r, path)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(v)
    return out


def prev_fmt(exp, cfg, path, nd=1):
    v = prev_values(exp, cfg, path)
    if not v:
        return "-"
    m = round(statistics.median(v), nd)
    lo, hi = round(min(v), nd), round(max(v), nd)
    return f"{m:g}" if lo == hi else f"{m:g} ({lo:g}-{hi:g})"


def prev_pass(exp, cfg):
    rs = PREV.get((exp, cfg), [])
    return sum(1 for r in rs if r["passed"]), len(rs)


def runs(exp, cfg):
    return DATA.get((exp, cfg), [])


def get(d, path, default=None):
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def values(exp, cfg, path):
    return [v for v in (get(r, path) for r in runs(exp, cfg)) if isinstance(v, (int, float)) and not isinstance(v, bool)]


def med(exp, cfg, path, nd=1):
    v = values(exp, cfg, path)
    return round(statistics.median(v), nd) if v else None


def rng(exp, cfg, path, nd=1):
    v = values(exp, cfg, path)
    return (round(min(v), nd), round(max(v), nd)) if v else None


def fmt(exp, cfg, path, nd=1, unit=""):
    """'median (min-max)' text; '-' when no data."""
    m, r = med(exp, cfg, path, nd), rng(exp, cfg, path, nd)
    if m is None:
        return "-"
    if r[0] == r[1]:
        return f"{m:g}{unit}"
    return f"{m:g}{unit} ({r[0]:g}-{r[1]:g})"


def total(exp, cfg, path):
    v = values(exp, cfg, path)
    return sum(v) if v else 0


def pass_counts(exp, cfg):
    rs = runs(exp, cfg)
    return sum(1 for r in rs if r["passed"]), len(rs)


def criteria_table(exp):
    """[(criterion, limit, {cfg: (passed_reps, reps, median_value)})]"""
    names = []
    cfgs = CONFIGS + sorted({k[1] for k in DATA if k[0] == exp and k[1] not in CONFIGS})
    for cfg in cfgs:
        for r in runs(exp, cfg):
            for c in r["criteria"]:
                if c["name"] not in [n[0] for n in names]:
                    names.append((c["name"], c["limit"], c["unit"]))
    rows = []
    for name, limit, unit in names:
        row = {}
        for cfg in cfgs:
            hits = [c for r in runs(exp, cfg) for c in r["criteria"] if c["name"] == name]
            vals = [c["value"] for c in hits if isinstance(c["value"], (int, float)) and not isinstance(c["value"], bool)]
            note = next((c["note"] for c in hits if c.get("note")), "")
            sample = hits[0]["value"] if hits else None
            row[cfg] = (sum(1 for c in hits if c["pass"]), len(hits), round(statistics.median(vals), 1) if vals else sample, note)
        rows.append((name, limit, unit, row))
    return rows


def alert_totals(cfg="endterm"):
    tp = fn = fp = 0
    per = {}
    for exp in EXPERIMENTS:
        t = f = p = 0
        delays = []
        for r in runs(exp, cfg):
            a = r["alerts"]
            t += len(a["true_positive"])
            f += len(a["false_negative"])
            p += len(a["false_positive"])
            if a.get("first_expected_delay_s") is not None:
                delays.append(a["first_expected_delay_s"])
        per[exp] = (t, f, p, round(statistics.median(delays), 1) if delays else None)
        tp, fn, fp = tp + t, fn + f, fp + p
    return tp, fn, fp, per


def fired_alerts(cfg="endterm"):
    n = 0
    for exp in EXPERIMENTS:
        for r in runs(exp, cfg):
            n += len([a for a in r["alerts"]["fired"] if a["fired_after_s"] >= -0.5])
    return n


def normal_load_latency(cfg):
    """Latency of the pre-fault windows (normal load) over all runs, to show the steady-state overhead of the controls."""
    avg, p95 = [], []
    for exp in EXPERIMENTS:
        for r in runs(exp, cfg):
            s = r.get("summary_pre") or {}
            if s.get("avg_ms") is not None and s.get("total", 0) > 100:
                avg.append(s["avg_ms"])
                p95.append(s["p95_ms"])
    return (round(statistics.median(avg), 1) if avg else None, round(statistics.median(p95), 1) if p95 else None, len(avg))
