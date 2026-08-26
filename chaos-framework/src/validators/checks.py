"""The four validators, as sub-evidence feeding the score.

The hypothesis is the VERDICT; these four are structured evidence. Each declares
`applicable` — and a non-applicable check is EXCLUDED from the score with the
remaining weights renormalised, never given partial credit.

Every one of them returns `invalid` rather than `pass` when its signal is
missing. A validator that returns pass on an empty result set is the single most
common way a home-grown chaos framework lies to you.
"""

from ..constants import (
    RECOVERY_HOLD_S,
    RESTART_TOLERANCE,
    SLI_WINDOW_S,
    TARGET_SCRAPE_INTERVAL_S,
    THROTTLE_RATIO_CEILING,
)
from ..scoring.scorer import Check

# Alert-rule arithmetic (ruling D-J). PrometheusRule ships `for: 20s` with a 30s
# group interval and 5s target scrape -> irreducible floor 55s, under the 60s SLO.
EVALUATION_INTERVAL_S = 30
RULE_FOR_S = 20
IRREDUCIBLE_LATENCY_S = TARGET_SCRAPE_INTERVAL_S + EVALUATION_INTERVAL_S + RULE_FOR_S
SLO_ALERT_LATENCY_S = 60


def slo_recovery(samples, *, availability_threshold: float = 0.99) -> Check:
    """Client SLI restored AND HELD for RECOVERY_HOLD_S consecutive seconds.
    A single healthy sample flaps; never accept one. Always applicable."""
    series = [(s.sampled_at, s.client.get("client_availability"))
              for s in samples.samples]
    present = [(t, v) for t, v in series if v is not None]
    if not present:
        return Check("slo_recovery", "client SLI restored and held", True, "invalid", None,
                     message="no client availability samples — cannot judge recovery")

    # Longest trailing run that satisfies the threshold.
    hold_s = 0.0
    run_start = None
    for t, v in present:
        if v >= availability_threshold:
            if run_start is None:
                run_start = t
        else:
            run_start = None
    if run_start is not None:
        hold_s = present[-1][0] - run_start

    healthy_at_end = present[-1][1] >= availability_threshold
    if healthy_at_end and hold_s >= RECOVERY_HOLD_S:
        score, outcome = 1.0, "pass"
    elif healthy_at_end:
        score, outcome = 0.5, "partial"      # restored but not yet held long enough
    else:
        score, outcome = 0.0, "fail"

    return Check(
        "slo_recovery", "client SLI restored and held", True, outcome, score,
        expected_value=f"availability >= {availability_threshold} held >= {RECOVERY_HOLD_S}s",
        actual_value=f"final {present[-1][1]:.4f}, held {hold_s:.0f}s",
        message=f"client availability held for {hold_s:.0f}s at window end",
        details={"hold_s": round(hold_s, 1), "samples": len(present)})


def alert_validation(expected_alerts: list[str], fired_at: dict[str, float | None],
                     fault_injected_at: float) -> Check:
    """Expected alert fired, graded on EXCESS latency — the part the system
    controls. Grading raw detection latency mostly grades your own Prometheus
    config, and a 60s target with a `for: 1m` rule is unreachable by construction.

    applicable=False when the experiment expects no alert."""
    if not expected_alerts:
        return Check("alert_validation", "expected alert fired", False, "pass", None,
                     message="experiment declares no expected alert")

    found = {name: ts for name, ts in fired_at.items() if ts is not None}
    if not found:
        return Check(
            "alert_validation", "expected alert fired", True, "fail", 0.0,
            expected_value=f"{expected_alerts} fire within {SLO_ALERT_LATENCY_S}s",
            actual_value="no expected alert fired",
            message=f"none of {expected_alerts} fired",
            details={"irreducible_latency_s": IRREDUCIBLE_LATENCY_S})

    earliest = min(found.values())
    detection = earliest - fault_injected_at
    excess = detection - IRREDUCIBLE_LATENCY_S
    budget = SLO_ALERT_LATENCY_S - IRREDUCIBLE_LATENCY_S     # what the system controls

    if len(found) < len(expected_alerts):
        score, outcome = 0.5, "partial"
    elif excess <= budget:
        score, outcome = 1.0, "pass"
    else:
        score, outcome = 0.5, "partial"

    return Check(
        "alert_validation", "expected alert fired", True, outcome, score,
        expected_value=f"excess <= {budget}s (SLO {SLO_ALERT_LATENCY_S}s - "
                       f"irreducible {IRREDUCIBLE_LATENCY_S}s)",
        actual_value=f"detection {detection:.1f}s, excess {excess:+.1f}s",
        message=f"{len(found)}/{len(expected_alerts)} expected alerts fired; "
                f"detection {detection:.1f}s of which {IRREDUCIBLE_LATENCY_S}s irreducible",
        details={"detection_latency_s": round(detection, 1),
                 "irreducible_latency_s": IRREDUCIBLE_LATENCY_S,
                 "excess_latency_s": round(excess, 1),
                 "fired": {k: round(v, 1) for k, v in found.items()}})


def resilience_pattern(samples, pattern_metric: str | None,
                       pattern_name: str | None) -> Check:
    """The named pattern transitioned as designed. Read as max_over_time across
    the whole window, not an instant query: a circuit breaker that opened and
    closed inside the sampling interval is a PASS, and an instant read at the
    wrong moment records it as a failure.

    applicable=False when the experiment exercises no resilience pattern — disk
    fill being the canonical case. That exclusion is why renormalisation exists."""
    if not pattern_metric:
        return Check("resilience_pattern", "resilience pattern activated", False,
                     "pass", None,
                     message="experiment exercises no resilience pattern")

    values = [s.server.get(pattern_metric) for s in samples.samples]
    present = [v for v in values if v is not None]
    if not present:
        return Check(
            "resilience_pattern", f"{pattern_name} activated", True, "invalid", None,
            message=f"no samples for {pattern_metric} — an unobservable pattern is "
                    "unverifiable; is resilience4j-micrometer on the classpath?")

    peak = max(present)                                  # the max_over_time equivalent
    activated = peak >= 1.0
    return Check(
        "resilience_pattern", f"{pattern_name} activated", True,
        "pass" if activated else "fail", 1.0 if activated else 0.0,
        expected_value=f"max_over_time({pattern_metric}) >= 1",
        actual_value=f"peak {peak:.4g}",
        message=f"{pattern_name} {'activated' if activated else 'never activated'} "
                f"(peak {peak:.4g})",
        details={"peak": peak, "samples": len(present)})


def recovery_completeness(samples) -> Check:
    """No lingering damage at the window's end: no new restarts, throttle ratio
    back within ceiling. A fix that starts a different problem is not a recovery.
    Always applicable."""
    restarts = [s.server.get("target_restarts") for s in samples.samples]
    throttle = [s.server.get("throttle_ratio") for s in samples.samples]
    restarts_present = [v for v in restarts if v is not None]
    throttle_present = [v for v in throttle if v is not None]

    if not restarts_present and not throttle_present:
        return Check("recovery_completeness", "no lingering damage", True, "invalid", None,
                     message="no golden-signal samples — cannot judge completeness")

    final_restarts = restarts_present[-1] if restarts_present else 0.0
    final_throttle = throttle_present[-1] if throttle_present else 0.0
    restarts_ok = final_restarts <= RESTART_TOLERANCE
    throttle_ok = final_throttle <= THROTTLE_RATIO_CEILING

    score = (1.0 if restarts_ok else 0.0) * 0.5 + (1.0 if throttle_ok else 0.0) * 0.5
    outcome = "pass" if score == 1.0 else ("fail" if score == 0.0 else "partial")

    return Check(
        "recovery_completeness", "no lingering damage", True, outcome, score,
        expected_value=f"restarts <= {RESTART_TOLERANCE}, "
                       f"throttle ratio <= {THROTTLE_RATIO_CEILING}",
        actual_value=f"restarts {final_restarts:.0f}, throttle {final_throttle:.4f}",
        message="clean recovery" if score == 1.0 else "lingering damage at window end",
        details={"final_restarts": final_restarts, "final_throttle_ratio": final_throttle,
                 "window_s": SLI_WINDOW_S})
