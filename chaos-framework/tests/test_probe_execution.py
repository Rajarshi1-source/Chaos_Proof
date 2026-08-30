"""Regression cases for defect D-C: the in-band abort probes never executed.

Litmus 3.31.0 loses DOUBLE quotes between the ChaosEngine CRD and the
Prometheus HTTP request, so every promProbe query returned a PromQL parse
error. Verified live with a three-probe engine on 30 Aug 2026:

    vector(1)                          -> Passed
    ...{testrun='ci-120rps'}           -> Passed, 120.01 rps
    ...{testrun="ci-120rps"}           -> parse error at column 41,
                                          exactly where the quote should be

Because every probe carries `stopOnFailure: true` and an ERRORED probe counts
as a failed one, Litmus halted each experiment seconds after injection. The
ChaosEngine reached `Stopped` — which is also what a successful abort looks
like — and the truncated window was scored as a real result.
"""

import pytest
import yaml

from src.integrations.litmus import PROBE_TEMPLATE, LitmusClient

RENDERED = PROBE_TEMPLATE.format(
    prom_endpoint="http://prometheus-operated.monitoring:9090",
    testrun="ci-120rps", target_ns="target-app",
    availability_floor=0.80, containment_ceiling=0.5)
PROBES = yaml.safe_load(RENDERED)["probe"]
QUERIES = [p["promProbe/inputs"]["query"] for p in PROBES]


# --------------------------------------------------------------------------- #
# The quoting itself.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("query", QUERIES)
def test_probe_queries_use_single_quotes_only(query):
    """Not a style preference. A double quote in a promProbe query reaches
    Prometheus stripped, and the probe fails with a parse error rather than a
    threshold breach."""
    assert '"' not in query, (
        "Litmus 3.31.0 loses double quotes in promProbe queries — "
        "use single quotes for label values")


@pytest.mark.parametrize("query", QUERIES)
def test_probe_queries_still_match_labels(query):
    """A query that lost its matchers rather than its quotes would also parse.
    This is the other half of the assertion."""
    assert "'" in query and "{" in query


def test_the_probe_block_is_valid_yaml_with_both_guards():
    assert {p["name"] for p in PROBES} == {"client-availability-guard",
                                           "blast-radius-containment"}


@pytest.mark.parametrize("probe", PROBES)
def test_every_probe_is_continuous_and_stops_the_fault(probe):
    """Continuous, not EoT: a guard evaluated only at the end of the experiment
    reports a breach it was supposed to prevent. stopOnFailure is what makes it
    an abort rather than a note in a report."""
    assert probe["mode"] == "Continuous"
    assert probe["runProperties"]["stopOnFailure"] is True


def test_availability_probe_reads_the_same_definition_as_the_watchdog():
    """The in-band and out-of-band abort paths must not drift: a guard that
    disagrees with the verdict layer about what availability means will abort
    runs the report then calls healthy, or miss ones it calls dead."""
    from src import queries
    probe = next(q for q in QUERIES if "k6_http_reqs" in q)
    assert "k6_http_reqs_total" in queries.CLIENT["client_availability"]
    assert 'expected_response' in probe
    assert probe.lstrip().startswith("1 -")
    assert "avg(" not in probe          # defect D-A must not survive here either


def test_probe_threshold_comes_from_the_experiments_abort_conditions():
    """Same number, inverted comparator. Hardcoding the floor in the template
    is how the two paths drift apart one edit at a time."""
    spec = {"abort_conditions": [
        {"name": "availability_collapse", "source": "k6",
         "metric": "client_availability", "comparator": "<", "threshold": 0.42}]}
    rendered = LitmusClient.probes_for(
        LitmusClient.__new__(LitmusClient), spec, "t", "http://prom:9090")
    guard = next(p for p in yaml.safe_load(rendered)["probe"]
                 if p["name"] == "client-availability-guard")
    assert guard["promProbe/inputs"]["comparator"]["value"] == "0.42"


# --------------------------------------------------------------------------- #
# The structural fix: a run whose guard never ran is not scoreable.
# --------------------------------------------------------------------------- #

def _result(descriptions: list[str]) -> dict:
    """Shape of what chaos_result() builds from a ChaosResult's probeStatuses."""
    errors = [f"p{i}: {d}" for i, d in enumerate(descriptions)
              if any(m in d for m in LitmusClient.PROBE_EXECUTION_ERRORS)]
    return {"verdict": "Stopped", "phase": "Stopped", "probe_errors": errors}


def test_a_promql_parse_error_is_recognised_as_an_execution_failure():
    detail = ('unable to run command, error: error querying prometheus: bad_data: '
              'invalid parameter "query": 1:41: parse error: unexpected identifier')
    assert _result([detail])["probe_errors"]


def test_an_unreachable_prometheus_is_an_execution_failure():
    assert _result(["error querying prometheus: connection refused"])["probe_errors"]


def test_a_breached_threshold_is_not_an_execution_failure():
    """The guard firing is the guard WORKING. Treating it as a framework fault
    would turn every successful abort into INVALID and delete the safety
    plane's entire output."""
    breach = ("Obtained the specified prometheus metrics. "
              "Actual value: 0.5. Expected value: >= 0.8")
    assert _result([breach])["probe_errors"] == []


def test_a_passing_probe_is_not_an_execution_failure():
    ok = "Obtained the specified prometheus metrics. Actual value: 120.01. Expected value: 1"
    assert _result([ok])["probe_errors"] == []


def test_stopped_alone_cannot_distinguish_an_abort_from_a_crashed_probe():
    """The reason probe_errors has to exist. `Stopped` is what BOTH look like
    from outside, which is exactly how this defect survived three phases."""
    crashed = _result(["unable to run command, error: error querying prometheus"])
    aborted = _result(["Actual value: 0.5. Expected value: >= 0.8"])
    assert crashed["verdict"] == aborted["verdict"] == "Stopped"
    assert bool(crashed["probe_errors"]) != bool(aborted["probe_errors"])
