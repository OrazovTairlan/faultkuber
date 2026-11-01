"""Assignment 4 - analytical part: incident classification, risk matrix, AI anomaly maths, detector replay.

Everything is computed from the synthetic dataset below; results go to results/security/analysis.json.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import detector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "security")

EVENTS = detector.DATASET

STAGES = {
    "09:01": ("Credential attack", "15 failed logins in the first minute from one source (10.20.4.15); no success yet - password guessing / spraying. First indicator of the incident.", "Medium: no access gained yet, but it is the earliest and cheapest point to stop the attack."),
    "09:03": ("Credential attack", "21 more failures from the same source, 36 in total within 3 min; the dataset raises severity to High - the attack is persistent, not a typo.", "High: failure count is rising 15 -> 21, so a success is likely next."),
    "09:04": ("Privilege compromise", "A successful login from the same IP 10.20.4.15 right after 36 failures = valid credentials obtained (account takeover). All later events follow within 14 minutes.", "Critical in my assessment (dataset: High): attacker is now authenticated; this is the point of no return."),
    "09:07": ("Data destruction", "320 files modified, source still 10.20.4.15, three minutes after the login - bulk write activity typical of ransomware staging/encryption start.", "Critical: integrity of research data is being destroyed."),
    "09:08": ("Data destruction", "CPU 95% on Research-Server-01 one minute after the file burst - encryption routine is running (cryptographic work is CPU-bound).", "High: confirms the file activity is automated; resource exhaustion also degrades the service."),
    "09:10": ("Data destruction", "1,200 files encrypted: 3.75x the 320 files modified at 09:07, i.e. about 400 files/min over the 3 minutes since 09:07.", "Critical: impact reached; data unavailable without a backup."),
    "09:12": ("Recovery attack", "Backup deletion attempt from the same host 2 min after encryption - the attacker removes the recovery path so that ransom is the only option.", "Critical: if it succeeds, RPO and RTO targets become impossible."),
    "09:13": ("Exfiltration", "Outbound connection from the server to an unknown IP - possible command-and-control or data theft (double extortion).", "High: a confidentiality breach may carry personal-data and research-IP obligations."),
    "09:15": ("Exfiltration", "Database access spike two minutes after the outbound connection - data collection/staging before upload.", "High: mass reads of the research database; scope of theft unknown."),
    "09:18": ("Persistence", "Antivirus disabled (needs administrative rights) - defence evasion that keeps the foothold and blocks clean-up. Mapped to the nearest allowed stage.", "Critical: attacker holds admin rights and detection is now blind on this host."),
}

LIKELIHOOD_IMPACT = {
    "Credential compromise": (5, 4, "Occurred: 36 failures then a success within 3 minutes; no MFA evidence in the dataset. Impact 4: full account takeover but initially limited to one user's rights."),
    "Ransomware": (4, 5, "Encryption of 1,200 files is already in the dataset; likelihood 4 (not 5) because recurrence depends on the controls added. Impact 5: research data and service unavailable."),
    "Backup destruction": (4, 5, "A deletion attempt at 09:12 proves intent (likelihood 4). Impact 5: it removes the only recovery path - without backups ransomware becomes unrecoverable."),
    "Data exfiltration": (3, 4, "Outbound connection and DB spike are indicators, not proof of theft (likelihood 3). Impact 4: research IP and personal data, with regulatory and reputation cost."),
    "Privilege escalation": (4, 4, "Disabling antivirus needs admin rights, so escalation very likely already happened (4). Impact 4: attacker can switch off defences and reach other systems."),
}
PRIORITY_ORDER = ["Backup destruction", "Ransomware", "Credential compromise", "Privilege escalation", "Data exfiltration"]

AI = {
    "Login failures": (0.10, 0.81), "File modifications": (0.12, 0.96), "CPU usage": (0.20, 0.88),
    "Outbound traffic": (0.15, 0.79), "Database requests": (0.18, 0.91),
}
THRESHOLD = 0.75
EVENT_WEIGHT = {"File modifications": 5, "Login failures": 3, "CPU usage": 2, "Outbound traffic": 4, "Database requests": 4}


def band(score):
    return "Low" if score <= 4 else "Medium" if score <= 9 else "High" if score <= 16 else "Critical"


def minutes(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def build():
    stages = []
    for e in EVENTS:
        st, ev, risk = STAGES[e["time"]]
        stages.append({"time": e["time"], "event": e["event"], "source": e["source"], "dataset_severity": e["severity"],
                       "stage": st, "evidence": ev, "risk": risk})
    risks = []
    for name in PRIORITY_ORDER:
        l, i, why = LIKELIHOOD_IMPACT[name]
        risks.append({"threat": name, "likelihood": l, "impact": i, "score": l * i, "level": band(l * i),
                      "priority": PRIORITY_ORDER.index(name) + 1, "justification": why})
    anomalies = []
    for k, (n, o) in AI.items():
        anomalies.append({"event": k, "normal": n, "observed": o, "delta": round(o - n, 2), "ratio": round(o / n, 1),
                          "anomaly": o >= THRESHOLD, "weight": EVENT_WEIGHT[k], "priority_score": round(o * EVENT_WEIGHT[k], 2)})
    n_anom = sum(a["anomaly"] for a in anomalies)
    ranked = sorted(anomalies, key=lambda a: -a["priority_score"])
    t0, t_login, t_enc, t_bak, t_av = (minutes(x) for x in ("09:01", "09:04", "09:10", "09:12", "09:18"))
    timeline = {"first_indicator_to_compromise_min": t_login - t0, "compromise_to_first_file_burst_min": minutes("09:07") - t_login,
                "compromise_to_encryption_min": t_enc - t_login, "first_indicator_to_backup_attack_min": t_bak - t0,
                "first_indicator_to_av_disabled_min": t_av - t0, "total_incident_window_min": t_av - t0,
                "failed_logins_total": 15 + 21, "failed_login_rate_per_min_first_3min": round((15 + 21) / 3, 1)}
    replay = detector.replay_dataset()
    return {"stages": stages, "risks": risks, "anomalies": anomalies,
            "anomaly_count": n_anom, "anomaly_total": len(anomalies), "anomaly_rate_pct": round(100 * n_anom / len(anomalies), 1),
            "highest_priority_by_score": max(anomalies, key=lambda a: a["observed"])["event"],
            "highest_priority_weighted": ranked[0]["event"], "ranking_weighted": [a["event"] for a in ranked],
            "timeline": timeline, "detector_replay": replay}


if __name__ == "__main__":
    res = build()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "analysis.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k: v for k, v in res.items() if k not in ("stages",)}, indent=1))
