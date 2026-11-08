"""Live demonstration of three endterm scenarios (also records a transcript for a screen recording).

  1  service failure: kill a Registration pod under load and watch detection (alert) and recovery
  2  security / misuse: unauthorized access and broken-authorization attempts, baseline versus endterm, with audit events
  3  graceful degradation: Course service down, timetable still answered from the stale cache

Usage: python scripts/endterm_demo.py [--fast]     transcript: results/endterm/demo_transcript.txt
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import endterm_experiments as X
import endterm_load as L
import obs

FAST = "--fast" in sys.argv
LINES = []


def say(text=""):
    print(text, flush=True)
    LINES.append(text)


def pause(seconds):
    time.sleep(0.3 if FAST else seconds)


def scenario_failure(st):
    say("\n=== Scenario 1: service failure (one Registration instance) ===")
    st.restore_state()
    collector = obs.Collector(st.ns, interval=1.0, scrape_metrics=True)
    collector.start()
    gen = L.LoadGen(st.port, 40, X.next_users(150), users=150).start()
    pause(6)
    pod = st.pods("registration")[0]
    say(f"Killing {pod} (kubectl delete --force --grace-period=0) while 40 req/s are flowing ...")
    t0 = time.time()
    st.k("delete", "pod", pod, "--grace-period=0", "--force", check=False)
    seen = set()
    while time.time() - t0 < 14:
        time.sleep(1)
        counts = st.ready_counts()
        fired = X.ENGINE.evaluate(collector.series, collector.ready_series, t0 - 1, time.time())
        for a in fired:
            if a["alert"] not in seen:
                seen.add(a["alert"])
                say(f"  +{a['fired_at'] - t0:4.1f}s ALERT {a['alert']} ({a['severity']})")
        recent = [r for r in gen.records if r[0] >= time.time() - 1]
        bad = sum(1 for r in recent if not 200 <= r[2] < 300)
        say(f"  +{time.time() - t0:4.1f}s registration pods ready={counts.get('registration', 0)}/2  last-second requests={len(recent)} failed={bad}")
    gen.finish()
    collector.stop()
    s = L.summarize(gen.records, t0)
    say(f"Result: {s['total']} requests after the failure, {s['failed']} failed, success {s['success_pct']}%, p95 {s['p95_ms']} ms")
    st.restore_state()


def scenario_security():
    say("\n=== Scenario 2: unauthorized access and broken authorization ===")
    for name in ("baseline", "endterm"):
        st = X.STACKS[name]
        att, vic = "S9701", "S9702"
        tok_a, tok_v = L.login(st.port, att), L.login(st.port, vic)
        L.http_call(st.port, "POST", "/registrations", {"student_id": vic, "course_code": "CS401"}, tok_v)
        say(f"-- {name} ({'midterm configuration' if name == 'baseline' else 'endterm implementation'})")
        probes = [("write without token", "POST", "/registrations", {"student_id": vic, "course_code": "CS312"}, None),
                  ("write for another student", "POST", "/registrations", {"student_id": vic, "course_code": "CS312"}, tok_a),
                  ("read another student's data", "GET", f"/registrations/{vic}", None, tok_a),
                  ("login without password", "POST", "/auth/login", {"student_id": vic}, None),
                  ("malformed JSON", "POST", "/registrations", None, tok_a)]
        for label, method, path, body, token in probes:
            t, lat, status, resp, headers = L.http_call(st.port, method, path, body, token, raw_body="{oops" if label == "malformed JSON" else None,
                                                        headers={"X-Request-ID": f"demo-{name}-{label[:5].replace(' ', '')}"})
            verdict = "DENIED " if status in (400, 401, 403, 404, 429) else "ALLOWED"
            say(f"   {verdict} HTTP {status:3d}  {label}")
    st = X.STACKS["endterm"]
    say("-- audit events recorded by the endterm services (last 6):")
    lines = []
    for app in ("gateway", "registration", "auth"):
        for pod in st.pods(app):
            for line in st.k("logs", pod, "--tail=200", check=False).splitlines():
                if '"audit": true' in line:
                    try:
                        lines.append(json.loads(line))
                    except ValueError:
                        continue
    for rec in sorted(lines, key=lambda r: r["ts"])[-6:]:
        say(f"   {rec['event']:<22} svc={rec['svc']:<12} rid={rec['rid']} subject={rec.get('subject', '-')}")


def scenario_degradation(st):
    say("\n=== Scenario 3: graceful degradation (Course service down) ===")
    st.restore_state()
    for path in ("/timetable?group=SE-2301", "/courses"):
        for _ in range(8):
            L.http_call(st.port, "GET", path)
    say("Scaling the Course service to 0 replicas ...")
    st.k("scale", "deploy/course", "--replicas=0")
    end = time.time() + 90
    while time.time() < end and st.ready_counts().get("course", 0):
        time.sleep(1)
    say("   Course pods ready: " + str(st.ready_counts().get("course", 0)))
    time.sleep(10)
    for path in ("/timetable?group=SE-2301", "/courses", "/timetable?group=SE-2302"):
        t, lat, status, resp, headers = L.http_call(st.port, "GET", path)
        say(f"   GET {path:<28} -> HTTP {status}  X-Degraded={headers.get('x-degraded', '-')}  {'(stale copy)' if isinstance(resp, dict) and resp.get('degraded') else ''}")
    tok = L.login(st.port, "S9703")
    t, lat, status, resp, _ = L.http_call(st.port, "POST", "/registrations", {"student_id": "S9703", "course_code": "CS205"}, tok)
    say(f"   POST /registrations (critical workflow)     -> HTTP {status}")
    st.k("scale", "deploy/course", "--replicas=2")
    st.restore_state()
    say("Course service restored.")


def main():
    X.ensure_proxy()
    st = X.STACKS["endterm"]
    scenario_failure(st)
    scenario_security()
    scenario_degradation(st)
    os.makedirs(X.OUT, exist_ok=True)
    with open(os.path.join(X.OUT, "demo_transcript.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    say("\nTranscript saved to results/endterm/demo_transcript.txt")


if __name__ == "__main__":
    main()
