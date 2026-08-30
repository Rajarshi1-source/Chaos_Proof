-- migrations/004_phase8_quality.sql  (PostgreSQL 18)
-- Phase 8: the MLOps wrapper. Retro-scoring, epoch provenance, quarantine history.

-- ---- 1. Retro-scores. A CORRECTED SCORE IS A NEW ROW. --------------------------
--
-- Never an UPDATE of experiment_executions.score. That column holds what the
-- scorer said AT THE TIME, under the epoch stamped on the row, and rewriting it
-- would destroy the only evidence that the scorer ever behaved differently.
-- Retro-scoring produces an ADDITIONAL score for the same run under a new epoch;
-- the two coexist, and the trend chart picks the one matching the epoch it is
-- drawing. That is the difference between retro-scoring and revisionism.
--
-- The UNIQUE constraint makes re-running idempotent rather than duplicative: one
-- score per (run, epoch), and a second attempt under the same epoch is a no-op.
CREATE TABLE IF NOT EXISTS retro_scores (
    id                  BIGSERIAL PRIMARY KEY,
    execution_id        BIGINT NOT NULL REFERENCES experiment_executions(id) ON DELETE CASCADE,
    scoring_epoch_id    INT NOT NULL REFERENCES scoring_epochs(id),
    score               NUMERIC(6,4),          -- NULL is meaningful: INVALID has no score
    status              TEXT NOT NULL CHECK (status IN ('pass','partial','fail','invalid')),
    weights_denominator NUMERIC(5,3),
    -- How much of this score was re-derived from raw samples versus carried over
    -- from stored check evidence. Provenance, so a retro-scored point on the
    -- trend chart can be interrogated rather than merely trusted.
    method              JSONB NOT NULL DEFAULT '{}',
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (execution_id, scoring_epoch_id)
);
CREATE INDEX IF NOT EXISTS idx_retro_epoch ON retro_scores (scoring_epoch_id);
CREATE INDEX IF NOT EXISTS idx_retro_execution ON retro_scores (execution_id);

-- ---- 2. Quarantine history. Status is current-state; this is the record. -------
--
-- experiment_flakiness holds the LATEST measurement, so it is necessarily
-- mutable. That makes it unable to answer "when did this experiment lose gating
-- status, and what was sigma at the time?" — which is exactly what someone asks
-- when a trend line breaks. Append-only, never updated.
CREATE TABLE IF NOT EXISTS flakiness_transitions (
    id                  BIGSERIAL PRIMARY KEY,
    experiment_type_id  INT NOT NULL REFERENCES experiment_types(id),
    from_gating         BOOLEAN,
    to_gating           BOOLEAN NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('uncharacterised','stable','flaky')),
    window_runs         INT NOT NULL,
    score_stddev        NUMERIC(6,4),
    verdict_flip_rate   NUMERIC(5,4),
    reason              TEXT NOT NULL,
    -- The epoch this transition OPENED, when it moved the gating bit. Null when
    -- the bit did not move: a re-measurement that confirms the status quo is
    -- not a scoring change and must not open an epoch.
    opened_epoch_id     INT REFERENCES scoring_epochs(id),
    issue_url           TEXT,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_flake_trans ON flakiness_transitions (experiment_type_id, occurred_at DESC);

-- ---- 3. Epoch provenance ------------------------------------------------------
-- Which experiment's transition opened this epoch, so a boundary on the trend
-- chart can explain itself without a human remembering.
ALTER TABLE scoring_epochs
    ADD COLUMN IF NOT EXISTS opened_by TEXT;
