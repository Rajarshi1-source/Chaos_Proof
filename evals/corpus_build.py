"""Authoring tool for the replay corpus. NOT part of the gate.

`evals/replay_eval.py` reads only the committed JSON under `evals/corpus/`.
That separation is deliberate: an eval that generated its own inputs would be
testing the generator against itself and would pass for as long as both halves
were wrong in the same direction. The committed files are the labelled dataset;
this script is how a human authors them.

Run:  python -m evals.corpus_build          (rewrites evals/corpus/*.json)

Every case states WHY it exists. A corpus case without a defect behind it is a
snapshot of current behaviour, and snapshots freeze bugs as readily as they
freeze fixes.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "chaos-framework"))

from src.quality import bundles                                  # noqa: E402
from src.scoring import scorer                                   # noqa: E402

CORPUS_DIR = pathlib.Path(__file__).resolve().parent / "corpus"
T0 = 1_788_000_000.0

# The epoch every corpus bundle is stamped with, except the mismatch case.
# Computed from the current code so the corpus travels with the scorer.
CORPUS_EPOCH = None          # filled in main()


def ticks(client: dict[str, list], server: dict[str, list] | None = None,
          interval_s: float = 5.0) -> list[dict]:
    server = server or {}
    lengths = {len(v) for v in list(client.values()) + list(server.values())}
    assert len(lengths) == 1, "all series must have the same tick count"
    n = lengths.pop()
    out = []
    for i in range(n):
        out.append({
            "t": T0 + i * interval_s,
            "client": {k: v[i] for k, v in client.items() if v[i] is not None},
            "server": {k: v[i] for k, v in server.items() if v[i] is not None},
        })
    return out


def inv(name, metric, comparator, threshold, *, source="k6",
        tolerance_s=None, recover_within_s=None) -> dict:
    d = {"name": name, "source": source, "metric": metric,
         "comparator": comparator, "threshold": threshold}
    if tolerance_s is not None:
        d["tolerance_s"] = tolerance_s
    else:
        d["recover_within_s"] = recover_within_s
    return d


def chk(check_type, outcome, score, *, applicable=True, message="") -> dict:
    return {"check_type": check_type, "check_name": check_type,
            "applicable": applicable, "outcome": outcome, "score": score,
            "message": message}


AVAIL_HOLDS = inv("client_availability_holds", "client_availability", ">=", 0.99,
                  tolerance_s=10)
BREAKER_OPENS = inv("circuit_breaker_opens", "cb_payment_open", ">=", 1,
                    source="prometheus", recover_within_s=90)

FOUR_CHECKS_PASS = [chk("slo_recovery", "pass", 1.0),
                    chk("alert_validation", "pass", 1.0),
                    chk("resilience_pattern", "pass", 1.0),
                    chk("recovery_completeness", "pass", 1.0)]


def case(name, why, *, invariants, client, server=None, load=None, checks=None,
         expected, epoch=None, hypothesis_version=1, git_sha="4c19ba2" * 5 + "abcde",
         cleanup=None, abort=None, preflight=None) -> dict:
    load = {"achieved_rps": 119.4, "dropped_iterations": 0, "coverage": 1.0,
            "target_rps": 120.0, "min_rps_floor": 90.0,
            "script_sha256": "a" * 64, "tool": "k6", "tool_version": "2.2.0",
            "workload_model": "open", **(load or {})}
    bundle = bundles.build(
        experiment=name,
        hypothesis={"version": hypothesis_version,
                    "description": why,
                    "invariants": invariants,
                    "min_rps_floor": load["min_rps_floor"],
                    "abort_conditions": [
                        {"name": "availability_collapse", "source": "k6",
                         "metric": "client_availability", "comparator": "<",
                         "threshold": 0.80}]},
        load=load,
        samples=ticks(client, server),
        checks=checks if checks is not None else FOUR_CHECKS_PASS,
        verdict=expected["verdict"],
        verdict_reason=expected.get("verdict_reason"),
        score=None if expected.get("score") is None else {"score": expected["score"]},
        epoch=epoch or CORPUS_EPOCH,
        cleanup=cleanup or [{"name": "stop_chaosengine", "ok": True},
                            {"name": "verify_steady_state", "ok": True}],
        abort=abort, preflight=preflight, git_sha=git_sha,
    )
    return {"name": name, "why": why, "expected": expected, "bundle": bundle}


def build_all() -> list[dict]:
    ok6 = [1.0] * 6
    cases: list[dict] = []

    # ----- the seven mandated regression cases (§21.2) --------------------- #

    cases.append(case(
        "no_load_zero_traffic",
        "D1, the defect this whole project exists to have caught: with no "
        "traffic every invariant is vacuously satisfiable, so a framework that "
        "reports HELD is certifying a system it never touched.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        load={"achieved_rps": 0.0},
        expected={"verdict": "invalid", "score": None,
                  "reason_contains": "0 rps"}))

    cases.append(case(
        "pattern_not_applicable",
        "D3: a non-applicable check is EXCLUDED and the weights renormalised, "
        "never given 0.5 partial credit. Disk fill is the canonical case. The "
        "expected value is 0.6667 = 0.50/0.75, NOT the 0.588 the plan first "
        "published (that is 0.50/0.85 — the 0.15 completeness weight excluded "
        "instead of the 0.25 pattern weight), and not the 0.625 the half-credit "
        "formula produced. Corrected and verified by GATE 4 on 26 Aug 2026.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[chk("slo_recovery", "pass", 1.0),
                chk("alert_validation", "fail", 0.0),
                chk("resilience_pattern", "pass", None, applicable=False),
                chk("recovery_completeness", "pass", 1.0)],
        expected={"verdict": "held", "score": 0.6667, "weights_denominator": 0.75,
                  "excluded": ["resilience_pattern"]}))

    cases.append(case(
        "empty_promql_series",
        "A missing or empty series is NEVER a passing series. This is the single "
        "most common way a home-grown chaos framework lies to you.",
        invariants=[AVAIL_HOLDS, BREAKER_OPENS],
        client={"client_availability": ok6},
        server={"cb_payment_open": [None] * 6},
        expected={"verdict": "invalid", "score": None,
                  "reason_contains": "could not be measured"}))

    cases.append(case(
        "alert_for_exceeds_slo",
        "D-J: alert validation grades EXCESS latency over the irreducible floor, "
        "not raw detection time. An alert arriving beyond its budget is partial, "
        "never a silent pass — and never a zero either, because the alert did "
        "fire.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[chk("slo_recovery", "pass", 1.0),
                chk("alert_validation", "partial", 0.5,
                    message="detection 130.0s, excess +75.0s over a 5s budget"),
                chk("resilience_pattern", "pass", 1.0),
                chk("recovery_completeness", "pass", 1.0)],
        expected={"verdict": "held", "score": 0.875, "weights_denominator": 1.0}))

    cases.append(case(
        "dropped_iterations_high",
        "Generator saturation is the GENERATOR failing, not the system. 72 "
        "dropped iterations were observed for real on 23 Aug 2026 when "
        "preAllocatedVUs was too low; the gate correctly returned INVALID and "
        "the fix was the generator, never the gate.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        load={"dropped_iterations": 72},
        expected={"verdict": "invalid", "score": None,
                  "reason_contains": "bottleneck"}))

    cases.append(case(
        "abort_mid_experiment",
        "An ABORTED run is not scoreable: the fault was cut short, so the "
        "hypothesis was never given the conditions it speaks about. Score is "
        "None — not zero — and the cleanup log is part of the evidence.",
        invariants=[AVAIL_HOLDS],
        client={"client_availability": [1.0, 1.0, 0.5, 0.5, 0.5, 0.5]},
        checks=[chk("slo_recovery", "invalid", None),
                chk("alert_validation", "invalid", None),
                chk("resilience_pattern", "invalid", None),
                chk("recovery_completeness", "invalid", None)],
        abort={"path": "python_watchdog", "condition": "availability_collapse",
               "observed": 0.5, "threshold": 0.8, "comparator": "<"},
        cleanup=[{"name": "stop_chaosengine", "ok": True},
                 {"name": "delete_chaosengine", "ok": True},
                 {"name": "verify_steady_state", "ok": True}],
        expected={"verdict": "aborted", "score": None, "cleanup_steps": 3}))

    # epoch_mismatch carries a DIFFERENT epoch on purpose.
    cases.append(case(
        "epoch_mismatch",
        "Scoring a bundle under an epoch it was not measured under is REFUSED, "
        "not silently coerced. Quiet re-scoring is how a trend chart becomes "
        "fiction: every number looks reasonable and none are comparable.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        epoch={"epoch_sha256": "0" * 64, "weights": scorer.WEIGHTS,
               "experiment_set": [], "slo_version": 0,
               "scorer_version": "0.0.0-not-a-real-epoch"},
        expected={"verdict": "held", "score": 1.0, "refuses": "EpochMismatch"}))

    # ----- defects found while building this project ----------------------- #

    cases.append(case(
        "breaker_never_opens",
        "GATE 7, as a corpus case: with @CircuitBreaker removed from order-api's "
        "payment call the state gauge publishes but never reaches 1. The "
        "invariant must FALSIFY and name itself — 'never recovered' is what the "
        "CI runner renders as 'never activated'.",
        invariants=[BREAKER_OPENS], client={"client_availability": ok6},
        server={"cb_payment_open": [0, 0, 0, 0, 0, 0]},
        checks=[chk("slo_recovery", "pass", 1.0),
                chk("alert_validation", "pass", 1.0),
                chk("resilience_pattern", "fail", 0.0,
                    message="paymentService never activated (peak 0)"),
                chk("recovery_completeness", "pass", 1.0)],
        expected={"verdict": "falsified", "score": 0.75,
                  "falsified": ["circuit_breaker_opens"]}))

    cases.append(case(
        "partition_breaker_opens",
        "The headline result, held: under a full partition the breaker opens "
        "within its deadline and the fallback keeps checkout available.",
        invariants=[inv("availability_via_fallback", "client_availability", ">=",
                        0.95, tolerance_s=30), BREAKER_OPENS],
        client={"client_availability": [1.0, 0.97, 0.96, 0.99, 1.0, 1.0]},
        # The gauge STAYS at 1 through the window. `circuit_breaker_opens` is a
        # recovery invariant, so it requires the signal to reach its threshold
        # and hold it to the end — a breaker that opened and then closed again
        # inside the window reads as "never recovered" and falsifies. That is
        # the current semantics; see `flapping_recovery_is_falsified` for the
        # same shape asserted deliberately.
        server={"cb_payment_open": [0, 0, 1, 1, 1, 1]},
        expected={"verdict": "held", "score": 1.0}))

    cases.append(case(
        "pattern_metric_absent_is_invalid_not_fail",
        "resilience4j-micrometer missing from the classpath looks exactly like a "
        "breaker that never opened. 'The pattern did not fire' and 'we cannot "
        "see the pattern' are different findings and only one is about the "
        "system, so the check is INVALID and poisons the score rather than "
        "scoring zero.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[chk("slo_recovery", "pass", 1.0),
                chk("alert_validation", "pass", 1.0),
                chk("resilience_pattern", "invalid", None,
                    message="no samples for cb_payment_open — is "
                            "resilience4j-micrometer on the classpath?"),
                chk("recovery_completeness", "pass", 1.0)],
        expected={"verdict": "held", "score": None, "score_status": "invalid"}))

    cases.append(case(
        "sparse_sample_coverage",
        "Execution #15, real: the load plane was killed mid-run and coverage "
        "fell to 44%. Caught by sample coverage rather than the rps floor — the "
        "mean rps over the surviving samples still looked fine.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        load={"coverage": 0.44},
        expected={"verdict": "invalid", "score": None,
                  "reason_contains": "44%"}))

    cases.append(case(
        "client_server_availability_gap",
        "D-A: server-side metrics said 100% while clients saw failures. "
        "Availability is computed CLIENT-side for exactly this reason — a guard "
        "reading server metrics cannot see failures that never reached a server.",
        invariants=[AVAIL_HOLDS,
                    inv("no_server_5xx_storm", "server_5xx_rate", "<=", 1.0,
                        source="prometheus", tolerance_s=10)],
        client={"client_availability": [1.0, 0.4, 0.4, 0.4, 0.4, 1.0]},
        server={"server_5xx_rate": [0.0] * 6},
        expected={"verdict": "falsified",
                  "falsified": ["client_availability_holds"], "score": 0.65,
                  "score_note": "server looked perfectly healthy throughout"},
        checks=[chk("slo_recovery", "fail", 0.0),
                chk("alert_validation", "pass", 1.0),
                chk("resilience_pattern", "pass", 1.0),
                chk("recovery_completeness", "pass", 1.0)]))

    # ----- invariant-kind semantics ---------------------------------------- #

    cases.append(case(
        "recovery_late_falsifies",
        "A recovery invariant's deadline runs from the FIRST BREACH, not from "
        "engine-apply time — Litmus bootstrap sits between the two and a verdict "
        "must speak about the system, not the injector's startup latency.",
        invariants=[inv("replicas_restored", "payment_replicas_available", ">=", 2,
                        source="prometheus", recover_within_s=10)],
        client={"client_availability": ok6},
        server={"payment_replicas_available": [2, 1, 1, 1, 1, 2]},
        expected={"verdict": "falsified", "falsified": ["replicas_restored"],
                  "score": 1.0}))

    cases.append(case(
        "recovery_within_deadline_holds",
        "The same shape inside its deadline. Paired with the case above so a "
        "change to the deadline arithmetic cannot pass both.",
        invariants=[inv("replicas_restored", "payment_replicas_available", ">=", 2,
                        source="prometheus", recover_within_s=15)],
        client={"client_availability": ok6},
        server={"payment_replicas_available": [2, 1, 1, 2, 2, 2]},
        expected={"verdict": "held", "score": 1.0}))

    cases.append(case(
        "never_breached_is_held_not_missing_recovery",
        "The fault did not dent this signal. That is a hold, and the evidence "
        "must say so — otherwise a reader cannot tell a resilient system from an "
        "ineffective fault.",
        invariants=[inv("replicas_restored", "payment_replicas_available", ">=", 2,
                        source="prometheus", recover_within_s=120)],
        client={"client_availability": ok6},
        server={"payment_replicas_available": [2] * 6},
        expected={"verdict": "held", "score": 1.0,
                  "evidence_note": "never breached"}))

    cases.append(case(
        "flapping_recovery_is_falsified",
        "Recovering and then breaking again is not recovery. Scoring the first, "
        "flattering interval would reward a bistable system.",
        invariants=[inv("replicas_restored", "payment_replicas_available", ">=", 2,
                        source="prometheus", recover_within_s=10)],
        client={"client_availability": [1.0] * 7},
        server={"payment_replicas_available": [2, 1, 2, 1, 1, 1, 1]},
        expected={"verdict": "falsified", "falsified": ["replicas_restored"],
                  "score": 1.0}))

    cases.append(case(
        "two_short_breaches_do_not_accumulate",
        "The hold metric is the LONGEST CONSECUTIVE breach, not total breach "
        "time. Two 5s dips is a system absorbing a fault; one 10s dip is a "
        "system that was down. Summing them conflates the two.",
        invariants=[inv("client_availability_holds", "client_availability", ">=",
                        0.99, tolerance_s=6)],
        client={"client_availability": [0.5, 0.5, 1.0, 0.5, 0.5, 1.0]},
        expected={"verdict": "held", "score": 1.0}))

    cases.append(case(
        "unhealed_breach_extends_to_window_end",
        "A breach still open on the final tick is not a 0s breach because the "
        "sampling happened to stop there.",
        invariants=[AVAIL_HOLDS],
        client={"client_availability": [1.0, 1.0, 0.5, 0.5, 0.5, 0.5]},
        expected={"verdict": "falsified",
                  "falsified": ["client_availability_holds"], "score": 1.0}))

    cases.append(case(
        "invalid_outranks_falsified",
        "If one signal could not be measured, the hypothesis as written was not "
        "tested. 'Mostly falsified' is not a verdict.",
        invariants=[AVAIL_HOLDS, BREAKER_OPENS],
        client={"client_availability": [0.0] * 6},
        server={"cb_payment_open": [None] * 6},
        expected={"verdict": "invalid", "score": None}))

    # ----- scoring semantics ------------------------------------------------ #

    cases.append(case(
        "all_checks_invalid_no_score",
        "INVALID produces NO SCORE, not a zero. A zero would drag the aggregate "
        "as if the system had failed, when in fact nothing was measured.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[chk(t, "invalid", None) for t in scorer.WEIGHTS],
        expected={"verdict": "held", "score": None, "score_status": "invalid"}))

    cases.append(case(
        "partial_credit_is_proportional",
        "Partial outcomes flow through the weighted mean unchanged. Pins the "
        "arithmetic itself, so a renormalisation change cannot quietly rescale "
        "every partial.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[chk(t, "partial", 0.5) for t in scorer.WEIGHTS],
        expected={"verdict": "held", "score": 0.5, "score_status": "partial"}))

    cases.append(case(
        "two_checks_excluded_renormalises_further",
        "Renormalisation must hold for more than one exclusion. With both the "
        "pattern and the alert checks inapplicable the denominator is 0.50, and "
        "a formula that special-cased a single exclusion breaks here.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[chk("slo_recovery", "pass", 1.0),
                chk("alert_validation", "pass", None, applicable=False),
                chk("resilience_pattern", "pass", None, applicable=False),
                chk("recovery_completeness", "fail", 0.0)],
        expected={"verdict": "held", "score": 0.7, "weights_denominator": 0.5,
                  "excluded": ["alert_validation", "resilience_pattern"]}))

    cases.append(case(
        "lingering_throttle_is_partial_recovery",
        "A fix that starts a different problem is not a recovery. Throttling "
        "still above the ceiling at the window's end costs half the "
        "completeness check.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[chk("slo_recovery", "pass", 1.0),
                chk("alert_validation", "pass", 1.0),
                chk("resilience_pattern", "pass", 1.0),
                chk("recovery_completeness", "partial", 0.5,
                    message="lingering damage at window end")],
        expected={"verdict": "held", "score": 0.925}))

    # ----- refusals are results -------------------------------------------- #

    cases.append(case(
        "preflight_skipped_no_steady_state",
        "Injecting into an already-sick cluster is not an experiment — you "
        "cannot attribute the damage. SKIPPED is a RECORDED outcome, not a "
        "silent no-op: a framework that quietly does nothing teaches nothing.",
        invariants=[AVAIL_HOLDS], client={"client_availability": [0.5] * 6},
        checks=[],
        preflight={"decision": "skipped",
                   "reason": "steady state not established before injection "
                             "(client availability 0.5000 < 0.99)"},
        expected={"verdict": "skipped", "score": None, "nothing_injected": True}))

    cases.append(case(
        "preflight_denied_by_policy",
        "A denial is a result. The policy file is data a reviewer can argue "
        "with, and widening it to make a run succeed is the one forbidden fix.",
        invariants=[AVAIL_HOLDS], client={"client_availability": ok6},
        checks=[],
        preflight={"decision": "denied", "rule": "deny-single-replica",
                   "reason": "target has fewer than 2 replicas, so any pod-level "
                             "fault is a full outage rather than a resilience test"},
        expected={"verdict": "denied", "score": None, "nothing_injected": True}))

    return cases


def main() -> int:
    global CORPUS_EPOCH
    epoch = {"epoch_sha256": scorer.epoch_sha256([]), **scorer.epoch_material([])}
    CORPUS_EPOCH = epoch

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for stale in CORPUS_DIR.glob("*.json"):
        stale.unlink()

    cases = build_all()
    names = [c["name"] for c in cases]
    assert len(names) == len(set(names)), "duplicate case names"

    for c in cases:
        path = CORPUS_DIR / f"{c['name']}.json"
        path.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"wrote {len(cases)} corpus cases to {CORPUS_DIR}")
    print(f"epoch: {epoch['epoch_sha256'][:12]}  scorer {epoch['scorer_version']}  "
          f"slo v{epoch['slo_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
