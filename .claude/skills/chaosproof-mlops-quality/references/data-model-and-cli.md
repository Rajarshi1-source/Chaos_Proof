# ChaosProof — Data Model, CLI Surface, Metrics, and REST API

Contents:
1. Database choice and why not the alternatives
2. Rev 1 core tables
3. Rev 2 tables (migration `002_rev2.sql`)
4. Cache and queue key conventions
5. The `chaosctl` surface
6. Exported Prometheus metrics
7. REST endpoints
8. Consistency and availability rules

---

## 1. Database choice and why not the alternatives

**Verdict: plain PostgreSQL 18.6 — deliberately NOT TimescaleDB.**

| Criteria | **PostgreSQL 18.6** | TimescaleDB | MongoDB | Cassandra | CosmosDB | Generic NoSQL |
|---|---|---|---|---|---|---|
| Event rate | ~120 check rows/day + SLI samples. Trivial | Built for millions/s | Fine | Absurd | Fine | Fine |
| Core query | "all checks for execution #47 with SLO + hypothesis + contract" = 4–5 JOINs | Same (is Postgres) | `$lookup` chains | Not for analytical JOINs | SQL API workable | No JOINs |
| Trend query | `GROUP BY` epoch, date → 30 rows | Continuous aggregates (overkill) | Aggregation pipeline | Poor | Workable | Client-side |
| Transactional integrity | ACID: execution + checks + score in one transaction | Same | Single-doc only | Eventual | Configurable, costly | Limited |
| JSONB raw observations | ✅ native, indexable | ✅ | ✅ native | ❌ | ✅ | ✅ |
| Cost at this scale | Free, one container | Free + extension ops | Free | High ops | **Consumption billing on a demo = surprise bill** | Lock-in |
| Verdict | ✅ **Chosen** | ❌ | ❌ | ❌ | ❌ | ❌ |

**Where TimescaleDB nearly wins, and the honest answer.** `sli_samples` holds 5-second-resolution samples during experiments — roughly 1,200 rows per experiment, ~7,000/day. That *is* time-series data. Still three orders of magnitude below where hypertables earn their complexity, and the rows are written in one burst and read once, so retention is a `DELETE` on a partitioned table rather than a compression policy. **State the threshold that would change the decision: continuous 5-second sampling of a hundred services, not six experiments a day.** Naming your own switching point is what separates a decision from a preference.

PostgreSQL 18 features that earn their place: `uuidv7()` (timestamp-ordered keys — good index locality, and the evidence bundle reads chronologically) and native range partitioning for `sli_samples`.

---

## 2. Rev 1 core tables

`experiment_types` (name, `litmus_type`, durations, `expected_alerts` JSONB, `expected_patterns` JSONB, SLO targets, `is_ci_gate`) · `experiment_executions` (trigger source, GitHub PR/run, status, score, recovery time, `baseline_metrics`/`observations`/`timeline`/`litmus_result` JSONB, timestamps) · `validation_checks` (execution FK, `check_type`, `passed`, `score`, expected/actual, `details` JSONB) · `daily_resilience_scores` · `slo_definitions` · `error_budget_events`.

These stay. Rev 2 adds columns to `experiment_executions` (see §3, item 5).

---

## 3. Rev 2 tables

```sql
-- migrations/002_rev2.sql   (PostgreSQL 18)

-- 1. Steady-state hypotheses. Versioned: bumping version resets gating status.
CREATE TABLE hypotheses (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    experiment_type_id  INT NOT NULL REFERENCES experiment_types(id),
    version             INT NOT NULL,
    description         TEXT NOT NULL,
    invariants          JSONB NOT NULL,   -- [{name, source, expr, comparator, threshold,
                                          --   tolerance_s | recover_within_s}]
    min_rps_floor       NUMERIC(8,2) NOT NULL,
    abort_conditions    JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_type_id, version)
);

-- 2. Per-invariant outcome. The verdict is per-invariant, not one blob.
CREATE TABLE hypothesis_results (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    execution_id    BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    hypothesis_id   UUID NOT NULL REFERENCES hypotheses(id),
    invariant_name  TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('held','falsified','invalid')),
    worst_value     NUMERIC(14,4),
    threshold       NUMERIC(14,4),
    breached_for_s  NUMERIC(8,2),
    evidence        JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX ON hypothesis_results (execution_id);

-- 3. The load run that made the experiment measurable. No load row, no score.
CREATE TABLE load_runs (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    execution_id        BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    tool                TEXT NOT NULL DEFAULT 'k6',
    tool_version        TEXT NOT NULL,
    workload_model      TEXT NOT NULL CHECK (workload_model IN ('open','closed')),
    target_rps          NUMERIC(8,2) NOT NULL,
    achieved_rps        NUMERIC(8,2),
    dropped_iterations  BIGINT DEFAULT 0,
    client_error_rate   NUMERIC(6,4),
    client_p99_ms       NUMERIC(10,2),
    script_sha256       CHAR(64) NOT NULL,   -- the load profile is part of the epoch
    raw_summary         JSONB NOT NULL DEFAULT '{}'
);

-- 4. High-resolution SLI samples. THE retro-scoring substrate. Partitioned monthly.
CREATE TABLE sli_samples (
    execution_id    BIGINT NOT NULL,
    sampled_at      TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('prometheus','k6')),
    metric          TEXT NOT NULL,
    value           NUMERIC(14,4),
    PRIMARY KEY (execution_id, sampled_at, source, metric)
) PARTITION BY RANGE (sampled_at);
CREATE TABLE sli_samples_2026_08 PARTITION OF sli_samples
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- 5. Scoring epochs. A trend line may only be drawn within one epoch.
CREATE TABLE scoring_epochs (
    id              SERIAL PRIMARY KEY,
    epoch_sha256    CHAR(64) UNIQUE NOT NULL,
    weights         JSONB NOT NULL,
    experiment_set  TEXT[] NOT NULL,          -- GATING experiments only
    slo_version     INT NOT NULL,
    scorer_version  TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    change_reason   TEXT NOT NULL
);
ALTER TABLE experiment_executions
    ADD COLUMN IF NOT EXISTS scoring_epoch_id INT REFERENCES scoring_epochs(id),
    ADD COLUMN IF NOT EXISTS hypothesis_id UUID REFERENCES hypotheses(id),
    ADD COLUMN IF NOT EXISTS verdict TEXT CHECK (verdict IN
        ('held','falsified','invalid','skipped','denied','aborted','error')),
    ADD COLUMN IF NOT EXISTS abort_reason TEXT,
    ADD COLUMN IF NOT EXISTS blast_radius JSONB,
    ADD COLUMN IF NOT EXISTS bundle_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS git_sha CHAR(40),
    ADD COLUMN IF NOT EXISTS weights_denominator NUMERIC(4,3);

-- 6. Service resilience contracts.
CREATE TABLE resilience_contracts (
    id               UUID PRIMARY KEY DEFAULT uuidv7(),
    service          TEXT NOT NULL,
    version          TEXT NOT NULL,
    provides         JSONB NOT NULL,
    tolerates        JSONB NOT NULL,
    does_not_inflict JSONB NOT NULL DEFAULT '[]',
    contract_sha256  CHAR(64) NOT NULL,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (service, version)
);
CREATE TABLE contract_validations (
    id            UUID PRIMARY KEY DEFAULT uuidv7(),
    contract_id   UUID NOT NULL REFERENCES resilience_contracts(id),
    execution_id  BIGINT NOT NULL REFERENCES experiment_executions(id),
    clause        TEXT NOT NULL,     -- "tolerates.payment-svc.max_latency_ms=800"
    outcome       TEXT NOT NULL CHECK (outcome IN ('honoured','violated','untested')),
    observed      JSONB NOT NULL DEFAULT '{}'
);

-- 7. Counterfactual pairs. Store EVERY run; report the distribution.
CREATE TABLE counterfactual_runs (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    pair_id         UUID NOT NULL,
    execution_id    BIGINT NOT NULL REFERENCES experiment_executions(id),
    arm             TEXT NOT NULL CHECK (arm IN ('with_pattern','without_pattern')),
    pattern_name    TEXT NOT NULL,
    repetition      INT NOT NULL,
    failed_requests BIGINT,
    p99_ms          NUMERIC(10,2),
    recovery_s      NUMERIC(8,2)
);

-- 8. Flakiness. A flaky experiment must not gate a merge.
CREATE TABLE experiment_flakiness (
    experiment_type_id  INT PRIMARY KEY REFERENCES experiment_types(id),
    window_runs         INT NOT NULL,
    score_stddev        NUMERIC(6,4),
    verdict_flip_rate   NUMERIC(5,4),
    gating              BOOLEAN NOT NULL DEFAULT TRUE,
    quarantined_at      TIMESTAMPTZ,
    quarantine_reason   TEXT
);
```

Three choices to be able to defend on sight: **`uuidv7()`** for the new tables; **monthly partitions on `sli_samples`** before it needs them; and **`weights_denominator` persisted on every execution** so a stored score can be re-derived and audited rather than merely trusted.

Migrations are **expand-contract**, forward-only via Alembic: additive with release N, destructive with N+1 once no old pods remain.

---

## 4. Cache and queue key conventions

```
stream   chaos-experiments                  single consumer — experiments MUST run serially
stream   chaos-experiments-dlq              alert on XLEN
lock     chaos:lock:{namespace}             SET NX, TTL 600s, VALUE = execution id (FENCE)
cache    dashboard:score:current            TTL 5m
cache    dashboard:trend:{epoch}:30d        TTL 1h — keyed BY EPOCH, never mixed
cache    prom:{query_hash}                  TTL 30s
```

**The lock must be fenced.** Rev 1 used an unfenced `SET NX`: a runner that stalls past the TTL can resume and act while a successor holds the lock, and two overlapping experiments make every result unattributable — the failure that would invalidate the entire dataset. Store the execution id as the value and re-verify ownership before every mutating call.

**Valkey/Redis is disposable.** Say it plainly: if it is wiped, ChaosProof loses queue position, the current lock, and cold caches. It does not lose a single execution, verdict, or evidence bundle.

Note the trend cache is keyed by epoch — a cache that mixes epochs reintroduces the comparability bug at the presentation layer.

---

## 5. The `chaosctl` surface

| Command | Purpose |
|---|---|
| `chaosctl run <experiment>` | Trigger one experiment; prints per-invariant verdicts |
| `chaosctl plan <experiment>` | Print blast radius, policy decision, and budget gate **without injecting** |
| `chaosctl replay <sha>` | Offline reconstruction from a bundle — no cluster, network, or database |
| `chaosctl audit --verify-bundles` | Re-hash stored bundles; exit non-zero on mismatch |
| `chaosctl freeze --reason "..."` | Global kill switch; recorded with reason and owner |
| `chaosctl unfreeze` | Requires a reason; recorded |
| `chaosctl flakiness [--experiment X]` | σ, flip rate, gating status per experiment |
| `chaosctl epoch [--show \| --open --reason "..."]` | Inspect or open a scoring epoch |
| `chaosctl bisect <experiment> --good <sha> --bad <sha>` | On-demand bisection with a cost estimate first |
| `chaosctl contracts validate <service>` | Run all generated experiments for one contract |

`chaosctl freeze` is worth ten minutes of build time: *"during a deploy or an incident review, one command turns all chaos off, and the freeze itself is auditable."*

`chaosctl replay` output format must stay stable — it doubles as a golden-file test and the dashboard renders it for the public read-only demo.

---

## 6. Exported Prometheus metrics

```
chaosproof_experiment_score{experiment,epoch}
chaosproof_verdict_total{experiment,verdict="held|falsified|invalid|skipped|denied|aborted"}
chaosproof_sli_validity_ratio{experiment}              # valid runs / total runs
chaosproof_experiment_score_stddev{experiment}         # the flakiness signal
chaosproof_gating_experiments                          # how many can block a merge
chaosproof_error_budget_spent_ratio{namespace}
chaosproof_budget_gate_state{namespace}                # 0 open, 1 restricted, 2 closed
chaosproof_chaos_breaker_state                         # 0 closed, 1 half_open, 2 open
chaosproof_orphaned_cleanups_total                     # alert if > 0
chaosproof_contract_clause_coverage_ratio{service}
chaosproof_client_server_availability_gap{experiment}
chaosproof_load_dropped_iterations{experiment}
chaosproof_narrator_discards_total                     # grounding-check rejections
```

LitmusChaos gained its own Prometheus metrics in **3.29.0**, so the chaos platform is observable alongside the target. Wire it — "the chaos framework failed silently" is otherwise indistinguishable from "everything passed."

Alert on: `chaosproof_orphaned_cleanups_total > 0`, `chaosproof_sli_validity_ratio < 0.9`, `chaosproof_chaos_breaker_state == 2`, and `chaosproof_gating_experiments` dropping (experiments quietly falling out of the gate).

---

## 7. REST endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/score` | Current score **with its epoch**; never epoch-less |
| GET | `/api/trends?epoch=<sha>` | Trend within one epoch; boundaries returned as annotations |
| GET | `/api/experiments` | Registry: hypothesis version, fault, blast radius, σ, gating status |
| GET | `/api/executions` | Paginated, filterable by verdict/experiment/epoch |
| GET | `/api/executions/{id}` | Full timeline, per-invariant outcomes, both SLI sources, bundle hash |
| GET | `/api/contracts` | Contracts with clause-level outcomes and coverage ratio |
| GET | `/api/counterfactuals` | Pairs with medians, IQRs, and the overlap flag |
| GET | `/api/slos/error-budget` | Remaining budget and gate state per namespace |
| POST | `/api/experiments/{name}/run` | **Never internet-reachable.** Absent from the read-only build |
| GET | `/api/health`, `/metrics` | Readiness checks PostgreSQL, Redis, Prometheus, K8s API |

**No endpoint that can inject a fault is exposed publicly.** The public demo is read-only and manual-trigger controls are absent from that build via a build-time flag, not merely disabled — a control that 404s invites someone to find out why.

---

## 8. Consistency and availability rules

**CAP position: CP for verdicts, AP for observation.**

| Concern | Model | Why |
|---|---|---|
| Executions, verdicts, epochs, bundles | **CP.** Single-writer PostgreSQL, transactional, fail-closed on partition | A verdict must never read as `held` when the evidence says otherwise |
| SLI sampling | **AP.** Gap-tolerant, coverage measured | A missed scrape must not abort a run — but coverage below 90% makes it `INVALID` |
| Dashboard reads | **AP.** Cache-aside, stale-while-revalidate | A dashboard that errors during a live demo is worse than one 30s stale |
| Cluster state | Eventually consistent | The K8s API lags; verification uses windows and thresholds, never instantaneous reads |

Rules that are easy to break:

1. **One transaction per state transition** — execution row, checks, hypothesis results, and score written together. A half-written execution silently skews aggregates.
2. **Monotonic state machine:** `queued → preflight → running → validating → {held | falsified | invalid | aborted | skipped | denied | error}`. Illegal transitions raise, and the raise is recorded.
3. **Immutable evidence.** Bundles are content-addressed and write-once; a corrected score is a new row under a new epoch, never an `UPDATE`.
4. **Fail-closed on PostgreSQL.** No evidence write means no experiment — the record is a precondition, not a side effect.
5. **RPO/RTO for the evidence store matters more than for the scheduler.** Losing a night's schedule costs a night; losing the evidence store costs the entire resilience history and every retro-scoring capability with it. `pg_dump` CronJob to S3 plus WAL archiving.
6. **≥30 days Prometheus retention**, because the error-budget window is 30 days. Check at startup; refuse to gate on an incomputable budget.
