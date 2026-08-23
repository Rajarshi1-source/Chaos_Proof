-- migrations/001_phase2_core.sql  (PostgreSQL 18)
-- Phase 2 substrate: executions (minimal — Phase 3's 002 extends it), the load
-- run that made the experiment measurable, and the raw SLI samples. sli_samples
-- is the RETRO-SCORING substrate, partitioned monthly before it needs it.

CREATE TABLE IF NOT EXISTS experiment_executions (
    id              BIGSERIAL PRIMARY KEY,
    experiment      TEXT NOT NULL,
    trigger_source  TEXT NOT NULL DEFAULT 'manual',
    verdict         TEXT CHECK (verdict IN
        ('held','falsified','invalid','skipped','denied','aborted','error')),
    verdict_reason  TEXT,
    git_sha         CHAR(40),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fault_injected_at TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_exec_experiment ON experiment_executions (experiment, started_at DESC);

-- No load row, no score.
CREATE TABLE IF NOT EXISTS load_runs (
    id                  BIGSERIAL PRIMARY KEY,
    execution_id        BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    tool                TEXT NOT NULL DEFAULT 'k6',
    tool_version        TEXT NOT NULL,
    workload_model      TEXT NOT NULL CHECK (workload_model IN ('open','closed')),
    target_rps          NUMERIC(8,2) NOT NULL,
    achieved_rps        NUMERIC(8,2),
    dropped_iterations  BIGINT DEFAULT 0,
    client_error_rate   NUMERIC(6,4),
    client_p99_ms       NUMERIC(10,2),
    script_sha256       CHAR(64) NOT NULL,          -- the load profile is part of the epoch
    raw_summary         JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sli_samples (
    execution_id    BIGINT NOT NULL,
    sampled_at      TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('prometheus','k6')),
    metric          TEXT NOT NULL,
    value           NUMERIC(14,4),
    PRIMARY KEY (execution_id, sampled_at, source, metric)
) PARTITION BY RANGE (sampled_at);

CREATE TABLE IF NOT EXISTS sli_samples_2026_08 PARTITION OF sli_samples
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS sli_samples_2026_09 PARTITION OF sli_samples
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
