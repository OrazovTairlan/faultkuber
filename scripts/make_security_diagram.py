"""diagrams/security_architecture.png (Assignment 4, Part C). Solid border = built and tested in the lab, dashed = design only."""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INK, MUTED = "#1f2933", "#52606d"
COL = {"edge": ("#e8eef7", "#3b6fb6"), "app": ("#e3f4ea", "#2e8b57"), "data": ("#fdf1d6", "#c28a00"),
       "mon": ("#fde9e4", "#c0562f"), "vault": ("#f3e5f5", "#7b3f98"), "rec": ("#e0f3f5", "#1b8a96"), "ext": ("#eeeeee", "#666666")}


def zone(ax, x, y, w, h, title, kind):
    f, e = COL[kind]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc=f, ec=e, lw=1.2, alpha=0.45, zorder=0))
    ax.text(x + 0.12, y + h - 0.16, title, fontsize=8.5, fontweight="bold", color=e, va="top", zorder=3)


def box(ax, x, y, w, h, text, kind, built=True, fs=7.8):
    f, e = COL[kind]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc="white", ec=e, lw=1.5,
                                ls="-" if built else (0, (4, 3)), zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=INK, zorder=3, linespacing=1.25)


def arrow(ax, p, q, color=MUTED, ls="-", rad=0.0, lw=1.3, label=None, lx=0, ly=0):
    ax.annotate("", xy=q, xytext=p, zorder=4,
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw, ls=ls, connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((p[0] + q[0]) / 2 + lx, (p[1] + q[1]) / 2 + ly, label, fontsize=7.5, color=color, ha="center", zorder=5,
                bbox=dict(fc="white", ec="none", pad=0.6))


fig, ax = plt.subplots(figsize=(14, 8.6))
ax.set_xlim(0, 14); ax.set_ylim(0, 8.6); ax.axis("off")
ax.text(0.15, 8.4, "Resilient security architecture - synthetic AITU research environment", fontsize=14, fontweight="bold", color=INK, va="center")
ax.text(0.15, 8.08, "Solid border = built and tested on the kind platform; dashed border = design only (not built). Numbers show the control flow.",
        fontsize=9, color=MUTED, va="center")

zone(ax, 0.15, 4.2, 1.9, 3.5, "1 Untrusted", "ext")
box(ax, 0.3, 6.1, 1.6, 0.9, "Students and\nresearchers", "ext")
box(ax, 0.3, 4.5, 1.6, 0.9, "Attacker\n(brute force,\nransomware)", "ext")

zone(ax, 2.3, 4.2, 2.6, 3.5, "2 Edge zone (DMZ)", "edge")
box(ax, 2.45, 6.65, 2.3, 0.65, "WAF + rate limit (design)", "edge", built=False)
box(ax, 2.45, 5.8, 2.3, 0.65, "MFA / IdP: TOTP, lockout\n(design)", "edge", built=False)
box(ax, 2.45, 4.45, 2.3, 1.15, "API Gateway x2\nonly public entry\n(NodePort, tested)", "edge")

zone(ax, 5.15, 4.2, 2.9, 3.5, "3 App zone (default-deny)", "app")
box(ax, 5.3, 6.45, 2.6, 0.7, "Auth x2", "app")
box(ax, 5.3, 5.55, 2.6, 0.7, "Course x2", "app")
box(ax, 5.3, 4.45, 2.6, 0.9, "Registration x2\n(only service allowed to reach DB)", "app")

zone(ax, 8.3, 4.2, 2.3, 3.5, "4 Data zone", "data")
box(ax, 8.45, 6.2, 2.0, 0.85, "DB node db-0\nPV, fsync", "data")
box(ax, 8.45, 4.75, 2.0, 0.85, "DB node db-1\nPV, fsync", "data")
arrow(ax, (9.45, 6.2), (9.45, 5.6), color=COL["data"][1], label="sync", lx=0.3)

zone(ax, 10.85, 3.1, 3.0, 4.6, "5 Vault zone (no inbound)", "vault")
box(ax, 11.0, 6.2, 2.7, 0.85, "vault-backup CronJob\nevery 5 min, sha256 manifest", "vault")
box(ax, 11.0, 5.0, 2.7, 0.95, "Immutable store\nchattr +i (WORM emulation)\nretention 7 d", "vault")
box(ax, 11.0, 3.35, 2.7, 1.3, "Offline / air-gapped copy\nin Kazakhstan, weekly\n(design - extra mechanism)", "vault", built=False)

zone(ax, 2.3, 0.25, 5.75, 3.55, "6 Monitoring and intrusion detection", "mon")
box(ax, 2.45, 2.25, 2.55, 0.9, "Log collector\n(kubectl logs, JSON lines)", "mon")
box(ax, 5.2, 2.25, 2.7, 0.9, "Rule engine R1-R8\n(failed logins, ransomware,\nbackup delete, ...)", "mon")
box(ax, 2.45, 0.45, 2.55, 1.2, "Synthetic probe\n5 failed reads = alert\n(black-box availability)", "mon")
box(ax, 5.2, 0.45, 2.7, 1.2, "AI anomaly score >= 0.75\n+ alert to on-call / SOC\n(design, advisory only)", "mon", built=False)

zone(ax, 8.3, 0.25, 5.55, 2.6, "7 Recovery environment (clean room)", "rec")
box(ax, 8.45, 0.5, 2.55, 1.4, "Clean data tier: volumes\nrecreated from YAML, secrets\nrotated (namespace rebuild: A3)", "rec")
box(ax, 11.15, 0.5, 2.55, 1.4, "Restore job: verify sha256\n-> write -> compare ids\n-> cut over", "rec")

arrow(ax, (1.9, 6.55), (2.45, 6.95), label="1", ly=0.15)
arrow(ax, (3.6, 6.65), (3.6, 6.45), color=COL["edge"][1])
arrow(ax, (3.6, 5.8), (3.6, 5.6), color=COL["edge"][1])
arrow(ax, (4.75, 5.0), (5.3, 6.75), color=COL["app"][1], label="2", lx=-0.1)
arrow(ax, (4.75, 5.0), (5.3, 5.9), color=COL["app"][1])
arrow(ax, (4.75, 5.0), (5.3, 4.9), color=COL["app"][1])
arrow(ax, (7.9, 4.9), (8.45, 6.4), color=COL["data"][1], label="3", lx=-0.1, ly=0.1)
arrow(ax, (7.9, 4.9), (8.45, 5.2), color=COL["data"][1])
arrow(ax, (1.1, 5.4), (2.45, 4.9), color="#b03030", ls=(0, (3, 2)))
ax.text(0.25, 4.3, "attack path", fontsize=7.5, color="#b03030")
arrow(ax, (10.45, 6.6), (11.0, 6.6), color=COL["vault"][1], label="4", ly=0.2)
arrow(ax, (12.35, 6.2), (12.35, 5.95), color=COL["vault"][1])
arrow(ax, (12.35, 5.0), (12.35, 4.65), color=COL["vault"][1], ls=(0, (3, 2)))
arrow(ax, (5.0, 2.7), (5.2, 2.7), color=COL["mon"][1])
arrow(ax, (6.5, 4.2), (6.5, 3.15), color=COL["mon"][1], ls=(0, (3, 2)), label="5 logs", lx=0.45)
arrow(ax, (3.6, 4.45), (3.6, 3.15), color=COL["mon"][1], ls=(0, (3, 2)))
arrow(ax, (13.4, 3.35), (13.4, 1.9), color=COL["rec"][1], label="6", lx=0.2)
arrow(ax, (11.0, 5.2), (10.0, 1.9), color=COL["rec"][1], rad=-0.15)
ax.text(9.1, 3.45, "6 restore source", fontsize=7.5, color=COL["rec"][1])
arrow(ax, (9.2, 1.9), (9.2, 4.75), color=COL["rec"][1], rad=0.1, ls=(0, (4, 2)), label="7 cut over", lx=-0.65, ly=-0.6)
fig.savefig(os.path.join(ROOT, "diagrams", "security_architecture.png"), dpi=165, bbox_inches="tight", facecolor="white")
print("ok")
