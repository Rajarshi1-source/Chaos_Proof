# EXPERIMENTS.md — the model card

ChaosProof's scorer is a deterministic model that maps evidence to a number, so
it gets the treatment a model gets. This is the card: per experiment, what it
claims, what it breaks, how much of the system that touches, what stops it, how
noisy it is, and **whether it is allowed to block a merge**.

The last two columns are the ones to read first. An experiment whose σ is
unknown cannot gate anything, and an experiment that gates without a σ is a
flaky gate waiting to be disabled by the third engineer it fails on.

---

## Gating status, at a glance

| Experiment | Fault | Blast score | σ | Runs | Authority |
|---|---|---|---|---|---|
| `pod_kill_payment_svc` | `pod-delete` | 61 | — | 0 | **advisory** |
| `network_latency_payment` | `pod-network-latency` | 61 | — | 0 | **advisory** |
| `network_partition_payment` | `pod-network-loss` | 61 | — | 0 | **advisory** |
| `disk_fill_inventory` | `disk-fill` | 61 | — | 0 | **advisory** |
| `cpu_spike_payment` | `pod-cpu-hog` | 61 | — | 0 | **advisory** |
| `container_kill_payment` | `container-kill` | 61 | — | 0 | **advisory** |
| 6 × `contract_order_api_tolerates_*` | generated | 61 | — | 0 | **advisory** |
| `counterfactual_*`, `partition_no_fallback` | staging only | 61–87 | n/a | — | **never gates** |

**Every experiment in this repository is advisory. None of them can block a
merge, and that is the correct state, not an omission.** The rule is that a new
or reflagged experiment cannot gate before 20 clean runs on unchanged code
(`FLAKINESS_WINDOW = 20`), and this project has not accumulated 20 real runs of
anything — the cluster is one kind node, and 20 runs of a single experiment is
about 100 minutes of continuous injection per experiment.

GATE 8 (30 Aug 2026) exercised promotion and demotion for real against a
*seeded* window: `pod_kill_payment_svc` measured σ=0.0083 with a 0.0% flip rate
and was promoted to gating, opening epoch `6d6f248dfc76`; it was then
destabilised, re-measured at σ=0.3078 with a 100% flip rate, auto-demoted, and
the demotion opened epoch `c4c49efd79f6`. Every row carried
`trigger_source = 'gate8-synthetic'` and was purged afterwards; the
`flakiness_transitions` audit trail was kept. The measurement, quarantine, epoch
and issue-filing logic all ran unstubbed. **The σ values in that gate are
synthetic and are not carried into the table above, which is why that column
reads `—`.**

Counterfactual experiments never gate under any circumstances: they run a
deliberately degraded configuration, in `chaos-staging` only, and averaging them
into a score would make the system look worse the more carefully it is measured.

---

## The blast-radius arithmetic

```
score = affected_pods + 10 × namespaces + 25 × user_facing + int(50 × replica_fraction)
```

Deliberately simple enough for a reviewer to evaluate mentally. For the standard
single-replica-of-two case: `1 + 10 + 25 + 25 = 61`. A full partition of both
replicas: `2 + 10 + 25 + 50 = 87`. The whole expressible range therefore runs
61–87, which is why `HALF_OPEN_MAX_BLAST_SCORE` is 65 and not the policy file's
60 — a ceiling of 60 sat below the entire range, made the chaos breaker's
recovery path unreachable, and is recorded in `constants.py` as the defect it
was.

---

## The six baseline experiments

### 1. `pod_kill_payment_svc` — `pod-delete`

**Hypothesis (v1).** Killing one of two `payment-service` replicas under 120 rps
holds ≥99.0% client-observed availability and client p99 ≤800ms, because
surviving replicas absorb traffic via Service endpoints and `order-api`'s
circuit breaker never needs to open. The killed replica returns to Available
within 120s of the dip.

| | |
|---|---|
| Target | `target-app` / `payment-service`, `PODS_AFFECTED_PERC=50` |
| Duration / recovery | 60s / 120s |
| Floor | 90 rps (below → `INVALID`) |
| Invariants | `client_availability_holds` (≥0.990, tol 10s) · `client_p99_holds` (≤800ms, tol 15s) · `no_server_5xx_storm` (≤1.0/s, tol 10s) · `replicas_restored` (≥2, **recover within 120s**) |
| Aborts | `availability_collapse` (<0.80) · `unrelated_namespace_impact` (>0.5/s) |
| Blast | 61 · 1 pod · 50% of replicas · user-facing |

**Why `CHAOS_INTERVAL` is left at the default.** It defaults to the full
duration, which produces a *single* kill event — and a single kill is the claim
the hypothesis actually makes. Repeated kills would be a different experiment
with a different hypothesis.

**Recovery counts from first breach, not from engine-apply.** Litmus bootstrap
(image pull, helper scheduling) sits between applying the engine and the actual
kill. A deadline measured from apply would be reporting the injector's startup
latency as the system's recovery time.

---

### 2. `network_latency_payment` — `pod-network-latency`

**Hypothesis (v1).** 500ms of injected latency degrades client p99 within a
declared chaos budget without breaching availability, and p99 returns to the
steady-state band after the fault clears.

| | |
|---|---|
| Target | `target-app` / `payment-service`, `NETWORK_LATENCY=500` |
| Duration / recovery | 60s / 120s |
| Invariants | `client_availability_holds` · `client_p99_degraded_within_budget` · `p99_recovers_to_steady` · `no_server_5xx_storm` |
| Aborts | `availability_collapse` · `unrelated_namespace_impact` |

**This is where the retry-amplification finding reproduced** (GATE 9). Latency
is the fault that exposes it: a retry that looks free under a fast failure
multiplies offered load against a slow one.

---

### 3. `network_partition_payment` — `pod-network-loss` at 100%

**Hypothesis (v2).** Under total packet loss to `payment-service`, availability
holds **via the fallback** rather than via the service, the circuit breaker
opens, and it short-circuits subsequent calls rather than merely opening.

| | |
|---|---|
| Target | `target-app` / `payment-service`, `NETWORK_PACKET_LOSS_PERCENTAGE=100` |
| Duration / recovery | 60s / 120s |
| Invariants | `availability_via_fallback` · `circuit_breaker_opens` · `breaker_short_circuits` · `no_server_5xx_storm` |

**Hypothesis version 2, and the version bump matters.** Changing what an
experiment asserts invalidates its flakiness history — the earlier runs measured
a different claim — so a version bump resets the experiment to advisory. That is
a feature of the promotion rule, not an inconvenience.

`breaker_short_circuits` is separate from `circuit_breaker_opens` on purpose. A
breaker that opens and then still passes calls through has satisfied the state
metric and none of the intent.

---

### 4. `disk_fill_inventory` — `disk-fill`

**Hypothesis (v1).** Filling the container's ephemeral storage causes the pod to
be **evicted and rescheduled**, with no data loss and availability held by the
surviving replica.

| | |
|---|---|
| Target | `target-app` / `inventory-service`, `FILL_PERCENTAGE=100` |
| Duration / recovery | 60s / 180s |
| Invariants | `pod_evicted` · `replicas_restored` · `client_availability_holds` · `no_server_5xx_storm` |

**The assertion is pod eviction, not `NodeDiskPressure`.** This is the
correction that produces a confident wrong verdict if missed. Filling one
container's ephemeral storage evicts *that pod* via the kubelet's
ephemeral-storage limit; it does not necessarily push the whole node into
`NodeDiskPressure`, and an experiment asserting the node condition would fail
against a system that behaved perfectly. The eviction only happens at all
because the deployment declares an `ephemeral-storage` limit — without one there
is nothing to exceed.

---

### 5. `cpu_spike_payment` — `pod-cpu-hog`

**Hypothesis (v1).** A CPU hog on one replica raises its throttle ratio, the
**aggregate** SLI holds because HPA adds healthy replicas, and the throttle
ratio recovers after the fault.

| | |
|---|---|
| Target | `target-app` / `payment-service`, `CPU_CORES=1`, 50% of pods |
| Duration / recovery | 60s / 180s |
| Invariants | `aggregate_availability_holds` · `client_p99_within_chaos_budget` · `throttle_ratio_recovers` (≤0.05) · `hpa_scales_up` |

**It never asserts that the CPU spike disappears.** The second correction that
produces a wrong verdict if missed: HPA responds by adding *healthy* replicas
while the hogged pod stays hogged for the whole fault duration. An experiment
waiting for the spike to subside is waiting for something that will not happen,
and would report a failure against textbook-correct behaviour. The assertions
are the throttle ratio on the affected pod and the aggregate SLI across the
workload — which is what a user experiences.

Throttle and pressure signals are only trustworthy on cgroup v2, which is why
Kubernetes 1.35 is the floor.

---

### 6. `container_kill_payment` — `container-kill`

**Hypothesis (v1).** Killing the container inside the pod causes a restart, and
the readiness probe keeps traffic away until the container is serving again.

| | |
|---|---|
| Target | `target-app` / `payment-service`, `TARGET_CONTAINER=payment-service` |
| Duration / recovery | 30s / 180s |
| Runtime | **`containerd`**, socket `/run/containerd/containerd.sock` |
| Invariants | `client_availability_holds` · `container_restarted` · `replicas_restored` · `no_server_5xx_storm` |

**CRI/containerd, not Docker.** The third correction. Litmus's container-kill
defaults still reference Docker in much of the documentation; on a kind cluster
running containerd, a Docker socket path produces a fault that never fires — and
a fault that never fires against a live load plane looks exactly like a system
that survived it.

---

## Contract-generated experiments

Six experiments under `experiments/generated/`, one per `tolerates` clause in
`order-api`'s resilience contract: `payment_service` and `inventory_service` ×
`latency`, `errors`, `outage`.

They carry `generated: true` and `gating: false` in the spec itself. A generated
experiment is a new experiment: it starts advisory like any other and earns
gating status the same way. Regenerating a contract produces a spec whose
hypothesis version may have moved, which resets the clock — correctly, because
the claim changed.

---

## Counterfactual pairs — and why they never gate

| Experiment | Pattern disabled | Namespace |
|---|---|---|
| `counterfactual_pod_kill_fallback` | `paymentService.fallback` | `chaos-staging` |
| `counterfactual_partial_loss_fallback` | `paymentService.fallback` | `chaos-staging` |
| `partition_no_fallback` | `paymentService.fallback` | `chaos-staging` |

Three constraints, all enforced rather than documented:

1. **`chaos-staging` only.** Running a deliberately degraded configuration
   anywhere else is not an experiment, it is an outage you scheduled.
2. **Excluded from the score and the trend.** They measure a configuration
   nobody ships.
3. **No delta is reported when the IQRs overlap.** `inconclusive` is a visible
   state on the panel, and GATE 10 produced one for the retry pattern.

---

## What every experiment carries, without exception

- **A falsifiable hypothesis** written *before* the result was seen, with a
  version number.
- **`min_rps_floor`.** Below it the run is `INVALID` — not `PASS`, and not a
  zero. No traffic means no evidence.
- **`abort_conditions`.** No experiment ships without them. They become Litmus
  `promProbe`s in Continuous mode with `stopOnFailure`, plus an independent
  Python watchdog that does not share a failure domain with the probes.
- **Advisory-only registration.** Gating is earned, never declared.

---

## What is measured and what is not

**Measured, unstubbed:** every hypothesis evaluation, every validator, the
scorer and its renormalisation, the epoch mechanism, flakiness measurement and
the quarantine transition, evidence bundling and offline replay, the contract
generator and diff, the counterfactual analysis, and every refusal path in
pre-flight, policy and bisection. 396 hermetic tests, no cluster required.

**Not measured:** run-to-run σ for any of the six experiments against real
injection, because that needs 20 clean runs each on a cluster this project has
one of. The consequence is stated at the top of this file rather than buried:
nothing gates.

**Not executed at all:** the live bisection driver (`bisect/driver.py`), which
needs one cluster exclusively for ninety minutes with a rebuild between
candidates. Its search logic, arithmetic, cost model and refusals are tested; its
shell-outs are not.
