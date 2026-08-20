# ChaosProof — PromQL Library, Window Arithmetic, and Thresholds

Contents:
1. Window arithmetic — why each number is what it is
2. Client-side SLI queries (k6, primary)
3. Server-side SLI queries (corroboration)
4. Recovery and golden-signal queries
5. Resilience-pattern queries
6. Alert-latency arithmetic
7. Threshold constants
8. Queries that look right and are wrong

---

## 1. Window arithmetic

Prometheus needs **at least two samples** inside a range window to compute `rate()` or `increase()`. So:

```
minimum usable window  =  2 × scrape_interval
recommended window     =  4–6 × scrape_interval   (tolerates one missed scrape)
```

| Scrape | Min window | Recommended | Can it resolve a 30s SLO? |
|---|---|---|---|
| 30s (chart default) | 60s | 120–180s | **No** — window alone is 2–6× the SLO |
| 15s | 30s | 60–90s | Marginal; a single missed scrape breaks it |
| **5s (this project, target app only)** | 10s | **30s** | **Yes** — 6 samples in a 30s window |

The rule to apply when reviewing any query in this project: **if the range window is longer than the SLO it is being compared against, the measurement is invalid.** Do not widen the window to make the query return data — that inverts the relationship between measurement and target.

Set it on the target's `ServiceMonitor` only:

```yaml
# charts/target-app/templates/servicemonitor.yaml
spec:
  endpoints:
    - port: http
      path: /actuator/prometheus
      interval: 5s              # NOT cluster-wide — see the cost note in the SKILL body
      scrapeTimeout: 4s         # must be < interval
```

`scrapeTimeout` must be strictly less than `interval`, or Prometheus rejects the config.

---

## 2. Client-side SLI queries (k6 — primary source of truth)

k6 exports through the Prometheus remote-write output; series are prefixed `k6_`.

```promql
# Client availability. THE primary availability SLI.
1 - (
  sum(rate(k6_http_reqs_failed_total{testrun="$TESTRUN"}[30s]))
  /
  sum(rate(k6_http_reqs_total{testrun="$TESTRUN"}[30s]))
)

# Client p99 latency, in milliseconds.
histogram_quantile(0.99,
  sum(rate(k6_http_req_duration_seconds_bucket{testrun="$TESTRUN"}[30s])) by (le)
) * 1000

# Achieved arrival rate — the validity gate reads this.
sum(rate(k6_http_reqs_total{testrun="$TESTRUN"}[30s]))

# Dropped iterations: the generator saturated before the system did => INVALID.
sum(increase(k6_dropped_iterations_total{testrun="$TESTRUN"}[1m]))
```

Always scope by `testrun` label. Two overlapping TestRuns would silently merge into one series set, and the serial-execution guarantee is what prevents that — but the label makes the mistake detectable rather than invisible.

---

## 3. Server-side SLI queries (corroboration only)

Spring Boot Actuator + Micrometer names these `http_server_requests_seconds_*`.

```promql
# Server availability. NOT primary — it cannot see requests that never reached a server.
sum(rate(http_server_requests_seconds_count{namespace="target-app",status!~"5.."}[30s]))
/
sum(rate(http_server_requests_seconds_count{namespace="target-app"}[30s]))

# 5xx rate, absolute. Used by the no_server_5xx_storm invariant.
sum(rate(http_server_requests_seconds_count{namespace="target-app",status=~"5.."}[30s]))

# The derived signal: requests that died before they were served.
# Export as chaosproof_client_server_availability_gap.
(
  sum(rate(k6_http_reqs_failed_total{testrun="$TESTRUN"}[30s]))
  / sum(rate(k6_http_reqs_total{testrun="$TESTRUN"}[30s]))
)
-
(
  sum(rate(http_server_requests_seconds_count{namespace="target-app",status=~"5.."}[30s]))
  / sum(rate(http_server_requests_seconds_count{namespace="target-app"}[30s]))
)
```

**Guard against the empty denominator.** With no traffic both ratios are `0/0` → `NaN`, and `NaN` compared against any threshold is false, which a naive validator reads as "no breach." The validity gate (achieved rps ≥ floor) is what prevents this from ever being evaluated, which is why the gate runs *before* invariants rather than alongside them.

---

## 4. Recovery and golden-signal queries

```promql
# Replicas available — the recovery invariant for pod-kill and container-kill.
sum(kube_deployment_status_replicas_available{namespace="target-app",deployment="$DEPLOY"})

# Restarts. Tolerance is ZERO for experiments that must not restart containers.
sum(increase(kube_pod_container_status_restarts_total{namespace="target-app"}[2m]))

# CPU throttle RATIO — dimensionless. Never compare raw throttled-period counters:
# they move with replica count and window length, which makes the check meaningless.
sum by (namespace,pod,container) (
  rate(container_cpu_cfs_throttled_periods_total{namespace="target-app"}[30s]))
/
sum by (namespace,pod,container) (
  rate(container_cpu_cfs_periods_total{namespace="target-app"}[30s]))

# OOM by COUNTER, not the flapping last_terminated_reason gauge.
sum by (namespace,pod,container) (
  increase(container_oom_events_total{namespace="target-app"}[2m]))

# PSI: share of wall time fully stalled. Beta 1.34, GA 1.36. Requires cgroup v2.
sum by (namespace,pod,container) (
  rate(container_pressure_memory_stalled_seconds_total{namespace="target-app"}[30s]))

# Pod eviction — the CORRECT assertion for the disk-fill experiment.
sum(increase(kube_pod_status_reason{namespace="target-app",reason="Evicted"}[5m]))
```

The throttle-ratio and OOM-counter choices are the same corrections applied across this portfolio; a raw throttled-period count or a `last_terminated_reason` gauge will pass review and then produce nonsense.

---

## 5. Resilience-pattern queries

These require **`resilience4j-micrometer` on the classpath** — it is *not* a transitive dependency of the Spring Boot starter. Without it every query here returns empty, and the pattern validator reports `applicable=false` for everything while appearing to work. Assert their presence at startup (see `chaosproof-spring-target-app`).

```promql
# Circuit breaker state. 0 CLOSED, 1 OPEN, 2 HALF_OPEN (verify enum on your version).
resilience4j_circuitbreaker_state{application="order-api",name="paymentService"}

# Did it actually transition? A state gauge alone can miss a fast open-close cycle.
max_over_time(
  resilience4j_circuitbreaker_state{application="order-api",name="paymentService"}[$FAULT_WINDOW])

# Calls by outcome — proves the breaker did something, not just that it changed state.
sum by (kind) (rate(resilience4j_circuitbreaker_calls_total{name="paymentService"}[30s]))

# Retry activity.
sum by (kind) (rate(resilience4j_retry_calls_total{name="inventoryService"}[30s]))

# Bulkhead headroom. Zero means thread-pool exhaustion — the cascade signal.
min(resilience4j_bulkhead_available_concurrent_calls{name="paymentProcessing"})
```

Use `max_over_time` for the transition check rather than an instant read: a circuit breaker that opened and closed inside the sampling interval is a **pass**, and an instant query at the wrong moment records it as a failure.

---

## 6. Alert-latency arithmetic

```
irreducible = scrape_interval + evaluation_interval + rule_for_duration
detection   = alert_fired_at − fault_injected_at
excess      = detection − irreducible        # the only part the system controls
```

Grade `excess` against the SLO; report both numbers. The CI lint:

```python
# evals/alert_rule_lint.py — gates CI
for rule in load_prometheus_rules("monitoring/alerting-rules.yml"):
    floor = rule.for_seconds + rule.group_interval + TARGET_SCRAPE_INTERVAL
    slo   = slo_for(rule.alertname).alert_latency_seconds
    assert floor <= slo, (
        f"{rule.alertname}: irreducible latency {floor}s exceeds its own SLO {slo}s. "
        f"Either lower `for:` to <= {slo - rule.group_interval - TARGET_SCRAPE_INTERVAL}s "
        f"or raise the SLO. As written this alert can never pass.")
```

Query firing alerts through **Alertmanager API v2** — v1 was removed in 0.27:

```
GET /api/v2/alerts?filter=alertname%3D"KubePodCrashLooping"&filter=namespace%3D"target-app"
```

`ALERTS{alertstate="firing"}` in Prometheus is an acceptable secondary source, but Alertmanager is authoritative for what was actually *notified*, which is what the alert-validation check claims to test.

---

## 7. Threshold constants

Keep these in one module. They are part of the scoring epoch — changing any of them opens a new epoch.

```python
# Validity (chaosproof-measurement-integrity)
MIN_SAMPLE_COVERAGE      = 0.90
DROPPED_CEILING          = 10
STEADY_STATE_WINDOW_S    = 120
RECOVERY_HOLD_S          = 30      # consecutive seconds of health before "recovered"

# Golden signals
THROTTLE_RATIO_CEILING   = 0.05    # 5% of CFS periods throttled
PSI_FULL_CEILING         = 0.05    # 5% of wall time fully stalled
RESTART_TOLERANCE        = 0

# Measurement
TARGET_SCRAPE_INTERVAL_S = 5
SLI_WINDOW_S             = 30
SAMPLE_INTERVAL_S        = 5

# Scoring (chaosproof-hypothesis-engine)
WEIGHTS = {"slo_recovery": 0.35, "alert_validation": 0.25,
           "resilience_pattern": 0.25, "recovery_completeness": 0.15}
PASS_THRESHOLD           = 0.80
FAIL_THRESHOLD           = 0.50

# Quality (chaosproof-mlops-quality)
FLAKINESS_WINDOW         = 20
MAX_SCORE_STDDEV         = 0.05
MAX_VERDICT_FLIP_RATE    = 0.05

# Safety (chaosproof-safety-plane)
BUDGET_WARN_PCT          = 50.0
BUDGET_FREEZE_PCT        = 80.0
MAX_BUDGET_BURN_PCT      = 2.0     # single experiment, of remaining budget
```

---

## 8. Queries that look right and are wrong

| Looks right | Actually | Use instead |
|---|---|---|
| `rate(http_server_requests_seconds_count[5m])` for a 30s SLO | Window is 10× the SLO; measures lag, not recovery | `[30s]` at 5s scrape |
| `histogram_quantile(0.99, ...[5m])` during a 60s fault | Averages the fault away | k6 client p99, or `[30s]` |
| Server availability as the primary SLI | Cannot see requests that never reached a server | k6 client availability |
| `container_cpu_cfs_throttled_periods_total` raw | Scales with replicas and window | Throttle **ratio** |
| `container_last_terminated_reason == "OOMKilled"` | Gauge, flaps, races the sampler | `increase(container_oom_events_total[2m])` |
| Instant read of `resilience4j_circuitbreaker_state` | Misses a fast open-close cycle | `max_over_time(...[$FAULT_WINDOW])` |
| `up{job="target-app"} == 1` as a health check | Says the scrape worked, not that the app is serving | Client availability |
| Empty result treated as "no breach" | `NaN`/empty compares false against every threshold | Return `invalid` explicitly |
| `constant-vus` load with any of the above | Offered load falls as the system degrades; the fault hides itself | `constant-arrival-rate` |

The last row is the one to internalise: **every query in this file becomes untrustworthy under a closed workload model**, because the denominator moves with system health. Fixing the queries without fixing the load model fixes nothing.
