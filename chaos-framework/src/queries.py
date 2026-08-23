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
    # Client p99 latency in ms (Trend stat gauge exported by k6).
    "client_p99_ms": f'max(k6_http_req_duration_p99{{testrun="$TESTRUN"}})',
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
}


def client_queries(testrun: str) -> dict[str, str]:
    return {k: v.replace("$TESTRUN", testrun) for k, v in CLIENT.items()}


def server_queries() -> dict[str, str]:
    return dict(SERVER)
