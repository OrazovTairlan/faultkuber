"""Deterministic detection rules (the "IDS/monitoring" layer of the security architecture).

Two modes:
  python scripts/detector.py replay     replay the incident dataset of Assignment 4 and print alerts
  python scripts/detector.py live       tail the auth service logs of the kind cluster and alert on failed-login bursts
The same rule engine serves both modes. Rules are plain thresholds - no ML - on purpose.
"""
import json
import subprocess
import sys
import time

DATASET = [
    {"time": "09:01", "event": "15 failed logins", "source": "10.20.4.15", "severity": "Medium", "kind": "failed_login", "count": 15},
    {"time": "09:03", "event": "21 failed logins", "source": "10.20.4.15", "severity": "High", "kind": "failed_login", "count": 21},
    {"time": "09:04", "event": "Successful login", "source": "10.20.4.15", "severity": "High", "kind": "login_success", "count": 1},
    {"time": "09:07", "event": "320 files modified", "source": "10.20.4.15", "severity": "Critical", "kind": "files_modified", "count": 320},
    {"time": "09:08", "event": "CPU usage increased to 95%", "source": "Research-Server-01", "severity": "High", "kind": "cpu", "count": 95},
    {"time": "09:10", "event": "1,200 files encrypted", "source": "Research-Server-01", "severity": "Critical", "kind": "files_encrypted", "count": 1200},
    {"time": "09:12", "event": "Backup deletion attempt", "source": "Research-Server-01", "severity": "Critical", "kind": "backup_delete", "count": 1},
    {"time": "09:13", "event": "Outbound connection to unknown IP", "source": "Research-Server-01", "severity": "High", "kind": "outbound_unknown", "count": 1},
    {"time": "09:15", "event": "Database access spike", "source": "Research-Server-01", "severity": "High", "kind": "db_spike", "count": 1},
    {"time": "09:18", "event": "Antivirus disabled", "source": "Research-Server-01", "severity": "Critical", "kind": "av_disabled", "count": 1},
]

FAILED_LOGIN_WARN, FAILED_LOGIN_CRIT, WINDOW_S = 10, 20, 180


class Engine:
    """Feeds events (t_seconds, kind, count, source) and returns the alerts they trigger."""

    def __init__(self):
        self.failed = []
        self.fired = set()

    def feed(self, t, kind, count=1, source="?"):
        alerts = []
        if kind == "failed_login":
            self.failed += [t] * count
            self.failed = [x for x in self.failed if t - x <= WINDOW_S]
            n = len(self.failed)
            if n >= FAILED_LOGIN_CRIT and "R1b" not in self.fired:
                self.fired.add("R1b"); alerts.append(("R1b", "CRITICAL", f"{n} failed logins in {WINDOW_S // 60} min - brute force"))
            elif n >= FAILED_LOGIN_WARN and "R1a" not in self.fired and "R1b" not in self.fired:
                self.fired.add("R1a"); alerts.append(("R1a", "WARNING", f"{n} failed logins in {WINDOW_S // 60} min"))
        elif kind == "login_success" and len(self.failed) >= FAILED_LOGIN_WARN:
            alerts.append(("R2", "CRITICAL", f"successful login after {len(self.failed)} failures from {source} - suspected account takeover"))
        elif kind == "files_modified" and count >= 100:
            alerts.append(("R3", "CRITICAL", f"{count} files modified in 1 min - ransomware behaviour"))
        elif kind == "files_encrypted":
            alerts.append(("R3b", "CRITICAL", f"{count} files encrypted"))
        elif kind == "backup_delete":
            alerts.append(("R4", "CRITICAL", "backup deletion attempt - recovery attack"))
        elif kind == "outbound_unknown":
            alerts.append(("R5", "HIGH", "outbound connection to non-allow-listed IP"))
        elif kind == "db_spike":
            alerts.append(("R8", "HIGH", "database request rate far above baseline - possible data collection"))
        elif kind == "av_disabled":
            alerts.append(("R6", "CRITICAL", "endpoint protection disabled"))
        elif kind == "cpu" and count >= 90:
            alerts.append(("R7", "HIGH", f"CPU {count}%"))
        return alerts


def replay_dataset():
    eng = Engine()
    out, first = [], None
    for e in DATASET:
        h, m = e["time"].split(":")
        t = (int(h) * 60 + int(m)) * 60
        for rule, level, msg in eng.feed(t, e["kind"], e["count"], e["source"]):
            first = first if first is not None else t
            out.append({"time": e["time"], "rule": rule, "level": level, "message": msg})
    first_critical = next(a for a in out if a["level"] == "CRITICAL")
    covered = {a["time"] for a in out}
    return {"alerts": out, "events_covered": len(covered), "events_total": len(DATASET),
            "first_alert": out[0]["time"], "first_critical_alert": first_critical["time"],
            "minutes_first_alert_before_encryption": 10 - int(out[0]["time"].split(":")[1]),
            "minutes_first_critical_before_encryption": 10 - int(first_critical["time"].split(":")[1]),
            "uncovered_events": [e["time"] + " " + e["event"] for e in DATASET if e["time"] not in covered]}


def live(ctx="kind-ft-aitu", seconds=120, poll=1.0, stop_after_alert=False):
    """Poll auth pod logs; count HTTP 401 responses as failed logins."""
    eng, seen, alerts = Engine(), set(), []
    end = time.time() + seconds
    while time.time() < end:
        out = subprocess.run(["kubectl", "--context", ctx, "-n", "aitu-sim", "logs", "-l", "app=auth", "--since=60s",
                              "--prefix=true", "--tail=500"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "/auth/login" not in line or '" 401 ' not in line:
                continue
            try:
                rec = json.loads(line[line.index("{"):])
            except ValueError:
                continue
            key = (rec["pod"], rec["ts"], rec["req"])
            if key in seen:
                continue
            seen.add(key)
            for rule, level, msg in eng.feed(rec["ts"], "failed_login", 1, "auth"):
                alerts.append({"detected_at": time.time(), "rule": rule, "level": level, "message": msg})
                print(json.dumps(alerts[-1]), flush=True)
        if stop_after_alert and any(a["level"] == "CRITICAL" for a in alerts):
            break
        time.sleep(poll)
    return alerts


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "replay"
    if mode == "replay":
        print(json.dumps(replay_dataset(), indent=1))
    else:
        live()
