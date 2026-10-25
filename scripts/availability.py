"""Part B - availability of the synthetic AITU platform (parallel + series model).

Rparallel = 1 - (1 - R1)(1 - R2)           (two independent replicas)
Rseries   = product of subsystem availabilities  (every tier is needed for a request)
"""
import json
import os

HOURS_YEAR = 8760
TARGET = 0.999

LB, GW = 0.9995, 0.9990
AUTH = COURSE = REG = 0.9950
DB = 0.9970


def par(*rs):
    q = 1.0
    for r in rs:
        q *= 1 - r
    return 1 - q


def par_cov(r, c):
    """Two units, failover succeeds only with probability c: R + (1-R)*c*R."""
    return r + (1 - r) * c * r


def series(*rs):
    p = 1.0
    for r in rs:
        p *= r
    return p


def downtime_min_year(a):
    return (1 - a) * HOURS_YEAR * 60


def pct(a, digits=4):
    return f"{a * 100:.{digits}f}%"


def build():
    sub = {"Auth": par(AUTH, AUTH), "Course": par(COURSE, COURSE),
           "Registration": par(REG, REG), "Database": par(DB, DB)}
    base = series(LB, GW, *sub.values())

    whatif = {
        "Given design (1 LB, 1 API gateway)": base,
        "+ 2nd API gateway replica": series(LB, par(GW, GW), *sub.values()),
        "+ 2nd load balancer": series(par(LB, LB), GW, *sub.values()),
        "+ 3rd Auth replica": series(LB, GW, par(AUTH, AUTH, AUTH), sub["Course"], sub["Registration"], sub["Database"]),
        "+ 3rd Registration replica": series(LB, GW, sub["Auth"], sub["Course"], par(REG, REG, REG), sub["Database"]),
        "+ 3rd database node": series(LB, GW, sub["Auth"], sub["Course"], sub["Registration"], par(DB, DB, DB)),
        "+ 2nd gateway AND 2nd load balancer": series(par(LB, LB), par(GW, GW), *sub.values()),
    }
    cov = {}
    for c in (1.0, 0.99, 0.95, 0.90):
        cov[f"coverage {int(c * 100)}%"] = series(
            LB, par_cov(GW, c), par_cov(AUTH, c), par_cov(COURSE, c), par_cov(REG, c), par_cov(DB, c))
    unavail = {"Load Balancer": 1 - LB, "API Gateway": 1 - GW, "Auth (2x)": 1 - sub["Auth"],
               "Course (2x)": 1 - sub["Course"], "Registration (2x)": 1 - sub["Registration"],
               "Database (2x)": 1 - sub["Database"]}
    return sub, base, whatif, cov, unavail


if __name__ == "__main__":
    sub, base, whatif, cov, unavail = build()
    print("Parallel subsystems (two independent replicas):")
    for k, v in sub.items():
        print(f"  {k:13s} {pct(v, 6)}   unavailability {1 - v:.3e}")
    print(f"\nEnd-to-end (series) = {pct(LB)} x {pct(GW)} x {pct(sub['Auth'], 4)} x {pct(sub['Course'], 4)} "
          f"x {pct(sub['Registration'], 4)} x {pct(sub['Database'], 4)} = {pct(base, 4)}")
    print(f"Target {pct(TARGET, 2)} met: {base >= TARGET}; downtime {downtime_min_year(base) / 60:.1f} h/year "
          f"(target allows {downtime_min_year(TARGET) / 60:.2f} h/year)")
    tot = sum(unavail.values())
    print("\nShare of total unavailability:")
    for k, v in sorted(unavail.items(), key=lambda kv: -kv[1]):
        print(f"  {k:18s} {v:.3e}  {100 * v / tot:5.1f}%")
    print("\nWhat-if:")
    for k, v in whatif.items():
        print(f"  {k:40s} {pct(v, 4)}  meets 99.9%: {v >= TARGET}  downtime {downtime_min_year(v) / 60:.2f} h/yr")
    print("\nFailover-coverage sensitivity (2 units everywhere, 1 LB):")
    for k, v in cov.items():
        print(f"  {k:14s} {pct(v, 4)}")
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "availability.json")
    with open(out, "w") as f:
        json.dump({"subsystems": sub, "end_to_end": base, "whatif": whatif, "coverage": cov,
                   "unavailability_share": {k: v / tot for k, v in unavail.items()}}, f, indent=2)
