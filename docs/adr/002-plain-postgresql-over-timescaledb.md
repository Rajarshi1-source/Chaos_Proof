# ADR-002 — Plain PostgreSQL 18.6 over TimescaleDB

**Status:** accepted · **Date:** 2026-08-23

## Context

The evidence store holds experiment executions, hypothesis results, validation
checks, scoring epochs, contracts, counterfactual runs, flakiness history — and
`sli_samples`, which is genuinely time-series data: 5-second-resolution samples
from two independent sources for the duration of every experiment.

That last table is the one that makes this a decision rather than a default.
Everything else is plainly relational and low-volume.

## The numbers, because the decision turns on them

| | Volume |
|---|---|
| Experiment executions | 6/day |
| Validation checks | ~20 per execution, ~120/day |
| `sli_samples` | ~1,200 rows per experiment, **~7,000/day** |

7,000 rows a day is roughly three orders of magnitude below where hypertables,
chunk pruning and compression policies start earning their operational
complexity. The access pattern makes the gap wider still: **samples are written
in one burst and read once**, by the scorer or by a retro-score. There is no
continuous ingest, no rolling aggregate, and no dashboard querying live
five-second data.

Retention is therefore a `DELETE` against a monthly partition, not a compression
policy — and monthly `RANGE` partitions on `sli_samples` are already in
`migrations/001_phase2_core.sql`, which is the 90% of TimescaleDB's benefit that
plain PostgreSQL gives away for free.

## Options

**TimescaleDB.** Would win on `sli_samples` alone at volume. At this volume it
adds an extension to install, a hypertable to manage, a second mental model for
anyone reading the schema, and a version-compatibility constraint against
PostgreSQL 18.6 — for a table that is written once and read once.

**MongoDB / Cassandra / CosmosDB / generic NoSQL.** Rejected on the shape of the
data rather than the volume. The core of this schema is referential: an
execution references a hypothesis, which references an experiment type; a score
references an epoch; a retro-score references both an execution and an epoch and
must be *unique* on the pair. Those constraints are the mechanism that stops a
score being silently overwritten (`UNIQUE (execution_id, scoring_epoch_id)`),
and re-implementing them in application code is how they stop holding.

**PostgreSQL 18.6, plain.** Chosen.

## Decision

Plain PostgreSQL 18.6 with monthly `RANGE` partitions on `sli_samples`.

## Consequences

The whole evidence store runs in one `postgres:18.6-alpine` container on a
mapped port, which matters more than it sounds: `make evidence-up` is a
prerequisite of the demo, and a demo that needs an extension compiled against a
specific server version is a demo that fails in the room.

## The reversal condition

**Continuous 5-second sampling of a hundred services, rather than six
experiments a day.** At that point `sli_samples` becomes a live ingest table
with rolling queries against it, the write pattern stops being bursty, and
retention stops being a partition drop — which is precisely the workload
hypertables exist for.

Naming the threshold is the part that makes this a decision. Worth noting the
deliberate contrast with the sibling project (KubeThrifty), where the same
reasoning applied to genuinely high-frequency metrics selected TimescaleDB *for*
that workload. Same criterion, opposite answer.
