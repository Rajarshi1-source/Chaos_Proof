# ChaosProof — chaos engineering with a verification layer

> Don't hope your system is resilient — prove it every day, and **refuse to
> answer when you can't measure.**

ChaosProof injects controlled faults into a Kubernetes microservices
application, holds steady synthetic traffic through the fault, and decides
whether the system's behaviour matched a **written hypothesis**. Every
experiment produces a content-addressed evidence bundle you can replay offline.

The headline: **chaos runs in CI.** A PR that removes a circuit breaker fails
the gate.

The reason the headline is not the interesting part: at KubeCon India in June
2026 the most common question at the LitmusChaos booth was how to shift chaos
left into CI/CD. The idea is in the air. What is not in the air is the layer
underneath it — **stating a hypothesis, proving you could measure it, gating on
safety, deciding honestly, and refusing to answer when the measurement was
invalid.**

---

## Architecture

```
                       ┌─────────────────── ChaosProof ───────────────────┐
   GitHub PR ──────────►│ CI runner  ──┐                                   │
   CronJob 03:00 ───────►│ Scheduler ───┤                                   │
                        │              ▼                                   │
                        │   ┌──── Pre-flight gate ────┐                    │
                        │   │ steady state · SLI floor │                   │
                        │   │ blast radius · budget    │                   │
                        │   └──────────┬───────────────┘                   │
                        │              │ proceed / skip / deny             │
   ┌─── Load plane ─────┤              ▼                                   │
   │ k6 Operator        │      Experiment runner ──► LitmusChaos CRD ──────┼──► target-app
   │ constant-arrival   │              │                    │              │    (Spring Boot 4
   │ 120 rps            │              │            probes (abort)         │     + Resilience4j)
   └────────┬───────────┘              ▼                    │              │         │
            │              Sampler (5s, dual-source) ◄──────┘              │         │
            │                          │                                  │         ▼
            └──────────────────────────┼──── client SLIs ─────────────────►│    Prometheus
                                       ▼                                   │    (5s scrape)
                              Hypothesis engine                            │         │
                                       │                                   │◄────────┘
                                       ▼                                   │
                      HELD / FALSIFIED / INVALID / ABORTED                 │
                                       │                                   │
                    ┌──────────────────┼──────────────────┐                │
                    ▼                  ▼                  ▼                │
              Score (epoch)      Evidence bundle       Slack               │
                    │             (signed, CAS)                            │
                    ▼                  ▼                                   │
              PostgreSQL 18      chaosctl replay                           │
                    │                                                      │
                    ▼                                                      │
              Next.js dashboard ◄───── Valkey cache                        │
                        └──────────────────────────────────────────────────┘
```

**The one arrow that matters is the leftmost one.** The load plane starts
*before* pre-flight, which runs before injection — in CI too. A chaos gate with
no load plane injects faults into an idle system: every invariant reads an empty
series, nothing can be falsified, the job goes green, and the badge says the
resilience gate passed. That is the original defect of this project reproduced
inside the pipeline, which is the hardest place to notice it.

---

## The six experiments

| Experiment | Hypothesis (abbreviated) | Authority |
|---|---|---|
| Pod kill | ≥99.0% client availability, p99 ≤800ms, replicas restored ≤120s | advisory |
| Network latency 500ms | p99 degrades within budget and recovers; availability holds | advisory |
| Network partition | availability **via fallback**, breaker opens *and* short-circuits | advisory |
| Disk fill | pod **evicted** and rescheduled, no data loss | advisory |
| CPU spike | throttle ratio recovers, **aggregate** SLI holds, HPA reacts | advisory |
| Container kill | restart via CRI, readiness probe holds traffic off | advisory |

Every one of them is advisory, and **that is the correct state.** An experiment
earns the right to block a merge after 20 clean runs on unchanged code; this
project has not accumulated 20 real runs of anything. See
[`EXPERIMENTS.md`](EXPERIMENTS.md) — the model card — for each experiment's
hypothesis, blast radius, abort conditions, σ and gating status.

---

## What makes this more than a Chaos Monkey clone

1. **Hypothesis-first** — falsifiable claims in YAML with per-invariant
   verdicts, not "did it recover?"
2. **It refuses to lie** — no traffic, missing metrics, or a saturated load
   generator all produce `INVALID`, never `PASS`. `INVALID` is not a zero and
   never contributes to a score.
3. **Client-side truth** — server-side metrics cannot see a request that never
   reached a server, and the client-vs-server gap is rendered rather than hidden.
4. **Safety plane** — blast radius before, abort during, saga cleanup after,
   with an out-of-band CronJob because cleanup must not depend on the happy path.
5. **Error-budget gated** — chaos does not run when the budget is spent.
6. **Counterfactual ROI** — the same fault with the pattern on and off, n=5,
   medians with IQRs, and a visible `inconclusive` when they overlap.
7. **Contracts generate experiments** — declared tolerances become tests.
8. **Flakiness quarantine** — an experiment that cannot hold σ<0.05 cannot block
   your merge, and it files an issue against itself.
9. **Scoring epochs** — the trend chart does not silently include scorer edits,
   and it never draws a line across a boundary.
10. **Signed, replayable evidence** — `chaosctl replay <sha>` offline,
    byte-identical, no cluster and no network.
11. **Bisection that abandons** — a candidate inside the flakiness band returns
    `abandoned`, never a guess.

---

## Head-to-head

| Capability | Chaos Monkey | LitmusChaos (alone) | Chaos Mesh | Gremlin | AWS FIS | xk6-disruptor | **ChaosProof** |
|---|---|---|---|---|---|---|---|
| Fault injection | pod/instance kill | ✅✅ 50+ faults | ✅✅ | ✅✅ | ✅ AWS only | ✅ k8s subset | ✅ *(via Litmus)* |
| Scheduling | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| In-experiment probes | ❌ | ✅ | ✅ | ✅ | ✅ | partial | ✅ *(as aborts)* |
| **Load generated as part of the experiment** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ *(is a load tool)* | ✅ |
| **Refuses to score unmeasurable runs (`INVALID`)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Client-side SLI as primary truth** | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| **Falsifiable hypothesis, per-invariant verdict** | ❌ | partial (probes) | partial | partial | ❌ | ❌ | ✅ |
| Alert-firing validation | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Honest alert latency (irreducible lag subtracted)** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Resilience-pattern activation validated | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Error-budget-gated execution** | ❌ | ❌ | ❌ | partial | ❌ | ❌ | ✅ |
| **Blast radius computed pre-flight** | ❌ | ❌ | ❌ | ✅ | partial | ❌ | ✅ |
| Chaos in CI blocking merges | ❌ | via CLI | via CLI | ✅ | ❌ | ✅ | ✅ |
| **Gate flakiness measured + auto-quarantine** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Counterfactual pattern ROI** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Contract-generated experiments** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Conditional fault DAG (cascades)** | ❌ | workflows (temporal) | ✅ workflows | ✅ scenarios | partial | ❌ | ✅ *(conditional)* |
| **Score comparability across scorer changes** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| **Signed, offline-replayable evidence** | ❌ | ChaosResult CR | CR | reports | reports | JSON | ✅ |
| Regression bisection | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Multi-tenancy, HA injection, VM chaos | ❌ | ✅ *(Flipkart's extensions)* | ✅ | ✅✅ | ✅ | ❌ | ❌ |
| Maturity, support, integrations | ✅ (historic) | ✅✅ CNCF | ✅✅ CNCF | ✅✅ | ✅✅ | ✅ | ❌ 12-week project |

### Where they win

A table with no losses reads as marketing, so here is the honest paragraph.

**LitmusChaos** has a CNCF-backed fault catalogue spanning Kubernetes, Linux,
AWS and GCP, monthly releases, and production adopters including Flipkart,
Canonical, Intuit, Adidas and Red Hat. **ChaosProof runs on it and would be
strictly worse without it** — every fault in this repository is a Litmus fault.
**Chaos Mesh** has excellent network chaos, better than what is used here.
**Gremlin** is a mature commercial platform whose blast-radius controls are more
sophisticated than the simplified arithmetic in `safety/blast_radius.py`; that
simplification is a teaching choice, not a superiority claim. **AWS FIS**
integrates with managed services this project cannot touch at all.
**xk6-disruptor** deserves particular respect: it means a thin version of the
load-plus-fault idea already exists inside k6 and ships from Grafana, and it is
better to say that first than to have an interviewer say it. **Chaos Monkey**
earned the category and is still the reason anyone runs any of this.

And the honest weakness that no column captures: **scale.** Six experiments a
day, three services, one cluster. No multi-tenancy, no HA chaos injection, no VM
chaos. Flipkart's KubeCon India talk describes solving exactly those four
problems on top of Litmus — hybrid multi-tenancy, DaemonSet HA injection, a
Script Runner fault, and hybrid VM chaos — and reading it is how you find out
what you have not solved. *(Cited as their talk describes it; the recording has
not been independently reviewed here.)*

**ChaosProof is not a replacement for any of them.** It is a demonstration of
the verification layer chaos engineering is missing: state a hypothesis, prove
you can measure it, gate on safety, decide honestly, and refuse to answer when
the measurement was invalid.

---

## What ChaosProof deliberately does NOT do

- **Inject faults in production** without an error-budget gate and a human-set
  radius cap.
- **Run counterfactual (pattern-disabled) experiments outside `chaos-staging`.**
  A deliberately degraded configuration anywhere else is not an experiment, it
  is a scheduled outage.
- **Let an LLM produce or alter a verdict.** `TemplateNarrator` is the default
  so the system works with no API key and no network; an `LLMNarrator` draft is
  discarded if it references any number not in the evidence bundle, and the
  discards are counted.
- **Report a score when it could not measure.** That is what `INVALID` is for,
  and it is not a zero.
- **Target its own namespace.** The framework's service account has no
  destructive verbs on the target; those are granted to Litmus, scoped per
  experiment.
- **Bisect automatically on a score dip.** Ninety minutes of exclusive cluster
  time triggered by noise is a denial of service against the daily schedule.
- **Ship trigger controls in the public build.** They are absent via a
  build-time flag, not disabled — a control that 404s invites someone to find
  out why.
- **Widen a rate window to make a query work.** If the window must exceed the
  SLO to return data, the SLO is unmeasurable and *that is the finding*.

Also deliberately absent, with reasons in
[`docs/future-work/`](docs/future-work/): multi-region chaos, security chaos,
game-day scoring, and a Litmus MCP server — a natural-language interface to
fault injection is a safety surface, not a feature.

---

## Quick start

```bash
make kind-up          # kind 1.36 + kube-prometheus-stack + Litmus 3.31 + k6 Operator
```
```bash
make evidence-up      # PostgreSQL 18.6 + migrations (idempotent)
```
```bash
make target-app       # Spring Boot 4 services with Resilience4j
```
```bash
make load             # k6 TestRun, 120 rps open model — ALWAYS BEFORE AN EXPERIMENT
```
```bash
make experiment NAME=pod_kill_payment_svc
```
```bash
make dashboard        # http://localhost:3000
```

Or everything at once:

```bash
make bootstrap
```

### `chaosctl`

```bash
python -m src.chaosctl run pod_kill_payment_svc
```
```bash
python -m src.chaosctl replay 21a5bf73        # offline: no cluster, no network, no DB
```
```bash
python -m src.chaosctl flakiness --apply
```
```bash
python -m src.chaosctl bisect pod_kill_payment_svc --good <sha> --bad <sha> --estimate
```

The hermetic gates need nothing but Python:

```bash
cd chaos-framework && python -m pytest -m "not chaos"
```

---

## Design decisions

Seven ADRs in [`docs/adr/`](docs/adr/), each naming the alternative it rejected
and **the threshold at which it flips**:

- [ADR-001](docs/adr/001-litmuschaos-over-alternatives.md) — LitmusChaos over
  Chaos Mesh, Gremlin, xk6-disruptor
- [ADR-002](docs/adr/002-plain-postgresql-over-timescaledb.md) — plain
  PostgreSQL over TimescaleDB, with the volume that would change it
- [ADR-003](docs/adr/003-open-workload-model.md) — **open workload model over
  closed**, and why constant-VUs hides the fault
- [ADR-004](docs/adr/004-sequential-experiment-execution.md) — sequential over
  parallel experiments
- [ADR-005](docs/adr/005-resilience4j-over-native-spring.md) — Resilience4j over
  Spring Boot 4 native resilience, with observability as the criterion
- [ADR-006](docs/adr/006-client-side-slis-as-primary.md) — client-side SLIs
  primary, server-side corroborating
- [ADR-007](docs/adr/007-bounded-bisection-with-visible-cost.md) — bounded
  bisection with visible cost

---

## Screenshots

1. Resilience gauge and 30-day trend with a **labelled epoch boundary** — the
   line stops at the boundary rather than crossing it
2. Hypothesis verdict with the falsified invariant highlighted
3. Client-vs-server availability overlay, the gap shaded
4. Recovery timeline: fault → first failure → alert (with the irreducible-lag
   marker) → restored
5. Counterfactual panel: with/without box plots, IQRs, and the `inconclusive`
   state visible
6. CI gate blocking a PR that removed `@CircuitBreaker`
7. Flakiness panel with a quarantined experiment

---

## Two-tier deploy

The public demo is a **read-only build**. Trigger controls are removed at build
time by `READ_ONLY`, not merely disabled — the components are never bundled, so
there is no disabled button and no route that 404s. `npm run verify:readonly`
asserts that the production bundle contains no trigger surface at all.

```bash
READ_ONLY=true npm run build && npm run verify:readonly    # public
```
```bash
READ_ONLY=false npm run build                              # internal
```

---

## Stack

Python 3.14 · Spring Boot 4.1 · Resilience4j 3 (Java 21) · k6 2.2 + k6 Operator
v1.0 · LitmusChaos 3.31.0 · Kubernetes 1.36 (1.35 floor, cgroup v2) ·
Prometheus with 5s scrape on the target · PostgreSQL 18.6 · Next.js 16.3 on Node
24 LTS · Helm 4.2 · Grafana 13 · cosign keyless OIDC.

Nothing is `:latest`, anywhere, including compose files and chart values.

---

## Demo

The five-minute script is in [`docs/DEMO.md`](docs/DEMO.md). Its order is
deliberate: **it opens with a bug in this project's own tooling**, because an
engineer who shows how their instruments used to lie to them — and the mechanism
that stops it — has established something no green dashboard can buy.
