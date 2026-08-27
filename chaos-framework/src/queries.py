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
    # Availability from the client's perspective. http_req_failed is a k6 Rate
    # metric (0..1 over the push interval).
    "client_availability": f'1 - avg(k6_http_req_failed_rate{{testrun="$TESTRUN"}})',
    # Client p99 latency in ms. VERIFIED 23 Aug 2026: k6 2.2.0 prometheus-rw
    # exports duration Trends in SECONDS (base units) — corroborated against the
    # server-side histogram (k6 13.0ms vs server 6.5ms on the same window).
    # Without the *1000 an 800ms threshold silently becomes an 800-SECOND one.
    "client_p99_ms": f'max(k6_http_req_duration_p99{{testrun="$TESTRUN"}}) * 1000',
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
    "cb_payment_not_permitted_rate": (
        f'sum(rate(resilience4j_circuitbreaker_calls_total'
        f'{{application="order-api",name="paymentService",kind="not_permitted"}}[{W}]))'
        f' or vector(0)'
    ),
}


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
