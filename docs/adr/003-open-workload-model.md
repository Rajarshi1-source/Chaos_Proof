# ADR-003 — Open workload model over closed

**Status:** accepted · **Date:** 2026-08-23 · **The strongest of the seven**

## Context

Every experiment holds synthetic traffic through the fault, because a fault
injected into an idle system produces no evidence. k6 offers two families of
executor for that, and the choice between them decides whether the measurement
means anything.

- **Closed model** (`constant-vus`): a fixed number of virtual users, each of
  which sends a request, *waits for the response*, and then sends the next one.
- **Open model** (`constant-arrival-rate`): a fixed request rate, independent of
  how long any request takes.

## The problem with the closed model

**A closed model silently reduces offered load exactly when the system
degrades.** If 100 VUs are looping and p99 latency triples under the fault, each
VU completes fewer iterations, so the offered rate falls — by roughly the factor
the system slowed down by.

The consequences compound in the wrong direction:

1. **The fault looks smaller than it is.** The system is protected from the load
   it would face in production, by the load generator, at the moment the
   experiment is meant to be hardest.
2. **Latency percentiles improve under load shedding.** Fewer concurrent
   requests means less queueing, so p99 can *fall* during a partial outage.
3. **Availability is computed over a shrinking denominator.** 99% of a rate that
   collapsed to a third is not 99% availability in any sense a user would
   recognise.

A closed-model chaos experiment therefore tends to produce a passing verdict,
and produces it *more* readily the worse the fault is. That is the single most
dangerous failure mode this project can have: it is not a wrong number, it is a
wrong number that is confidently green.

## Decision

**`constant-arrival-rate`**, at 120 rps against a `min_rps_floor` of 90,
`preAllocatedVUs` sized for the *worst-case* latency rather than the healthy
p99, with `maxVUs` headroom above it.

Two mechanisms make that pin real rather than aspirational:

- **`dropped_iterations` is generator saturation, and it is measured.** When the
  arrival rate cannot be sustained because k6 ran out of VUs, k6 says so. Beyond
  `DROPPED_CEILING` in the window, the run is `INVALID` — the load plane failed,
  so the experiment measured the load plane.
- **The achieved rate is checked against the floor.** Below it, `INVALID`, never
  `PASS`. This is demo beat 1 and GATE 2: stop the k6 `TestRun`, kill a pod, and
  watch the verdict come back `INVALID — achieved 0 rps, floor 90`.

Sizing `preAllocatedVUs` for the worst case rather than the healthy p99 was not
theoretical — it was defect D-F, found in Phase 7, where a generator sized for
healthy latency saturated during the fault and turned a real experiment into an
`INVALID` one.

## Consequences

The load plane needs real headroom, which is part of why the node is a
`t3.large` and not a `t3.medium` (a starved node produces `INVALID` runs, not
insight). It also means the load plane must start *before* pre-flight, which
runs before injection — in CI too, where it is the single most important line in
`chaos-gate.yml`.

## The reversal condition

**None.** This is the one decision here with no threshold. A closed model cannot
measure what this project measures, at any scale, because the failure is
structural rather than a matter of volume. If throughput were ever deliberately
elastic — a batch workload where reduced offered load under stress is the
*intended* behaviour — the question would not arise, because there would be no
availability SLO to falsify.
