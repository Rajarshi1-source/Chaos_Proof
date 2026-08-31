"""The PromQL library — every query in one place, per the corrected reference
(measurement-integrity references/promql-and-sli.md): [30s] windows at 5s scrape,
never wider than the SLO they are compared against.

k6 series names below were EMPIRICALLY VERIFIED against k6 2.2.0's
experimental-prometheus-rw output on 23 Aug 2026 (see Phase 2 notes): counters
export as k6_<name>_total, Rate metrics as k6_<name>_rate, and Trend metrics as
k6_<name>_<stat> per K6_PROMETHEUS_RW_TREND_STATS.
"""

W = "30s"  # SLI window — 6 samples at 5s scrape; NEVER widen to make a query "work"

# --- client (k6) — PRIMARY source of truth -------------------------------------
CLIENT = {
    # Achieved arrival rate — what the validity gate reads.
    "client_rps": f'sum(rate(k6_http_reqs_total{{testrun="$TESTRUN"}}[{W}]))',
    # Availability from the client's perspective — VOLUME-WEIGHTED, from the
    # request counter.
    #
    # DEFECT D-A, found by the Phase 7 gate and corrected 30 Aug 2026. This was
    #     1 - avg(k6_http_req_failed_rate{testrun="$TESTRUN"})
    # and it did not measure availability. k6 exports http_req_failed as a Rate
    # metric split by the `status` and `expected_response` system tags, so once
    # a single request has failed there are exactly two series — one pinned at 0
    # and one pinned at 1 — and their unweighted `avg` is 0.5 forever, whatever
    # the real traffic mix. Observed live: 17,695 successes against 4 failures
    # (true availability 99.98%) reported as 0.5000.
    #
    # The failure mode is worse than a wrong number. It is THREE-VALUED — 1.0
    # before the first failure, 0.5 after it, 0.0 only if every series fails —
    # so it reads perfect on a clean run and looks like a catastrophic outage on
    # a run with one bad request. That is what GATE 5's "availability collapsed
    # to 0.5000" was, and it is why an abort threshold of 0.80 tripped on a
    # system that was serving fine.
    #
    # Counting failures rather than successes is deliberate: when EVERY request
    # fails there is no expected_response="true" series at all, and a
    # success/total ratio would go empty — reporting INVALID for a total outage,
    # which is precisely backwards. `or vector(0)` supplies the no-failures
    # case; an absent DENOMINATOR (no traffic at all) still yields no samples,
    # which is INVALID, and correctly so.
    "client_availability": (
        f'1 - (sum(rate(k6_http_reqs_total{{testrun="$TESTRUN",'
        f'expected_response="false"}}[{W}])) or vector(0))'
        f' / sum(rate(k6_http_reqs_total{{testrun="$TESTRUN"}}[{W}]))'
    ),
    # Client p99 latency in ms, over the SAME [30s] window as every other SLI.
    #
    # DEFECT D-B, found alongside D-A. This was
    #     max(k6_http_req_duration_p99{testrun="$TESTRUN"}) * 1000
    # reading k6's trend-stat GAUGES. Those gauges hold a statistic accumulated
    # since the start of the test run, so their window is the whole run: a p99
    # cannot fall back after a fault, cannot resolve a 60s fault window, and is
    # not comparable to the 30s-windowed SLO it is checked against. Taking `max`
    # across the per-status series compounded it by letting a handful of failed
    # requests set the p99 for all of them.
    #
    # Observed live, same instant: cumulative gauge 8445ms, windowed native
    # histogram 17.98ms, server-side histogram 17.01ms. The windowed client
    # figure sits just above the server figure, which is what a correct
    # client-side measurement looks like — it includes the network the server
    # never sees. The 470x error was in the direction that silently falsifies
    # every latency invariant the project has.
    #
    # k6 ships the raw distribution as a native histogram when the
    # native-histograms feature is on (k6 side: K6_FEATURES; Prometheus side:
    # --enable-feature=native-histograms). The `_seconds` suffix is k6's, and it
    # settles the unit question that the *1000 above was guessing at.
    "client_p99_ms": (
        f'histogram_quantile(0.99, sum(rate('
        f'k6_http_req_duration_seconds{{testrun="$TESTRUN"}}[{W}]))) * 1000'
    ),
    # Generator saturation — drops mean the GENERATOR was the bottleneck.
    "dropped_iterations": f'sum(k6_dropped_iterations_total{{testrun="$TESTRUN"}}) or vector(0)',
}

# --- server (Prometheus) — corroboration, never primary ------------------------
SERVER = {
    "server_availability": (
        f'sum(rate(http_server_requests_seconds_count{{namespace="target-app",status!~"5.."}}[{W}]))'
        f' / sum(rate(http_server_requests_seconds_count{{namespace="target-app"}}[{W}]))'
    ),
    "server_5xx_rate": (
        f'sum(rate(http_server_requests_seconds_count{{namespace="target-app",status=~"5.."}}[{W}]))'
        f' or vector(0)'
    ),
    "payment_replicas_available": (
        'sum(kube_deployment_status_replicas_available'
        '{namespace="target-app",deployment="payment-service"})'
    ),
    # Blast-radius containment: 5xx OUTSIDE THE EXPERIMENT'S OWN NAMESPACE.
    # This was hardcoded to namespace!="target-app", which made a chaos-staging
    # experiment count its own EXPECTED failures as blast-radius escape and
    # abort itself on the first tick. A containment check must be relative to
    # the blast radius it is containing, not to whichever namespace was typed
    # in first.
    "other_namespace_5xx_rate": (
        f'sum(rate(http_server_requests_seconds_count{{status=~"5..",namespace!="$TARGET_NS"}}[{W}]))'
        f' or vector(0)'
    ),
    # Circuit-breaker observability (experiments 2-3 assert on the named instance).
    # The state gauge is per-state series: state="open" == 1 while the breaker is open.
    "cb_payment_open": (
        'max(resilience4j_circuitbreaker_state'
        '{application="order-api",name="paymentService",state="open"})'
    ),
    # The breaker DOING something, not just changing state: short-circuited calls.
    #
    # DEFECT D-E, found by the Phase 7 gate and corrected 30 Aug 2026. This read
    #     resilience4j_circuitbreaker_calls_total{...,kind="not_permitted"}
    # and that metric does not exist. Resilience4j's Micrometer binding publishes
    # `resilience4j_circuitbreaker_calls_seconds_count` with kind in
    # {successful, failed, ignored} — not_permitted is NOT one of them — and
    # exports short-circuited calls as a separate counter,
    # `resilience4j_circuitbreaker_not_permitted_calls_total`.
    #
    # The trailing `or vector(0)` is what made this dangerous rather than
    # obvious. A wrong metric name yields an empty series, `or vector(0)`
    # rewrites empty as zero, and the invariant reads "the breaker
    # short-circuited nothing" — a confident wrong answer with evidence
    # attached, for a breaker that had in fact rejected 21,265 calls. That is
    # precisely the rule this project states everywhere else: a missing series
    # is never a passing series.
    #
    # `or vector(0)` is gone deliberately, not just moved to the right metric.
    # This counter is registered the moment the breaker instance exists, so a
    # genuine zero already arrives as a real sample (rate of an existing counter
    # is 0). Absence therefore means the pattern is UNOBSERVABLE, and the
    # correct verdict for an unobservable pattern is INVALID, not zero.
    "cb_payment_not_permitted_rate": (
        f'sum(rate(resilience4j_circuitbreaker_not_permitted_calls_total'
        f'{{application="order-api",name="paymentService"}}[{W}]))'
    ),
}


# --- contract observation queries (Phase 9, §19.2) -----------------------------
# `does_not_inflict` clauses are consumer-side promises about LOAD, not faults.
# Nothing is injected to check them; they are observed during any experiment.
#
# NOTE ON ATTRIBUTION: inbound rate at the dependency is the total from ALL
# consumers, and in this system order-api is the only consumer of both
# payment-service and inventory-service — so total inbound IS what order-api
# inflicts. In a system with several consumers this would over-attribute, and
# the honest fix is a per-caller label rather than a quieter query.
CONTRACT = {
    "dependency_request_rate": (
        'sum(rate(http_server_requests_seconds_count'
        '{namespace="$TARGET_NS",job="$DEPENDENCY"}[$W]))'
    ),
    # Retry amplification, as a rate. This is the mechanism that pushes the
    # offered rate above what the consumer promised.
    "retried_calls_rate": (
        'sum(rate(resilience4j_retry_calls_total'
        '{application="$CONSUMER",kind="successful_with_retry"}[$W]))'
        ' or vector(0)'
    ),
}


def contract_queries(dependency: str, consumer: str, target_ns: str = "target-app",
                     window: str = "1m") -> dict[str, str]:
    """A 1m window by default rather than the 30s SLI window: this measures
    offered LOAD, which is compared against a sustained-rate promise, not
    against a latency SLO. Widening it here is legitimate for the same reason
    widening an SLI window is not — the thing being compared is different."""
    return {k: (v.replace("$TARGET_NS", target_ns)
                 .replace("$DEPENDENCY", dependency)
                 .replace("$CONSUMER", consumer)
                 .replace("$W", window))
            for k, v in CONTRACT.items()}


# --- target-scoped server queries (experiments 4-6) ----------------------------
# Parameterised by the experiment's target deployment. Every one of these is
# sampled every tick and becomes evidence, whether or not an invariant reads it.
TARGET_SCOPED = {
    # Replicas the target deployment currently has Available.
    "target_replicas_available": (
        'sum(kube_deployment_status_replicas_available'
        '{namespace="$TARGET_NS",deployment="$TARGET"}) or vector(0)'
    ),
    # Container restarts — the container-kill assertion, and a completeness signal.
    "target_restarts": (
        'sum(kube_pod_container_status_restarts_total'
        '{namespace="$TARGET_NS",pod=~"$TARGET-.*"}) or vector(0)'
    ),
    # CPU throttle RATIO — dimensionless. NEVER raw throttled-period counters:
    # they scale with replica count and window length, which makes the check
    # meaningless (measurement-integrity: looks-right-but-wrong table).
    "throttle_ratio": (
        f'(sum(rate(container_cpu_cfs_throttled_periods_total'
        f'{{namespace="$TARGET_NS",pod=~"$TARGET-.*",container="$TARGET"}}[{W}]))'
        f' / '
        f'clamp_min(sum(rate(container_cpu_cfs_periods_total'
        f'{{namespace="$TARGET_NS",pod=~"$TARGET-.*",container="$TARGET"}}[{W}])), 0.001))'
        f' or vector(0)'
    ),
    # Pods currently marked Evicted — the CORRECT disk-fill assertion.
    # kube_pod_status_reason is a GAUGE (1 while the pod carries the reason), so
    # a plain sum is the count of evicted pods. increase() on a gauge that springs
    # into existence is unreliable; the gauge form is deterministic.
    "evicted_pods": (
        'sum(kube_pod_status_reason'
        '{namespace="$TARGET_NS",reason="Evicted"}) or vector(0)'
    ),
    # HPA current replicas — the CPU-spike experiment reads this to see the
    # autoscaler react. Absent unless an HPA owns the workload.
    "hpa_replicas": (
        'sum(kube_horizontalpodautoscaler_status_current_replicas'
        '{namespace="$TARGET_NS",horizontalpodautoscaler="$TARGET"}) or vector(0)'
    ),
}


def client_queries(testrun: str) -> dict[str, str]:
    return {k: v.replace("$TESTRUN", testrun) for k, v in CLIENT.items()}


def server_queries(target: str | None = None,
                   target_ns: str = "target-app") -> dict[str, str]:
    """Shared server queries plus, when a target deployment is given, the
    target-scoped ones. `payment_replicas_available` is retained verbatim so
    experiments 1-3 keep their hypothesis version — renaming a metric an
    invariant reads would silently return empty, which reads as a pass."""
    out = {k: v.replace("$TARGET_NS", target_ns) for k, v in SERVER.items()}
    if target:
        out.update({k: v.replace("$TARGET_NS", target_ns).replace("$TARGET", target)
                    for k, v in TARGET_SCOPED.items()})
    return out
