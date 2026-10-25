"""Draws diagrams/architecture.png and diagrams/rbd.png (matplotlib, no external tools)."""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "diagrams")
os.makedirs(OUT, exist_ok=True)

INK, MUTED = "#1f2933", "#52606d"
FILL = {"edge": "#e8eef7", "svc": "#e3f4ea", "db": "#fdf1d6", "dr": "#f3e5f5", "bad": "#fde2e0"}
LINE = {"edge": "#3b6fb6", "svc": "#2e8b57", "db": "#c28a00", "dr": "#7b3f98", "bad": "#c0392b"}


def box(ax, x, y, w, h, text, kind="svc", fs=9, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=FILL[kind], ec=LINE[kind], lw=1.4))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=INK,
            fontweight="bold" if bold else "normal", linespacing=1.3)


def arrow(ax, p, q, style="-|>", color=MUTED, ls="-", lw=1.4, rad=0.0):
    ax.annotate("", xy=q, xytext=p, arrowprops=dict(arrowstyle=style, color=color, lw=lw, ls=ls,
                                                     connectionstyle=f"arc3,rad={rad}"))


def architecture():
    fig, ax = plt.subplots(figsize=(13, 7.2))
    ax.set_xlim(0, 13); ax.set_ylim(0, 7.2); ax.axis("off")
    ax.text(0.2, 6.95, "AITU Digital Student Services Platform - resilient architecture (synthetic)",
            fontsize=13, fontweight="bold", color=INK, va="center")
    ax.text(0.2, 6.62, "Kubernetes namespace aitu-sim, 3 nodes (1 control-plane, 2 workers). "
            "Every tier has 2 instances spread over different worker nodes.",
            fontsize=9, color=MUTED, va="center")

    box(ax, 0.2, 3.0, 1.3, 1.2, "Students\n10 000 users\n500 req/s peak", "edge", 9)
    box(ax, 2.0, 2.55, 1.6, 2.1, "Load Balancer\n(NodePort 30080 /\ncloud LB)\n\n99.95%\nredundant LB\nrecommended", "edge", 8.5)
    box(ax, 4.2, 4.15, 1.7, 0.85, "API Gateway #1\n99.90%", "edge", 9)
    box(ax, 4.2, 2.2, 1.7, 0.85, "API Gateway #2\n99.90%", "edge", 9)
    ax.text(5.05, 3.62, "retry on the other\nreplica, 200 ms timeout", ha="center", fontsize=7.5, color=MUTED)
    ys = {"Auth": 5.0, "Course": 3.45, "Registration": 1.9}
    for name, y in ys.items():
        box(ax, 7.0, y, 1.55, 0.6, f"{name} #1\n99.50%", "svc", 8.5)
        box(ax, 7.0, y - 0.7, 1.55, 0.6, f"{name} #2\n99.50%", "svc", 8.5)
    box(ax, 10.0, 3.3, 1.5, 0.75, "DB node db-0\n99.70% (PV)", "db", 8.5)
    box(ax, 10.0, 1.65, 1.5, 0.75, "DB node db-1\n99.70% (PV)", "db", 8.5)
    arrow(ax, (10.75, 3.3), (10.75, 2.4), style="<|-|>", color=LINE["db"])
    ax.text(10.85, 2.85, "sync\nreplication", fontsize=7.5, color=LINE["db"], va="center")
    box(ax, 10.0, 0.25, 1.5, 0.75, "Backup storage\noff-cluster\nevery 5 min", "dr", 8)
    box(ax, 11.8, 0.25, 1.1, 0.75, "Git repo\nYAML = config\nbackup", "dr", 8)
    arrow(ax, (10.75, 1.65), (10.75, 1.0), color=LINE["dr"], ls="--")

    arrow(ax, (1.5, 3.6), (2.0, 3.6))
    arrow(ax, (3.6, 3.9), (4.2, 4.5)); arrow(ax, (3.6, 3.3), (4.2, 2.65))
    for gy in (4.55, 2.65):
        for name, y in ys.items():
            arrow(ax, (5.9, gy), (7.0, y + 0.3 - (0.0 if gy > 3.6 else 0.05)), lw=0.8)
    for y in (ys["Registration"], ys["Registration"] - 0.7):
        arrow(ax, (8.55, y + 0.3), (10.0, 3.7 if y > 1.5 else 2.0), lw=0.9, color=LINE["db"])
    notes = ("Mechanisms: readiness + liveness probes, Deployment/ReplicaSet self-healing, "
             "rolling update (maxUnavailable 0), PodDisruptionBudget,\n"
             "topology spread, Service DNS discovery, request timeouts + 1 retry, circuit breaker "
             "Registration->DB, graceful 503 when DB is down.")
    ax.text(0.2, 0.35, notes, fontsize=8, color=MUTED, va="bottom")
    ax.text(0.2, 1.25, "Colour code:  blue = edge tier,  green = stateless services,\n"
            "amber = stateful DB,  purple = backup / DR", fontsize=8, color=MUTED, linespacing=1.5)
    fig.savefig(os.path.join(OUT, "architecture.png"), dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def rbd():
    fig, ax = plt.subplots(figsize=(13, 4.6))
    ax.set_xlim(0, 13); ax.set_ylim(0, 4.6); ax.axis("off")
    ax.text(0.1, 4.35, "Reliability block diagram: series of parallel subsystems", fontsize=13,
            fontweight="bold", color=INK)
    ax.text(0.1, 4.0, "R_parallel = 1 - (1 - R1)(1 - R2);  R_system = product of all blocks "
            "(a request needs every tier).", fontsize=9, color=MUTED)
    blocks = [("Load Balancer", "99.95%", None, "bad"), ("API Gateway", "99.90%", None, "bad"),
              ("Auth", "99.50% x2", "99.9975%", "svc"), ("Course", "99.50% x2", "99.9975%", "svc"),
              ("Registration", "99.50% x2", "99.9975%", "svc"), ("Database", "99.70% x2", "99.9991%", "db")]
    x = 0.3
    ax.plot([0.1, x], [2.0, 2.0], color=MUTED, lw=1.4)
    for name, r, rp, kind in blocks:
        w = 1.6
        if rp is None:
            box(ax, x, 1.6, w, 0.8, f"{name}\n{r}\n(single)", kind, 8.5)
        else:
            box(ax, x, 2.35, w, 0.7, f"{name} #1\n{r.split()[0]}", kind, 8.5)
            box(ax, x, 0.95, w, 0.7, f"{name} #2\n{r.split()[0]}", kind, 8.5)
            ax.plot([x - 0.18, x - 0.18], [1.3, 2.7], color=MUTED, lw=1.2)
            ax.plot([x + w + 0.18, x + w + 0.18], [1.3, 2.7], color=MUTED, lw=1.2)
            for yy in (2.7, 1.3):
                ax.plot([x - 0.18, x], [yy, yy], color=MUTED, lw=1.2)
                ax.plot([x + w, x + w + 0.18], [yy, yy], color=MUTED, lw=1.2)
            ax.plot([x - 0.18 - 0.1, x - 0.18], [2.0, 2.0], color=MUTED, lw=1.2)
            ax.plot([x + w + 0.18, x + w + 0.28], [2.0, 2.0], color=MUTED, lw=1.2)
            ax.text(x + w / 2, 0.55, f"= {rp}", ha="center", fontsize=9, color=LINE[kind], fontweight="bold")
        nx = x + w + 0.45
        ax.plot([x + w + (0.28 if rp else 0), nx], [2.0, 2.0], color=MUTED, lw=1.4)
        x = nx
    ax.text(0.3, 0.12, "End-to-end = 99.95% x 99.90% x 99.9975%^3 x 99.9991% = 99.8417%  ->  below the 99.9% target. "
            "Red blocks are the single points of failure.", fontsize=9, color=INK)
    fig.savefig(os.path.join(OUT, "rbd.png"), dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    architecture(); rbd(); print("diagrams written to", OUT)
