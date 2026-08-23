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
    "other_namespace_5xx_rate": (
        f'sum(rate(http_server_requests_seconds_count{{status=~"5..",namespace!="target-app"}}[{W}]))'
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


def client_queries(testrun: str) -> dict[str, str]:
    return {k: v.replace("$TESTRUN", testrun) for k, v in CLIENT.items()}


def server_queries() -> dict[str, str]:
    return dict(SERVER)
