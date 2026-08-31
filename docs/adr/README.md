# Architecture Decision Records

Seven decisions where a plausible alternative existed and the reason for
choosing between them is not obvious from the code.

Each record states the option that was rejected, what it would have cost, and —
where one exists — **the threshold at which the decision flips**. That last part
is the point. A decision with no stated reversal condition is a preference
wearing a decision's clothes, and it cannot be revisited later by anyone who was
not in the room.

| ADR | Decision | Reversal condition |
|---|---|---|
| [001](001-litmuschaos-over-alternatives.md) | LitmusChaos over Chaos Mesh, Gremlin and xk6-disruptor | A managed platform's blast-radius controls become worth the licence, or fault injection must reach VMs and cloud APIs |
| [002](002-plain-postgresql-over-timescaledb.md) | Plain PostgreSQL 18.6 over TimescaleDB | Continuous 5-second sampling of ~100 services, not six experiments a day |
| [003](003-open-workload-model.md) | Open (constant-arrival-rate) over closed (constant-VUs) workload model | None. A closed model cannot measure what this project measures |
| [004](004-sequential-experiment-execution.md) | Sequential over parallel experiment execution | 50+ services, parallelised across *disjoint* dependency subgraphs only |
| [005](005-resilience4j-over-native-spring.md) | Resilience4j over Spring Boot 4 native resilience | Spring ships an observable circuit breaker emitting state to Micrometer |
| [006](006-client-side-slis-as-primary.md) | Client-side SLIs primary, server-side corroborating | None. Server-side metrics cannot see a request that never reached a server |
| [007](007-bounded-bisection-with-visible-cost.md) | Bounded, on-demand bisection with a visible cost estimate | A second cluster exists that the daily schedule does not use |

Deferred work that is deliberately *not* an ADR yet lives in
[`docs/future-work/`](../future-work/).
