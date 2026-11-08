"""Figures and the monitoring dashboard for the endterm report (PNG for the report, HTML dashboard with ApexCharts)."""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import endterm_results as R

D = os.path.join(R.ROOT, "diagrams")
OUT = os.path.join(R.ROOT, "results", "endterm")
BLUE, GREY, RED, INK, MUTED, GRID = "#2a78d6", "#8a8f98", "#c0392b", "#0b0b0b", "#52514e", "#e6e5e1"
NAMES = {"E1": "E1 instance failure", "E2": "E2 dependency timeout", "E3": "E3 load spike", "E4": "E4 service down +\ndegradation",
         "E5": "E5 unauthorized\naccess", "E6": "E6 abusive /\nmalformed input", "E7": "E7 recovery +\nconsistency"}


def clean(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=8)


def pass_rate():
    fig, ax = plt.subplots(figsize=(10.5, 4.2))
    cfgs = R.CONFIGS + (["endterm-no-netpol"] if any(k[1] == "endterm-no-netpol" for k in R.DATA) else [])
    width = 0.8 / len(cfgs)
    colors = {"baseline": GREY, "endterm": BLUE, "endterm-no-netpol": "#1baf7a"}
    labels = {"baseline": "midterm baseline", "endterm": "endterm (with NetworkPolicy)", "endterm-no-netpol": "endterm without NetworkPolicy"}
    for i, cfg in enumerate(cfgs):
        vals = []
        for exp in R.EXPERIMENTS:
            hits = [c for r in R.runs(exp, cfg) for c in r["criteria"]]
            vals.append(100 * sum(1 for c in hits if c["pass"]) / len(hits) if hits else 0)
        pos = [k + (i - (len(cfgs) - 1) / 2) * width for k in range(len(R.EXPERIMENTS))]
        ax.bar(pos, vals, width, color=colors[cfg], label=labels[cfg], zorder=3)
        for x, v in zip(pos, vals):
            ax.text(x, v + 1.5, f"{v:.0f}%", ha="center", fontsize=7.5, color=INK)
    ax.set_xticks(range(len(R.EXPERIMENTS)))
    ax.set_xticklabels([NAMES[e] for e in R.EXPERIMENTS], fontsize=7.5)
    ax.set_ylim(0, 112)
    ax.set_ylabel("acceptance criteria passed (%)", fontsize=9, color=MUTED)
    ax.grid(axis="y", color=GRID, zorder=0)
    ax.legend(fontsize=7.5, frameon=False, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.32))
    ax.set_title("Acceptance criteria passed per experiment (all repetitions)", fontsize=11, loc="left", color=INK)
    clean(ax)
    fig.tight_layout()
    fig.savefig(os.path.join(D, "endterm_pass_rate.png"), dpi=165, facecolor="white")
    plt.close(fig)


def timelines():
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
    for ax, (exp, title, lo, hi) in zip(axes, (("E2", "E2 - database blocked for 20 s", -8, 42), ("E4", "E4 - Course service down for 20 s", -8, 42))):
        for cfg, color in (("baseline", GREY), ("endterm", BLUE), ("endterm-no-netpol", "#1baf7a")):
            rs = R.runs(exp, cfg)
            if not rs:
                continue
            bins = {b["t"]: 100 * b["failed"] / b["total"] for b in rs[0]["per_second"] if b["total"] and lo <= b["t"] <= hi}
            xs = sorted(bins)
            ax.plot(xs, [bins[x] for x in xs], color=color, lw=1.8, label={"baseline": "midterm baseline", "endterm": "endterm", "endterm-no-netpol": "endterm without NetworkPolicy"}[cfg])
        ax.axvspan(0, 20, color="#f3e5f5", alpha=0.5, zorder=0)
        ax.text(10, 96, "fault active", ha="center", fontsize=8, color=MUTED, va="top")
        ax.set_title(title, fontsize=10, loc="left", color=INK)
        ax.set_xlabel("seconds since injection", fontsize=8.5, color=MUTED)
        ax.set_ylim(0, 100)
        ax.grid(axis="y", color=GRID)
        clean(ax)
    axes[0].set_ylabel("failed requests per second (%)", fontsize=9, color=MUTED)
    axes[0].legend(fontsize=8, frameon=False, loc="center right")
    fig.tight_layout()
    fig.savefig(os.path.join(D, "endterm_timelines.png"), dpi=165, facecolor="white")
    plt.close(fig)


def load_spike():
    series = [("midterm baseline", [r for r in R.runs("E3", "baseline")], GREY), ("endterm", [r for r in R.runs("E3", "endterm")], BLUE)]
    abl = [r for r in R.DATA.get(("E3", "endterm-no-netpol"), [])]
    if abl:
        series.append(("endterm without NetworkPolicy", abl, "#1baf7a"))
    fresh = [r for r in R.DATA.get(("E3", "endterm-fresh-cni"), [])]
    if fresh:
        series.append(("endterm, fresh CNI state", fresh, "#6250d6"))
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.6))
    for k, (metric, label) in enumerate((("during_spike.avg_ms", "average latency (ms)"), ("during_spike.p95_ms", "p95 latency (ms)"))):
        ax = axes[k]
        for i, (name, rs, color) in enumerate(series):
            vals = [R.get(r, metric) for r in rs if R.get(r, metric) is not None]
            if not vals:
                continue
            ax.bar(i, sorted(vals)[len(vals) // 2], color=color, zorder=3, width=0.6)
            ax.scatter([i] * len(vals), vals, color=INK, s=12, zorder=4)
            ax.text(i, sorted(vals)[len(vals) // 2] + 3, f"{sorted(vals)[len(vals) // 2]:.0f}", ha="center", fontsize=8)
        ax.set_xticks(range(len(series)))
        ax.set_xticklabels([s[0].replace(" without", "\nwithout") for s in series], fontsize=7.5)
        ax.set_ylabel(label, fontsize=9, color=MUTED)
        ax.grid(axis="y", color=GRID, zorder=0)
        clean(ax)
    fig.suptitle(f"E3 load spike ({R.get(R.runs('E3', 'baseline')[0], 'rps_normal', 50)} -> 100 req/s for 20 s): latency during the spike (median bars, dots = repetitions)", fontsize=9.5, x=0.01, ha="left", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(os.path.join(D, "endterm_load.png"), dpi=165, facecolor="white")
    plt.close(fig)


def dashboard():
    exps = [("E2", "E2: database blocked (20 s)"), ("E4", "E4: Course service down (20 s)")]
    fig, axes = plt.subplots(4, 2, figsize=(11, 8.2), sharex="col")
    panels = (("rps", "requests / s", BLUE), ("errors_5xx", "5xx responses / s", RED), ("p95_ms", "gateway p95 (ms, 5 s window)", "#6250d6"),
              ("ready_pods", "Ready pods (of 10)", "#008300"))
    for col, (exp, title) in enumerate(exps):
        rs = R.runs(exp, "endterm")
        if not rs:
            continue
        run = rs[0]
        series = [p for p in run["dashboard"] if p["t"] >= -8]
        for row, (key, label, color) in enumerate(panels):
            ax = axes[row][col]
            xs = [p["t"] for p in series if p[key] is not None]
            ys = [p[key] for p in series if p[key] is not None]
            ax.plot(xs, ys, color=color, lw=1.6)
            ax.set_ylim(0, 10.5 if key == "ready_pods" else None)
            ax.axvspan(0, 20, color="#f3e5f5", alpha=0.45, zorder=0)
            for a in run["alerts"]["fired"]:
                if a["fired_after_s"] >= -0.5:
                    ax.axvline(a["fired_after_s"], color=RED, ls="--", lw=0.9)
                    if row == 0:
                        ax.text(a["fired_after_s"] + 0.3, ax.get_ylim()[1] * 0.95, a["alert"], rotation=90, fontsize=6.5, color=RED, va="top")
            if key == "p95_ms":
                ax.axhline(500, color=MUTED, ls=":", lw=1)
                ax.text(xs[0], 505, "500 ms requirement", fontsize=7, color=MUTED, va="bottom")
            ax.set_ylabel(label, fontsize=8, color=MUTED)
            ax.grid(axis="y", color=GRID)
            clean(ax)
        axes[0][col].set_title(title, fontsize=10, loc="left", color=INK)
        axes[3][col].set_xlabel("seconds since injection (shaded = fault active, red dashed = alert fired)", fontsize=8, color=MUTED)
    fig.suptitle("Monitoring dashboard - endterm metrics collected from /metrics (1 s scrape), alerts from observability/alerts.json", fontsize=11, x=0.01, ha="left", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(os.path.join(D, "endterm_dashboard.png"), dpi=165, facecolor="white")
    plt.close(fig)


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Endterm monitoring dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/apexcharts@3.54.1/dist/apexcharts.min.js"></script>
<style>
  body { margin:0; padding:20px 16px; background:#fcfcfb; color:#0b0b0b; font:14px/1.4 system-ui,Segoe UI,Arial,sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; } h1 { font-size:20px; margin:0 0 4px; } p.sub { margin:0 0 14px; color:#52514e; }
  .cols { display:grid; grid-template-columns:repeat(2,1fr); gap:16px; } h2 { font-size:15px; margin:0 0 6px; }
  .alerts { font-size:12.5px; color:#52514e; margin:2px 0 8px; } .alerts b { color:#c0392b; }
  @media (max-width:820px) { .cols { grid-template-columns:1fr; } }
</style></head>
<body><div class="wrap">
  <h1>Endterm monitoring dashboard</h1>
  <p class="sub">Source: results/endterm/runs/*_endterm_rep1.json (metrics scraped from every pod once per second). Shaded band = fault active; red lines = alert fired.</p>
  <div class="cols" id="cols"></div>
</div>
<script>
const DATA = __DATA__;
const cols = document.getElementById('cols');
const PANELS = [['rps', 'requests / s', '#2a78d6'], ['errors_5xx', '5xx responses / s', '#c0392b'], ['p95_ms', 'gateway p95 (ms)', '#6250d6'], ['ready_pods', 'Ready pods (of 10)', '#008300']];
DATA.forEach((exp, ci) => {
  const box = document.createElement('div');
  box.innerHTML = '<h2>' + exp.title + '</h2><div class="alerts">Alerts: ' + (exp.alerts.length ? exp.alerts.map(a => '<b>' + a.name + '</b> at +' + a.t + ' s').join(', ') : 'none') + '</div>';
  PANELS.forEach(([key, label, color], i) => {
    const div = document.createElement('div'); div.id = 'c' + ci + '_' + i; box.appendChild(div);
    setTimeout(() => new ApexCharts(div, {
      chart: { type: 'line', height: 190, toolbar: { show: false }, animations: { enabled: false }, fontFamily: 'inherit' },
      series: [{ name: label, data: exp.series.filter(p => p[key] !== null).map(p => ({ x: p.t, y: p[key] })) }],
      colors: [color], stroke: { width: 2 },
      xaxis: { type: 'numeric', tickAmount: 10, labels: { formatter: v => Math.round(v), style: { colors: '#52514e' } }, title: { text: i === 3 ? 'seconds since injection' : '' } },
      yaxis: { title: { text: label, style: { color: '#52514e', fontWeight: 400 } }, labels: { formatter: v => Math.round(v), style: { colors: '#52514e' } }, min: 0 },
      annotations: { xaxis: [{ x: 0, x2: 20, fillColor: '#b58fd0', opacity: 0.18 }].concat(exp.alerts.map(a => ({ x: a.t, borderColor: '#c0392b', strokeDashArray: 3 }))),
                     yaxis: key === 'p95_ms' ? [{ y: 500, borderColor: '#52514e', strokeDashArray: 2, label: { text: '500 ms requirement', style: { background: 'transparent', color: '#52514e' } } }] : [] },
      grid: { borderColor: '#e6e5e1' }, tooltip: { x: { formatter: v => '+' + v + ' s' } }
    }).render(), 0);
  });
  cols.appendChild(box);
});
</script></body></html>
"""


def dashboard_html():
    payload = []
    for exp, title in (("E2", "E2: database blocked for 20 s"), ("E4", "E4: Course service down for 20 s"), ("E1", "E1: one Registration instance killed")):
        rs = R.runs(exp, "endterm")
        if rs:
            run = rs[0]
            payload.append({"title": title, "series": [p for p in run["dashboard"] if p["t"] >= -8],
                            "alerts": [{"name": a["alert"], "t": a["fired_after_s"]} for a in run["alerts"]["fired"] if a["fired_after_s"] >= -0.5]})
    with open(os.path.join(D, "endterm_dashboard.html"), "w", encoding="utf-8") as f:
        f.write(HTML.replace("__DATA__", json.dumps(payload)))


if __name__ == "__main__":
    pass_rate()
    timelines()
    load_spike()
    dashboard()
    dashboard_html()
    print("figures written")
