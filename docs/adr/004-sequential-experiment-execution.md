# ADR-004 — Sequential experiment execution over parallel

**Status:** accepted · **Date:** 2026-08-23

## Context

Six experiments take roughly fifteen minutes run one after another. Run
concurrently they would take about three. For a 03:00 scheduled job the
difference is invisible; for the CI gate, where two experiments run on every PR,
it is the difference between a five-minute check and a two-minute one.

So there is a real temptation here, and it is worth being explicit about why it
is refused.

## Options

**Parallel.** Fast. But a CPU spike running concurrently with a network
partition makes it impossible to say which fault caused which SLI breach.
Attribution is the entire product: "pod kill caused client availability to drop
to 94% and recovery took 12.4 seconds" is a finding, and "something we did to
the cluster in the last three minutes caused availability to drop" is a shrug.
Validation does not become harder under parallelism — it becomes meaningless.

There is a second, quieter problem. Blast radius is computed pre-flight against
the *current* state of the cluster. Two experiments injecting at once each
computed their radius against a cluster the other was about to change, so the
combined radius is larger than either gate approved. The safety plane's
arithmetic assumes it is the only thing perturbing the system.

**Sequential.** Fifteen minutes, with a recovery window between experiments so
each starts from a re-established steady state.

## Decision

**Sequential**, enforced at two levels rather than assumed:

- A single consumer on the execution queue, so serial execution is a property of
  the architecture rather than of the scheduler's politeness.
- Litmus pinned at ≥3.29.0, which fixed duplicate triggers under concurrent
  reconciles. Serial execution at the framework level does not survive an
  operator that fires the same engine twice.

The recovery window between experiments is not padding. Pre-flight re-checks
steady state before the next injection, and an experiment that starts while the
previous one is still recovering measures the recovery, not the fault.

CI runs two experiments, not six — pod kill and network partition — taking about
five minutes. That is a scope decision, not a concurrency one.

## Consequences

The daily schedule occupies roughly fifteen minutes of one cluster. Bisection
(ADR-007) consumes ninety, which is why it is confined to a nightly window that
does not overlap the 03:00 run: two things injecting faults into one cluster at
once is not two experiments, it is one confounded experiment.

## The reversal condition

**Fifty or more services.** At that point experiments would parallelise across
**disjoint dependency subgraphs** — a pod kill on service A concurrently with
network latency on service B, where A and B share no downstream. Same-service
experiments stay serial always.

That requires two things this project does not have: a real dependency graph
rather than a three-service topology, and a blast-radius calculation that is a
graph query rather than a pod count. At that scale blast radius becomes the
bottleneck rather than execution — "what else does this touch" is the expensive
question once the topology is large enough for parallelism to be worth having.
