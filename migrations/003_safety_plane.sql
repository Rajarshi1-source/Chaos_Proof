-- migrations/003_safety_plane.sql  (PostgreSQL 18) — expand-contract, forward-only.
-- The safety plane's own evidence: what pre-flight decided, what tripped an abort,
-- what cleanup did, and every freeze. A guardrail that fires without leaving a
-- record teaches you nothing.

-- Manual kill switch. `chaosctl freeze` is itself a recorded event with a reason
-- and an owner — "who turned chaos off and why" must survive the person.
CREATE TABLE IF NOT EXISTS chaos_freezes (
    id          BIGSERIAL PRIMARY KEY,
    reason      TEXT NOT NULL,
    owner       TEXT NOT NULL DEFAULT current_user,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    lifted_at   TIMESTAMPTZ,
    lift_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_freeze_active ON chaos_freezes (lifted_at)
    WHERE lifted_at IS NULL;

-- Pre-flight decisions, INCLUDING the ones that refused. A SKIPPED or DENIED run
-- is a first-class outcome: "the framework correctly refused" is the data point
-- that proves the guardrails are load-bearing rather than decorative.
CREATE TABLE IF NOT EXISTS preflight_decisions (
    id            BIGSERIAL PRIMARY KEY,
    execution_id  BIGINT REFERENCES experiment_executions(id) ON DELETE CASCADE,
    experiment    TEXT NOT NULL,
    decision      TEXT NOT NULL CHECK (decision IN ('proceed','skipped','denied')),
    reason        TEXT,
    policy_rule   TEXT,
    blast_radius  JSONB,
    budget_state  TEXT,
    evidence      JSONB NOT NULL DEFAULT '{}',
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_preflight_experiment
    ON preflight_decisions (experiment, decided_at DESC);

-- Which abort path fired, and on what evidence. `path` distinguishes the in-band
-- Litmus probe from the out-of-band watchdog — the point of having two is being
-- able to tell which one saved you.
CREATE TABLE IF NOT EXISTS abort_events (
    id            BIGSERIAL PRIMARY KEY,
    execution_id  BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    path          TEXT NOT NULL CHECK (path IN ('litmus_probe','python_watchdog')),
    condition     TEXT NOT NULL,
    observed      NUMERIC(14,4),
    threshold     NUMERIC(14,4),
    comparator    TEXT,
    tripped_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    evidence      JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_abort_exec ON abort_events (execution_id);

-- The cleanup saga's own log. `escalated` is the field that matters: cleanup is
-- not done until steady state is re-established, and a saga that could not get
-- there must say so rather than closing quietly.
CREATE TABLE IF NOT EXISTS cleanup_logs (
    id                BIGSERIAL PRIMARY KEY,
    execution_id      BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    steps             JSONB NOT NULL,
    escalated         BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_reason TEXT,
    completed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cleanup_exec ON cleanup_logs (execution_id);

-- The out-of-band cleanup path needs to find abandoned runs: a `running` row past
-- its deadline is a fault that may still be injected with nobody watching.
ALTER TABLE experiment_executions
    ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'finished'
        CHECK (state IN ('queued','preflight','running','validating','finished')),
    ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS chaos_engine TEXT,
    ADD COLUMN IF NOT EXISTS flag_snapshot JSONB;

CREATE INDEX IF NOT EXISTS idx_exec_running
    ON experiment_executions (state, deadline_at) WHERE state <> 'finished';
