# ADR-001 — LitmusChaos as the fault injector

**Status:** accepted · **Date:** 2026-08-23 · **Supersedes:** none

## Context

ChaosProof needs something to actually break pods, add latency, drop packets,
fill disks and burn CPU inside a Kubernetes cluster. That is a solved problem
with at least four mature answers, and building a fifth would be the least
interesting part of this project.

The real question is not "which injector is best" but **which injector leaves
the most room for the layer this project is actually about** — hypothesis
verification, measurement validity, and safety gating. An injector that also
wanted to own verdicts would fight the framework rather than carry it.

## Options

**Chaos Mesh (CNCF).** Excellent network chaos, arguably better than Litmus in
that one dimension, with a mature dashboard. Rejected on fit rather than
quality: its workflow engine and its status model both want to be the thing that
decides whether a chaos run passed, which is exactly the decision this project
exists to make differently.

**Gremlin.** A mature commercial platform whose blast-radius controls are
genuinely more sophisticated than the ones in `safety/`. Rejected because a
portfolio project that requires a commercial licence to demonstrate cannot be
demonstrated, and because reimplementing a simplified blast-radius calculation
is where a large part of this project's value sits — the arithmetic is the
learning, not the feature.

**xk6-disruptor.** Deserves particular respect: it puts Kubernetes fault
injection inside k6, which means a thin version of this project's load-plus-fault
idea already exists and ships from Grafana. Rejected because its fault catalogue
is a subset (no disk fill, no CPU hog with cgroup-level throttle visibility), it
has no scheduling, and its probe support is partial. **It is named in the README
grid rather than omitted**, because an interviewer who knows k6 will raise it and
it is better to have raised it first.

**Writing the injection directly** with `kubectl delete pod` and `tc netem` in a
privileged DaemonSet. Rejected fastest: it is a week of work to reach a worse
version of a CNCF project's fault catalogue, with no RBAC story and no
`ChaosResult` to put in an evidence bundle.

## Decision

**LitmusChaos, pinned to an exact chart version** — currently `litmus-core`
3.31.0, operator and CRDs only. The ChaosCenter UI chart is deliberately not
installed: the framework applies `ChaosEngine` CRDs itself, and a second
dashboard with its own opinion about run status is a source of contradiction in
a demo.

Three things about the pinning are load-bearing:

- **Never `3.x`.** Litmus ships monthly, so a floating minor is a moving target
  in a project whose entire claim is reproducibility. Any version named in the
  plan is a floor, not a target.
- **3.28.0 fixed a stale config leak across same-type probes.** The safety plane
  runs `promProbe`s in Continuous mode with `stopOnFailure`; probe config
  leaking between two probes of the same type would silently disarm an abort.
- **3.29.0 fixed duplicate triggers under concurrent reconciles.** Serial
  execution (ADR-004) is only serial if the operator does not fire an engine
  twice.

Destructive verbs on the target namespace are granted to *Litmus*, scoped per
experiment, and never to the ChaosProof framework itself. The framework's own
service account cannot delete a pod. That separation is what makes "ChaosProof
never targets its own namespace" enforceable rather than aspirational.

## Consequences

ChaosProof **runs on** LitmusChaos and would be strictly worse without it. The
README's competitive grid says so in the same paragraph that lists what this
project adds, because a grid with no losses reads as marketing.

The dependency is real: a Litmus API change breaks the `integrations/litmus.py`
adapter, and the CRD shape is asserted in `tests/test_workflow_shape.py` so that
break is loud and local rather than diffuse.

## The reversal condition

Two, and they are different in kind. If blast-radius control becomes a
compliance requirement with an audit trail attached, a commercial platform's
controls are worth the licence. And if fault injection has to reach VMs, cloud
APIs or managed databases, this decision is simply out of scope — Litmus covers
some of that surface and AWS FIS covers the rest, and the honest answer is that
ChaosProof has never been run against either.
