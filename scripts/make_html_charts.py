"""Writes diagrams/failure_timelines.html (ApexCharts) from results/*_requests.csv.
Open the file in a browser and take a screenshot to replace diagrams/failure_timelines.png."""
import csv
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANELS = [("exp2_all_registration_replicas", "Exp 2 - all Registration replicas killed", -5, 15),
          ("exp3_database_unavailable", "Exp 3 - database down 30 s", -5, 45),
          ("gw_before_single_replica", "Gateway BEFORE: 1 replica, pod killed", -5, 40),
          ("gw_after_two_replicas", "Gateway AFTER: 2 replicas, 1 pod killed", -5, 40)]


def per_second(name, lo, hi):
    tot, bad = defaultdict(int), defaultdict(int)
    with open(os.path.join(ROOT, "results", name + "_requests.csv")) as f:
        for r in csv.DictReader(f):
            k = int(float(r["t_rel_s"]) // 1)
            tot[k] += 1
            bad[k] += not r["status"].startswith("2")
    return [{"x": str(k), "y": round(100 * bad[k] / tot[k], 1) if k in tot else None}
            for k in range(lo, hi + 1)]


panels = [{"id": f"c{i}", "title": t, "data": per_second(n, lo, hi)} for i, (n, t, lo, hi) in enumerate(PANELS)]

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Failure timelines</title>
<script src="https://cdn.jsdelivr.net/npm/apexcharts@3.54.1/dist/apexcharts.min.js"></script>
<style>
  :root { --bg:#fcfcfb; --ink:#0b0b0b; --muted:#52514e; --grid:#e6e5e1; --series:#2a78d6; }
  body { margin:0; padding:24px 16px; background:var(--bg); color:var(--ink); font:14px/1.4 system-ui,Segoe UI,Arial,sans-serif; }
  .wrap { max-width:1100px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 4px; }
  p.sub { margin:0 0 16px; color:var(--muted); }
  .grid { display:grid; grid-template-columns:repeat(2,1fr); gap:16px; }
  .card { padding:8px 4px 0; }
  .card h2 { font-size:14px; margin:0 8px 0; font-weight:600; }
  @media (max-width:760px) { .grid { grid-template-columns:1fr; } }
</style></head>
<body><div class="wrap">
  <h1>Share of failed client requests per second</h1>
  <p class="sub">100 req/s mixed load; dashed line = failure injected (t = 0). Source: results/*_requests.csv</p>
  <div class="grid">__CARDS__</div>
</div>
<script>
const PANELS = __DATA__;
PANELS.forEach(p => {
  new ApexCharts(document.getElementById(p.id), {
    chart: { type: 'bar', height: 280, toolbar: { show: false }, animations: { enabled: false },
             fontFamily: 'inherit', background: 'transparent' },
    series: [{ name: 'Failed requests', data: p.data }],
    colors: ['#2a78d6'],
    plotOptions: { bar: { columnWidth: '80%', borderRadius: 2 } },
    dataLabels: { enabled: false },
    stroke: { show: false },
    grid: { borderColor: '#e6e5e1', strokeDashArray: 0, xaxis: { lines: { show: false } } },
    xaxis: { type: 'category', tickAmount: 9, tooltip: { enabled: false },
             labels: { rotate: 0, hideOverlappingLabels: true, style: { colors: '#52514e', fontSize: '11px' } },
             title: { text: 'seconds since injection', style: { color: '#52514e', fontWeight: 400, fontSize: '12px' } },
             axisTicks: { show: false } },
    yaxis: { min: 0, max: 100, tickAmount: 5,
             labels: { style: { colors: '#52514e', fontSize: '11px' }, formatter: v => v + '%' },
             title: { text: 'failed requests (%)', style: { color: '#52514e', fontWeight: 400, fontSize: '12px' } } },
    annotations: { xaxis: [{ x: '0', borderColor: '#52514e', strokeDashArray: 4,
                            label: { text: 'failure injected', orientation: 'horizontal', borderWidth: 0,
                                     style: { color: '#52514e', background: 'transparent', fontSize: '11px' } } }] },
    tooltip: { y: { formatter: v => v + '% failed' }, x: { formatter: v => 't = ' + v + ' s' } },
    legend: { show: false }
  }).render();
});
</script></body></html>
"""
cards = "".join(f'<div class="card"><h2>{p["title"]}</h2><div id="{p["id"]}"></div></div>' for p in panels)
out = os.path.join(ROOT, "diagrams", "failure_timelines.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(HTML.replace("__CARDS__", cards).replace("__DATA__", json.dumps(panels)))
print("written", out)
