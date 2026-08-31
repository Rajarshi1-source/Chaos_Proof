-- migrations/005_phase12_bisection.sql  (PostgreSQL 18)
-- Phase 12: regression bisection (§20).

-- ---- 1. Bisections. Every outcome is recorded, including the refusals. -------
--
-- A bisection costs roughly ninety minutes of exclusive cluster time, so the
-- decision to start one is itself worth an audit trail: who asked, over what
-- range, at what estimated cost, and what came back.
--
-- The `outcome` CHECK is where the point of this feature lives. `abandoned` and
-- `refused` are first-class rows, not error states:
--
--   found      a single commit, with the trail that reached it
--   abandoned  a candidate landed inside the flakiness band and the search
--              STOPPED. It did not guess a half. One misclassification sends a
--              binary search down the wrong half permanently, because the
--              correct half is never revisited and no later evidence can
--              contradict the mistake — so the search still terminates, still
--              names a commit, and is still wrong. Recording `abandoned` is how
--              that non-answer stays visible instead of being retried until it
--              accidentally produces one.
--   refused    pre-flight said no before any cluster time was spent: the
--              anchors were not separable at any affordable repetition count,
--              the cost did not fit the nightly window, or the range contained
--              an epoch-defining change.
CREATE TABLE IF NOT EXISTS bisections (
    id                  BIGSERIAL PRIMARY KEY,
    experiment_type_id  INT NOT NULL REFERENCES experiment_types(id),
    outcome             TEXT NOT NULL CHECK (outcome IN ('found','abandoned','refused')),
    good_sha            CHAR(40) NOT NULL,
    bad_sha             CHAR(40) NOT NULL,
    -- NULL for `refused`, and for `abandoned` this is the AMBIGUOUS candidate
    -- rather than a culprit. The outcome column is what says which it is; a
    -- reader who looks only at this column and sees a sha would be misled, so
    -- never render it without the outcome beside it.
    commit_sha          CHAR(40),

    -- The sigma arithmetic, stored so the decision can be re-derived rather
    -- than merely trusted. reps_derived is what
    -- `2 x (sigma_good + sigma_bad) / sqrt(r)` demanded; reps_used is what ran.
    -- They differ only when a human overrode the derivation, and that is
    -- exactly the case someone will want to find later.
    score_good          NUMERIC(6,4) NOT NULL,
    score_bad           NUMERIC(6,4) NOT NULL,
    sigma_good          NUMERIC(6,4) NOT NULL,
    sigma_bad           NUMERIC(6,4) NOT NULL,
    reps_derived        INT,
    reps_used           INT NOT NULL,

    -- The estimate SHOWN TO THE HUMAN before they authorised the time, kept
    -- alongside the actual candidate count. An estimate nobody can check
    -- afterwards is a marketing number.
    est_candidates      INT NOT NULL,
    est_minutes         NUMERIC(7,1) NOT NULL,
    candidates_run      INT NOT NULL DEFAULT 0,

    -- The epoch every candidate was scored under. One epoch per bisection,
    -- always: comparing scores across epochs is the thing epochs exist to
    -- prevent, and a bisection is that comparison repeated log2(n) times.
    scoring_epoch_id    INT REFERENCES scoring_epochs(id),

    -- Ran outside the nightly window on an explicit override. Recorded rather
    -- than blocked: a regression blocking a release at 10:00 is a legitimate
    -- reason to take the cluster, and the record is what keeps it exceptional.
    outside_window      BOOLEAN NOT NULL DEFAULT FALSE,
    requested_by        TEXT,
    reason              TEXT NOT NULL,
    trail               JSONB NOT NULL DEFAULT '[]',
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bisect_experiment
    ON bisections (experiment_type_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_bisect_outcome ON bisections (outcome);
