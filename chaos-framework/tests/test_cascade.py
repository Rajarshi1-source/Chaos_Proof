"""Cascade DAGs (§18) — conditional triggers, and the abort path.

A cascade you cannot stop is not an experiment, so the teardown and abort
ordering get more tests here than the happy path. Every side effect is injected
as a callable, which is what lets the dangerous paths be exercised without a
cluster — they are the ones least likely to be tried by hand.
"""

import pathlib

import pytest

from src.cascade import executor as E
from src.cascade import model as M

SCENARIOS = pathlib.Path(__file__).resolve().parents[2] / "scenarios"


@pytest.fixture(scope="module")
def cascade() -> M.Scenario:
    return M.load(SCENARIOS / "cache_outage_cascade.yaml")


# --------------------------------------------------------------------------- #
# The shipped scenario.
# --------------------------------------------------------------------------- #

def test_the_shipped_scenario_parses(cascade):
    assert cascade.name == "cache_outage_cascade"
    assert len(cascade.stages) == 3


def test_it_has_a_conditional_trigger_not_only_a_delay(cascade):
    """`after: X, trigger: <promql crosses threshold>` means the second fault
    fires when the cascade ACTUALLY propagates, not on a stopwatch."""
    db = cascade.stage("db_pressure")
    assert db.trigger is not None
    assert db.trigger.comparator == ">"
    assert db.trigger.threshold == 0.200
    assert db.trigger.timeout_s == 90


def test_it_has_an_observation_only_stage(cascade):
    """Retry storms and queue growth appear AFTER injection stops."""
    observe = cascade.stage("observe_only")
    assert observe.observation_only
    assert observe.fault == M.NO_FAULT


def test_a_scenario_without_abort_conditions_is_refused():
    """A cascade multiplies blast radius and is the one feature in this plan
    that could genuinely take down a cluster."""
    raw = {"scenario": {"name": "x", "hypothesis": {}, "abort_conditions": [],
                        "stages": [{"id": "a", "fault": "pod-delete"}]}}
    with pytest.raises(M.ScenarioError, match="abort_conditions"):
        M.parse(raw)


def test_a_cyclic_dag_is_refused_at_parse_time():
    """A scenario that cannot be ordered would hang while holding an injected
    fault, so it must fail before anything is injected."""
    raw = {"scenario": {"name": "x", "hypothesis": {},
                        "abort_conditions": [{"name": "a"}],
                        "stages": [{"id": "a", "after": "b", "fault": "pod-delete"},
                                   {"id": "b", "after": "a", "fault": "pod-delete"}]}}
    with pytest.raises(M.ScenarioError, match="cycle or missing dependency"):
        M.parse(raw)


def test_teardown_is_the_reverse_of_execution_order(cascade):
    """Stage 2's fault was injected on top of stage 1's conditions, so undoing
    stage 1 first leaves stage 2 applied to a system no longer in the state it
    was injected into."""
    assert [s.id for s in cascade.teardown_order()] == list(
        reversed([s.id for s in cascade.order()]))


# --------------------------------------------------------------------------- #
# Triggers. A missing series is never a satisfied trigger.
# --------------------------------------------------------------------------- #

def test_a_missing_series_never_satisfies_a_trigger():
    """Firing a second fault because a query returned nothing would inject into
    a cascade nobody has evidence is happening."""
    trigger = M.Trigger("q", ">", 0.2, 90)
    assert trigger.satisfied(None) is False
    assert trigger.satisfied(0.3) is True


@pytest.mark.parametrize("comparator,value,expected", [
    (">", 0.3, True), (">", 0.1, False),
    (">=", 0.2, True), ("<", 0.1, True), ("<=", 0.2, True),
])
def test_trigger_comparators(comparator, value, expected):
    assert M.Trigger("q", comparator, 0.2, 90).satisfied(value) is expected


def test_a_trigger_that_trips_fires_the_stage():
    stage = M.Stage("s", "pod-delete", trigger=M.Trigger("q", ">", 0.2, 90))
    fired, waited, value = E.wait_for_trigger(
        stage, lambda _q: 0.5, now=_clock([0, 0]), sleep=lambda _s: None)
    assert fired is True and value == 0.5


def test_a_trigger_that_never_trips_is_the_finding():
    """THE most valuable output this feature produces: the upstream failure was
    absorbed rather than amplified, proved rather than assumed."""
    stage = M.Stage("s", "pod-delete", trigger=M.Trigger("q", ">", 0.2, 90))
    fired, waited, value = E.wait_for_trigger(
        stage, lambda _q: 0.05, now=_clock([0, 30, 60, 95]), sleep=lambda _s: None)
    assert fired is False
    assert waited >= 90


def _clock(values):
    it = iter(values + [values[-1]] * 50)
    return lambda: next(it)


# --------------------------------------------------------------------------- #
# Execution, abort, and teardown.
# --------------------------------------------------------------------------- #

def _harness(cascade, *, trigger_value=0.5, abort_after=None):
    """Records what happened, so ordering can be asserted."""
    state = {"injected": [], "stopped": [], "observed": [], "calls": 0}

    def inject(stage):
        state["injected"].append(stage.id)
        return f"engine-{stage.id}"

    def stop_stage(stage):
        state["stopped"].append(stage.id)

    def observe(stage):
        state["observed"].append(stage.id)

    def check_aborts():
        state["calls"] += 1
        if abort_after is not None and state["calls"] > abort_after:
            return "availability_collapse"
        return None

    # A fake clock so the `did_not_propagate` timeout path is exercised in
    # microseconds. Without it that branch could only be tested by waiting out a
    # real 90s timeout, which means the single most valuable outcome this module
    # produces would be the one branch nobody tests.
    result = E.run(cascade, inject=inject, stop_stage=stop_stage,
                   sample_value=lambda _q: trigger_value,
                   check_aborts=check_aborts, observe=observe, poll_s=0,
                   now=_ticking_clock(), sleep=lambda _s: None)
    return result, state


def _ticking_clock(step: float = 30.0):
    """Advances 30s per read, so any timeout is reached within a few calls."""
    state = {"t": 0.0}

    def now() -> float:
        state["t"] += step
        return state["t"]
    return now


def test_a_propagating_cascade_fires_every_stage(cascade):
    result, state = _harness(cascade, trigger_value=0.5)
    assert state["injected"] == ["kill_cache", "db_pressure"]
    assert state["observed"] == ["observe_only"]
    assert result.propagated is True


def test_a_cascade_that_does_not_propagate_records_the_finding(cascade):
    result, state = _harness(cascade, trigger_value=0.05)
    assert state["injected"] == ["kill_cache"]
    assert result.propagated is False
    assert "DID NOT PROPAGATE" in result.headline()


def test_a_stage_whose_upstream_never_fired_is_skipped(cascade):
    """Injecting stage 3 after stage 2 was absorbed would measure something the
    scenario never claimed."""
    result, _state = _harness(cascade, trigger_value=0.05)
    observe = next(s for s in result.stages if s.stage_id == "observe_only")
    assert observe.outcome == E.SKIPPED


def test_teardown_runs_in_reverse_dependency_order(cascade):
    _result, state = _harness(cascade, trigger_value=0.5)
    assert state["stopped"] == ["db_pressure", "kill_cache"]


def test_an_abort_mid_cascade_tears_down_every_fired_stage(cascade):
    """Aborting stage 2 while stage 1 is active must halt BOTH."""
    result, state = _harness(cascade, trigger_value=0.5, abort_after=2)
    assert result.aborted_by == "availability_collapse"
    assert set(state["stopped"]) == set(state["injected"])
    assert state["stopped"] == list(reversed(
        [s for s in ["kill_cache", "db_pressure"] if s in state["injected"]]))


def test_teardown_attempts_every_stage_even_if_one_fails(cascade):
    """A teardown that stops at the first error leaves the remaining faults
    injected — the outcome the whole safety plane exists to prevent."""
    stopped = []

    def stop_stage(stage):
        stopped.append(stage.id)
        if stage.id == "db_pressure":
            raise RuntimeError("kubectl unavailable")

    result = E.run(cascade, inject=lambda s: "e", stop_stage=stop_stage,
                   sample_value=lambda _q: 0.5, check_aborts=lambda: None,
                   observe=lambda s: None, poll_s=0,
                   now=_ticking_clock(), sleep=lambda _s: None)
    assert stopped == ["db_pressure", "kill_cache"]
    assert any("FAILED" in entry for entry in result.teardown)
    assert any(entry.startswith("kill_cache=ok") for entry in result.teardown)


def test_teardown_runs_even_when_injection_raises(cascade):
    """Cleanup on EVERY exit path, including an exception mid-cascade."""
    stopped = []

    def inject(stage):
        if stage.id == "db_pressure":
            raise RuntimeError("litmus refused")
        return "engine"

    with pytest.raises(RuntimeError):
        E.run(cascade, inject=inject, stop_stage=lambda s: stopped.append(s.id),
              sample_value=lambda _q: 0.5, check_aborts=lambda: None,
              observe=lambda s: None, poll_s=0,
              now=_ticking_clock(), sleep=lambda _s: None)
    assert stopped == ["kill_cache"]


def test_the_result_serialises_for_the_bundle(cascade):
    result, _state = _harness(cascade, trigger_value=0.05)
    payload = result.to_dict()
    assert payload["propagated"] is False
    assert payload["stages"][1]["outcome"] == E.DID_NOT_PROPAGATE
    assert "absorbed" in payload["stages"][1]["note"]
