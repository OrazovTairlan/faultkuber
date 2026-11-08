"""diagrams/endterm_architecture.png: endterm architecture; green = new or changed since the midterm baseline."""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INK, MUTED = "#1f2933", "#52606d"
OLD = ("#f1f3f5", "#7b8794")
NEW = ("#e3f4ea", "#2e8b57")
OBS = ("#fde9e4", "#c0562f")
SEC = ("#f3e5f5", "#7b3f98")
DATA = ("#fdf1d6", "#c28a00")


def box(ax, x, y, w, h, text, col, fs=7.8, lw=1.4, dashed=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=col[0], ec=col[1], lw=lw,
                                ls=(0, (4, 3)) if dashed else "-", zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=INK, zorder=3, linespacing=1.25)


def zone(ax, x, y, w, h, title, col):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc=col[0], ec=col[1], lw=1.1, alpha=0.4, zorder=0))
    ax.text(x + 0.1, y + h - 0.12, title, fontsize=8.5, fontweight="bold", color=col[1], va="top", zorder=3)


def arrow(ax, p, q, color=MUTED, ls="-", lw=1.3, label=None, lx=0.0, ly=0.0, rad=0.0):
    ax.annotate("", xy=q, xytext=p, zorder=4, arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, ls=ls, connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((p[0] + q[0]) / 2 + lx, (p[1] + q[1]) / 2 + ly, label, fontsize=7, color=color, ha="center", zorder=5, bbox=dict(fc="white", ec="none", pad=0.5))


fig, ax = plt.subplots(figsize=(14, 8.4))
ax.set_xlim(0, 14); ax.set_ylim(0, 8.4); ax.axis("off")
ax.text(0.15, 8.2, "Endterm architecture - resilient university information system (synthetic)", fontsize=14, fontweight="bold", color=INK, va="center")
ax.text(0.15, 7.9, "Grey = midterm baseline component, green = new or changed in the endterm, red = observability, purple = security. Namespace aitu-sim on a 3-node kind cluster.",
        fontsize=8.5, color=MUTED, va="center")

box(ax, 0.2, 4.7, 1.5, 1.0, "Students /\nclients\n(+ attacker)", OLD)
box(ax, 2.0, 4.5, 1.6, 1.4, "Load balancer\nService NodePort\nkube-proxy, Ready\npods only", OLD)

zone(ax, 3.9, 3.2, 2.6, 3.9, "Edge", NEW)
box(ax, 4.05, 4.2, 2.3, 2.6, "API Gateway x2\n- request id (X-Request-ID)\n- token check + ownership\n- rate limit 429\n- input limits 400/413\n- breaker per upstream\n- stale cache (degraded)\n- login -> fixed replica", NEW, fs=7.4)
box(ax, 4.05, 3.35, 2.3, 0.65, "timeouts 200 ms, 1 retry", OLD, fs=7.2)

zone(ax, 6.8, 3.2, 2.9, 3.9, "Services (2 replicas each)", NEW)
box(ax, 6.95, 5.85, 2.6, 0.8, "Auth: PBKDF2 login, lockout\nsigned expiring tokens, roles", NEW, fs=7.4)
box(ax, 6.95, 4.95, 2.6, 0.7, "Course (non-critical)\ntimetable, courses", OLD, fs=7.4)
box(ax, 6.95, 3.35, 2.6, 1.4, "Registration\n- validation, ownership\n- idempotent id, duplicate flag\n- DB breaker -> 503 + Retry-After", NEW, fs=7.4)

zone(ax, 10.0, 3.2, 1.9, 3.9, "Data", DATA)
box(ax, 10.1, 5.4, 1.7, 1.0, "db-0\nPV, fsync\nfail-closed", DATA, fs=7.6)
box(ax, 10.1, 3.7, 1.7, 1.0, "db-1\nPV, fsync\nfail-closed", DATA, fs=7.6)
arrow(ax, (10.95, 5.4), (10.95, 4.7), color=DATA[1], label="sync", lx=0.35)

zone(ax, 12.2, 3.2, 1.65, 3.9, "Security", SEC)
box(ax, 12.28, 5.75, 1.5, 0.95, "NetworkPolicy\ndefault deny", SEC, fs=7.2)
box(ax, 12.28, 4.7, 1.5, 0.85, "Pod Security\nnon-root, caps", SEC, fs=7.2)
box(ax, 12.28, 3.55, 1.5, 0.9, "Secret, config\nchecks at start", SEC, fs=7.2)

zone(ax, 0.2, 0.25, 9.6, 2.55, "Observability (new)", OBS)
box(ax, 0.35, 1.35, 2.3, 1.0, "/metrics on every pod\nrequests, latency histogram,\nevent counters (scraped)", OBS, fs=7.4)
box(ax, 2.9, 1.35, 2.2, 1.0, "Collector\n1 s scrape via\nkubectl proxy", OBS, fs=7.4)
box(ax, 5.35, 1.35, 2.2, 1.0, "Alert engine\n10 rules (alerts.json)\nthresholds + response", OBS, fs=7.4)
box(ax, 7.8, 1.35, 1.85, 1.0, "Dashboard\n(HTML) + runbook", OBS, fs=7.4)
box(ax, 0.35, 0.4, 4.7, 0.75, "JSON access log + audit log (stdout)\ncorrelation id: gateway -> auth -> registration -> db", OBS, fs=7.4)
box(ax, 5.35, 0.4, 4.3, 0.75, "Test harness: unit + integration tests,\n7 experiments x 3 reps, baseline vs endterm", OBS, fs=7.4)

zone(ax, 10.0, 0.25, 3.85, 2.55, "Midterm baseline (kept for comparison)", OLD)
box(ax, 10.15, 0.5, 3.55, 1.8, "namespace baseline (git a6b1daa)\nsame services, probes, replicas,\ntimeouts, DB breaker\nNO metrics, NO alerts, NO authz,\nNO validation, NO rate limit,\nNO NetworkPolicy", OLD, fs=7.4)

arrow(ax, (1.7, 5.2), (2.0, 5.2))
arrow(ax, (3.6, 5.2), (4.05, 5.2))
arrow(ax, (6.35, 6.3), (6.95, 6.4))
arrow(ax, (6.35, 5.5), (6.95, 5.4))
arrow(ax, (6.35, 4.6), (6.95, 4.2))
arrow(ax, (9.55, 4.1), (10.1, 5.8))
arrow(ax, (9.55, 4.0), (10.1, 4.2))
arrow(ax, (2.65, 1.85), (2.9, 1.85), color=OBS[1])
arrow(ax, (5.1, 1.85), (5.35, 1.85), color=OBS[1])
arrow(ax, (7.55, 1.85), (7.8, 1.85), color=OBS[1])
fig.savefig(os.path.join(ROOT, "diagrams", "endterm_architecture.png"), dpi=165, bbox_inches="tight", facecolor="white")
print("ok")
