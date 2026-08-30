"""Regression cases for the two SLI defects the Phase 7 gate surfaced.

Both were found by pointing the chaos gate at a live cluster and not believing
the numbers. Both had been shipping since Phase 2, both passed every previous
gate, and neither was visible from inside the framework — the queries returned
data, the data had the right shape, and the values were wrong.

These are query-shape assertions, not value assertions. They cannot prove the
PromQL is right; only a cluster can do that, and it did. What they prevent is
the specific regression: someone simplifying these expressions back to the form
that looked cleaner and measured nothing.
"""

import pytest

from src import queries
from src.scoring import scorer

CLIENT = queries.CLIENT


# --------------------------------------------------------------------------- #
# D-A: client availability was an unweighted average over per-status series.
#
# k6 splits http_req_failed by the `status` and `expected_response` system tags.
# Once one request has failed there are two series, one pinned at 0 and one at
# 1, and `avg` of them is 0.5 whatever the traffic mix. Observed live: 17,695
# successes against 4 failures reported as 0.5000 availability.
# --------------------------------------------------------------------------- #

def test_availability_is_not_an_average_of_the_rate_metric():
    expr = CLIENT["client_availability"]
    assert "avg(" not in expr, (
        "avg() over k6_http_req_failed_rate averages LABEL SERIES, not requests — "
        "it reports 0.5 for any run containing a single failure")
    assert "k6_http_req_failed_rate" not in expr


def test_availability_is_volume_weighted_from_the_request_counter():
    expr = CLIENT["client_availability"]
    assert "k6_http_reqs_total" in expr
    assert expr.count("rate(") >= 2, "both sides of the ratio must be windowed rates"


def test_availability_counts_failures_so_a_total_outage_reads_zero():
    """Counting successes instead would make a 100%-failure window return an
    EMPTY vector — there is no expected_response="true" series when nothing
    succeeds — and report INVALID for the one case that is unambiguously a
    total outage. The direction of the ratio is load-bearing."""
    expr = CLIENT["client_availability"]
    assert 'expected_response="false"' in expr
    assert 'expected_response="true"' not in expr
    assert expr.lstrip().startswith("1 -")


def test_availability_tolerates_zero_failures_but_not_zero_traffic():
    """`or vector(0)` belongs on the NUMERATOR only. On the denominator it would
    turn 'no traffic' into a division by zero rather than an empty result, and
    no traffic must stay INVALID — no evidence never scores as success."""
    expr = CLIENT["client_availability"]
    numerator, denominator = expr.split(") / ", 1)
    assert "or vector(0)" in numerator
    assert "or vector(0)" not in denominator


# --------------------------------------------------------------------------- #
# D-B: client p99 read k6's trend-stat GAUGES, which accumulate over the whole
# test run. Observed live at one instant: gauge 8445ms, windowed native
# histogram 17.98ms, server-side histogram 17.01ms.
# --------------------------------------------------------------------------- #

def test_p99_does_not_read_the_run_cumulative_trend_gauge():
    expr = CLIENT["client_p99_ms"]
    assert "k6_http_req_duration_p99" not in expr, (
        "the _p99 trend gauge accumulates from the start of the test run: it "
        "cannot fall back after a fault and cannot resolve a 60s fault window")
    assert "max(" not in expr, (
        "max() across per-status series lets a handful of failed requests set "
        "the p99 for all of them")


def test_p99_is_a_windowed_quantile_over_the_native_histogram():
    expr = CLIENT["client_p99_ms"]
    assert expr.startswith("histogram_quantile(0.99,")
    assert "k6_http_req_duration_seconds" in expr
    assert f"[{queries.W}]" in expr


def test_p99_converts_seconds_to_milliseconds():
    """k6's `_seconds` suffix settles the unit question the old `* 1000` was
    guessing at. Dropping the conversion makes an 800ms threshold an 800-second
    one; keeping it on a millisecond series makes it 0.8ms. Both are silent."""
    assert CLIENT["client_p99_ms"].rstrip().endswith("* 1000")
    assert "_seconds" in CLIENT["client_p99_ms"]


# --------------------------------------------------------------------------- #
# Standing rules both defects violated.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(CLIENT))
def test_every_client_query_is_scoped_to_one_testrun(name):
    """An unscoped client query aggregates across every k6 run Prometheus still
    holds, including the previous experiment's."""
    assert 'testrun="$TESTRUN"' in CLIENT[name]


@pytest.mark.parametrize("name", sorted(CLIENT))
def test_no_client_query_widens_its_window(name):
    """Never widen a rate window to make a query 'work'. If a window must exceed
    the SLO it is compared against, the SLO is unmeasurable and THAT is the
    finding."""
    expr = CLIENT[name]
    windows = {w.split("]")[0] for w in expr.split("[")[1:]}
    assert windows <= {queries.W}, f"{name} uses a window other than {queries.W}"


def test_the_sli_window_stays_at_thirty_seconds():
    """6 samples at a 5s scrape. Changing this changes what every stored score
    means, so it opens an epoch — it is not a tuning knob."""
    assert queries.W == "30s"


def test_redefining_an_sli_opened_a_new_epoch():
    """The scorer did not change; the definition of what it consumes did. Scores
    either side of that are not comparable, and the mandate is that a change to
    a weight, threshold, SLO or the scorer opens a new epoch — never an UPDATE
    to what is already stored."""
    assert scorer.SLO_VERSION >= 2
    assert scorer.epoch_material(["x"])["slo_version"] == scorer.SLO_VERSION


# --------------------------------------------------------------------------- #
# D-E: cb_payment_not_permitted_rate read a metric that does not exist, and
# `or vector(0)` rewrote the resulting empty series as a confident zero. The
# breaker had in fact rejected 21,265 calls.
# --------------------------------------------------------------------------- #

SERVER = queries.SERVER


def test_short_circuit_rate_reads_the_metric_resilience4j_actually_publishes():
    """Resilience4j's Micrometer binding exports call outcomes as
    `..._calls_seconds_count` with kind in {successful, failed, ignored} —
    not_permitted is NOT among them — and short-circuited calls as the separate
    counter `..._not_permitted_calls_total`. Verified against the live cluster
    on 30 Aug 2026."""
    expr = SERVER["cb_payment_not_permitted_rate"]
    assert "resilience4j_circuitbreaker_not_permitted_calls_total" in expr
    assert "circuitbreaker_calls_total" not in expr
    assert 'kind="not_permitted"' not in expr


def test_the_pattern_metrics_do_not_paper_over_an_absent_series():
    """`or vector(0)` is what turned a wrong metric name into 'the breaker
    short-circuited nothing'. These two series exist from the moment the breaker
    instance is registered, so a genuine zero already arrives as a real sample;
    absence can only mean the pattern is unobservable, and the honest verdict
    for an unobservable pattern is INVALID."""
    for name in ("cb_payment_open", "cb_payment_not_permitted_rate"):
        assert "or vector(0)" not in SERVER[name], (
            f"{name} must let an absent series stay absent — a missing series "
            "is never a passing series")


def test_the_breaker_state_gauge_selects_the_open_state():
    """`resilience4j_circuitbreaker_state` is one series per state. Dropping the
    state matcher would sum all six and always read 1."""
    assert 'state="open"' in SERVER["cb_payment_open"]
