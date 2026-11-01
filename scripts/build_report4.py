"""Fills the Assignment 4 Word template from results/security/*.json (numbers are never typed by hand)."""
import datetime
import hashlib
import json
import os

import docx_helpers as dh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "Assignment_4_AITU_Security_Resilience_Report_Template.docx")
OUTPUT = os.path.join(ROOT, "Assignment_4_AITU_Security_Resilience_Report_Completed.docx")
TODO = "[student to fill in]"
SEC = os.path.join(ROOT, "results", "security")


def load(name, default=None):
    path = os.path.join(SEC, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha(rel):
    with open(os.path.join(ROOT, rel), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


A = load("analysis.json")
D2 = load("drill.json")
D1 = load("history/drill_run1_failopen.json")
SEG = load("segmentation.json")
IMM = load("immutability.json")
PSS = load("pod_security.json", {"attempts": [], "all_rejected": False})
S = D2["steps"]
P1, P2, P3 = D2["P1_credential_attack"], D2["P2_lateral_movement"], D2["P3_impact"]
RPO, RTO, UI = D2["rpo"], D2["rto"], D2["user_impact"]
R1a = next(a for a in P1["alerts"] if a["rule"] == "R1a")
R1b = next(a for a in P1["alerts"] if a["rule"] == "R1b")
rep = dh.Report(TEMPLATE, ROOT)
doc = rep.doc


def mins(sec):
    return f"{sec / 60:.1f} min"


def cum(n):
    s = S[str(n)]
    return s["started_after_impact_s"] + s["duration_s"]


risks = {r["threat"]: r for r in A["risks"]}
n_seg = len(SEG["checks"])
seg_ok = sum(1 for r in SEG["checks"] if r["as_designed"])

tA = rep.table("Time", "Event")
for t in doc.tables:
    if len(t.rows) == 11 and len(t.columns) == 5 and t.rows[0].cells[2].text.strip() == "Attack stage":
        tA = t
rep.fill(tA, [[s["event"], s["stage"], s["evidence"], s["risk"]] for s in A["stages"]], size=7.5, first_col=1)
c = rep.cursor(tA)
tl = A["timeline"]
rd = A["detector_replay"]
c.heading("How the ten events fit together")
c.bullets([
    f"Kill chain in order: credential attack (09:01-09:03, {tl['failed_logins_total']} failures = {tl['failed_login_rate_per_min_first_3min']} per minute) -> account takeover (09:04) -> "
    f"data destruction (09:07-09:10) -> recovery attack (09:12) -> exfiltration (09:13-09:15) -> persistence/defence evasion (09:18). The whole incident lasts {tl['total_incident_window_min']} minutes.",
    f"Dwell time is short: {tl['compromise_to_encryption_min']} minutes from the successful login to 1,200 encrypted files, so a human reading dashboards cannot be the first line of defence; containment has to be automatic or pre-approved.",
    "No event fits reconnaissance or lateral movement. I did not force a label: all events come from one host or one source IP, and the dataset has no east-west flow data, so absence of evidence is not proof that these stages did not happen.",
    "Two events are mapped to the nearest allowed stage: antivirus disabled (09:18) is really defence evasion, classified as persistence; the database spike (09:15) is data collection, classified as exfiltration.",
    f"My own re-rating: the successful login at 09:04 is Critical (dataset: High), because it is the transition from attempts to a confirmed compromise. The deterministic rules in scripts/detector.py fire on {rd['events_covered']} of {rd['events_total']} events; the first CRITICAL alert is at {rd['first_critical_alert']}, "
    f"{rd['minutes_first_critical_before_encryption']} minutes before the encryption at 09:10.",
], size=9)
c.picture("diagrams/incident_timeline.png", 6.8, "Figure A1 - Attack stage per event and the moment each deterministic rule fires (scripts/detector.py replay of the dataset).")

tB = rep.table("Threat", "Likelihood")
order = ["Credential compromise", "Ransomware", "Backup destruction", "Data exfiltration", "Privilege escalation"]
rep.fill(tB, [[f"{risks[n]['likelihood']}", f"{risks[n]['impact']}",
               f"{risks[n]['likelihood']} x {risks[n]['impact']} = {risks[n]['score']} ({risks[n]['level']})",
               f"P{risks[n]['priority']}"] for n in order], size=9, first_col=1)
c = rep.cursor(tB)
c.picture("diagrams/risk_matrix.png", 4.6, "Figure B1 - Position of the five threats on the 5 x 5 matrix; numbers are the priority order.")
c.heading("Scales and justification of every score")
c.text("Likelihood and impact are scored 1-5 (1 rare/negligible ... 5 almost certain/catastrophic); bands as required: 1-4 Low, 5-9 Medium, 10-16 High, 17-25 Critical.", size=9)
c.table([["Threat", "L", "I", "Score", "Why these numbers (own justification)"]] +
        [[r["threat"], str(r["likelihood"]), str(r["impact"]), f"{r['score']} {r['level']}", r["justification"]] for r in A["risks"]],
        widths=[1.2, 0.3, 0.3, 0.8, 4.4], size=8)
c.text("Priority rule: three threats tie at 20. I order ties by (1) what destroys the ability to recover, then (2) impact, then (3) likelihood. "
       "Backup destruction is therefore P1: if it succeeds, ransomware and every other loss become permanent, while with an intact backup the other threats are recoverable inside the RTO "
       "(proved in the drill, Part D). Ransomware is P2, credential compromise P3 (it is the entry point and the cheapest to fix, but it is not irreversible). "
       "Privilege escalation (16) and exfiltration (12) are High, not Critical.", size=9)
res = {"Backup destruction": (2, 5, "vault is immutable (all 7 tamper attempts as root on the node refused); stays High because the impact is still 5 until the air-gapped copy exists"),
       "Ransomware": (3, 3, f"data tier restored from the vault in {mins(RTO['impact_to_registration_service_restored_s'])}, segmentation limits spread"),
       "Credential compromise": (4, 3, f"detected in {R1a['after_first_failed_login_s']:.0f} s and rotated, but MFA/lockout are not built"),
       "Privilege escalation": (3, 3, "Pod Security blocks privileged and hostPath pods in the app namespace; RBAC not hardened"),
       "Data exfiltration": (2, 4, "egress from app and data pods is denied; but the DB path stays readable from Registration")}
c.heading("Residual risk with the controls actually built in the lab")
c.table([["Threat", "Inherent", "Residual (built controls)", "Basis"]] +
        [[n, f"{risks[n]['score']} {risks[n]['level']}", f"{res[n][0]} x {res[n][1]} = {res[n][0] * res[n][1]} "
          f"{'Low' if res[n][0] * res[n][1] <= 4 else 'Medium' if res[n][0] * res[n][1] <= 9 else 'High' if res[n][0] * res[n][1] <= 16 else 'Critical'}", res[n][2]]
         for n in ["Backup destruction", "Ransomware", "Credential compromise", "Privilege escalation", "Data exfiltration"]],
        widths=[1.4, 0.9, 1.4, 3.3], size=8)
c.text("Residual scores are my judgement from the measured evidence, not measurements. The largest remaining risk is credential compromise, because MFA exists only as a design.", size=9)

tC = rep.table("Threat", "Preventive control")
B, DZ = "[BUILT] ", "[DESIGN] "
rep.fill(tC, [
    [DZ + "MFA (TOTP) at the IdP, account lockout after 5 failures, WAF rate limit. " + B + f"Only the gateway is reachable from outside ({seg_ok}/{n_seg} matrix checks); secrets in Kubernetes Secret.",
     B + f"Rules R1a/R1b/R2 on auth logs: warning after {R1a['after_first_failed_login_s']:.0f} s (10th failure), critical after {R1b['after_first_failed_login_s']:.0f} s (20th) in the live test.",
     B + "Rotate the signing secret and restart auth and gateway (invalidates all stolen tokens), drill step 7; " + DZ + "disable the account at the IdP."],
    [B + "Default-deny NetworkPolicy, Pod Security baseline, non-root pods with dropped capabilities; " + DZ + "EDR/antivirus on hosts, least-privilege RBAC.",
     B + f"Black-box probe: 5 failed reads = alert, {UI['monitor_alert_after_impact_s']} s after impact; rules R3/R3b (file bursts) in the dataset replay; " + DZ + "file-integrity monitor in the DB pod.",
     B + f"Immutable vault backup + clean rebuild: data tier restored in {mins(RTO['impact_to_registration_service_restored_s'])}, full return {mins(RTO['impact_to_fully_back_in_production_s'])} (limit 30 min)."],
    [B + f"Vault in its own namespace, no inbound traffic, write-once (chattr +i): {sum(1 for a in IMM['attempts'] if a['blocked'])}/{len(IMM['attempts'])} tamper attempts refused; the writer has no delete right and the sealer applies retention.",
     DZ + "Rule R4 (dataset replay detects the deletion attempt at 09:12); in the lab a node-level rm is blocked but NOT alerted - needs auditd/Falco on /vault.",
     B + "Newest backup before the incident is selected and verified by sha256 + immutable flag (step 4); the backup CronJob is suspended during the incident so it cannot overwrite good copies. " + DZ + "weekly air-gapped copy."],
    [B + "Egress default-deny: pods cannot reach the internet (registration, course, db-0 tested), DB reachable only from Registration, db peer and vault. " + DZ + "DLP, TLS inspection.",
     DZ + "R5 (outbound to non-allow-listed IP) and R8 (DB spike) in the replay; AI outbound-traffic score 0.79. Lab gap: blocked connections are not logged.",
     B + "Evidence snapshot with SHA-256 manifest (step 3) to scope the leak; " + DZ + "key rotation and notification to the data-protection officer / national CERT."],
    [B + f"Pod Security baseline: {sum(1 for a in PSS['attempts'] if a['rejected_by_pod_security'])}/{len(PSS['attempts'])} privileged/hostPath/hostNetwork pods rejected in the app namespace; privileged sealer isolated in the vault namespace. " + DZ + "RBAC audit, JIT admin access.",
     DZ + "R6 (antivirus disabled) in the replay; Kubernetes audit log to the collector.",
     B + "Quarantine by relabel: the compromised pod loses its Service and policy membership in seconds (step 2: " + f"{S['2']['duration_s']} s); " + DZ + "rebuild nodes from clean images."],
], size=7.5, first_col=1)
c = rep.cursor(tC)
c.text("[BUILT] = implemented and tested on the kind platform of Assignment 3; [DESIGN] = proposed, not built (a dashed box in the figure). I keep the two apart deliberately so the report does not claim controls that do not exist.", size=8.5, italic=True, color="52514E")
c.picture("diagrams/security_architecture.png", 6.9, "Figure C1 - Security architecture (scripts/make_security_diagram.py; Mermaid source in diagrams/security_architecture.mmd).")
c.heading("Control flow of the diagram")
c.bullets([
    "1 A user or attacker reaches only the edge zone. Intended flow: WAF/rate limit -> MFA at the IdP -> API gateway (WAF and MFA are design; the gateway is built).",
    "2 The gateway talks only to Auth, Course and Registration; default-deny NetworkPolicy drops every other flow between app pods (verified, see table).",
    "3 Only Registration may reach the two database nodes, which replicate synchronously. Auth and Course cannot reach the data zone.",
    "4 The vault CronJob in its own namespace pulls a dump every 5 minutes and writes data + sha256 manifest; the sealer sets the immutable flag within seconds. Nothing can connect into the vault zone.",
    "5 Logs from edge, app and data flow to the rule engine and the synthetic probe; alerts go to the on-call/SOC person. The AI score is advisory and sits beside, not instead of, the rules.",
    "6 After an incident the recovery environment (clean volumes and DB tier built from versioned YAML) is prepared; the restore job verifies the sha256 of the newest good backup, writes it, and compares ids; 7 traffic is cut over only after verification (drill steps 4-7).",
], size=9)
c.heading("Required architecture elements and their status")
c.table([["Element", "Status", "Evidence"],
         ["MFA", "Design only", "Not implemented; credential attack is detected (rules R1/R2) and recovered (secret rotation) but not prevented. Largest open gap."],
         ["Network segmentation", "Built", f"{seg_ok}/{n_seg} connection checks as designed (results/security/segmentation.json); k8s/security/10-networkpolicies.yaml"],
         ["Immutable backup", "Built (emulated WORM)", f"{len(IMM['attempts'])}/{len(IMM['attempts'])} root tamper attempts refused; k8s/security/20-vault.yaml + security/vault/*.py"],
         ["Monitoring", "Built (minimal)", "Synthetic probe and log collector; no dashboard or paging"],
         ["Intrusion detection", "Built (rules)", f"scripts/detector.py: {rd['events_covered']}/{rd['events_total']} dataset events covered; live brute-force alert in {R1a['after_first_failed_login_s']:.0f}/{R1b['after_first_failed_login_s']:.0f} s"],
         ["Secure failover", "Built (from Assignment 3)", "2 replicas per tier, PDB, rolling update; restored DB nodes stay fail-closed (below)"],
         ["Recovery environment", "Built (clean data tier); whole-namespace rebuild tested in Assignment 3", f"Volumes recreated, DB tier rebuilt from YAML and restored by the vault-restore job, drill total {mins(RTO['impact_to_fully_back_in_production_s'])}; deleting and re-applying the whole namespace took 134 s in Assignment 3"]],
        widths=[1.5, 1.4, 4.1], size=8)
c.heading("Evidence: network segmentation (19 real connection attempts)")
c.table([["From", "To", "Expected", "Observed"]] + [[r["from"], r["to"], r["expected"], r["observed"] + ("" if r["as_designed"] else "  MISMATCH")] for r in SEG["checks"]],
        widths=[2.2, 2.2, 1.2, 1.4], size=7.5)
c.text("The last row is a control: a pod in an unrestricted namespace can reach 1.1.1.1:443, so the BLOCKED results above come from the policy, not from missing internet access.", size=8.5, italic=True, color="52514E")
c.heading("Evidence: immutable backup (root on the node, results/security/immutability.json)")
c.table([["Attempt", "Exit code", "Result"]] + [[a["cmd"].replace("/vault/", "").replace(IMM["target_file"], "<backup>")[:70], str(a["exit"]), a["output"][-55:]] for a in IMM["attempts"]],
        widths=[2.8, 0.8, 3.4], size=7.5)
c.text("This is an emulation: the immutable attribute is enforced by the node kernel and set by a separate privileged sealer; a real deployment would use object-lock/WORM storage in another failure domain (a person with root and CAP_LINUX_IMMUTABLE on that node could still clear the flag).", size=8.5)

tD = rep.table("Step", "Action")
rep.fill(tD, [
    ["Rule engine R1-R8 + synthetic probe, on-call/SOC person",
     f"<= 2 min (T+2). Measured: brute force flagged {R1a['after_first_failed_login_s']:.0f} s (warning) / {R1b['after_first_failed_login_s']:.0f} s (critical) after the first failed login; ransomware impact flagged {S['1']['duration_s']} s after encryption"],
    ["NetworkPolicy quarantine (relabel pod), suspend backup CronJob, Registration scaled to 0 = maintenance mode; operator or automation",
     f"<= 3 min (T+5). Measured: {S['2']['duration_s']} s; quarantined pod -> DB: {S['2']['db_reachable_from_quarantined_pod']}"],
    ["Evidence collector (kubectl logs/get, hex copy of locked files, SHA256SUMS); analyst",
     f"<= 5 min (T+10). Measured: {S['3']['duration_s']} s, {S['3']['files']} files in {S['3']['evidence_dir']}"],
    ["Vault: newest immutable backup before the incident, sha256 + flag check",
     f"<= 2 min (T+12). Measured: {S['4']['duration_s']} s; backup {S['4']['backup']} ({S['4']['backup_age_at_impact_s']} s old), sha256 match {S['4']['manifest_sha256_matches_file']}, immutable {S['4']['immutable_flag_set']}"],
    ["Clean data tier (PVCs recreated) + vault-restore Job",
     f"<= 10 min (T+22). Measured: {S['5']['duration_s']} s for {S['5']['restore_job']['records']} records (restore job {S['5']['restore_job']['restore_s']} s, sequential writes with synchronous replication)"],
    ["Integrity check (id-set sha256 on both nodes vs manifest, functional test); write traffic held",
     f"<= 5 min (T+27). Measured: {S['6']['duration_s']} s; nodes identical {S['6']['nodes_identical']}; all {S['6']['backup_records']} backup records present {S['6']['all_backup_records_present']}"],
    ["Secret rotation, rolling restart of auth/gateway, delete quarantined pod, Registration scaled back to 2, resume backups",
     f"<= 3 min (T+30). Measured: {S['7']['duration_s']} s; functional check {S['7']['functional_check']}"],
], size=7.5, first_col=2)
c = rep.cursor(tD)
c.picture("diagrams/drill_steps.png", 5.6, "Figure D1 - Measured step times against the target budget (results/security/drill.json).")
c.heading("How RPO <= 5 min and RTO <= 30 min are addressed")
c.table([["Requirement", "Mechanism", "Measured in the drill"],
         ["RPO <= 5 min", "Vault backup every 5 min (worst case = one interval); synchronous replica covers single-node loss",
          f"Backup used was {RPO['backup_created_before_impact_s']} s old at impact (limit 300 s). The {RPO['writes_acked_after_backup_before_impact']} writes acknowledged in those seconds were lost - that is the RPO cost; "
          f"{RPO['acked_before_backup_lost']} acknowledged writes from before the backup were lost"],
         ["RTO <= 30 min", "Seven-step plan, budget in the table above (sums to 30 min)",
          f"Data tier restored and verified {mins(RTO['impact_to_registration_service_restored_s'])} after impact; registration back in production {mins(RTO['impact_to_fully_back_in_production_s'])} "
          f"({100 * RTO['impact_to_fully_back_in_production_s'] / 1800:.0f}% of the 30-minute budget)"],
         ["Controlled degradation", "Registration is scaled to zero (maintenance mode) from isolation until verification; timetable, courses and login stay up",
          f"{UI['registration_requests_failed']} registration requests failed (502/503); other endpoints failed: {UI['timetable_courses_login_failed']} of {UI['total_requests']} total requests"],
         ["Encryption window", "Between the file being encrypted and the node being removed, the live process can still answer",
          f"{RPO['acked_between_impact_and_restore_done']} writes were acknowledged between the start of the encryption and the end of the restore; {RPO['lost_of_those_accepted_while_data_missing']} of them are lost (they were written to the encrypted node in the first second, before it was removed)"]],
        widths=[1.2, 2.6, 3.2], size=8)
c.heading("Before / after: four drill runs and what each one exposed")
runs = []
for label, f, fix in (("1 (image 1.2)", "history/drill_run1_failopen.json", "-"),
                      ("2 (image 1.3, marker = file exists)", "history/drill_run2_race.json", "fail-closed marker"),
                      ("3 (image 1.4, marker = record count)", "history/drill_run3_open_during_restore.json", "count-based integrity check"),
                      ("4 final (image 1.4 + maintenance mode)", None, "registration held until verified")):
    d = D2 if f is None else load(f)
    rr = d["rto"]
    runs.append([label, f"{rr['first_successful_registration_write_after_impact_s']} s" if f else f"{rr['first_successful_registration_write_after_impact_s']} s",
                 str(d["steps"]["6"]["nodes_identical"]), mins(rr["impact_to_fully_back_in_production_s"]), fix])
c.table([["Run", "First successful registration write after impact", "Nodes identical at verification", "Impact -> back in production", "Change introduced after this run"]] + runs,
        widths=[1.7, 1.5, 1.2, 1.2, 1.4], size=7.5)
c.text("Column 2 means different things: in runs 1-2 it is the empty restarted nodes accepting writes (a defect); in runs 3-4 it is the still-running process answering during the ~1 s encryption window, before the pod is deleted (unavoidable).", size=8.5, italic=True, color="52514E")
c.bullets([
    "Run 1: the database nodes restarted EMPTY and kept accepting writes (first write after 7.2 s with nothing restored); my verification also failed because users kept writing during the check. Fix: hold write traffic during verification and make a node with missing data report not-ready.",
    "Run 2: one node was still Ready. Cause found in the evidence: the old process was alive when the file was deleted, accepted one more write and re-created records.jsonl, so 'file exists' looked healthy. Fix: the marker stores the number of records and a node whose file has fewer records than the marker is damaged.",
    "Run 3: both nodes stayed not-ready until the volumes were recreated, but the clean empty nodes then served live traffic for about 6 minutes while the restore ran, so users would see an empty history. Fix: Registration is scaled to zero (maintenance mode) during recovery and scaled back only after the integrity check.",
    f"Run 4 (final, results/security/drill.json): {RPO['acked_between_impact_and_restore_done']} acknowledged writes between impact and restore completion, {RPO['lost_of_those_accepted_while_data_missing']} lost (encryption window), nodes identical {S['6']['nodes_identical']}, functional check {S['7']['functional_check']}.",
], size=9)
c.text(f"Honest limits: the drill uses synthetic data ({S['4']['backup_records']} records in the backup used); the restore is sequential at about {S['5']['restore_job']['restore_s'] / S['5']['restore_job']['records'] * 1000:.0f} ms per record, "
       f"so the 10-minute restore budget would be exhausted at roughly {int(600 / (S['5']['restore_job']['restore_s'] / S['5']['restore_job']['records'])):,} records - a real plan needs bulk restore and rehearsal on production-sized data. "
       "The restore job is started by an operator; dataset minutes are compressed to seconds in the lab (results/security/drill.json).", size=9)

an = A["anomalies"]
c = rep.cursor(rep.par("Assume score"))
c.picture("diagrams/anomaly_scores.png", 5.6, "Figure E1 - Normal vs observed anomaly score per signal.")
c.table([["Event", "Normal", "Observed", "Delta", "x normal", ">= 0.75?", "Weight*", "Priority score"]] +
        [[x["event"], f"{x['normal']:.2f}", f"{x['observed']:.2f}", f"+{x['delta']:.2f}", f"{x['ratio']}x", "yes" if x["anomaly"] else "no", str(x["weight"]), f"{x['priority_score']}"] for x in an],
        widths=[1.5, 0.7, 0.8, 0.7, 0.8, 0.8, 0.7, 1.0], size=8)
c.text("*Weight = my impact weight of the signal (1-5): file modifications 5 because they mean irreversible data destruction, outbound traffic and DB requests 4 (confidentiality), login failures 3, CPU 2. Priority score = observed score x weight.", size=8.5, italic=True, color="52514E")
answers = {
    "Calculate the number and rate": (
        f"{A['anomaly_count']} of {A['anomaly_total']} signals are anomalies (observed >= 0.75), a detection rate of {A['anomaly_count']}/{A['anomaly_total']} = {A['anomaly_rate_pct']:.0f}%. "
        "Sensitivity of the result: the margins are small for outbound traffic (0.79, only 0.04 above the threshold); with a threshold of 0.80 the count would be 4 of 5 (80%). "
        "The dataset covers only the attack window, so it cannot say anything about false positives: 100% here is a true-positive rate on an incident, not an accuracy figure."),
    "Identify the highest-priority event": (
        f"File modifications. It has the highest observed score (0.96), the largest jump from normal (+0.84, 8.0x), and the highest weighted priority ({an[1]['priority_score']}, next is database requests {an[4]['priority_score']}): "
        "it is the signal of ongoing encryption, which is irreversible and happens first in the impact stage (09:07, before the encryption at 09:10), so acting on it is what limits the damage. "
        "Weighted ranking: " + " > ".join(A["ranking_weighted"]) + "."),
    "Explain why AI-assisted detection": (
        "A score is a probability-like hint, not a guarantee. (1) It can miss or be evaded: an attacker who encrypts slowly stays below 0.75, and a model trained on normal behaviour drifts when research workloads change. "
        "(2) It cannot explain itself well enough for an incident decision, and false positives would cause outages or alert fatigue. (3) It acts after the event; prevention and recovery come from deterministic controls. "
        f"My evidence: the segmentation matrix ({seg_ok}/{n_seg}), the immutable vault ({len(IMM['attempts'])}/{len(IMM['attempts'])} refusals) and the restore ({mins(RTO['impact_to_registration_service_restored_s'])}) worked with no model at all, and the threshold rules flagged the incident at "
        f"{rd['first_critical_alert']}, seven minutes before the encryption. So the AI score is used as a second opinion that prioritises and adds context, while block, isolate and restore stay rule-based and tested."),
    "Recommend one additional resilience mechanism": (
        "An air-gapped (offline) backup copy kept inside Kazakhstan, written weekly and restore-tested every quarter. Why it fits: research data and grant results are irreplaceable and often cannot be recomputed; "
        "universities have limited budgets and bandwidth, so a tape/disk set at a second campus or in a national data centre is cheaper than a hot site; and personal data of Kazakhstan citizens must be stored on the territory of Kazakhstan under the personal-data law (please verify the exact article with the university's compliance office), "
        "so a foreign cloud bucket is not a safe default. It complements the online immutable vault, whose weakness I showed: a node-level administrator could still clear the flag."),
}
for key, text in answers.items():
    c2 = rep.cursor(rep.par(key))
    c2.text("Answer: " + text, size=9.5)
c = rep.cursor(rep.par("Answer: An air-gapped"))
c.heading("Short conclusion", size=11)
c.text(f"The incident is a fast, five-stage chain: guessed credentials, account takeover, encryption within {tl['compromise_to_encryption_min']} minutes, an attack on backups, then exfiltration and defence evasion. "
       "The risk analysis puts backup destruction first because it decides whether any other loss is recoverable. In the lab, deterministic controls did the real work: default-deny segmentation blocked "
       f"all {seg_ok} of {n_seg} checked flows as designed, the immutable vault refused every tamper attempt, rules flagged the credential attack in {R1a['after_first_failed_login_s']:.0f} s, and the seven-step plan restored the service "
       f"in {mins(RTO['impact_to_fully_back_in_production_s'])} against a 30-minute RTO with {RPO['lost_of_those']} acknowledged writes lost inside the 5-minute RPO window. MFA, EDR and an air-gapped copy remain design-only and are the next investments; "
       "AI scores should advise, not decide.", size=9.5)

t1 = rep.table("Required field")
vals = {"Student full name": TODO, "Student ID": TODO, "Group / cohort": TODO, "Instructor": TODO, "Date of submission": datetime.date.today().strftime("%d / %m / %Y"),
        "Individual variant / task code assigned by instructor": TODO + " (not given in the task text)",
        "Git repository / project link": "Local git repository PycharmProjects/faulttolerancemicroservices (base commit a6b1daa 'completed'); Assignment 4 files are uncommitted - add the remote URL / commit after pushing",
        "Attached code / file names": "scripts/{incident_analysis,detector,security_checks,security_drill,make_security_charts,make_security_diagram,build_report4,docx_helpers}.py, scripts/security_setup.sh, "
                                     "k8s/security/*.yaml, security/vault/*.py, dr/vault-restore-job.yaml, app/service.py (fail-closed DB), k8s/10-db.yaml, results/security/*, diagrams/*.png|mmd"}
for r in t1.rows[1:]:
    k = r.cells[0].text.strip()
    if k in vals:
        rep.set_cell(r.cells[1], vals[k])
t2 = rep.table("Item")
wv = {"Work start date/time": "01.11.2026, 17:50 (UTC+5)", "Work completion date/time": datetime.datetime.now().strftime("%d.%m.%Y, %H:%M") + " (UTC+5)",
      "Main task completed": "Analysis of the synthetic ransomware incident; risk matrix; resilient security architecture implemented on the Assignment 3 platform; timed 7-step recovery drill; AI-anomaly analysis; report.",
      "Tools / technologies used": "Python 3.13, Docker Desktop, kind v0.27, kubectl, Kubernetes NetworkPolicy / Pod Security / CronJob / Job, Linux immutable attribute (chattr +i), matplotlib, python-docx.",
      "Main technical output produced": f"Segmentation ({seg_ok}/{n_seg} checks), immutable vault ({len(IMM['attempts'])}/{len(IMM['attempts'])} refusals), rule detector, drill with RTO {mins(RTO['impact_to_fully_back_in_production_s'])}, completed report."}
for r in t2.rows[1:]:
    k = r.cells[0].text.strip()
    if k in wv:
        rep.set_cell(r.cells[1], wv[k])

sec = [t for t in doc.tables if t.rows[0].cells[0].text.strip() == "Required content"]
texts = [
    [f"All ten events were classified into the required stages (credential attack x2, privilege compromise, data destruction x3, recovery attack, exfiltration x2, persistence); no event supports reconnaissance or lateral movement and I say so rather than force a label. "
     f"The chain from first indicator to encryption takes {tl['first_indicator_to_compromise_min'] + tl['compromise_to_encryption_min']} minutes.",
     "Part A table; Figure A1; scripts/incident_analysis.py -> results/security/analysis.json; scripts/detector.py replay.",
     f"I re-rated the 09:04 login from High to Critical and mapped 'antivirus disabled' to persistence and 'database spike' to exfiltration with a stated reason; the replay shows the first CRITICAL alert {rd['minutes_first_critical_before_encryption']} min before the encryption."],
    ["Five threats scored with Likelihood x Impact on 1-5 scales; three tie at 20, so I added an explicit tie-break (what destroys recoverability first). Backup destruction is P1.",
     "Part B tables and Figure B1; results/security/analysis.json (risks).",
     "I also gave a residual score for the controls that were really built, so the matrix shows what the work achieved and what is still open (credential compromise)."],
    [f"Seven required elements: segmentation, immutable backup, monitoring, IDS, failover and a clean-data-tier recovery environment are built and tested; MFA is design only. Every control in the threat table is tagged [BUILT] or [DESIGN].",
     f"Figure C1, k8s/security/*.yaml, security/vault/*.py; results/security/segmentation.json ({seg_ok}/{n_seg}), immutability.json ({len(IMM['attempts'])} attempts), pod_security.json.",
     "I put the backup system in a separate namespace with no inbound traffic and a privileged sealer isolated from the backup writer, so a compromised application namespace cannot reach or delete the backups."],
    [f"The seven steps ran in the lab and fit the 30-minute budget: full return after {mins(RTO['impact_to_fully_back_in_production_s'])}; backup used was {RPO['backup_created_before_impact_s']} s old (RPO limit 300 s).",
     "results/security/drill.json, drill_requests.csv, drill_run1_failopen.json, results/security/evidence/<stamp>/SHA256SUMS.txt; Figure D1; results/logs/security_drill.log.",
     f"The drill was run four times; each run exposed a flaw (empty DB serving, a re-created file hiding the damage, empty nodes serving users during restore) and the final run restores {S['4']['backup_records']} records with nodes identical = {S['6']['nodes_identical']}."],
    [f"{A['anomaly_count']} of {A['anomaly_total']} signals exceed 0.75 ({A['anomaly_rate_pct']:.0f}%); highest priority: file modifications (0.96, +0.84, 8.0x). AI is advisory; deterministic controls prevent, block and restore.",
     "Part E table, Figure E1, results/security/analysis.json (anomalies).",
     "I added an impact weight per signal (file modifications 5 ... CPU 2) so priority is not just the largest number, and I showed the 0.80-threshold sensitivity (4 of 5)."],
    ["Add an air-gapped, in-country, restore-tested backup copy to the online vault.",
     "Section 'Answer' in Part E; vault design in Figure C1 (dashed box).",
     "Chosen because it removes the one weakness I could demonstrate in my own vault (node-level admin can clear the immutable flag) and fits budget and data-localisation limits of a Kazakhstan university."],
]
for t, (s1, s2, s3) in zip(sec, texts):
    rep.set_cell(t.rows[1].cells[1], s1); rep.set_cell(t.rows[2].cells[1], s2); rep.set_cell(t.rows[3].cells[1], s3)

t3 = rep.table("Individual evidence")
iv = {"My unique technical decision": "Fail-closed data tier plus an immutable, separate-namespace vault; backups are selected by 'newest before the incident' and verified by sha256 and the immutable flag, and the backup job is suspended during an incident.",
      "My own implementation / calculation evidence": f"scripts/incident_analysis.py (classification, risk, anomaly maths), scripts/detector.py, scripts/security_checks.py, scripts/security_drill.py, k8s/security/*, security/vault/*; own run on 2026-11-01 ({D2['started']}).",
      "Exact file, code, commit, notebook, or log reference": f"scripts/incident_analysis.py (SHA-256 prefix {sha('scripts/incident_analysis.py')}), results/security/analysis.json, results/security/drill.json; dataset = Assignment_4 template PDF; "
                                                              "repo base commit a6b1daa, Assignment 4 files uncommitted.",
      "One problem I personally encountered and how I solved it": "My first drill showed the DB nodes restarting empty and accepting writes. My first fix (marker + 'file exists') still failed because the live process re-created the encrypted file and a node looked healthy; I found this by reading the pod readiness in my own evidence snapshot and changed the marker to store the record count. The next run showed users reading an empty history while the restore ran, so I added a maintenance mode (Registration scaled to zero until verified).",
      "One limitation of my own solution": "MFA, EDR, WAF and the air-gapped copy are design only; the immutable vault is an emulation on one node (not object-lock in another failure domain); file-encryption and backup-delete events are not detected live (only by dataset replay); the drill uses ~3,000 synthetic records and compressed timing."}
for r in t3.rows[1:]:
    k = r.cells[0].text.strip()
    if k in iv:
        rep.set_cell(r.cells[1], iv[k])

checks = ["Completed required tables and calculations", "Architecture / RBD / FTA / security diagram", "Source code or configuration files", "Execution logs / experiment results",
          "Before/after comparison", "Final technical conclusion"]
notes = {"Screenshots": " - reproducible evidence provided as logs, per-request CSVs, evidence hashes and generated charts (no GUI screenshots)",
         "Links to code": " - local repository only (commit a6b1daa + uncommitted files); add the remote URL after pushing"}
for para in doc.paragraphs:
    txt = para.text.strip()
    for k in checks:
        if txt.startswith("☐ " + k):
            para.runs[0].text = para.runs[0].text.replace("☐", "☑", 1)
    for k, n in notes.items():
        if txt.startswith("☐ " + k):
            para.add_run(n).italic = True

rep.save(OUTPUT)
print("saved", OUTPUT)
