---
name: chaosproof-measurement-integrity
description: "Guarantee that ChaosProof's SLI numbers mean something. Read this BEFORE writing any code that computes, queries, or reports an SLI. Covers the k6 load plane (k6 2.2.x plus k6 Operator v1.0 in-cluster), the open constant-arrival-rate workload model and why constant-vus hides the fault, the SLI validity gate and the INVALID verdict, dropped_iterations as generator saturation, client-side k6 metrics as the primary source of truth, the client-versus-server availability gap, 5s scrape with 30s rate windows, and honest alert latency that subtracts irreducible detection lag. Trigger on SLI, availability, load generator, k6, TestRun, constant-arrival-rate, workload model, validity gate, INVALID, dropped iterations, rate window, scrape interval, recovery time, throttle ratio, PSI, alert latency, or PromQL. MANDATE: no traffic means no evidence, and no evidence must never score as success. Never compute availability from server-side metrics alone, and never widen a rate window past the SLO it is compared against."
---

# ChaosProof Measurement Integrity

**Read this skill before writing any code that computes, queries, or reports an SLI.** Rev 1 of this project had a defect that invalidated its headline number: there was no load generator, so every SLI was a ratio over a request counter with nothing generating requests — `0/0`. A pod-kill experiment against an idle cluster reported a perfect recovery because nothing was in flight to fail. This skill exists so that cannot happen again.

The governing rule: **no traffic means no evidence, and no evidence must never score as success.**

Read `references/promql-and-sli.md` for the full corrected query library, window arithmetic, and threshold constants before writing PromQL.

## The three planes, and which one is the source of truth

| Plane | Tool | Role |
|---|---|---|
| **Load** | k6 2.2.x via **k6 Operator v1.0**, in-cluster as a `TestRun` CR | Generates the traffic that makes measurement possible. **A precondition, not a best-effort dependency** |
| **Client SLI** | k6's own metrics, exported to Prometheus | **Primary source of truth** for availability and latency |
| **Server SLI** | `http_server_requests_seconds_*` from Spring Boot Actuator | Corroboration, and one genuinely useful derived signal |

Load runs **in-cluster**, never from a laptop or a GitHub runner: the network path under test must be the real one, and the load must outlive the CI job.

## Why the client is the source of truth

**Server-side metrics structurally under-report pod kills.** When a pod is deleted, in-flight and newly-arriving requests to that endpoint fail at the TCP or HTTP layer *before reaching any application*. Those failures never increment `http_server_requests_seconds_count`, because that counter only exists inside a running server. So server-side availability computed as `non-5xx / total` is biased **upward** during exactly the fault this project leads with.

That gap is itself a signal worth exporting:

```
client_error_rate − server_error_rate  ≈  requests that never reached a server
```

Export it as `chaosproof_client_server_availability_gap{experiment}` and show it on the dashboard as a shaded overlay. It is a direct estimate of requests that died before they were served.

## Open workload model — never `constant-vus`

This is the second documented scaling decision and it is non-negotiable in this project.

```javascript
// profiles/steady_120rps.js
export const options = {
  discardResponseBodies: true,
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',   // OPEN model. Never constant-vus.
      rate: 120, timeUnit: '1s',
      duration: '10m',
      preAllocatedVUs: 200, maxVUs: 800,   // headroom so k6 never becomes the bottleneck
    },
  },
  thresholds: {
    'http_req_failed':    ['rate<0.01'],
    'http_req_duration':  ['p(99)<800'],
    'dropped_iterations': ['count<10'],    // exceeded => the run is INVALID
  },
};
```

**Why a closed model is disqualifying:** with `constant-vus`, each virtual user waits for a response before sending the next request. When the system slows down, offered load automatically falls. A pod kill that doubles latency halves throughput — so fewer requests are in flight to fail, the error *count* drops, and **the availability ratio can improve while users are having a worse time.** The fault partially hides itself. An open model launches requests on a schedule regardless, so queues build the way real traffic does.

**Trade-off to state, not hide:** an open model *can* overwhelm the target, so it needs `preAllocatedVUs`/`maxVUs` headroom and `dropped_iterations` monitoring to distinguish "the system is failing" from "the generator is failing." A closed model is correct for capacity planning ("how many concurrent users can this serve?"), never for fault measurement.

## The validity gate

Every verdict must pass this before it is scoreable. `INVALID` is a **third verdict**, distinct from pass and fail, and it never contributes to the resilience score.

```python
MIN_SAMPLE_COVERAGE = 0.90
DROPPED_CEILING     = 10

def is_valid(self, min_rps_floor: float) -> bool:
    if self.achieved_rps < min_rps_floor:
        self.invalidity_reason = (f"achieved {self.achieved_rps:.0f} rps, floor is "
                                  f"{min_rps_floor:.0f} — cannot measure availability")
        return False
    if self.dropped_iterations > DROPPED_CEILING:
        self.invalidity_reason = ("k6 could not sustain the arrival rate — the load "
                                  "generator was the bottleneck, not the system")
        return False
    if self.coverage < MIN_SAMPLE_COVERAGE:
        self.invalidity_reason = f"only {self.coverage:.0%} sample coverage in the window"
        return False
    return True
```

`dropped_iterations` deserves attention: under an open model, if k6 cannot start iterations at the requested rate it reports drops, and drops mean the generator saturated before the system did — so any conclusion about the system is unfounded. Note that a naive addition of k6 using `constant-vus` would have hidden this failure mode permanently.

Every experiment declares its own `min_rps_floor` in its hypothesis YAML. There is no global default worth trusting.

## Recovery timing that is physically possible

Rev 1 asserted a 30-second recovery SLO while measuring with `rate(...[5m])`. A 5-minute rate window is a 5-minute moving average: after an instantaneous full recovery it needs minutes to return to baseline. **The measurement lag exceeded the thing being measured by an order of magnitude.**

| Setting | Wrong | Right | Why |
|---|---|---|---|
| Target-app scrape interval | 30s (chart default) | **5s**, on the target `ServiceMonitor` only | A 30s window needs ≥2 samples; 5s scrape gives 6 |
| SLI rate window | `[5m]` | **`[30s]`** during experiments | Shortest window that is statistically meaningful at 5s scrape |
| Latency percentile | `histogram_quantile(0.99, ...[5m])` | k6 client-side p99, native histograms where available | Server-side quantiles over long windows cannot resolve a 30s event |
| Primary recovery clock | Prometheus | **k6 time series (1s granularity)** | The client knows it is succeeding again ~5s before Prometheus does |

Raise scrape frequency **only for the target app.** Cluster-wide 5s scraping multiplies Prometheus load for no benefit — scoping it is the right call and worth saying out loud, because it shows you understand the cost of your own instrumentation.

## What "recovered" means — three conditions, all required

1. **Client-observed SLI restored** — k6 error rate below threshold and **holding for 30 consecutive seconds**. A single sample flaps; never accept one.
2. **Golden signals recovered** — no new restarts; CPU throttle **ratio** within ceiling (dimensionless — raw throttled-period counters move with replica count and window length and would make the check meaningless); PSI full-stall share within ceiling; `kube_deployment_status_replicas_available` back at target.
3. **No new alert on the target that the fault could have caused** — a fix that starts a different problem is not a recovery.

Signals unavailable → `INVALID` and escalate. Never a silent success.

## Alert latency, measured honestly

"Did the alert fire within 60 seconds?" mostly grades your own Prometheus configuration, because much of the delay is irreducible:

```
irreducible_latency = scrape_interval + evaluation_interval + rule `for:` duration
                    = 5s              + 30s                 + 60s              = 95s
```

With a `for: 1m` rule, a 60-second target is **unachievable by construction** — Rev 1 would have scored a correctly-configured alerting stack as a failure on every single run.

```python
detection_latency = alert_fired_at - fault_injected_at        # what happened
irreducible       = scrape_interval + evaluation_interval + rule_for_duration
excess_latency    = detection_latency - irreducible           # what you control

# Grade excess against the SLO; report BOTH. And lint the config itself:
assert rule_for_duration + evaluation_interval + scrape_interval <= slo_alert_latency_s, \
    "alert rule cannot possibly meet its own latency SLO — fix the rule, not the system"
```

That assertion ships as `evals/alert_rule_lint.py` and **gates CI**. It catches alert SLOs that are arithmetically impossible before anyone relies on them — a genuinely useful piece of tooling and a strong interview moment.

Alert state comes from **Alertmanager API v2** (`GET /api/v2/alerts?filter=...`); v1 was removed in 0.27.

## The dual-source sampler

```python
# Fixed-deadline scheduling. Rev 1 used `await asyncio.sleep(5)` AFTER sequential
# awaits, so the sample period was 5s PLUS query latency and the timeline drifted.
async def stream(self, experiment, until: float, interval_s: int = 5, on_abort=None):
    next_tick = time.monotonic()
    samples = SampleSet()
    while time.monotonic() < until:
        next_tick += interval_s
        sampled_at = time.time()                      # record ACTUAL timestamps
        client, server = await asyncio.gather(
            self.k6.snapshot(experiment), self.prom.snapshot(experiment))
        samples.add(sampled_at, client, server)
        if on_abort and (reason := self.check_abort(experiment, samples)):
            on_abort(reason); break
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
    return samples
```

Persist every sample to `sli_samples` (partitioned monthly). This is not logging — it is the **retro-scoring substrate**: because raw observations are stored, a scorer change can re-score historical runs and produce a comparable history (see `chaosproof-mlops-quality`).

## Dashboard panels this skill owns

SLI-validity strip (achieved rps vs floor, red for `INVALID` runs) · client-vs-server availability overlay with the gap shaded · recovery timeline marking fault, first client failure, alert fired, the irreducible-latency marker, and SLI restored as distinct events · `dropped_iterations` as load-generator health.

## Non-negotiables when editing this area

1. **Load starts before pre-flight, which runs before injection.** In CI too — a chaos gate with no load plane reproduces the original defect inside the pipeline where a green badge hides it.
2. **A missing or empty series is never a passing series.** If `resilience4j_circuitbreaker_state` returns nothing because `resilience4j-micrometer` was never on the classpath, a naive validator sees "no failures" and passes. Return `invalid`.
3. **Never widen a rate window to make a query "work."** If the window must exceed the SLO to return data, the SLO is unmeasurable and that is the finding.
4. **Never compute availability from server-side metrics alone.**
5. **`INVALID` never contributes to a score.** Not as zero, not as partial credit — the experiment is simply not scoreable.

> Interview framing: *"My first version had no load generator, so every SLI was zero over zero and every experiment passed. Now k6 holds a constant arrival rate in-cluster through the whole experiment, and any run below my rps floor — or where k6 reports dropped iterations, meaning the generator saturated before the system did — is `INVALID` rather than `PASS`. I'd rather my dashboard say 'I couldn't tell' than lie to me."*
