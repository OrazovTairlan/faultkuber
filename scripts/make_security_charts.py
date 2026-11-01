"""Assignment 4 figures: incident timeline, risk heat map, anomaly scores, drill step times."""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, "results", "security")
D = os.path.join(ROOT, "diagrams")
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
STAGE_COL = {"Credential attack": "#eb6834", "Privilege compromise": "#e34948", "Data destruction": "#6250d6",
             "Recovery attack": "#008300", "Exfiltration": "#1baf7a", "Persistence": "#eda100"}


def load(name):
    with open(os.path.join(R, name), encoding="utf-8") as f:
        return json.load(f)


def minute(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m) - 540


def clean(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=8)


SHORT = {"09:01": "15 failed\nlogins", "09:03": "21 failed\nlogins", "09:04": "successful\nlogin", "09:07": "320 files\nmodified",
         "09:08": "CPU 95%", "09:10": "1,200 files\nencrypted", "09:12": "backup\ndeletion", "09:13": "outbound to\nunknown IP",
         "09:15": "DB access\nspike", "09:18": "antivirus\ndisabled"}


def timeline(a):
    fig, ax = plt.subplots(figsize=(11, 4.4))
    alerts = {x["time"]: x for x in a["detector_replay"]["alerts"]}
    for i, s in enumerate(a["stages"]):
        x = minute(s["time"])
        ax.scatter(x, 1, s=90, color=STAGE_COL[s["stage"]], zorder=3)
        ax.text(x, 1.1 + 0.22 * (i % 2), f'{s["time"]}\n{SHORT[s["time"]]}', ha="center", va="bottom", fontsize=7, color=INK)
        if s["time"] in alerts:
            ax.scatter(x, 0.6, marker="^", s=70, color="#2a78d6", zorder=3)
    ax.axvline(10, color="#c0392b", ls="--", lw=1)
    ax.annotate("", xy=(10, 0.4), xytext=(3, 0.4), arrowprops=dict(arrowstyle="<->", color="#c0392b", lw=1))
    ax.text(6.5, 0.28, "7 min between the first CRITICAL alert (09:03)\nand the encryption of 1,200 files (09:10)", fontsize=7.5,
            color="#c0392b", ha="center", va="top")
    ax.set_xlim(-0.8, 19.5); ax.set_ylim(-0.1, 2.0)
    ax.set_xticks(range(0, 19, 2)); ax.set_xticklabels([f"09:{m:02d}" for m in range(0, 19, 2)])
    ax.set_yticks([0.6, 1]); ax.set_yticklabels(["rule fires", "event"], fontsize=8)
    handles = [plt.Line2D([], [], marker="o", ls="", color=c, label=k) for k, c in STAGE_COL.items()]
    handles.append(plt.Line2D([], [], marker="^", ls="", color="#2a78d6", label="deterministic rule alert (replay)"))
    ax.legend(handles=handles, fontsize=7, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.12), frameon=False)
    clean(ax)
    ax.set_title("Incident timeline: attack stage per event and when the deterministic rules fire", fontsize=11, loc="left", color=INK)
    fig.tight_layout(); fig.savefig(os.path.join(D, "incident_timeline.png"), dpi=165, facecolor="white"); plt.close(fig)


def heatmap(a):
    fig, ax = plt.subplots(figsize=(7.6, 5.6))
    cmap = {"Low": "#d7efdc", "Medium": "#fff1b8", "High": "#ffd2a8", "Critical": "#f6b3ad"}
    for l in range(1, 6):
        for i in range(1, 6):
            sc = l * i
            lvl = "Low" if sc <= 4 else "Medium" if sc <= 9 else "High" if sc <= 16 else "Critical"
            ax.add_patch(Rectangle((i - 0.5, l - 0.5), 1, 1, fc=cmap[lvl], ec="white", lw=2))
            ax.text(i + 0.33, l - 0.36, str(sc), fontsize=7, color=MUTED, ha="right")
    for r in a["risks"]:
        ax.scatter(r["impact"] + (-0.2 if r["threat"] in ("Ransomware",) else 0.2 if r["threat"] == "Backup destruction" else 0),
                   r["likelihood"] + (0.05 if r["threat"] == "Credential compromise" else 0), s=520, color="#1f2933", zorder=3)
        ax.text(r["impact"] + (-0.2 if r["threat"] == "Ransomware" else 0.2 if r["threat"] == "Backup destruction" else 0),
                r["likelihood"] + (0.05 if r["threat"] == "Credential compromise" else 0), str(r["priority"]), color="white",
                ha="center", va="center", fontsize=11, fontweight="bold", zorder=4)
    ax.set_xlim(0.5, 5.5); ax.set_ylim(0.5, 5.5)
    ax.set_xticks(range(1, 6)); ax.set_yticks(range(1, 6))
    ax.set_xlabel("Impact", fontsize=9, color=MUTED); ax.set_ylabel("Likelihood", fontsize=9, color=MUTED)
    legend = "\n".join(f'{r["priority"]} = {r["threat"]} ({r["likelihood"]}x{r["impact"]}={r["score"]})' for r in a["risks"])
    ax.text(5.65, 3, legend, fontsize=8, va="center", color=INK)
    ax.set_title("Risk matrix (Risk = Likelihood x Impact)", fontsize=11, loc="left", color=INK)
    clean(ax)
    fig.tight_layout(); fig.savefig(os.path.join(D, "risk_matrix.png"), dpi=165, facecolor="white", bbox_inches="tight"); plt.close(fig)


def anomalies(a):
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    names = [x["event"] for x in a["anomalies"]]
    xs = range(len(names))
    ax.bar([i - 0.2 for i in xs], [x["normal"] for x in a["anomalies"]], 0.38, color="#b7d3f6", label="normal score")
    ax.bar([i + 0.2 for i in xs], [x["observed"] for x in a["anomalies"]], 0.38, color="#2a78d6", label="observed score")
    ax.axhline(0.75, color="#c0392b", ls="--", lw=1.2)
    ax.text(4.6, 0.77, "anomaly threshold 0.75", color="#c0392b", fontsize=8, ha="right", bbox=dict(fc="white", ec="none", pad=1.5), zorder=5)
    for i, x in enumerate(a["anomalies"]):
        ax.text(i + 0.2, x["observed"] + 0.015, f'{x["observed"]:.2f}', ha="center", fontsize=8, color=INK)
    ax.set_xticks(list(xs)); ax.set_xticklabels(names, fontsize=8)
    ax.set_ylim(0, 1.1); ax.grid(axis="y", color=GRID); ax.set_axisbelow(True)
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    ax.set_title("AI-assisted detection: 5 of 5 events above the threshold (100%)", fontsize=11, loc="left", color=INK)
    clean(ax)
    fig.tight_layout(); fig.savefig(os.path.join(D, "anomaly_scores.png"), dpi=165, facecolor="white"); plt.close(fig)


def drill_steps(d):
    steps = d["steps"]
    names = ["Detect", "Isolate", "Preserve evidence", "Activate backup", "Restore services", "Verify integrity", "Return to production"]
    target = [2, 3, 5, 2, 10, 5, 3]
    meas = [steps[str(i)]["duration_s"] / 60 for i in range(1, 8)]
    fig, ax = plt.subplots(figsize=(9, 4))
    y = range(len(names))
    ax.barh([i + 0.2 for i in y], target, 0.38, color="#b7d3f6", label="target budget (min)")
    ax.barh([i - 0.2 for i in y], meas, 0.38, color="#2a78d6", label="measured in drill (min)")
    for i, m in enumerate(meas):
        ax.text(m + 0.1, i - 0.2, f"{m * 60:.0f} s", va="center", fontsize=8, color=INK)
    ax.set_yticks(list(y)); ax.set_yticklabels([f"{i + 1}. {n}" for i, n in enumerate(names)], fontsize=8)
    ax.invert_yaxis(); ax.grid(axis="x", color=GRID); ax.set_axisbelow(True)
    ax.set_xlabel("minutes", fontsize=9, color=MUTED); ax.legend(fontsize=8, frameon=False, loc="lower right")
    ax.set_title(f"Recovery drill: total {d['rto']['impact_to_fully_back_in_production_s'] / 60:.1f} min of the 30 min RTO", fontsize=11, loc="left", color=INK)
    clean(ax)
    fig.tight_layout(); fig.savefig(os.path.join(D, "drill_steps.png"), dpi=165, facecolor="white"); plt.close(fig)


if __name__ == "__main__":
    a = load("analysis.json")
    timeline(a); heatmap(a); anomalies(a)
    if os.path.exists(os.path.join(R, "drill.json")):
        drill_steps(load("drill.json"))
    print("charts written")
