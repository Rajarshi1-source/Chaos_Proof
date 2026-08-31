"""Counterfactual analysis (§17) — the statistical honesty IS the feature.

The single most important behaviour in this file: when the interquartile ranges
overlap, no delta exists. Not a small delta, not a hedged delta — none. Every
test that looks like it is testing arithmetic is really testing that the system
refuses to overclaim.
"""

import pytest

from src.counterfactual import analysis as A
from src.counterfactual import cost as C
from src.counterfactual import runner as CF

SEPARATED_WITH = [120, 140, 131, 125, 138]
SEPARATED_WITHOUT = [5200, 5400, 5310, 5250, 5380]
OVERLAPPING_WITH = [310, 290, 330, 275, 345]
OVERLAPPING_WITHOUT = [300, 340, 285, 360, 295]


def analyse(with_v, without_v, **kw):
    return A.analyse("p", "failed_requests", with_v, without_v, **kw)


# --------------------------------------------------------------------------- #
# The suppression rule.
# --------------------------------------------------------------------------- #

def test_overlapping_iqrs_produce_inconclusive_and_no_delta():
    """Reporting a median delta from overlapping distributions is how
    dashboards become fiction."""
    r = analyse(OVERLAPPING_WITH, OVERLAPPING_WITHOUT)
    assert r.verdict == A.INCONCLUSIVE
    assert r.overlap is True
    assert r.delta_median is None


def test_the_delta_is_absent_from_the_serialised_form_too():
    """A renderer reads the dict, not the object. If `delta_median` survived
    serialisation as a number the suppression would be cosmetic."""
    r = analyse(OVERLAPPING_WITH, OVERLAPPING_WITHOUT)
    assert r.to_dict()["delta_median"] is None


def test_medians_are_still_reported_when_inconclusive():
    """Suppressing the DELTA is not the same as hiding the data. The panel must
    still show both distributions so a reader can see why it is inconclusive."""
    r = analyse(OVERLAPPING_WITH, OVERLAPPING_WITHOUT)
    assert r.with_median is not None and r.without_median is not None
    assert r.with_iqr is not None and r.without_iqr is not None


def test_separated_iqrs_produce_a_delta():
    r = analyse(SEPARATED_WITH, SEPARATED_WITHOUT)
    assert r.verdict == A.PATTERN_EFFECTIVE
    assert r.overlap is False
    assert r.delta_median == pytest.approx(5310 - 131)


def test_touching_iqrs_count_as_overlapping():
    """A boundary case rounded toward a confident answer is the exact failure
    this check prevents."""
    assert A.iqr_overlap((1.0, 2.0), (2.0, 3.0)) is True
    assert A.iqr_overlap((1.0, 2.0), (2.01, 3.0)) is False


def test_below_minimum_repetitions_is_insufficient_not_inconclusive():
    """Different findings: 'we cannot separate the arms' and 'we did not run
    enough'. The second is fixable by running more."""
    r = analyse(SEPARATED_WITH[:2], SEPARATED_WITHOUT[:2])
    assert r.verdict == A.INSUFFICIENT_DATA
    assert r.delta_median is None
    assert "5" in r.reason


def test_min_repetitions_is_five():
    assert A.MIN_REPETITIONS == 5


# --------------------------------------------------------------------------- #
# Direction. A pattern that makes things worse is a real result.
# --------------------------------------------------------------------------- #

def test_a_pattern_that_makes_things_worse_is_flagged_harmful():
    """Folding this into `pattern_effective` with a negative delta would let a
    reader skim past a sign and conclude the opposite of the truth."""
    r = analyse(SEPARATED_WITHOUT, SEPARATED_WITH)
    assert r.verdict == A.PATTERN_HARMFUL
    assert r.delta_median < 0


def test_higher_is_better_metrics_invert_the_comparison():
    """Availability is higher-is-better. Assuming lower-is-better everywhere
    would report a working pattern as harmful."""
    r = A.analyse("p", "availability", [0.99, 0.995, 0.992, 0.991, 0.994],
                  [0.50, 0.52, 0.48, 0.51, 0.49], lower_is_better=False)
    assert r.verdict == A.PATTERN_EFFECTIVE


# --------------------------------------------------------------------------- #
# Interleaving.
# --------------------------------------------------------------------------- #

def test_run_order_alternates_arms():
    """All-with-then-all-without confounds the pattern with time: if the node
    gets busier over the hour the second block looks worse for a reason that
    has nothing to do with the pattern."""
    order = A.interleave(5)
    assert len(order) == 10
    assert order[::2] == ["with_pattern"] * 5
    assert order[1::2] == ["without_pattern"] * 5


def test_plan_pair_is_interleaved_and_numbered():
    spec = _staging_spec()
    plan = CF.plan_pair(spec, repetitions=5)
    assert [a for a, _ in plan][:4] == [
        "with_pattern", "without_pattern", "with_pattern", "without_pattern"]
    assert [r for _, r in plan][:4] == [1, 1, 2, 2]


# --------------------------------------------------------------------------- #
# Safety. Disabling a resilience pattern is the most dangerous thing here.
# --------------------------------------------------------------------------- #

def _staging_spec(**over) -> dict:
    spec = {
        "name": "partition_no_fallback",
        "target": {"namespace": "chaos-staging", "app": "payment-service"},
        "mode": "counterfactual",
        "disable_patterns": ["paymentService.fallback"],
        "abort_conditions": [
            {"name": "availability_collapse", "source": "k6",
             "metric": "client_availability", "comparator": "<", "threshold": 0.80}],
    }
    spec.update(over)
    return spec


def test_a_counterfactual_outside_staging_is_refused():
    """The correct response to this refusal is to move the experiment, never to
    widen the policy rule."""
    spec = _staging_spec(target={"namespace": "target-app", "app": "payment-service"})
    with pytest.raises(CF.CounterfactualSafetyError, match="chaos-staging"):
        CF.plan_pair(spec)


def test_a_spec_not_marked_counterfactual_is_refused():
    """Without the marker the policy engine never applies the staging
    restriction, so this module must not run it either."""
    spec = _staging_spec(mode="standard")
    with pytest.raises(CF.CounterfactualSafetyError, match="counterfactual"):
        CF.plan_pair(spec)


def test_a_pair_with_nothing_to_disable_is_refused():
    """Two identical arms and a misleading chart."""
    spec = _staging_spec(disable_patterns=[])
    with pytest.raises(CF.CounterfactualSafetyError, match="disable_patterns"):
        CF.plan_pair(spec)


def test_a_pair_without_abort_conditions_is_refused():
    spec = _staging_spec(abort_conditions=[])
    with pytest.raises(CF.CounterfactualSafetyError, match="abort"):
        CF.plan_pair(spec)


def test_safety_is_asserted_before_any_flag_is_flipped():
    """A check that runs after the flags are flipped can leave a cluster
    degraded while it raises."""
    spec = _staging_spec(target={"namespace": "target-app", "app": "x"})
    with pytest.raises(CF.CounterfactualSafetyError):
        CF.spec_for_arm(spec, "without_pattern") if CF.plan_pair(spec) else None


# --------------------------------------------------------------------------- #
# The tighter abort threshold — direction-aware, because getting it backwards
# loosens exactly the arm that needs bounding.
# --------------------------------------------------------------------------- #

def test_the_without_arm_tightens_a_less_than_threshold_upward():
    """`availability < 0.80` fires when availability FALLS. Tighter means a
    HIGHER threshold, so the guard trips sooner."""
    spec = CF.tighten_aborts(_staging_spec())
    condition = spec["abort_conditions"][0]
    assert condition["threshold"] > 0.80
    assert condition["tightened_for_arm"] == "without_pattern"


def test_the_without_arm_tightens_a_greater_than_threshold_downward():
    """`error_rate > 0.5` fires when errors RISE. Tighter means a LOWER
    threshold."""
    spec = _staging_spec(abort_conditions=[
        {"name": "errors", "source": "prometheus", "metric": "server_5xx_rate",
         "comparator": ">", "threshold": 0.5}])
    tightened = CF.tighten_aborts(spec)["abort_conditions"][0]
    assert tightened["threshold"] < 0.5


def test_the_with_arm_has_nothing_disabled():
    """The baseline must be the IDENTICAL experiment, or the comparison
    measures two different faults rather than one fault twice."""
    arm = CF.spec_for_arm(_staging_spec(), "with_pattern")
    assert "disable_patterns" not in arm
    assert arm["counterfactual_arm"] == "with_pattern"


def test_the_without_arm_keeps_its_disable_patterns():
    arm = CF.spec_for_arm(_staging_spec(), "without_pattern")
    assert arm["disable_patterns"] == ["paymentService.fallback"]


# --------------------------------------------------------------------------- #
# Only measured runs enter a distribution.
# --------------------------------------------------------------------------- #

def test_an_aborted_arm_is_not_usable():
    """The 'without' arm is designed to fail harder, so it aborts more often.
    Letting an aborted run contribute a truncated failure count would bias the
    arm the abort was protecting."""
    run = CF.ArmRun("without_pattern", 1, 9, "aborted", failed_requests=400)
    assert run.usable is False


def test_an_invalid_arm_is_not_usable():
    run = CF.ArmRun("with_pattern", 1, 9, "invalid", failed_requests=10)
    assert run.usable is False


def test_a_run_with_no_measurement_is_not_usable():
    assert CF.ArmRun("with_pattern", 1, 9, "held", failed_requests=None).usable is False


def test_unusable_runs_are_excluded_from_the_distribution():
    runs = [CF.ArmRun("with_pattern", i, i, "held", failed_requests=100 + i)
            for i in range(1, 6)]
    runs.append(CF.ArmRun("with_pattern", 6, 60, "aborted", failed_requests=99999))
    pair = CF.PairResult("pid", "p", "exp", runs)
    assert 99999 not in pair.values("with_pattern")
    assert len(pair.discarded()) == 1


# --------------------------------------------------------------------------- #
# Cost translation.
# --------------------------------------------------------------------------- #

def test_no_cost_figure_without_a_delta():
    """Currency reads as precision. A cost attached to an inconclusive result
    would be the most persuasive wrong number this system could produce."""
    assert C.estimate(analyse(OVERLAPPING_WITH, OVERLAPPING_WITHOUT)) is None


def test_no_cost_figure_below_minimum_repetitions():
    assert C.estimate(analyse(SEPARATED_WITH[:2], SEPARATED_WITHOUT[:2])) is None


def test_cost_is_produced_for_a_separated_result():
    estimate = C.estimate(analyse(SEPARATED_WITH, SEPARATED_WITHOUT))
    assert estimate is not None
    assert estimate.value_per_incident == pytest.approx((5310 - 131) * 450.0 * 1.0)
    assert estimate.value_per_year == pytest.approx(estimate.value_per_incident * 12)


def test_every_assumption_is_labelled_and_editable():
    """An estimate that is not labelled an estimate is being passed off as a
    measurement."""
    estimate = C.estimate(analyse(SEPARATED_WITH, SEPARATED_WITHOUT))
    assert len(estimate.assumptions) == 3
    for a in estimate.assumptions:
        assert a.to_dict()["tag"] == "[assumption]"
        assert a.editable is True
        assert a.basis, f"{a.key} has no stated basis"


def test_changing_an_assumption_moves_the_figure():
    """The panel is editable precisely so a reader can do this."""
    base = C.estimate(analyse(SEPARATED_WITH, SEPARATED_WITHOUT))
    cheaper = C.default_assumptions()
    cheaper[0].value = 45.0
    moved = C.estimate(analyse(SEPARATED_WITH, SEPARATED_WITHOUT), cheaper)
    assert moved.value_per_incident == pytest.approx(base.value_per_incident / 10)


def test_the_rendered_inconclusive_form_states_no_delta():
    r = analyse(OVERLAPPING_WITH, OVERLAPPING_WITHOUT)
    text = C.render(r, C.estimate(r))
    assert "NO DELTA REPORTED" in text
    assert "INCONCLUSIVE" in text
    assert "₹" not in text


def test_the_rendered_effective_form_carries_its_assumptions():
    r = analyse(SEPARATED_WITH, SEPARATED_WITHOUT)
    text = C.render(r, C.estimate(r))
    assert text.count("[assumption]") == 3
    assert "₹" in text


# --------------------------------------------------------------------------- #
# Quartiles.
# --------------------------------------------------------------------------- #

def test_quartiles_use_the_inclusive_method():
    """At n=5 the exclusive method discards the extremes — 40% of the evidence.
    The choice changes the IQR and therefore changes verdicts, so it is pinned."""
    q1, median, q3 = A.quartiles([1, 2, 3, 4, 5])
    assert (q1, median, q3) == (2.0, 3.0, 4.0)


def test_quartiles_of_a_single_value_do_not_raise():
    assert A.quartiles([7.0]) == (7.0, 7.0, 7.0)


# --------------------------------------------------------------------------- #
# The chaos breaker must not be tripped by the arm that is DESIGNED to fail.
# Surfaced by GATE 10: three consecutive `without_pattern` aborts opened the
# breaker, which then denied the rest of the same pair — including the healthy
# `with_pattern` baseline arms. No counterfactual could ever reach n=5.
# --------------------------------------------------------------------------- #

def test_the_breaker_query_excludes_counterfactual_without_arms():
    """A `without` arm aborting is the PREDICTION, not evidence that the system
    is behaving worse than predicted. Counting it inverts the breaker's own
    stated meaning."""
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1]
              / "src" / "safety" / "budget_gate.py").read_text(encoding="utf-8")
    assert "counterfactual_runs" in source
    assert "'without_pattern'" in source


def test_the_breaker_still_counts_with_arm_aborts():
    """A `with_pattern` abort IS unexpected: the pattern was in place and the
    system ran away anyway. Excluding those would blind the breaker to the
    signal it exists for."""
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1]
              / "src" / "safety" / "budget_gate.py").read_text(encoding="utf-8")
    # The exclusion is scoped to one arm, never to counterfactuals as a class.
    assert "arm = 'without_pattern'" in source
    assert "arm = 'with_pattern'" not in source
