"""Fills the Assignment 3 Word template from results/ and writes the completed report.

All numbers are read from results/*.json so the text cannot drift from the measurements.
Personal fields (name, ID, group, instructor, variant, signatures) are left for the student.
"""
import copy
import datetime
import hashlib
import json
import os

import docx
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "Assignment_3_AITU_Cloud_Microservices_Resilience_Report_Template.docx")
OUTPUT = os.path.join(ROOT, "Assignment_3_AITU_Cloud_Microservices_Resilience_Report_Completed.docx")
TODO = "[student to fill in]"


def res(name):
    with open(os.path.join(ROOT, "results", name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


AV = res("availability")
BASE, ROLL = res("baseline"), res("rolling_update")
E1, E1B, E2 = res("exp1_one_registration_replica"), res("exp1b_container_crash_restart"), res("exp2_all_registration_replicas")
E3, E3B = res("exp3_database_unavailable"), res("exp3b_one_database_node")
GWB, GWA = res("gw_before_single_replica"), res("gw_after_two_replicas")
DR, PEAK, CAP = res("dr_full_recovery"), res("peak_500rps"), res("capacity_500rps")
sub, e2e = AV["subsystems"], AV["end_to_end"]
what = AV["whatif"]
share = AV["unavailability_share"]
yaml_sha = hashlib.sha256(read("k8s/40-registration.yaml").encode()).hexdigest()[:16]
dr_recovery = round(DR["full_recovery_RTO_s"] - DR["namespace_delete_s"], 0)
restore_s = round(DR["restore_done_s"] - DR["db_ready_s"], 0)


def p(x, d=4):
    return f"{x * 100:.{d}f}%"


def kind_fail(res_, key, kind):
    k = res_[key][kind]
    return f"{k['failed']}/{k['total']}"


doc = docx.Document(TEMPLATE)


def set_cell(cell, text, size=8.5, bold=False):
    """Replace cell content; `text` may be a str or a list of paragraphs."""
    paras = text if isinstance(text, list) else [text]
    first = cell.paragraphs[0]
    for extra in cell.paragraphs[1:]:
        extra._p.getparent().remove(extra._p)
    for r in list(first.runs):
        r._r.getparent().remove(r._r)
    for i, t in enumerate(paras):
        para = first if i == 0 else cell.add_paragraph()
        run = para.add_run(t)
        run.font.size = Pt(size)
        run.bold = bold
        para.paragraph_format.space_after = Pt(2)


def find_table(first_header):
    for t in doc.tables:
        if t.rows[0].cells[0].text.strip() == first_header:
            return t
    raise KeyError(first_header)


def find_par(startswith):
    for para in doc.paragraphs:
        if para.text.strip().startswith(startswith):
            return para
    raise KeyError(startswith)


class Cursor:
    """Inserts new block elements one after another after an existing element."""

    def __init__(self, element):
        self.el = element._tbl if isinstance(element, Table) else element._p

    def _place(self, new_p):
        self.el.addnext(new_p._p)
        self.el = new_p._p
        return new_p

    def text(self, t, size=9.5, bold=False, italic=False, color=None, space_after=4):
        para = doc.add_paragraph()
        run = para.add_run(t)
        run.font.size, run.bold, run.italic = Pt(size), bold, italic
        if color:
            run.font.color.rgb = RGBColor.from_string(color)
        para.paragraph_format.space_after = Pt(space_after)
        return self._place(para)

    def bullets(self, items, size=9.5):
        for it in items:
            para = doc.add_paragraph(style="List Bullet")
            run = para.add_run(it)
            run.font.size = Pt(size)
            para.paragraph_format.space_after = Pt(1)
            self._place(para)

    def picture(self, rel, width=6.8, caption=None):
        para = doc.add_paragraph()
        para.add_run().add_picture(os.path.join(ROOT, rel), width=Inches(width))
        self._place(para)
        if caption:
            self.text(caption, size=8.5, italic=True, color="52514E")

    def code(self, source, size=7):
        para = doc.add_paragraph()
        lines = source.rstrip().split("\n")
        for i, line in enumerate(lines):
            run = para.add_run(line)
            run.font.name, run.font.size = "Consolas", Pt(size)
            run._r.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Consolas")
            if i < len(lines) - 1:
                run.add_break(WD_BREAK.LINE)
        shade = OxmlElement("w:shd")
        shade.set(qn("w:val"), "clear"); shade.set(qn("w:fill"), "F3F3F1")
        para._p.get_or_add_pPr().append(shade)
        para.paragraph_format.space_after = Pt(6)
        self._place(para)

    def table(self, rows, widths=None, size=8):
        t = doc.add_table(rows=len(rows), cols=len(rows[0]))
        t.style = "Table Grid"
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                set_cell(t.rows[i].cells[j], val, size=size, bold=(i == 0))
        if widths:
            for row in t.rows:
                for j, w in enumerate(widths):
                    row.cells[j].width = Inches(w)
        self.el.addnext(t._tbl)
        self.el = t._tbl
        self.text("", size=4, space_after=2)


def fill_rows(table, rows, size=8.5, start=1):
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            set_cell(table.rows[start + i].cells[j], val, size=size)


def add_rows(table, rows, size=8.5):
    for row in rows:
        new = copy.deepcopy(table.rows[-1]._tr)
        table._tbl.append(new)
        for j, val in enumerate(row):
            set_cell(table.rows[-1].cells[j], val, size=size)


tA = find_table("Component")
for t in doc.tables:
    if len(t.columns) == 4 and t.rows[0].cells[0].text.strip() == "Component":
        tA = t
fill_rows(tA, [
    ["Load Balancer",
     "LB instance or its zone fails, or health checks mark every gateway unhealthy. Single entry point (99.95%).",
     "Total loss of access for all 10,000 students until traffic is redirected; no data loss.",
     "Redundant LB pair / multi-zone cloud LB with active health checks and DNS or anycast failover. In the lab the "
     "Service (NodePort 30080) + kube-proxy balance only across Ready gateway pods. Recommended: 2nd LB "
     f"(end-to-end {p(what['+ 2nd load balancer'], 4)} alone; {p(what['+ 2nd gateway AND 2nd load balancer'], 4)} with 2nd gateway)."],
    ["API Gateway",
     "Gateway pod crashes, is evicted, or is overloaded; a bad release is rolled out.",
     f"With ONE replica every request fails: measured {GWB['failed_requests_total']} failed of "
     f"{GWB['whole_run']['total']} requests ({100 * GWB['failed_requests_total'] / GWB['whole_run']['total']:.1f}%), all request types. "
     f"With TWO replicas: {GWA['failed_requests_total']} failed of {GWA['whole_run']['total']}.",
     "2 replicas on different nodes, readiness probe (removed from Service in ~0.1-0.2 s), 200 ms upstream timeout + 1 retry, "
     "rolling update with maxUnavailable 0, PodDisruptionBudget minAvailable 1."],
    ["Auth",
     "One Auth replica dies, or both.",
     "One replica: none (tokens are stateless HMAC, any replica verifies them). Both: new logins and every "
     "/registrations call fail with 401/502; public timetable and course pages stay available.",
     "2 replicas, stateless tokens (no session store to lose), gateway retry on the other replica, Secret-based signing key, probes."],
    ["Course",
     "One or both Course replicas fail.",
     "One replica: none visible. Both: timetable and course information unavailable (read-only data); registration and login keep working.",
     "2 replicas, read-only synthetic data baked into the image, readiness/liveness probes; recommended: cache timetable at the gateway/CDN."],
    ["Registration",
     "Pod crash, container crash, node loss, or all replicas down (Exp 1, 1b, 2).",
     f"One pod: {E1['failed_requests_total']} failed requests (Exp 1). Container crash: {E1B['failed_requests_total']} failed. "
     f"All pods: {E2['failed_requests_total']} failed requests in a {E2['client_last_failure_done_s']:.1f} s window, only /registrations.",
     "2 replicas (topology spread), liveness + readiness probes, Deployment self-healing, rolling update, deterministic "
     "record id student:course so a retry is idempotent, circuit breaker towards the DB."],
    ["Database",
     "One DB node (pod/PV/node) fails, both fail, or data is corrupted/deleted.",
     f"One node: {E3B['failed_requests_total']} failed requests (Exp 3b). Both nodes: registration reads/writes get a controlled HTTP 503 "
     f"(Retry-After) while timetable, courses and login stay at 0 failures (Exp 3). Corruption: restore from backup.",
     "2 nodes (StatefulSet, anti-affinity, own PersistentVolume each), synchronous peer replication + fsync, anti-entropy "
     "catch-up at start, 5-minute backups to off-cluster storage, restore Job."],
], size=8)
c = Cursor(tA)
c.text("Figure 1 - Architecture diagram (generated by scripts/make_diagrams.py).", size=8.5, italic=True, color="52514E")
c.picture("diagrams/architecture.png", 6.9)

tB = find_table("Subsystem")
fill_rows(tB, [
    ["Auth", "2 replicas", f"1 - (1 - 0.995)(1 - 0.995) = 1 - 0.005 x 0.005 = {p(sub['Auth'], 4)}"],
    ["Course", "2 replicas", f"1 - (1 - 0.995)(1 - 0.995) = {p(sub['Course'], 4)}"],
    ["Registration", "2 replicas", f"1 - (1 - 0.995)(1 - 0.995) = {p(sub['Registration'], 4)}"],
    ["Database", "2 nodes", f"1 - (1 - 0.997)(1 - 0.997) = 1 - 0.003 x 0.003 = {p(sub['Database'], 4)}"],
    ["End-to-end", "Series",
     f"0.9995 x 0.9990 x {sub['Auth']:.6f} x {sub['Course']:.6f} x {sub['Registration']:.6f} x {sub['Database']:.6f} = "
     f"{p(e2e, 4)}  (about {(1 - e2e) * 8760:.1f} h downtime/year; the 99.9% target allows 8.76 h)"],
])
ans = {
    "Does the design meet 99.9%?":
        f"No. The given design (single LB, single API gateway, two replicas everywhere else) reaches {p(e2e, 4)}, below 99.9%. "
        "Even with perfect failover the two edge components alone give 0.9995 x 0.9990 = 99.85%, so no amount of "
        "backend redundancy can reach 99.9%.",
    "Which component is the bottleneck?":
        f"The API Gateway (99.90%, single): it causes {share['API Gateway'] * 100:.1f}% of all unavailability; the Load Balancer adds "
        f"{share['Load Balancer'] * 100:.1f}%. The four redundant subsystems together contribute only "
        f"{(share['Auth (2x)'] + share['Course (2x)'] + share['Registration (2x)'] + share['Database (2x)']) * 100:.1f}% - "
        "they are no longer the problem.",
    "What redundancy change would have the largest effect?":
        f"A second API-gateway replica: {p(what['Given design (1 LB, 1 API gateway)'], 4)} -> {p(what['+ 2nd API gateway replica'], 4)} "
        f"(meets 99.9%, ~{(1 - what['+ 2nd API gateway replica']) * 8760:.1f} h/year). A second LB alone gives {p(what['+ 2nd load balancer'], 4)} (still short); "
        f"a third Auth/Registration replica only +{(what['+ 3rd Auth replica'] - what['Given design (1 LB, 1 API gateway)']) * 100:.4f} points; "
        f"both a 2nd gateway and a 2nd LB give {p(what['+ 2nd gateway AND 2nd load balancer'], 4)}.",
}
for q, a in ans.items():
    para = find_par(q)
    Cursor(para).text("Answer: " + a, size=9.5)
rows_wi = [["Change (one at a time)", "End-to-end availability", "Meets 99.9%?", "Downtime / year"]]
for k, v in what.items():
    rows_wi.append([k, p(v, 4), "yes" if v >= 0.999 else "no", f"{(1 - v) * 8760:.2f} h"])
cov = AV["coverage"]
c = Cursor(find_par("What redundancy change"))
c = Cursor(find_par("Answer: A second API-gateway"))
c.text("Table B2 - sensitivity of the end-to-end result (scripts/availability.py).", size=8.5, italic=True, color="52514E")
c.table(rows_wi, widths=[2.9, 1.5, 1.0, 1.2])
c.text("Assumptions and caveat: component failures are independent and failover is perfect. If a failover only works with "
       f"probability c, the design with a 2nd gateway gives {p(cov['coverage 99%'], 3)} at c=99%, {p(cov['coverage 95%'], 3)} at c=95% "
       "(target missed again) - this is why I measured failover instead of assuming it (Part D).", size=9)
c.picture("diagrams/rbd.png", 6.9, "Figure 2 - Reliability block diagram of the GIVEN design (red = single points of failure).")

tC = find_table("Mechanism")
for t in doc.tables:
    if t.rows[0].cells[0].text.strip() == "Mechanism" and t.rows[0].cells[1].text.strip() == "Implemented?":
        tC = t
rowsC = {
    "Replicas": ["Yes - spec.replicas: 2 + topologySpreadConstraints (hostname) + PodDisruptionBudget minAvailable 1",
                 "Removes the single point of failure; a pod/node loss leaves one serving instance."],
    "Liveness probe": ["Yes - GET /healthz every 5 s, timeout 1 s, failureThreshold 3 (restart after ~15 s)",
                       "Detects a hung/deadlocked process that is still 'running' and makes the kubelet restart the container."],
    "Readiness probe": ["Yes - GET /readyz every 2 s, timeout 1 s, failureThreshold 2 (unready after ~4 s)",
                        "Keeps a starting or sick pod out of the Service endpoints, so users are not routed to it."],
    "Resource limits": ["Yes - requests 100m CPU / 64Mi, limits 250m CPU / 128Mi",
                        "Scheduler can place pods predictably; a runaway pod cannot starve neighbours (CPU throttle / OOM-kill only itself)."],
    "Rolling update": ["Yes - RollingUpdate, maxUnavailable 0, maxSurge 1, minReadySeconds 3, preStop sleep 5, SIGTERM handler",
                       f"Release without downtime: measured {ROLL['failed_requests_total']} failed of {ROLL['whole_run']['total']} requests during a live rollout."],
    "Service discovery": ["Yes - ClusterIP Service 'registration' (DNS registration.aitu-sim.svc), db-headless for db-0/db-1",
                          "Callers use a stable name; endpoints follow only Ready pods; load is spread across replicas."],
}
for r in tC.rows[1:]:
    key = r.cells[0].text.strip()
    if key in rowsC:
        set_cell(r.cells[1], rowsC[key][0]); set_cell(r.cells[2], rowsC[key][1])
add_rows(tC, [["Automatic restart", "Yes - restartPolicy: Always (kubelet) + Deployment/ReplicaSet recreates deleted pods",
               f"Exp 1b: container stopped on the node -> kubelet restarted it in place (restartCount {E1B['restart_count']}); "
               "a replacement pod appeared after pod deletion in Exp 1/2."]])
c = Cursor(tC)
c.text("Registration Service manifest - file k8s/40-registration.yaml "
       f"(SHA-256 prefix {yaml_sha}; image aitu-sim:1.2). Synthetic, no real AITU data:", size=9.5, bold=True)
c.code(read("k8s/40-registration.yaml"), size=6.5)
c.text("Live check of the deployed object (results/logs/kubectl_evidence.txt): replicas 2, strategy RollingUpdate maxSurge 1 / "
       "maxUnavailable 0, both probes and the resource block exactly as above, endpoints registration = two pod IPs, "
       "DNS resolution of 'registration' and 'db-0.db-headless' from inside the gateway pod. During a heavy-load test db-1 "
       "failed a liveness probe (timeout) and was restarted automatically by the kubelet (restartCount 1) - an unplanned but real "
       "confirmation of the liveness probe.", size=9)

tD = find_table("Experiment")
e2w = E2["during_window"]
e3_down = E3["during_outage_by_kind"]
fill_rows(tD, [
    ["1", "One Registration replica (pod force-deleted, grace 0)",
     f"{E1['k8s_detection_s']} s: pod left the Service endpoints (kubectl poll, ~0.3 s resolution)",
     f"{E1['k8s_recovery_s']} s: replacement pod Ready, 2/2",
     f"None visible: {E1['failed_requests_total']} of {E1['whole_run']['total']} requests failed; p99 {E1['whole_run']['latency_ms']['p99']} ms. "
     "The surviving replica and the gateway retry absorbed the loss."],
    ["2", "All Registration replicas (both pods force-deleted together)",
     f"{E2['k8s_detection_s']} s in Kubernetes; first user-visible error {E2['client_first_failure_s']} s after injection",
     f"{E2['k8s_recovery_s']} s until 2/2 Ready; last failed request at {E2['client_last_failure_done_s']} s",
     f"{E2['failed_requests_total']} of {e2w['total']} requests in the window failed (HTTP 502): "
     f"{kind_fail(e2w, 'by_kind', 'reg_write')} registration writes, {kind_fail(e2w, 'by_kind', 'reg_read')} reads. "
     f"Timetable {kind_fail(e2w, 'by_kind', 'timetable')}, courses {kind_fail(e2w, 'by_kind', 'courses')}, login {kind_fail(e2w, 'by_kind', 'login')} failed. No data lost."],
    ["3", "Database unavailable (both nodes, StatefulSet scaled to 0 for 30 s)",
     f"{E3['k8s_detection_s']} s in Kubernetes; first failing request {E3['client_first_failure_s']} s after injection "
     "(Registration answers HTTP 503 + Retry-After, circuit breaker opens after 3 failures)",
     f"DB pods Ready {E3['db_pods_ready_after_restore_s']} s after scale-up; first successful write {E3['first_successful_write_after_restore_s']} s after scale-up",
     f"During the {E3['db_outage_s']} s outage registration reads {e3_down['reg_read']['failed']}/{e3_down['reg_read']['total']} "
     f"and writes {e3_down['reg_write']['failed']}/{e3_down['reg_write']['total']} failed (503); timetable {e3_down['timetable']['failed']}/{e3_down['timetable']['total']}, "
     f"courses {e3_down['courses']['failed']}/{e3_down['courses']['total']}, login {e3_down['login']['failed']}/{e3_down['login']['total']} failed. "
     f"Max latency {E3['max_latency_during_outage_ms']} ms (< 500 ms). All {E3['acked_writes']} acknowledged writes present on both nodes after recovery (0 missing)."],
])
add_rows(tD, [
    ["1b (extra)", "Container crash on the node (crictl stop) - tests automatic restart",
     f"{E1B['k8s_detection_s']} s (Pod Ready=false)", f"{E1B['k8s_recovery_s']} s; restartCount {E1B['restart_count']}",
     f"{E1B['failed_requests_total']} of {E1B['whole_run']['total']} requests failed (502), window {E1B['client_first_failure_s']}-{E1B['client_last_failure_done_s']} s."],
    ["3b (extra)", "One of two DB nodes (db-0 force-deleted)",
     f"{E3B['k8s_detection_s']} s", f"{E3B['k8s_recovery_s']} s (pod Ready; data re-synced from db-1)",
     f"{E3B['failed_requests_total']} failed requests of {E3B['whole_run']['total']}; both nodes hold identical records afterwards (0 acknowledged writes missing)."],
    ["4 (extra)", "Rolling update of Registration under load (image 1.1 -> 1.2)",
     "n/a (planned)", "rollout completed under load",
     f"{ROLL['failed_requests_total']} failed of {ROLL['whole_run']['total']} requests; p99 {ROLL['whole_run']['latency_ms']['p99']} ms."],
    ["5 (extra)", "Gateway BEFORE: 1 replica, pod killed (in-cluster load)",
     f"{GWB['k8s_detection_s']} s", f"{GWB['k8s_recovery_s']} s to Ready, but clients kept failing until {GWB['client_last_failure_done_s']} s",
     f"{GWB['failed_requests_total']} of {GWB['whole_run']['total']} requests failed ({100 * GWB['failed_requests_total'] / GWB['whole_run']['total']:.1f}%), every request type - total outage."],
    ["5 (extra)", "Gateway AFTER: 2 replicas, one pod killed (in-cluster load)",
     f"{GWA['k8s_detection_s']} s", f"{GWA['k8s_recovery_s']} s to 2/2 Ready",
     f"{GWA['failed_requests_total']} of {GWA['whole_run']['total']} failed; p99 {GWA['whole_run']['latency_ms']['p99']} ms."],
], size=8)
c = Cursor(tD)
c.text("How the numbers were obtained", size=10, bold=True)
c.bullets([
    "Platform: kind cluster 'ft-aitu' (1 control-plane + 2 workers), namespace aitu-sim; Gateway -> Auth/Course/Registration -> 2-node DB (see k8s/).",
    "Load: scripts/loadgen.py, open-loop 100 req/s mixed traffic (40% timetable, 20% courses, 10% login, 15% registration reads, "
    "15% registration writes with unique ids), through the gateway NodePort. Every request is logged (results/*_requests.csv).",
    "Injection: kubectl delete --force --grace-period=0 (crash), kubectl scale sts/db --replicas=0/2, crictl stop on the node (container crash).",
    "Experiment 5 (gateway before/after) uses the same generator as a Job inside the cluster against the Service gateway:8080 because the Docker Desktop host port "
    "proxy stalls when the only gateway disappears; its timestamps are converted to the host clock (measured offset ~0.05 s).",
    "Detection (Kubernetes) = injection -> first poll with fewer Ready pods; recovery = poll with all pods Ready again; "
    "user-visible detection/recovery = first/last failed client request. Poll resolution is about 0.3 s.",
    f"Baseline without failures: {BASE['total']} requests, {BASE['failed']} failed, p50/p95/p99 = {BASE['latency_ms']['p50']}/{BASE['latency_ms']['p95']}/{BASE['latency_ms']['p99']} ms (limit 500 ms).",
    "Data safety check after every run: every acknowledged write must exist on db-0 and db-1 ('acked_missing' = 0 in all results).",
], size=9)
c.picture("diagrams/failure_timelines.png", 6.8, "Figure 3 - Share of failed requests per second around the injection (from results/*_requests.csv).")
c.text("Answer to the oral-defence question - what happens if both Registration replicas fail?", size=10, bold=True)
c.text(f"Registration endpoints return HTTP 502 from the gateway (kube-proxy has no endpoint, the gateway retries once and gives up). Everything else keeps working because "
       f"the services are independent: timetable, courses and login had 0 failures in Exp 2. The Deployment controller recreates both pods; "
       f"in my run the service was back after {E2['k8s_recovery_s']} s (last failed request at {E2['client_last_failure_done_s']} s). "
       "No data is lost because Registration is stateless and only acknowledged writes (already on the DB) count. "
       "If the pods were crash-looping (bad image) the outage would last until a rollback; the PodDisruptionBudget does not protect against "
       "involuntary failure, so production would add alerting + automatic rollback.", size=9.5)
c.text("Honest limits of the load tests", size=10, bold=True)
c.text(f"The 500 req/s peak was NOT demonstrated. From the host (single Python client + Docker Desktop port proxy) I reached {PEAK['achieved_rps']} req/s with "
       f"{100 - PEAK['success_pct']:.0f}% errors and p99 {PEAK['latency_ms']['p99']} ms; from 4 generator pods inside the cluster "
       f"the original limits gave {CAP['runs'][0]['achieved_rps']} req/s, {100 - CAP['runs'][0]['success_pct']:.0f}% errors, and raising CPU limits to 1000m and the gateway to 4 replicas "
       f"changed little ({CAP['runs'][1]['achieved_rps']} req/s). The limit is the single-process Python services plus the generator and the platform all sharing one laptop, "
       "so this lab validates behaviour at 100 req/s (20% of peak) and the failure/recovery logic, not capacity. "
       "Meeting 500 req/s at p99 < 500 ms needs a real load test on production-grade runtimes (multi-worker/async servers, HPA on CPU). "
       "(results/peak_500rps.json, results/capacity_500rps.json)", size=9.5)

tE = find_table("Mechanism")
for t in doc.tables:
    if t.rows[0].cells[0].text.strip() == "Mechanism" and t.rows[0].cells[1].text.strip() == "Frequency":
        tE = t
fill_rows(tE, [
    ["Database backup", "Every 5 min (CronJob */5), last 12 kept, stored outside the cluster (host path standing in for object storage)",
     "<= 5 min worst case (changes since the last run); 0 in the drill because a backup was taken just before the disaster",
     f"Restore Job: about {restore_s:.0f} s for {DR['records_before_disaster']} records (~{restore_s / DR['records_before_disaster'] * 1000:.0f} ms/record, naive sequential replay)",
     "Protects against logical corruption, accidental deletion, and loss of both DB nodes / volumes - failures replication copies faithfully."],
    ["Replica", "Continuous: every write is fsync'ed on one node and replicated synchronously to the peer; peer re-syncs at start and every 5 s",
     f"0 for a single node loss: {E3B['audit']['db-0']['acked_missing'] + E3B['audit']['db-1']['acked_missing']} acknowledged writes missing in Exp 3b",
     f"No client-visible failure ({E3B['failed_requests_total']} failed); replacement node Ready in {E3B['k8s_recovery_s']} s",
     "High availability: keep serving writes and reads when one node/pod/PV fails."],
    ["Configuration backup", "On every change: manifests in k8s/ and dr/ (declarative YAML, to be kept in Git); Secrets re-created from the secret store",
     "= last committed change (0 for committed config); the diagram + YAML are the source of truth",
     f"kubectl apply of 7 manifests; DB tier Ready {DR['db_ready_s']} s after the namespace was gone (drill)",
     "Rebuild the platform in a new namespace/cluster without manual re-configuration."],
    ["Full recovery", "Drill executed once: namespace deleted (pods, Services, PVCs), manifests re-applied, newest backup restored",
     f"<= 5 min worst case; drill: {DR['records_before_disaster']} records before vs {DR['records_after_restore_db1']} after restore (0 lost)",
     f"About {dr_recovery:.0f} s ({dr_recovery / 60:.1f} min) from empty cluster to all tiers Ready and data restored; target RTO 15 min = {100 * dr_recovery / 900:.0f}% of the budget",
     "Disaster recovery: survive loss of a whole site/cluster/namespace. Checked with a functional test through the gateway (timetable, login, registrations all HTTP 200)."],
])
c = Cursor(tE)
c.text("RTO/RPO verdict", size=10, bold=True)
c.text(f"RPO target 5 min: met by design (5-minute backups; synchronous replica for node failures). RTO target 15 min: met in the drill "
       f"({dr_recovery:.0f} s), but restore time grows with data volume: at ~{restore_s / DR['records_before_disaster'] * 1000:.0f} ms/record my sequential replay would use the whole 15 min at roughly "
       f"{int(900 / (restore_s / DR['records_before_disaster'])):,} records. A real DBMS would use bulk restore / point-in-time recovery. The drill backup was taken immediately before the disaster, "
       "so it shows RTO, not the worst-case RPO.", size=9.5)
c.text("Why high availability and disaster recovery are different engineering objectives", size=10, bold=True)
c.text("High availability keeps the service running through common, small, local failures (a pod, a node, a process) by removing single points of failure "
       "and failing over automatically within seconds; its enemy is component failure and its metric is availability (99.9% = 8.76 h/year). "
       "Disaster recovery restores service and data after a large or logical failure that redundancy cannot hide - loss of the whole site/cluster/namespace, "
       "operator error, ransomware, or corrupted data that the replica copied faithfully; its metrics are RPO (how much data may be lost) and RTO (how long to be back), "
       "and its mechanism is independent copies (backups in another place) plus a rehearsed procedure. In my results the replica gives RPO 0 and no user impact for "
       f"a node failure, yet deleting the namespace destroyed both replicas at once - only the backup brought the {DR['records_before_disaster']} records back, in about {dr_recovery:.0f} s, "
       "with a human-triggered procedure. HA costs always-on duplicate capacity; DR costs backup storage and regular drills. A system can be highly available and still have no "
       "disaster recovery, and the reverse.", size=9.5)

tF = find_table("Required field")
vals = {
    "Student full name": TODO, "Student ID": TODO, "Group / cohort": TODO, "Instructor": TODO,
    "Date of submission": "25 / 10 / 2026", "Individual variant / task code assigned by instructor": TODO + " (not given in the task text)",
    "Git repository / project link": "No remote yet; local project folder PycharmProjects/faulttolerancemicroservices (not a git repository at submission time - add the link after pushing)",
    "Attached code / file names": "app/service.py, app/Dockerfile, k8s/*.yaml (kind-cluster, 00-base, 10-db, 20-auth, 30-course, 40-registration, 50-gateway, 60-backup), dr/restore-job.yaml, "
                                  "scripts/{availability,loadgen,run_experiments,capacity_test,make_diagrams,make_charts,build_report}.py, scripts/run_all.sh, results/*.json|csv, results/logs/*, diagrams/*.png, README.md",
}
for r in tF.rows[1:]:
    k = r.cells[0].text.strip()
    if k in vals:
        set_cell(r.cells[1], vals[k])

tW = find_table("Item")
wvals = {
    "Work start date/time": "25.10.2026, 18:16 (UTC+5)",
    "Work completion date/time": datetime.datetime.now().strftime("%d.%m.%Y, %H:%M") + " (UTC+5)",
    "Main task completed": "Resilient design of the synthetic AITU student-services platform: architecture analysis, availability model, "
                           "Kubernetes manifests, 3 required + 4 extra failure scenarios (container crash, one DB node, rolling update, gateway before/after), backup/restore drill, RPO/RTO plan.",
    "Tools / technologies used": "Python 3.13 (stdlib services, matplotlib, python-docx), Docker Desktop 28.3, kind (3-node cluster), kubectl, Kubernetes "
                                 "Deployments/StatefulSet/CronJob/Job/PDB, crictl.",
    "Main technical output produced": f"Running 2-replica platform in kind, availability model ({p(e2e, 4)} given -> {p(what['+ 2nd API gateway replica'], 4)} with 2nd gateway), "
                                      "measured failure/recovery data, DR drill (about 2 min), this report.",
}
for r in tW.rows[1:]:
    k = r.cells[0].text.strip()
    if k in wvals:
        set_cell(r.cells[1], wvals[k])

sec = [t for t in doc.tables if t.rows[0].cells[0].text.strip() == "Required content"]
SUMMARY, EVID, DEC = 1, 2, 3
text = [
    ["Six components were analysed (Part A). The stateless tiers (Gateway, Auth, Course, Registration) get 2 replicas, probes, rolling updates and "
     "self-healing; the DB gets 2 nodes with synchronous replication and backups. The single points of failure in the GIVEN design are the LB and the gateway.",
     "Table in Part A; Figure 1 (diagrams/architecture.png); k8s/*.yaml; measured effects in Exp 1-3b (results/*.json).",
     "Registration stays Ready when the DB is down (readiness checks only the process) and answers HTTP 503 + Retry-After behind a circuit breaker, "
     "instead of failing readiness: otherwise both pods would leave the Service and the failure would cascade into 502s and restart noise. "
     "I also made registration ids deterministic (student:course) so retries are idempotent."],
    [f"Parallel subsystems: Auth/Course/Registration {p(sub['Auth'], 4)}, DB {p(sub['Database'], 4)}. Series with LB and gateway: {p(e2e, 4)} < 99.9% - target NOT met. "
     f"Bottleneck: single API gateway ({share['API Gateway'] * 100:.1f}% of unavailability). Largest improvement: 2nd gateway -> {p(what['+ 2nd API gateway replica'], 4)}.",
     "Part B tables and formulas; scripts/availability.py; results/availability.json; Figure 2 (RBD); Table B2 (what-if and failover-coverage sensitivity).",
     "I did not stop at 'add replicas': I compared every single change (Table B2) and showed that a 3rd replica of Auth/Registration/DB adds <0.003 points, "
     "and that the result depends on failover coverage (95% coverage drops the improved design to 99.847%). That is why the gateway failover was measured (Exp 5)."],
    ["Registration Deployment: 2 replicas spread over nodes, readiness (2 s) and liveness (5 s) probes, requests/limits, RollingUpdate maxUnavailable 0 / maxSurge 1, "
     "restartPolicy Always, ClusterIP Service + PDB. All seven mechanisms are implemented and verified on a live cluster.",
     f"k8s/40-registration.yaml (SHA-256 prefix {yaml_sha}); results/logs/kubectl_evidence.txt; Exp 4: {ROLL['failed_requests_total']} failures in {ROLL['whole_run']['total']} requests during rollout; "
     f"Exp 1b: container restarted by kubelet (restartCount {E1B['restart_count']}).",
     "Tuned setting: readinessProbe.periodSeconds 10 -> 2 s with failureThreshold 2 (unready after ~4 s instead of ~30 s) and maxUnavailable 0 with maxSurge 1 so a rollout never "
     "reduces capacity. The rollout result (0 failed) supports maxUnavailable 0; the probe period was chosen by reasoning and I did not run a separate hang test to compare."],
    [f"All three required experiments ran on the live cluster under 100 req/s: one replica {E1['failed_requests_total']} failures, all replicas {E2['failed_requests_total']} failures "
     f"(only /registrations, {E2['k8s_recovery_s']} s to recover), DB down {E3['db_outage_s']:.0f} s -> controlled 503 on registration only, no data loss. Extras: container crash, one DB node, rolling update, gateway before/after.",
     "results/exp1_*, exp1b_*, exp2_*, exp3_*, exp3b_*, rolling_update, gw_before_*, gw_after_* (.json summary + _requests.csv per request); results/logs/*.log; Figure 3.",
     f"The gateway before/after test gave the clearest evidence for my design change: {GWB['failed_requests_total']} failed requests with one replica vs {GWA['failed_requests_total']} with two. "
     f"Note: in the 'before' run clients kept failing until {GWB['client_last_failure_done_s']} s although the pod was Ready after {GWB['k8s_recovery_s']} s; I observed this but did not find its root cause "
     "(suspects: restart under a request backlog on one CPU-limited replica)."],
    [f"RPO/RTO table completed (Part E): replica RPO 0, backups every 5 min (RPO <= 5 min), measured full recovery about {dr_recovery:.0f} s vs 15 min target. "
     "HA and DR differ in the failures they address, the metrics (availability vs RPO/RTO) and the mechanism (redundancy vs independent copies + procedure).",
     f"results/dr_full_recovery.json; dr/restore-job.yaml; k8s/60-backup.yaml; results/backups/*.json (CronJob output on the host); results/exp3b_one_database_node.json.",
     f"Restore took {restore_s:.0f} s of the {dr_recovery:.0f} s because my restore replays records one by one; that part grows with data size, so for production I would use bulk restore or PITR."],
    ["Most important change: run the API gateway with 2 replicas (and plan a 2nd load balancer). It is the largest single contributor to unavailability.",
     f"Availability {p(what['Given design (1 LB, 1 API gateway)'], 4)} -> {p(what['+ 2nd API gateway replica'], 4)} (target met); measured failure: {GWB['failed_requests_total']}/{GWB['whole_run']['total']} failed (1 replica) vs "
     f"{GWA['failed_requests_total']}/{GWA['whole_run']['total']} (2 replicas); recovery of the Registration tier {E2['k8s_recovery_s']} s; DR {dr_recovery:.0f} s < 15 min.",
     f"I did not spend redundancy where it adds almost nothing (3rd Auth/Registration/DB replica: <0.003 points) and a 2nd LB is still required for {p(what['+ 2nd gateway AND 2nd load balancer'], 4)} but cannot be shown in kind, so it is a recommendation, not a measurement."],
]
for t, (s_, e_, d_) in zip(sec, text):
    set_cell(t.rows[SUMMARY].cells[1], s_); set_cell(t.rows[EVID].cells[1], e_); set_cell(t.rows[DEC].cells[1], d_)

tI = find_table("Individual evidence")
ivals = {
    "My unique technical decision": "Registration stays Ready during a DB outage and degrades gracefully (HTTP 503 + Retry-After, circuit breaker after 3 failures, 150 ms DB timeout), and "
                                    "registration ids are deterministic so gateway retries are idempotent. Gateway given 2 replicas as the key availability fix.",
    "My own implementation / calculation evidence": f"Own services (app/service.py), manifests, scripts/availability.py ({p(e2e, 4)} -> {p(what['+ 2nd API gateway replica'], 4)}), own experiment run on "
                                                    "2026-10-25 with per-request logs (results/*_requests.csv), own DR drill (results/dr_full_recovery.json).",
    "Exact file, code, commit, notebook, or log reference": f"k8s/40-registration.yaml (SHA-256 prefix {yaml_sha}, image aitu-sim:1.2); scripts/run_experiments.py; results/exp2_all_registration_replicas.json; "
                                                           "results/logs/run_all.log. No git commit exists (folder is not a repository).",
    "One problem I personally encountered and how I solved it": "First baseline showed p50 = 2,055 ms although the pods answered in 3 ms. Cause: the Windows client resolved 'localhost' via IPv6 first; "
                                                                "fixed by using 127.0.0.1 (p50 12 ms). Later, new-TCP-connection-per-request exhausted Windows ephemeral ports and stalled Docker Desktop's "
                                                                "port proxy for minutes (status 0 on every request) - fixed with keep-alive connections, and by running overload tests inside the cluster. "
                                                                "Also: Python as PID 1 ignored SIGTERM, so every pod waited the full 20 s grace period - fixed with a SIGTERM handler.",
    "One limitation of my own solution": "500 req/s is not demonstrated (reached ~300-380 req/s with errors); the 'database' is a synthetic 2-node store, not PostgreSQL; kind nodes share one machine, so a real zone/node-hardware "
                                         "failure and a 2nd load balancer are not testable; timings have ~0.3 s resolution; the gateway-before run has an unexplained long failure tail.",
}
for r in tI.rows[1:]:
    k = r.cells[0].text.strip()
    if k in ivals:
        set_cell(r.cells[1], ivals[k])

checks = {
    "Completed required tables": "☑", "Architecture / RBD": "☑", "Source code or configuration": "☑", "Execution logs": "☑",
    "Before/after comparison": "☑", "Final technical conclusion": "☑",
}
notes = {
    "Screenshots": " - reproducible evidence provided as logs, per-request CSVs and generated charts (no GUI screenshots)",
    "Links to code": " - local paths only (see section 1); add repository URL after pushing",
}
for para in doc.paragraphs:
    t = para.text.strip()
    for k, mark in checks.items():
        if t.startswith("☐ " + k):
            para.runs[0].text = para.runs[0].text.replace("☐", mark, 1)
    for k, n in notes.items():
        if t.startswith("☐ " + k):
            para.add_run(n).italic = True

doc.save(OUTPUT)
print("saved", OUTPUT)
