"""The verdict layer. The rule these tests exist to defend is one sentence:
a missing or empty series is never a passing series.
"""

import pytest

from src.hypothesis.engine import (
    HypothesisVerdict,
    Invariant,
    evaluate,
    parse_abort_conditions,
    parse_invariants,
)
from src.measurement.validity import LoadFacts

from helpers import make_samples

VALID = LoadFacts(achieved_rps=119.4, dropped_iterations=0, coverage=1.0)
FLOOR = 90.0


def hold(name="avail", metric="client_availability", source="k6",
         comparator=">=", threshold=0.99, tolerance_s=10.0) -> Invariant:
    return Invariant(name, source, metric, comparator, threshold,
                     tolerance_s=tolerance_s)


def recovery(name="replicas", metric="payment_replicas_available",
             source="prometheus", comparator=">=", threshold=2.0,
             recover_within_s=120.0) -> Invariant:
    return Invariant(name, source, metric, comparator, threshold,
                     recover_within_s=recover_within_s)


# --------------------------------------------------------------------------- #
# The validity gate runs FIRST — before any invariant is looked at.
# --------------------------------------------------------------------------- #

def test_no_load_is_invalid_not_held():
    """The original defect, as a test. With no traffic every invariant is
    vacuously satisfiable, and a framework that reports HELD here is reporting
    that a system it never touched is resilient."""
    ss = make_samples({"client_availability": [1.0] * 6})
    no_load = LoadFacts(achieved_rps=0.0, dropped_iterations=0, coverage=1.0)

    verdict = evaluate([hold()], ss, no_load, FLOOR)

    assert verdict.verdict == "invalid"
    assert verdict.outcomes == []       # not even evaluated
    assert "0 rps" in verdict.reason


def test_validity_gate_precedes_invariants_even_when_they_would_falsify():
    ss = make_samples({"client_availability": [0.0] * 6})
    no_load = LoadFacts(achieved_rps=3.0, dropped_iterations=0, coverage=1.0)
    assert evaluate([hold()], ss, no_load, FLOOR).verdict == "invalid"


def test_generator_saturation_is_invalid_and_says_so():
    ss = make_samples({"client_availability": [1.0] * 6})
    saturated = LoadFacts(achieved_rps=119.0, dropped_iterations=72, coverage=1.0)
    verdict = evaluate([hold()], ss, saturated, FLOOR)
    assert verdict.verdict == "invalid"
    assert "bottleneck" in verdict.reason


def test_sparse_coverage_is_invalid():
    ss = make_samples({"client_availability": [1.0] * 6})
    sparse = LoadFacts(achieved_rps=119.0, dropped_iterations=0, coverage=0.44)
    verdict = evaluate([hold()], ss, sparse, FLOOR)
    assert verdict.verdict == "invalid"
    assert "44%" in verdict.reason


# --------------------------------------------------------------------------- #
# Empty series. The single most common way a home-grown framework lies.
# --------------------------------------------------------------------------- #

def test_missing_series_is_invalid_not_held():
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [None] * 6})
    verdict = evaluate([hold(), recovery("cb", "cb_payment_open", threshold=1.0)],
                       ss, VALID, FLOOR)
    assert verdict.verdict == "invalid"
    cb = next(o for o in verdict.outcomes if o.name == "cb")
    assert cb.outcome == "invalid"
    assert "no samples" in cb.evidence["reason"]


def test_one_invalid_invariant_invalidates_the_whole_verdict():
    """Not 'mostly held'. If one signal could not be measured, the hypothesis as
    written was not tested."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [None] * 6})
    verdict = evaluate([hold(), recovery("cb", "cb_payment_open", threshold=1.0)],
                       ss, VALID, FLOOR)
    assert verdict.verdict == "invalid"
    assert next(o for o in verdict.outcomes if o.name == "avail").outcome == "held"


def test_invalid_outranks_falsified():
    ss = make_samples({"client_availability": [0.0] * 6},
                      {"cb_payment_open": [None] * 6})
    verdict = evaluate([hold(), recovery("cb", "cb_payment_open", threshold=1.0)],
                       ss, VALID, FLOOR)
    assert verdict.verdict == "invalid"


# --------------------------------------------------------------------------- #
# Hold-throughout: the metric is the LONGEST CONSECUTIVE breach, not a count.
# --------------------------------------------------------------------------- #

def test_brief_breach_within_tolerance_holds():
    # 5s ticks: one breached tick is a 0s run; two consecutive is 5s.
    ss = make_samples({"client_availability": [1.0, 1.0, 0.95, 0.95, 1.0, 1.0]})
    verdict = evaluate([hold(tolerance_s=10.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "held"
    assert verdict.outcomes[0].breached_for_s == pytest.approx(5.0)


def test_breach_beyond_tolerance_falsifies():
    ss = make_samples({"client_availability": [1.0, 0.5, 0.5, 0.5, 0.5, 1.0]})
    verdict = evaluate([hold(tolerance_s=10.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "falsified"
    assert verdict.outcomes[0].breached_for_s == pytest.approx(15.0)
    assert verdict.outcomes[0].worst_value == pytest.approx(0.5)


def test_two_short_breaches_do_not_add_up():
    """Total-breach-time would falsify here; longest-consecutive-run does not.
    The distinction matters: two 5s dips is a system absorbing a fault, one 10s
    dip is a system that was down."""
    ss = make_samples({"client_availability": [0.5, 0.5, 1.0, 0.5, 0.5, 1.0]})
    verdict = evaluate([hold(tolerance_s=6.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "held"


def test_unhealed_breach_extends_to_the_window_end():
    """A breach still open on the last tick is not a 0s breach because the run
    happened to stop there."""
    ss = make_samples({"client_availability": [1.0, 1.0, 0.5, 0.5, 0.5, 0.5]})
    verdict = evaluate([hold(tolerance_s=10.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "falsified"
    assert verdict.outcomes[0].breached_for_s == pytest.approx(15.0)


# --------------------------------------------------------------------------- #
# Recovery: deadline counts from the FIRST BREACH, not from engine-apply time.
# --------------------------------------------------------------------------- #

def test_recovery_within_deadline_holds():
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"payment_replicas_available": [2, 1, 1, 2, 2, 2]})
    verdict = evaluate([recovery(recover_within_s=15.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "held"
    assert verdict.outcomes[0].breached_for_s == pytest.approx(10.0)


def test_recovery_after_deadline_falsifies():
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"payment_replicas_available": [2, 1, 1, 1, 1, 2]})
    verdict = evaluate([recovery(recover_within_s=10.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "falsified"
    assert verdict.outcomes[0].breached_for_s == pytest.approx(20.0)


def test_never_breached_is_held_and_labelled():
    """The fault did not dent this signal. That is a hold, not a missing
    recovery — and the evidence has to say which, or a reader cannot tell a
    resilient system from an ineffective fault."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"payment_replicas_available": [2, 2, 2, 2, 2, 2]})
    verdict = evaluate([recovery()], ss, VALID, FLOOR)
    assert verdict.verdict == "held"
    assert verdict.outcomes[0].evidence["note"] == "never breached"


def test_never_recovered_is_falsified_and_labelled():
    """This is GATE 7's shape: a breaker-state gauge that never once reaches 1.
    `never recovered` is what ci_runner turns into 'never activated'."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 0, 0, 0, 0]})
    verdict = evaluate(
        [recovery("circuit_breaker_opens", "cb_payment_open", threshold=1.0,
                  recover_within_s=90.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "falsified"
    outcome = verdict.outcomes[0]
    assert outcome.evidence["note"] == "never recovered"
    assert outcome.worst_value == 0
    assert "circuit_breaker_opens" in verdict.reason


def test_a_later_breach_voids_an_earlier_recovery():
    """Flapping is not recovery. Recovering then breaking again must not be
    scored on the first, flattering interval."""
    ss = make_samples({"client_availability": [1.0] * 7},
                      {"payment_replicas_available": [2, 1, 2, 1, 1, 1, 1]})
    verdict = evaluate([recovery(recover_within_s=10.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "falsified"
    assert verdict.outcomes[0].evidence["note"] == "never recovered"


# --------------------------------------------------------------------------- #
# The two invariant kinds must never be conflated.
# --------------------------------------------------------------------------- #

def test_invariant_must_declare_exactly_one_kind():
    both = {"name": "x", "source": "k6", "metric": "m", "comparator": ">=",
            "threshold": 1, "tolerance_s": 5, "recover_within_s": 10}
    neither = {"name": "x", "source": "k6", "metric": "m", "comparator": ">=",
               "threshold": 1}
    for raw in (both, neither):
        with pytest.raises(ValueError, match="EXACTLY ONE"):
            parse_invariants([raw])


def test_abort_conditions_carry_neither_deadline():
    """An abort is instantaneous by definition — a tolerance window on an abort
    condition is a guard that waits before guarding."""
    aborts = parse_abort_conditions([
        {"name": "availability_collapse", "source": "k6",
         "metric": "client_availability", "comparator": "<", "threshold": 0.80}])
    assert aborts[0].tolerance_s is None
    assert aborts[0].recover_within_s is None


@pytest.mark.parametrize("comparator,value,expected", [
    (">=", 1.0, True), (">=", 0.9, False),
    ("<=", 1.0, True), ("<=", 1.1, False),
    (">", 1.1, True), (">", 1.0, False),
    ("<", 0.9, True), ("<", 1.0, False),
])
def test_comparators(comparator, value, expected):
    assert Invariant("i", "k6", "m", comparator, 1.0, tolerance_s=0).satisfied(value) is expected


def test_unknown_comparator_raises():
    with pytest.raises(ValueError, match="unknown comparator"):
        Invariant("i", "k6", "m", "~=", 1.0, tolerance_s=0).satisfied(1.0)


@pytest.mark.parametrize("comparator,expected", [
    (">=", 0.5), (">", 0.5), ("<=", 1.5), ("<", 1.5),
])
def test_worse_picks_the_value_further_from_satisfying(comparator, expected):
    assert Invariant("i", "k6", "m", comparator, 1.0,
                     tolerance_s=0).worse(0.5, 1.5) == expected


def test_all_held_returns_held_with_no_reason():
    ss = make_samples({"client_availability": [1.0] * 6})
    verdict = evaluate([hold()], ss, VALID, FLOOR)
    assert isinstance(verdict, HypothesisVerdict)
    assert verdict.verdict == "held"
    assert verdict.reason is None


# --------------------------------------------------------------------------- #
# Activation (defect D-D). A pattern is asserted on having FIRED, never on
# still being active — the third invariant kind exists because those are
# different claims and conflating them made the first one unsatisfiable.
# --------------------------------------------------------------------------- #

def activation(name="circuit_breaker_opens", metric="cb_payment_open",
               source="prometheus", comparator=">=", threshold=1.0,
               activates_within_s=90.0) -> Invariant:
    return Invariant(name, source, metric, comparator, threshold,
                     activates_within_s=activates_within_s)


def test_a_breaker_that_opens_then_closes_holds():
    """THE regression case. As a recovery invariant this falsified, because
    recovery voids on any later breach — so a circuit breaker could only pass by
    jamming open forever, which is the failure the pattern exists to prevent."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 1, 1, 0, 0]})
    verdict = evaluate([activation()], ss, VALID, FLOOR)
    assert verdict.verdict == "held"
    assert verdict.outcomes[0].evidence["kind"] == "activation"
    assert verdict.outcomes[0].evidence["activated_after_s"] == pytest.approx(10.0)


def test_the_same_series_falsifies_under_recovery_semantics():
    """Pins the distinction rather than asserting it in a comment: identical
    samples, opposite verdicts, because the two kinds make different claims."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 1, 1, 0, 0]})
    as_recovery = evaluate([recovery("circuit_breaker_opens", "cb_payment_open",
                                     threshold=1.0, recover_within_s=90.0)],
                           ss, VALID, FLOOR)
    assert as_recovery.verdict == "falsified"


def test_a_breaker_that_never_opens_is_falsified():
    """GATE 7's case: @CircuitBreaker removed, so the gauge publishes but never
    leaves 0."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 0, 0, 0, 0]})
    verdict = evaluate([activation()], ss, VALID, FLOOR)
    assert verdict.verdict == "falsified"
    assert verdict.outcomes[0].evidence["note"] == "never activated"


def test_activating_after_the_deadline_is_falsified():
    """A breaker that opens two minutes into a sixty-second fault protected
    nobody. Activation is not 'eventually'."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 0, 0, 0, 1]})
    verdict = evaluate([activation(activates_within_s=10.0)], ss, VALID, FLOOR)
    assert verdict.verdict == "falsified"
    assert verdict.outcomes[0].evidence["note"] == "activated late"


def test_activation_latency_counts_from_the_window_start():
    """Unlike recovery, which counts from the first breach. An activation
    invariant has no breach to count from — the signal being at its resting
    value is the normal state, not a violation."""
    ss = make_samples({"client_availability": [1.0] * 6},
                      {"cb_payment_open": [0, 0, 0, 1, 0, 0]})
    outcome = evaluate([activation()], ss, VALID, FLOOR).outcomes[0]
    assert outcome.breached_for_s == pytest.approx(15.0)


def test_activation_on_the_very_first_sample_holds():
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"cb_payment_open": [1, 0, 0]})
    assert evaluate([activation()], ss, VALID, FLOOR).verdict == "held"


def test_a_missing_series_is_still_invalid_for_an_activation_invariant():
    """The empty-series rule does not get an exception for the new kind."""
    ss = make_samples({"client_availability": [1.0] * 3},
                      {"cb_payment_open": [None, None, None]})
    verdict = evaluate([activation()], ss, VALID, FLOOR)
    assert verdict.verdict == "invalid"


def test_an_invariant_may_declare_only_one_of_the_three_kinds():
    base = {"name": "x", "source": "k6", "metric": "m", "comparator": ">=", "threshold": 1}
    for extra in ({"tolerance_s": 5, "activates_within_s": 9},
                  {"recover_within_s": 5, "activates_within_s": 9},
                  {"tolerance_s": 5, "recover_within_s": 9, "activates_within_s": 1},
                  {}):
        with pytest.raises(ValueError, match="EXACTLY ONE"):
            parse_invariants([base | extra])


def test_activation_is_parsed_from_yaml_shaped_input():
    inv = parse_invariants([{"name": "circuit_breaker_opens", "source": "prometheus",
                             "metric": "cb_payment_open", "comparator": ">=",
                             "threshold": 1, "activates_within_s": 90}])[0]
    assert inv.activates_within_s == 90
    assert inv.tolerance_s is None and inv.recover_within_s is None
