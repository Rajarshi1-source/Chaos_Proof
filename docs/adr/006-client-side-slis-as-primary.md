# ADR-006 — Client-side SLIs as primary truth, server-side as corroboration

**Status:** accepted · **Date:** 2026-08-23

## Context

Every invariant needs a number, and there are two places to get it: the k6 load
generator, which is a client, and Prometheus, which scrapes the services.

The obvious choice is Prometheus. It is already there, it has a query language,
it has retention, and the target application already exports request counts and
latency histograms. Using it for everything would remove an entire data path.

## Why the obvious choice is wrong

**Server-side metrics cannot see a request that never reached a server.**

That is the whole argument, and every failure mode this project cares about is
an instance of it:

- A pod is killed and the Service endpoint has not yet converged. Requests are
  refused at the network. No server records them.
- A network partition drops packets. No server records them.
- The node is saturated and connections time out in the accept queue. No server
  records them.

In each case the server-side error rate stays low — sometimes it *improves*,
because the surviving replicas are handling a smaller, cleaner load. A
server-side availability SLI computed during a partition can read as a healthy
100% while a user is receiving nothing.

Availability computed from server-side metrics alone is therefore not merely
imprecise. It is systematically biased in the direction of a passing verdict,
during exactly the faults the experiment was designed to detect.

## Decision

**k6 client-side metrics are the primary source of truth for availability and
latency. Prometheus corroborates and covers what a client cannot see.**

The two sources are sampled *simultaneously*, every 5 seconds, and both are
stored in `sli_samples`. Neither is derived from the other.

Prometheus remains primary for everything genuinely internal — replica counts,
CPU throttle ratio, PSI, circuit-breaker state, queue depth — because a client
has no visibility into any of it.

### The gap is a feature, not noise

The two series diverge during a fault, and **the size of the gap is one of the
more interesting numbers this project produces**. The dashboard renders both
lines with the gap shaded, and demo beat 2 points at it directly: client errors
appear at 1.2s, server-side metrics react at 6s. That ~4.8s difference is the
irreducible detection lag — scrape interval plus rate window plus evaluation —
and naming it is what makes the alert-latency measurement honest rather than
flattering.

### Consequences for windows

Client-side sampling at 5s against a 30s rate window is what makes a 30-second
recovery SLO measurable at all. **A rate window is never widened past the SLO it
is compared against.** If a query needs `[5m]` to return data against a 30s SLO,
the SLO is not measurable and *that is the finding* — not a reason to widen the
window until the number appears.

## Consequences

There is a hard dependency on the load plane. No traffic means no client-side
SLI, which means `INVALID`, which is correct: no evidence must never score as
success. This is the coupling that makes ADR-003 and this decision one design
rather than two.

It also means the load plane starts before pre-flight, which runs before
injection — in CI too.

## The reversal condition

**None.** No amount of server-side instrumentation can observe a request that
was refused before it arrived. The only thing that would change is *which*
client: a real user-journey probe from outside the cluster would be strictly
better than an in-cluster k6, and would widen the same gap this decision exists
to measure.
