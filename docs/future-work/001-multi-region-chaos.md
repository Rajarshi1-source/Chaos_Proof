# FW-001 — Multi-region chaos

**Status:** deferred · **Date:** 2026-08-31

## What it would be

Injecting faults that a single cluster cannot express: a region becoming
unreachable, cross-region replication lag, a global load balancer failing over,
a control plane partitioned from its data plane.

## Why it is not built

**Cross-region failure is a different failure model, not a bigger version of
this one.** Almost every assumption in the current design is single-cluster:

- **Blast radius is a pod count.** `affected_pods + 10 × namespaces + 25 ×
  user_facing + int(50 × replica_fraction)` produces a number between 61 and 87
  for everything this project can express. A region-level fault is not a larger
  value on that scale — it is a different unit, and reusing the scale would put
  "one pod of two" and "all of eu-west-1" on the same axis.
- **The client-side SLI has one vantage point.** k6 runs *inside* the cluster.
  During a region failure the interesting question is what a client in
  *another* region observes, and an in-cluster load generator cannot answer it.
  The measurement would be systematically wrong in the flattering direction,
  which is the failure mode this project exists to avoid.
- **The error-budget gate is one SLO over one service topology.** Multi-region
  budgets are usually per-region *and* global, and "the budget" stops being a
  scalar.
- **Recovery timing loses its meaning.** `recover_within_s` measured from first
  breach assumes one clock and one convergence process. Failover across regions
  has DNS TTLs and propagation delays in it that are not the system recovering.

Building it on top of the current abstractions would produce numbers. It would
not produce trustworthy ones, and this project's entire claim is the difference.

## What would change it

A second region carrying real traffic, and an SLO defined *across* both — plus
at least one out-of-region measurement vantage point, because otherwise the
availability number is computed from inside the thing being tested.

## The honest scale statement

This belongs in the same paragraph as the rest of the scale weakness: six
experiments a day, three services, one cluster, no multi-tenancy, no HA
injection, no VM chaos. Flipkart's KubeCon India talk describes solving four of
those on top of Litmus, and the README cites it as their description rather than
as analysis done here.
