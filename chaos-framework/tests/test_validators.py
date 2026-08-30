"""The four validators, as sub-evidence feeding the score.

Every one of them must return `invalid` rather than `pass` when its signal is
missing. A validator that returns pass on an empty result set is how a
home-grown chaos framework tells you your system is resilient when what it
actually measured was nothing.
"""

import pytest
from helpers import make_samples

from src.constants import RECOVERY_HOLD_S, THROTTLE_RATIO_CEILING
from src.validators import checks as V


# --------------------------------------------------------------------------- #
# slo_recovery — restored AND HELD. A single healthy sample flaps.
# --------------------------------------------------------------------------- #

def test_slo_recovery_invalid_when_no_client_samples():
    check = V.slo_recovery(make_samples({"client_availability": [None] * 6}))
    assert check.outcome == "invalid"
    assert check.score is None
    assert check.applicable is True          # applicable but unmeasurable


def test_slo_recovery_requires_the_hold_not_just_the_last_sample():
    """Healthy on the final tick but only for 10s of a required 30s. Accepting
    this is how a flapping service scores as recovered."""
    ss = make_samples({"client_availability": [0.5, 0.5, 0.5, 0.5, 1.0, 1.0]})
    check = V.slo_recovery(ss)
    assert check.outcome == "partial"
    assert check.score == 0.5


def test_slo_recovery_passes_when_held_long_enough():
    n = int(RECOVERY_HOLD_S / 5) + 2
    ss = make_samples({"client_availability": [0.5, 0.5] + [1.0] * n})
    check = V.slo_recovery(ss)
    assert check.outcome == "pass"
    assert check.score == 1.0


def test_slo_recovery_fails_when_still_broken_at_the_end():
    ss = make_samples({"client_availability": [1.0, 1.0, 1.0, 1.0, 0.5, 0.5]})
    check = V.slo_recovery(ss)
    assert check.outcome == "fail"
    assert check.score == 0.0


def test_slo_recovery_ignores_gaps_rather_than_treating_them_as_failures():
    n = int(RECOVERY_HOLD_S / 5) + 2
    ss = make_samples({"client_availability": [1.0, None] + [1.0] * n})
    assert V.slo_recovery(ss).outcome == "pass"


# --------------------------------------------------------------------------- #
# alert_validation — graded on EXCESS latency, the part the system controls.
# --------------------------------------------------------------------------- #

def test_alert_validation_not_applicable_when_no_alert_expected():
    check = V.alert_validation([], {}, 0.0)
    assert check.applicable is False
    assert check.score is None               # excluded, never partial-credited


def test_alert_validation_fails_when_the_expected_alert_never_fired():
    check = V.alert_validation(["TargetPodEvicted"], {"TargetPodEvicted": None}, 100.0)
    assert check.outcome == "fail"
    assert check.score == 0.0
    assert check.details["irreducible_latency_s"] == V.IRREDUCIBLE_LATENCY_S


def test_alert_within_the_irreducible_floor_is_a_full_pass():
    """55s of a 60s SLO is scrape interval + evaluation interval + `for:`. It is
    physics, not slowness, and grading it as a miss would grade the Prometheus
    config rather than the system."""
    check = V.alert_validation(["A"], {"A": 100.0 + V.IRREDUCIBLE_LATENCY_S}, 100.0)
    assert check.outcome == "pass"
    assert check.score == 1.0
    assert check.details["excess_latency_s"] == pytest.approx(0.0)


def test_alert_beyond_the_budget_is_partial_not_a_zero():
    late = 100.0 + V.SLO_ALERT_LATENCY_S + 30
    check = V.alert_validation(["A"], {"A": late}, 100.0)
    assert check.outcome == "partial"
    assert check.score == 0.5
    assert check.details["excess_latency_s"] > 0


def test_alert_validation_partial_when_only_some_expected_alerts_fired():
    check = V.alert_validation(["A", "B"], {"A": 120.0, "B": None}, 100.0)
    assert check.outcome == "partial"
    assert check.score == 0.5


def test_irreducible_latency_is_below_the_alert_slo():
    """D-J's arithmetic. If the floor exceeded the SLO the rule could never pass,
    and grading against it would be grading an impossibility."""
    assert V.IRREDUCIBLE_LATENCY_S < V.SLO_ALERT_LATENCY_S


# --------------------------------------------------------------------------- #
# resilience_pattern — the check GATE 7 turns red.
# --------------------------------------------------------------------------- #

def test_pattern_not_applicable_when_the_experiment_asserts_none():
    check = V.resilience_pattern(make_samples({"client_availability": [1.0]}), None, None)
    assert check.applicable is False
    assert check.score is None


def test_pattern_invalid_when_the_metric_is_absent_entirely():
    """resilience4j-micrometer missing from the classpath looks exactly like
    this. It must be INVALID, not a fail: 'the breaker did not open' and 'we
    cannot see the breaker' are different findings, and only one of them is
    about the system."""
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"cb_payment_open": [None, None, None]})
    check = V.resilience_pattern(ss, "cb_payment_open", "paymentService")
    assert check.outcome == "invalid"
    assert check.score is None
    assert "resilience4j-micrometer" in check.message


def test_pattern_fails_when_present_but_never_activated():
    """The metric publishes, the breaker just never opened — which is what
    removing @CircuitBreaker from order-api's payment call produces."""
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"cb_payment_open": [0, 0, 0]})
    check = V.resilience_pattern(ss, "cb_payment_open", "paymentService")
    assert check.outcome == "fail"
    assert check.score == 0.0
    assert "never activated" in check.message


def test_pattern_read_as_max_over_time_not_as_an_instant():
    """A breaker that opened and closed inside the window is a PASS. Reading the
    gauge at the wrong instant would record the correct behaviour as a failure."""
    ss = make_samples({"client_availability": [1.0] * 5},
                      {"cb_payment_open": [0, 0, 1, 0, 0]})
    check = V.resilience_pattern(ss, "cb_payment_open", "paymentService")
    assert check.outcome == "pass"
    assert check.score == 1.0


# --------------------------------------------------------------------------- #
# recovery_completeness — a fix that starts a different problem is not recovery.
# --------------------------------------------------------------------------- #

def test_completeness_invalid_without_any_golden_signal():
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"target_restarts": [None] * 3, "throttle_ratio": [None] * 3})
    check = V.recovery_completeness(ss)
    assert check.outcome == "invalid"
    assert check.score is None


def test_completeness_passes_on_a_clean_end_state():
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"target_restarts": [0, 0, 0], "throttle_ratio": [0.4, 0.1, 0.0]})
    check = V.recovery_completeness(ss)
    assert check.outcome == "pass"
    assert check.score == 1.0


def test_completeness_partial_when_one_signal_is_still_dirty():
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"target_restarts": [0, 0, 0],
                       "throttle_ratio": [0.4, 0.4, THROTTLE_RATIO_CEILING + 0.2]})
    check = V.recovery_completeness(ss)
    assert check.outcome == "partial"
    assert check.score == 0.5


def test_completeness_fails_when_both_signals_are_dirty():
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"target_restarts": [0, 1, 2],
                       "throttle_ratio": [0.4, 0.4, 0.9]})
    check = V.recovery_completeness(ss)
    assert check.outcome == "fail"
    assert check.score == 0.0


def test_completeness_reads_the_final_value_not_the_worst():
    """Throttling during the fault is expected — that is the fault working.
    Grading the peak would fail every CPU experiment by construction."""
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"target_restarts": [0, 0, 0], "throttle_ratio": [0.9, 0.5, 0.01]})
    assert V.recovery_completeness(ss).outcome == "pass"
