# Endterm experiment results

| Experiment | Type | Baseline | Endterm |
|---|---|---|---|
| E1 Service / instance failure | fault injection | FAIL (0/3 repetitions) | FAIL (2/3 repetitions) |
| E2 Dependency timeout | fault injection | FAIL (0/3 repetitions) | FAIL (2/3 repetitions) |
| E3 Load spike | abnormal condition (load) | PASS (3/3 repetitions) | FAIL (0/3 repetitions) |
| E4 Monitoring / alert test with graceful degradation | fault injection | FAIL (0/3 repetitions) | FAIL (1/3 repetitions) |
| E5 Unauthorized access | security / misuse | FAIL (0/3 repetitions) | PASS (3/3 repetitions) |
| E6 Abusive or malformed requests | security / misuse | FAIL (0/3 repetitions) | FAIL (1/3 repetitions) |
| E7 Recovery and consistency | fault injection | PASS (3/3 repetitions) | PASS (3/3 repetitions) |

Criteria (passed repetitions / repetitions; median value):

## E1 Service / instance failure

| Criterion | Limit | Baseline | Endterm |
|---|---|---|---|
| success ratio during the run | >= 99.0 % | 3/3 (99.9 %) | 2/3 (99.7 %) |
| all pods Ready again | <= 30 s | 3/3 (3.8 s) | 3/3 (3.7 s) |
| lost acknowledged writes | == 0  | 3/3 (0) | 3/3 (0) |
| automated alert for the lost replica | <= 5 s | 0/3 (None s) - baseline has no alerting | 3/3 (2.0 s) |

## E2 Dependency timeout

| Criterion | Limit | Baseline | Endterm |
|---|---|---|---|
| requests independent of the DB (timetable, courses, login) succeed | >= 99.0 % | 3/3 (100.0 %) | 2/3 (100.0 %) |
| failed DB-dependent requests fail fast (p95 latency) | <= 500 ms | 3/3 (405.8 ms) | 2/3 (308.0 ms) |
| no request slower than 1 s during the fault | <= 1000 ms | 3/3 (476.7 ms) | 2/3 (665.5 ms) |
| registration works again after the block is removed | <= 15 s | 3/3 (2.2 s) | 3/3 (2.0 s) |
| lost acknowledged writes | == 0  | 3/3 (0) | 3/3 (0) |
| automated alert (DatabaseUnavailable / HighErrorRate) | <= 10 s | 0/3 (None s) - baseline has no alerting | 3/3 (1.0 s) |

## E3 Load spike

| Criterion | Limit | Baseline | Endterm |
|---|---|---|---|
| success ratio during the spike | >= 99.0 % | 3/3 (99.7 %) | 0/3 (81.1 %) |
| p95 latency during the spike | <= 500 ms | 3/3 (89.1 ms) | 0/3 (614.7 ms) |
| throughput reaches the offered load (>= 90 %) | >= 90 req/s | 3/3 (95.0 req/s) | 3/3 (95.0 req/s) |
| lost acknowledged writes | == 0  | 3/3 (0) | 3/3 (0) |

## E4 Monitoring / alert test with graceful degradation

| Criterion | Limit | Baseline | Endterm |
|---|---|---|---|
| non-critical reads (timetable, courses) keep succeeding (graceful degradation) | >= 99.0 % | 0/3 (17.4 %) | 3/3 (100.0 %) |
| critical workflow (login, registration) unaffected | >= 99.0 % | 3/3 (100.0 %) | 1/3 (97.0 %) |
| degraded answers are flagged to the client (X-Degraded) | > 0  | 0/3 (0) | 3/3 (367) |
| automated alert for the outage | <= 10 s | 0/3 (None s) - baseline has no alerting | 3/3 (2.0 s) |
| lost acknowledged writes | == 0  | 3/3 (0) | 3/3 (0) |

## E5 Unauthorized access

| Criterion | Limit | Baseline | Endterm |
|---|---|---|---|
| unauthorized attempts that succeeded for the attacker (2xx) | 0/24  | 0/3 (12/24) | 3/3 (0/24) - 1 attempt(s) inconclusive (transport error or 5xx), not counted as success |
| victim's data changed by the attacker's write attempts | False  | 0/3 (True) | 3/3 (False) |
| victim's data returned to the attacker | == 0  | 0/3 (3) | 3/3 (0) |
| every denied attempt left an audit event | >= 12  | 0/3 (0) - baseline has no audit log | 3/3 (24.0) |
| automated alert for the attack | <= 10 s | 0/3 (None s) - baseline has no alerting | 3/3 (0.9 s) |

## E6 Abusive or malformed requests

| Criterion | Limit | Baseline | Endterm |
|---|---|---|---|
| malformed requests answered with 4xx (no 5xx, no crash) | == 0  | 0/3 (2) | 3/3 (0) |
| junk/invalid records stored | == 0  | 0/3 (6) | 3/3 (0) |
| burst from one account is rate limited (429 seen) | >= 1  | 0/3 (0) | 3/3 (707) |
| password guessing is stopped within 5 failures | <= 6  | 0/3 (None) | 3/3 (6) |
| other users unaffected during the abuse (success) | >= 99.0 % | 0/3 (26.2 %) | 1/3 (98.8 %) |
| pods restarted by the abuse | == 0  | 3/3 (0) | 3/3 (0) |
| automated alert for the abuse | <= 10 s | 0/3 (None s) - baseline has no alerting | 3/3 (1.2 s) |

## E7 Recovery and consistency

| Criterion | Limit | Baseline | Endterm |
|---|---|---|---|
| lost acknowledged writes | == 0  | 3/3 (0) | 3/3 (0) |
| duplicate records | == 0  | 3/3 (0) | 3/3 (0) |
| both database nodes identical after recovery | <= 60 s | 3/3 (3.3 s) | 3/3 (3.0 s) |
| replaying all 300 requests creates no new records | == 0  | 3/3 (0) | 3/3 (0) |
| every write acknowledged after bounded retries | == 0  | 3/3 (0) | 3/3 (0) |
