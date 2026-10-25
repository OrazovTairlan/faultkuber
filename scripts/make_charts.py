"""results/*_requests.csv -> diagrams/failure_timelines.png (share of failed requests per second)."""
import csv
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLUE, INK, MUTED, GRID = "#2a78d6", "#0b0b0b", "#52514e", "#e6e5e1"
PANELS = [("exp2_all_registration_replicas", "Exp 2 - all Registration replicas killed", (-5, 15)),
          ("exp3_database_unavailable", "Exp 3 - database down 30 s", (-5, 45)),
          ("gw_before_single_replica", "Gateway BEFORE: 1 replica, pod killed", (-5, 40)),
          ("gw_after_two_replicas", "Gateway AFTER: 2 replicas, 1 pod killed", (-5, 40))]


def per_second(name):
    tot, bad = defaultdict(int), defaultdict(int)
    with open(os.path.join(ROOT, "results", name + "_requests.csv")) as f:
        for r in csv.DictReader(f):
            k = int(float(r["t_rel_s"]) // 1)
            tot[k] += 1
            bad[k] += not r["status"].startswith("2")
    return {k: 100 * bad[k] / tot[k] for k in tot}


fig, axes = plt.subplots(2, 2, figsize=(11, 6.2), sharey=True)
for ax, (name, title, (lo, hi)) in zip(axes.flat, PANELS):
    d = per_second(name)
    xs = [k for k in sorted(d) if lo <= k <= hi]
    ax.bar(xs, [d[k] for k in xs], width=0.8, color=BLUE, zorder=3)
    ax.axvline(0, color=MUTED, lw=1, ls="--", zorder=2)
    ax.text(0.3, 96, "failure injected", fontsize=8, color=MUTED, va="top")
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.set_xlim(lo, hi); ax.set_ylim(0, 100)
    ax.grid(axis="y", color=GRID, zorder=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
for ax in axes[1]:
    ax.set_xlabel("seconds since injection", fontsize=9, color=MUTED)
for ax in axes[:, 0]:
    ax.set_ylabel("failed requests per second (%)", fontsize=9, color=MUTED)
fig.suptitle("Share of failed client requests per second (100 req/s mixed load)", x=0.01, ha="left",
             fontsize=12, fontweight="bold", color=INK)
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(os.path.join(ROOT, "diagrams", "failure_timelines.png"), dpi=170, facecolor="#fcfcfb")
print("ok")
