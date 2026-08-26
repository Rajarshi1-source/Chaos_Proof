"""GATE 4 — re-score Rev 1's §4 worked example under the corrected scorer.

Hermetic: no cluster, no Prometheus, no database. This is the corpus case that
pins the scoring fix (D3), per the rule that no scoring change ships without one.

Rev 1 scored a non-applicable check as 0.5 partial credit. Rev 2 excludes it and
renormalises the remaining weights over the applicable set.

Every number here is DERIVED from the weights, never copied from the plan. The
plan's published figures are carried only for comparison, because two of them
turned out to be arithmetically wrong (see DISCREPANCIES below) — and a corpus
case that asserted the published number would have pinned the error instead of
the behaviour.

Run:  python -m evals.scoring_worked_example
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "chaos-framework"))

from src.constants import WEIGHTS                      # noqa: E402
from src.scoring.scorer import Check, calculate        # noqa: E402

# Rev 1's §4 worked example, verbatim. `None` marks the check Rev 1 called "N/A"
# and silently scored as 0.5 partial credit.
WORKED_EXAMPLE = {
    "Pod Kill":          {"slo_recovery": 1.0, "alert_validation": 1.0,
                          "resilience_pattern": 1.0, "recovery_completeness": 1.0},
    "Network Latency":   {"slo_recovery": 1.0, "alert_validation": 1.0,
                          "resilience_pattern": 0.5, "recovery_completeness": 1.0},
    "Network Partition": {"slo_recovery": 0.5, "alert_validation": 1.0,
                          "resilience_pattern": 1.0, "recovery_completeness": 0.5},
    "Disk Fill":         {"slo_recovery": 1.0, "alert_validation": 0.0,
                          "resilience_pattern": None, "recovery_completeness": 1.0},
    "CPU Spike":         {"slo_recovery": 1.0, "alert_validation": 1.0,
                          "resilience_pattern": 1.0, "recovery_completeness": 1.0},
    "Container Kill":    {"slo_recovery": 1.0, "alert_validation": 1.0,
                          "resilience_pattern": 1.0, "recovery_completeness": 1.0},
}

# What the docs publish, AFTER the 26 Aug 2026 arithmetic corrections this eval
# produced. Compared, never asserted — if a doc drifts from the weights again,
# it shows up here as a discrepancy instead of silently becoming the truth.
# (Originally published: Network Partition 0.725, Rev1 daily 87.1%,
#  Rev2 disk-fill 0.588, Rev2 daily 86.5% — all three slips corrected in
#  ChaosProof_Implementation_Plan_Rev2.md §4/§C.4, IMPLEMENTATION_PLAN.md,
#  chaosproof-build-plan.html and chaosproof-hypothesis-engine.)
PLAN_REV1 = {"Pod Kill": 1.0, "Network Latency": 0.875, "Network Partition": 0.750,
             "Disk Fill": 0.625, "CPU Spike": 1.0, "Container Kill": 1.0}
PLAN_REV1_DAILY = 87.5
PLAN_REV2_DISK = 0.6667
PLAN_REV2_DAILY = 88.2


def rev1_score(scores: dict[str, float | None]) -> float:
    """Rev 1: N/A silently became 0.5 partial credit, full denominator of 1.0."""
    return sum(WEIGHTS[k] * (0.5 if v is None else v) for k, v in scores.items())


def rev2_checks(scores: dict[str, float | None]) -> list[Check]:
    return [Check(check_type=k, check_name=k, applicable=v is not None,
                  outcome="pass" if v else "fail", score=v)
            for k, v in scores.items()]


def main() -> int:
    failures: list[str] = []
    discrepancies: list[str] = []

    print(f"{'EXPERIMENT':20} {'REV1':>7} {'REV2':>7} {'DENOM':>7}  EXCLUDED")
    rev1_all, rev2_all = [], []
    for name, scores in WORKED_EXAMPLE.items():
        r1 = rev1_score(scores)
        result = calculate(rev2_checks(scores))
        rev1_all.append(r1)
        rev2_all.append(result.score)
        if abs(r1 - PLAN_REV1[name]) > 5e-4:
            discrepancies.append(
                f"§4 {name}: plan publishes {PLAN_REV1[name]}, weights give {r1:.4f}")
        print(f"{name:20} {r1:>7.4f} {result.score:>7.4f} "
              f"{result.weights_denominator:>7.3f}  {','.join(result.excluded) or '-'}")

    d1 = sum(rev1_all) / len(rev1_all) * 100
    d2 = sum(rev2_all) / len(rev2_all) * 100
    print(f"\n{'DAILY AGGREGATE':20} {d1:>7.1f}% {d2:>7.1f}%")

    disk = calculate(rev2_checks(WORKED_EXAMPLE["Disk Fill"]))

    # --- hard assertions on the SCORER's behaviour ----------------------------
    if disk.excluded != ["resilience_pattern"]:
        failures.append(f"disk-fill must exclude resilience_pattern, got {disk.excluded}")
    if abs(disk.weights_denominator - 0.75) > 1e-9:
        failures.append(f"disk-fill denominator must be 0.75 (1.00 - 0.25), "
                        f"got {disk.weights_denominator}")
    expected = (0.35 * 1.0 + 0.25 * 0.0 + 0.15 * 1.0) / 0.75
    if abs(disk.score - expected) > 5e-4:
        failures.append(f"disk-fill {disk.score} != renormalised {expected:.4f}")
    if abs(rev1_score(WORKED_EXAMPLE["Disk Fill"]) - 0.625) > 1e-9:
        failures.append("Rev 1 disk-fill must reproduce at 0.625 — the defect is real")
    for name, scores in WORKED_EXAMPLE.items():
        r = calculate(rev2_checks(scores))
        for c in rev2_checks(scores):
            if not c.applicable and c.check_type not in r.excluded:
                failures.append(f"{name}: non-applicable {c.check_type} not excluded")
    # INVALID poisons the whole experiment: no score, never a zero.
    poisoned = calculate([Check("slo_recovery", "x", True, "invalid", None),
                          Check("alert_validation", "x", True, "pass", 1.0)])
    if poisoned.score is not None or poisoned.status != "invalid":
        failures.append(f"INVALID must produce no score, got {poisoned}")

    # --- documentation discrepancies ------------------------------------------
    if abs(d1 - PLAN_REV1_DAILY) > 0.05:
        discrepancies.append(
            f"§4 daily aggregate: plan publishes {PLAN_REV1_DAILY}%, weights give {d1:.1f}%")
    if abs(disk.score - PLAN_REV2_DISK) > 5e-4:
        discrepancies.append(
            f"§C.4 disk-fill Rev 2: plan claims {PLAN_REV2_DISK}, renormalisation "
            f"gives {disk.score:.4f} (0.50/0.75; {PLAN_REV2_DISK} is 0.50/0.85 — "
            f"the 0.15 completeness weight excluded instead of the 0.25 pattern weight)")
    if abs(d2 - PLAN_REV2_DAILY) > 0.05:
        discrepancies.append(
            f"§C.4 daily aggregate Rev 2: plan claims {PLAN_REV2_DAILY}%, "
            f"renormalisation gives {d2:.1f}%")

    print()
    print(f"disk-fill        Rev 1 0.6250  ->  Rev 2 {disk.score:.4f}   "
          f"(denominator {disk.weights_denominator}, excluded resilience_pattern)")
    print(f"daily aggregate  Rev 1 {d1:.1f}%   ->  Rev 2 {d2:.1f}%")

    if discrepancies:
        print("\nDOCUMENTATION DISCREPANCIES (arithmetic, not scorer behaviour):")
        for d in discrepancies:
            print(f"  - {d}")

    print()
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("GATE 4: scorer assertions hold. Non-applicable checks are excluded and "
          "the remaining weights renormalised — no free half-credit anywhere.")
    return 0 if not discrepancies else 0


if __name__ == "__main__":
    raise SystemExit(main())
