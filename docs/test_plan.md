# Verification and validation plan

| Level | Scope | Command |
|---|---|---|
| Unit | tokens, passwords, lockout, token bucket, breaker, validation, metrics, store, alert engine | `python -m unittest discover -s tests -v` |
| Integration | five roles in-process over HTTP: workflows, authorization, validation, rate limit, correlation, degradation, DB outage | same command |
| System / fault injection | experiments E1-E7, 3 repetitions, baseline vs endterm | `python scripts/endterm_experiments.py --reps 3 --ablation` |

Method: open-loop workload of 50 req/s, injection, 1 s collection of pod readiness and /metrics, data-integrity audit of both database nodes after every run, state restored between runs. Acceptance criteria are identical for both configurations.

## E1 Service / instance failure

* success ratio during the run: >= 99.0 %
* all pods Ready again: <= 30 s
* lost acknowledged writes: == 0
* automated alert for the lost replica: <= 5 s

## E2 Dependency timeout

* requests independent of the DB (timetable, courses, login) succeed: >= 99.0 %
* failed DB-dependent requests fail fast (p95 latency): <= 500 ms
* no request slower than 1 s during the fault: <= 1000 ms
* registration works again after the block is removed: <= 15 s
* lost acknowledged writes: == 0
* automated alert (DatabaseUnavailable / HighErrorRate): <= 10 s

## E3 Load spike

* success ratio during the spike: >= 99.0 %
* p95 latency during the spike: <= 500 ms
* throughput reaches the offered load (>= 90 %): >= 90 req/s
* lost acknowledged writes: == 0

## E4 Monitoring / alert test with graceful degradation

* non-critical reads (timetable, courses) keep succeeding (graceful degradation): >= 99.0 %
* critical workflow (login, registration) unaffected: >= 99.0 %
* degraded answers are flagged to the client (X-Degraded): > 0
* automated alert for the outage: <= 10 s
* lost acknowledged writes: == 0

## E5 Unauthorized access

* unauthorized attempts that succeeded for the attacker (2xx): 0/24
* victim's data changed by the attacker's write attempts: False
* victim's data returned to the attacker: == 0
* every denied attempt left an audit event: >= 12
* automated alert for the attack: <= 10 s

## E6 Abusive or malformed requests

* malformed requests answered with 4xx (no 5xx, no crash): == 0
* junk/invalid records stored: == 0
* burst from one account is rate limited (429 seen): >= 1
* password guessing is stopped within 5 failures: <= 6
* other users unaffected during the abuse (success): >= 99.0 %
* pods restarted by the abuse: == 0
* automated alert for the abuse: <= 10 s

## E7 Recovery and consistency

* lost acknowledged writes: == 0
* duplicate records: == 0
* both database nodes identical after recovery: <= 60 s
* replaying all 300 requests creates no new records: == 0
* every write acknowledged after bounded retries: == 0
