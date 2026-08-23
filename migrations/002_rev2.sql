-- migrations/002_rev2.sql  (PostgreSQL 18) — expand-contract, forward-only.
-- Adds the §6.2 core tables 001 deferred, the Rev 2 tables from the plan's §C.7,
-- and the Rev 2 columns on experiment_executions. uuidv7() keys on new tables:
-- timestamp-ordered, good index locality, and the evidence reads chronologically.

-- ---- §6.2 core: experiment registry --------------------------------------------
CREATE TABLE IF NOT EXISTS experiment_types (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,          -- pod_kill_payment_svc, ...
    litmus_type     TEXT NOT NULL,                 -- pod-delete, pod-network-latency, ...
    description     TEXT,
    fault_duration_seconds  INT NOT NULL DEFAULT 30,
    recovery_window_seconds INT NOT NULL DEFAULT 120,
    expected_alerts   JSONB NOT NULL DEFAULT '[]',
    expected_patterns JSONB NOT NULL DEFAULT '[]',
    is_ci_gate      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backfill from the TEXT column 001 shipped, then link executions to the registry.
INSERT INTO experiment_types (name, litmus_type)
    SELECT DISTINCT experiment, 'pod-delete' FROM experiment_executions
    ON CONFLICT (name) DO NOTHING;

ALTER TABLE experiment_executions
    ADD COLUMN IF NOT EXISTS experiment_type_id INT REFERENCES experiment_types(id);
UPDATE experiment_executions e
    SET experiment_type_id = t.id
    FROM experiment_types t WHERE t.name = e.experiment AND e.experiment_type_id IS NULL;

-- ---- 1. Steady-state hypotheses. Versioned: bumping version resets gating. -----
CREATE TABLE IF NOT EXISTS hypotheses (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    experiment_type_id  INT NOT NULL REFERENCES experiment_types(id),
    version             INT NOT NULL,
    description         TEXT NOT NULL,
    invariants          JSONB NOT NULL,   -- [{name, source, metric, comparator, threshold,
                                          --   tolerance_s | recover_within_s}]
    min_rps_floor       NUMERIC(8,2) NOT NULL,
    abort_conditions    JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_type_id, version)
);

-- ---- 2. Per-invariant outcome. The verdict is per-invariant, not one blob. -----
CREATE TABLE IF NOT EXISTS hypothesis_results (
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
CREATE INDEX IF NOT EXISTS idx_hypres_exec ON hypothesis_results (execution_id);

-- ---- 5. Scoring epochs. A trend line may only be drawn within one epoch. -------
CREATE TABLE IF NOT EXISTS scoring_epochs (
    id              SERIAL PRIMARY KEY,
    epoch_sha256    CHAR(64) UNIQUE NOT NULL,
    weights         JSONB NOT NULL,
    experiment_set  TEXT[] NOT NULL,               -- GATING experiments only
    slo_version     INT NOT NULL,
    scorer_version  TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    change_reason   TEXT NOT NULL
);

ALTER TABLE experiment_executions
    ADD COLUMN IF NOT EXISTS scoring_epoch_id INT REFERENCES scoring_epochs(id),
    ADD COLUMN IF NOT EXISTS hypothesis_id UUID REFERENCES hypotheses(id),
    ADD COLUMN IF NOT EXISTS abort_reason TEXT,
    ADD COLUMN IF NOT EXISTS blast_radius JSONB,
    ADD COLUMN IF NOT EXISTS bundle_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS score NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS weights_denominator NUMERIC(4,3);   -- audit: re-derive any score

-- ---- 6. Service resilience contracts (generator lands Phase 9). ----------------
CREATE TABLE IF NOT EXISTS resilience_contracts (
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
CREATE TABLE IF NOT EXISTS contract_validations (
    id            UUID PRIMARY KEY DEFAULT uuidv7(),
    contract_id   UUID NOT NULL REFERENCES resilience_contracts(id),
    execution_id  BIGINT NOT NULL REFERENCES experiment_executions(id),
    clause        TEXT NOT NULL,
    outcome       TEXT NOT NULL CHECK (outcome IN ('honoured','violated','untested')),
    observed      JSONB NOT NULL DEFAULT '{}'
);

-- ---- 7. Counterfactual pairs (Phase 10). Store EVERY run; report distributions. -
CREATE TABLE IF NOT EXISTS counterfactual_runs (
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

-- ---- 8. Flakiness (Phase 8). A flaky experiment must not gate a merge. ----------
CREATE TABLE IF NOT EXISTS experiment_flakiness (
    experiment_type_id  INT PRIMARY KEY REFERENCES experiment_types(id),
    window_runs         INT NOT NULL,
    score_stddev        NUMERIC(6,4),
    verdict_flip_rate   NUMERIC(5,4),
    gating              BOOLEAN NOT NULL DEFAULT FALSE,   -- advisory until characterised
    quarantined_at      TIMESTAMPTZ,
    quarantine_reason   TEXT
);

-- ---- §6.2 remainder: SLOs and error budget (enforced by the Phase 5 gate). ------
CREATE TABLE IF NOT EXISTS slo_definitions (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    description     TEXT,
    target          NUMERIC(8,3) NOT NULL,
    unit            TEXT NOT NULL,
    sli_query       TEXT NOT NULL,
    during_chaos_target NUMERIC(8,3),
    error_budget_days   INT NOT NULL DEFAULT 30,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS error_budget_events (
    id              BIGSERIAL PRIMARY KEY,
    slo_id          INT REFERENCES slo_definitions(id),
    execution_id    BIGINT REFERENCES experiment_executions(id),
    budget_consumed  NUMERIC(8,4),
    budget_remaining NUMERIC(8,4),
    sli_value        NUMERIC(12,4),
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- §6.2 remainder: legacy check evidence (validators land Phase 4). -----------
CREATE TABLE IF NOT EXISTS validation_checks (
    id              BIGSERIAL PRIMARY KEY,
    execution_id    BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    check_type      TEXT NOT NULL,
    check_name      TEXT NOT NULL,
    applicable      BOOLEAN NOT NULL DEFAULT TRUE,
    outcome         TEXT NOT NULL CHECK (outcome IN ('pass','fail','partial','invalid')),
    score           NUMERIC(4,3),
    expected_value  TEXT,
    actual_value    TEXT,
    message         TEXT,
    details         JSONB NOT NULL DEFAULT '{}',
    validated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_check_exec ON validation_checks (execution_id);
