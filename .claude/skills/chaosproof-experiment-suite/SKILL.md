---
name: chaosproof-experiment-suite
description: "Define and generate ChaosProof experiments. Use for ANY work on the six baseline experiments and what each may legitimately assert, cascading fault DAGs with conditional triggers, contract-generated experiments, and counterfactual pattern-on-versus-off pairs. Contains three corrections that produce confident wrong verdicts if missed: disk-fill asserts pod eviction (needs ephemeral-storage limits), not NodeDiskPressure; pod-cpu-hog asserts throttle ratio and aggregate SLI, never that the spike disappears, because HPA adds healthy replicas while the hogged pod stays hogged; container-kill is CRI/containerd, not Docker. Trigger on pod-delete, pod-network-latency, pod-network-loss, disk-fill, pod-cpu-hog, container-kill, cascade, fault DAG, scenario, resilience contract, contract generator, counterfactual, or retry amplification. MANDATE: every experiment carries a hypothesis with min_rps_floor and abort conditions, and starts advisory-only."
---

# ChaosProof Experiment Suite

What experiments exist, what each one may legitimately assert, and the three ways new experiments get created: hand-written, contract-generated, and counterfactual pairs.

Every experiment in this project — regardless of origin — carries a hypothesis with `min_rps_floor` and `abort_conditions` (`chaosproof-hypothesis-engine`, `chaosproof-safety-plane`).

## The six baseline experiments, with corrected assertions

Three of Rev 1's six asserted the wrong thing. These corrections matter because a wrong assertion produces a confident wrong verdict.

| # | Experiment | Litmus fault | Assert **this** | Not this |
|---|---|---|---|---|
| 1 | Pod kill | `pod-delete` | Client availability ≥99%, P99 ≤800ms, replicas restored ≤30s | — |
| 2 | Network latency | `pod-network-latency` | Circuit breaker transitions within 10s, fallback served, P99 ≤2000ms | — |
| 3 | Network partition | `pod-network-loss` | Availability ≥95% via fallback, CB opens, alert fires | — |
| 4 | **Disk fill** | `disk-fill` | **Pod evicted and rescheduled**, no data loss, alert ≤60s | ~~`NodeDiskPressure` fires~~ |
| 5 | **CPU spike** | `pod-cpu-hog` | **Throttle ratio rises, aggregate SLI holds, HPA reacts** | ~~"the spike goes away"~~ |
| 6 | **Container kill** | `container-kill` | Recovery ≤15s via readiness probe, **CRI-level** | ~~"Docker-level"~~ |

### Why disk fill was wrong

Litmus `disk-fill` consumes the **container's ephemeral storage** and requires `ephemeral-storage` limits set on the pod. Exceeding them triggers **pod eviction** — not necessarily node-level `DiskPressure`. These are two different failure modes with two different alerts.

```yaml
# The target pod MUST declare limits or disk-fill has nothing to exceed.
resources:
  limits:   { ephemeral-storage: 1Gi }
  requests: { ephemeral-storage: 512Mi }
```

Assert eviction-and-reschedule — it is deterministic and observable:
```promql
sum(increase(kube_pod_status_reason{namespace="target-app",reason="Evicted"}[5m]))
```
Filling the *node's* filesystem to assert `NodeDiskPressure` is a legitimate alternative experiment, but it is a different experiment with a different blast radius. Do not conflate them.

### Why CPU spike was wrong — the scaling paradox

`pod-cpu-hog` burns CPU **inside the target container**. So:

- HPA sees high utilisation and adds replicas — each new replica is healthy, while the hogged pod stays hogged. **Scaling does not relieve the injected load.**
- If CPU limits are set, the hog is throttled and utilisation may barely move, so HPA might not react at all.

Assert what should actually happen: throttle **ratio** rises, latency degrades *within budget*, HPA reacts, and the **aggregate** client SLI holds. Never assert that the spike disappears — the fault ends on its own schedule, not because autoscaling fixed it.

Read `horizontalpodautoscalers` in pre-flight so the experiment knows whether an HPA owns the workload; the hypothesis differs depending on the answer.

### Why container kill was wrong

The Docker shim was removed in Kubernetes 1.24; nodes run **containerd** (or CRI-O). Litmus `container-kill` talks to the CRI socket:

```yaml
env:
  - name: CONTAINER_RUNTIME
    value: containerd
  - name: SOCKET_PATH
    value: /run/containerd/containerd.sock
```

Say "container-runtime level," never "Docker-level" — it dates the work by four years.

## Cascading scenarios — a fault DAG with conditional triggers

Real outages are cascades: a cache eviction raises database load, which raises latency, which exhausts a connection pool, which times out a caller, which retries, which amplifies the load. One fault cannot express that.

```yaml
# scenarios/cache_outage_cascade.yaml
scenario:
  name: cache_outage_cascade
  hypothesis:
    description: >
      The system degrades gracefully: client availability stays >=97% and P99 under
      1500ms throughout, because inventory-service serves stale catalogue data and
      order-api's bulkhead prevents thread-pool exhaustion from spreading.
    invariants:
      - { name: availability, source: k6, expr: "1 - (http_req_failed/http_reqs)",
          comparator: ">=", threshold: 0.97, tolerance_s: 20 }
      - { name: no_thread_pool_exhaustion, source: prometheus,
          expr: "min(resilience4j_bulkhead_available_concurrent_calls)",
          comparator: ">", threshold: 0, tolerance_s: 0 }   # zero tolerance: this IS the cascade

  stages:
    - id: kill_cache
      at: 0s
      fault: pod-delete
      target: { app: redis, namespace: target-app }

    - id: db_pressure
      after: kill_cache
      trigger:                       # CONDITIONAL, not a stopwatch
        promql: histogram_quantile(0.99, sum(rate(db_query_seconds_bucket[30s])) by (le))
        comparator: ">"
        threshold: 0.200
        timeout_s: 90                # never trips => record "cascade did not propagate"
      fault: pod-network-latency
      target: { app: inventory-service }
      params: { latency_ms: 100 }

    - id: observe_only
      after: db_pressure
      duration: 60s
      fault: none                    # watch amplification without adding to it
```

Two design decisions to preserve:

- **Conditional triggers over fixed delays.** The second fault fires when the cascade actually propagates. **If the trigger never trips within `timeout_s`, that is the most valuable output this feature produces:** *"the cascade did not propagate — the stale-cache fallback absorbed the cache outage completely."* A delay-only DAG cannot produce that result.
- **A stage with `fault: none`.** Retry storms and queue growth appear *after* injection stops. A fault-then-immediately-validate loop misses them entirely.

**Cascades multiply blast radius.** Aborting stage 2 while stage 1 is active must halt both, and cleanup runs stage cleanups in reverse dependency order. Build `chaosproof-safety-plane` first.

## Contract-generated experiments

Each service declares what it tolerates; the generator emits one experiment per clause. **The suite is derived from declared architecture, not from a human remembering to write a test.**

```yaml
# target-app/order-api/resilience-contract.yaml
service: order-api
version: 1.2.0

provides:
  availability_slo: 99.5
  latency_p99_ms: 500
  graceful_degradation_modes:
    - "stale-inventory-data-on-cache-miss (max staleness 60s)"
    - "queued-payment-on-payment-svc-failure (max queue depth 1000)"

tolerates:
  - dependency: payment-service
    max_latency_ms: 800
    max_error_rate_pct: 5
    max_outage_seconds: 120
  - dependency: redis
    max_outage_seconds: 600

does_not_inflict:
  - "request rate > 100 rps on any single dependency"
```

```python
# chaos-framework/src/contracts/generator.py
def generate(contract) -> list[Experiment]:
    out = []
    for tol in contract.tolerates:
        # "I tolerate 800ms from payment-service" -> inject exactly 800ms,
        # assert MY OWN published SLO still holds.
        out.append(Experiment(
            name=f"contract_{contract.service}_tolerates_{tol.dependency}_latency",
            litmus_fault="pod-network-latency",
            target={"app": tol.dependency},
            params={"latency_ms": tol.max_latency_ms},
            hypothesis=Hypothesis(
                description=(f"{contract.service} claims it tolerates {tol.max_latency_ms}ms "
                             f"from {tol.dependency}; its own P99 SLO must still hold"),
                invariants=[Invariant("consumer_p99_holds", source="k6",
                                      expr="http_req_duration_p99_ms", comparator="<=",
                                      threshold=contract.provides.latency_p99_ms,
                                      tolerance_s=15)]),
            provenance=ContractRef(contract.service, contract.version,
                                   f"tolerates.{tol.dependency}.max_latency_ms")))
    return out
```

The report reads as architecture review, not test output:

```
CONTRACT VALIDATION — order-api v1.2.0            epoch 7f2a…  git 4c19ba2
  tolerates payment-service latency <= 800ms   -> P99 stayed 480ms   HONOURED
  tolerates payment-service errors   <= 5%     -> own errors 12.1%   VIOLATED
        └─ retry budget amplifies: 3 attempts x 5% upstream = 14% effective.
           Reduce maxAttempts to 2 or add a retry budget cap.
  tolerates redis outage             <= 600s   -> not tested         UNTESTED
```

Three properties that make this more than a YAML file:

- **`UNTESTED` is reported**, so contract coverage is a metric (`chaosproof_contract_clause_coverage_ratio`) and gaps are visible rather than invisible.
- **A violated clause names the mechanism** where it can. The retry-amplification finding above — a 5% upstream error rate becoming 14% observed through three retry attempts — is a real distributed-systems failure mode no schema test would ever find. It is the project's best single result; preserve the ability to produce it.
- **`provenance` links every generated experiment back to its clause**, so a failure points at the architectural claim it falsified.

**CI hook:** a PR that *weakens* a contract (raising `max_error_rate_pct`, dropping a degradation mode) surfaces a diff to the clause owners. It does **not** auto-block — a legitimate weakening should be visible to the people who relied on it, not forbidden. Tooling that forces a conversation beats tooling that forces a merge failure.

## Counterfactual pairs

Same fault, run with a resilience pattern enabled and disabled, to measure what the pattern is actually worth.

**Statistical honesty is the whole feature.** A single with/without pair cannot support a number: pod scheduling, endpoint propagation, and JIT warmup all vary between runs.

```python
MIN_REPETITIONS = 5

@dataclass
class CounterfactualResult:
    pattern: str
    n: int
    with_median_failed: float;    with_iqr: tuple[float, float]
    without_median_failed: float; without_iqr: tuple[float, float]
    delta_median: float
    overlap: bool
    verdict: str      # "pattern_effective" | "no_measurable_effect" | "inconclusive"

def analyse(runs) -> CounterfactualResult:
    ...
    if iqr_overlap(with_iqr, without_iqr):
        verdict = "inconclusive"    # NOT "pattern_effective with a smaller number"
```

Rules:

- **n ≥ 5 per arm, interleaved** (`with, without, with, without…`) so drift in cluster conditions affects both arms equally. All-with-then-all-without confounds the comparison with time.
- **Overlapping IQRs → `inconclusive`.** Reporting a median delta from overlapping distributions is how dashboards become fiction. The restraint is itself the interview signal.
- **Cost translation carries its assumptions in the UI**, not buried: revenue per checkout, conversion loss per failed request, incidents per year — each labelled `[assumption]`.
- **Staging only, enforced by policy.** The "without" arm is *designed* to fail harder.
- **Flag snapshot restored by the cleanup saga**, with the CronJob as the out-of-band path.
- **The "without" arm gets a tighter abort threshold** — harder failure is expected, unbounded failure is not.
- **Counterfactual executions are excluded from the resilience score.** They are deliberately-degraded configurations and would poison the trend.

## Adding a new experiment — the checklist

1. Hypothesis YAML with a falsifiable description, invariants tagged hold-throughout or recovery, `min_rps_floor`, and `abort_conditions`.
2. Blast radius reviewed; policy rules checked against it.
3. Correct assertion for the actual fault mechanics — re-read the three corrections above before assuming.
4. `chaos_fixture` in the target app that reliably produces the fault.
5. Registered **advisory-only**; it earns gating status through flakiness characterisation (`chaosproof-mlops-quality`).
6. A bundle added to the replay-eval corpus.

> Interview framing: *"One of my experiments is a fault DAG with conditional triggers rather than fixed delays — I kill Redis, and the second fault only fires if database P99 actually crosses 200ms. The most valuable outcome is when it doesn't fire, because that's my stale-cache fallback absorbing the outage completely, and I can prove it rather than assume it. And my best single finding came from the contract system: order-api claimed it tolerated a 5% error rate from payment-service, but with three retry attempts that became 14% observed. The contract was violated by its own retry policy."*
