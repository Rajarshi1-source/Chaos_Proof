# ADR-005 — Resilience4j over Spring Boot 4 native resilience

**Status:** accepted · **Date:** 2026-08-23 · **Criterion: observability, not features**

## Context

Spring Boot 4 ships native resilience annotations — `@Retryable`,
`@ConcurrencyLimit`, `@EnableResilientMethods` — which removes the need for a
third-party resilience library in many applications. Taking a dependency that
the framework now covers natively needs a reason.

## The comparison that decides it

| Pattern | Spring Boot 4 native | Resilience4j 3 |
|---|---|---|
| Retry | `@Retryable` | yes |
| Concurrency limiting | `@ConcurrencyLimit` | bulkhead |
| Rate limiting | — | yes |
| **Circuit breaker** | **none** | yes |
| **Pattern state published to Micrometer** | **no** | yes, via `resilience4j-micrometer` |

Two rows carry the decision, and the second matters more than the first.

The circuit breaker is the single most important pattern ChaosProof validates —
it is what demo beat 3 aborts on, what the CI gate blocks a PR for removing, and
what the counterfactual panel measures the ROI of. Spring's native resilience
does not have one.

But the deeper reason is the last row. **This project can only validate patterns
that publish their state.** A `resilience_pattern` check asks whether the
circuit breaker actually opened during the fault; it answers that by reading
`resilience4j_circuitbreaker_state` out of Prometheus. A pattern that works
perfectly and emits nothing is, to this framework, indistinguishable from a
pattern that is not there. It is unverifiable, and an unverifiable resilience
pattern is exactly the thing this project exists to catch.

## Decision

**Resilience4j 3 on Java 21**, using the `resilience4j-spring-boot4` artifact —
never `-spring-boot3`, which fails on Spring Framework 7 in a way that looks like
a framework incompatibility rather than a wrong artifact.

Two things are non-negotiable and both are enforced in code, not in a comment:

1. **`resilience4j-micrometer` is declared explicitly.** It is *not* a transitive
   dependency. Without it every pattern check reports `applicable=false`, the
   weights renormalise around it, and the framework looks like it is working
   while validating nothing at all — the most dangerous silent failure in the
   system, because it produces higher scores.
2. **A startup assertion fails readiness when pattern metrics are missing.** The
   service does not come up. GATE 1 proved this assertion catches a deliberately
   removed `resilience4j-micrometer`, and that test stays.

Resilience4j 3 requires Java 21 (Resilience4j 2 requires 17), which is
consistent with the rest of the stack.

There is a known packaging trap recorded here so it is not rediscovered:
resilience4j issue **#2427** — `resilience4j-spring-boot4` was omitted from the
BOM, so BOM-managed builds fail to resolve it and the failure presents as a
Spring Framework 7 incompatibility. If the BOM still omits it, pin
`${resilience4j.version}` explicitly on both modules.

## Consequences

Use Boot 4's `@Retryable` where retry is wanted *without* verification, and
Resilience4j everywhere a validator needs to see inside. That is a real
distinction rather than a blanket preference, and it is the answer to "why not
just use the framework".

Retry brings its own hazard, addressed elsewhere: retry amplification is
accounted for in the resilience contracts (§19), because adding a retry without
declaring its amplification is how a fallback turns a partial outage into a full
one.

## The reversal condition

**Spring ships an observable circuit breaker whose state reaches Micrometer.**
The criterion here is not feature parity — it is whether the pattern publishes
enough for a validator to read. If native Spring meets that bar, the dependency
stops paying for itself.
