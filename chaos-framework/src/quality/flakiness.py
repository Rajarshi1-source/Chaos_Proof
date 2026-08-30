"""Flakiness quarantine (§21.3) — why anyone would trust the CI gate.

The honest answer to the strongest objection to chaos-in-CI: **a flaky gate
gets disabled.** If a chaos check fails randomly on innocent PRs, developers
route around it within a week — `--no-verify`, an admin merge, or deleting the
workflow. Every CI gate that has ever been abandoned was abandoned for this
reason, and no amount of insisting that the gate is valuable survives the third
false failure on someone else's PR.

So ChaosProof measures its own variance, on UNCHANGED CODE, and demotes any
experiment that cannot gate reliably.

Three load-bearing rules:

  1. A new experiment is ADVISORY-ONLY until characterised. It reports and
     comments; it cannot block. It earns gating status after FLAKINESS_WINDOW
     clean runs — progressive delivery applied to our own tooling.
  2. A flaky experiment is AUTO-QUARANTINED to advisory, with a Slack notice
     naming sigma and a GitHub issue opened against the EXPERIMENT.
     Quarantine is not deletion: it keeps running and reporting, because an
     experiment nobody can see is an experiment nobody will fix.
  3. Bumping an experiment's `hypothesis.version` RESETS it to advisory.
     Changing what an experiment asserts invalidates its flakiness history —
     the old runs measured a different claim.

And the consequence people miss: only GATING experiments are in the epoch's
experiment_set, so quarantining one OPENS A NEW EPOCH. That is correct rather
than incidental — the composite score is now an average over a different set,
and a trend line across that boundary would be comparing two different things.
"""

import statistics
from dataclasses import dataclass, field

from ..constants import FLAKINESS_WINDOW, MAX_SCORE_STDDEV, MAX_VERDICT_FLIP_RATE

# A verdict that is not scoreable says nothing about stability: INVALID means
# the measurement plane failed, ABORTED means the safety plane cut the run
# short, and SKIPPED/DENIED mean it never ran. Counting those as "flips" would
# quarantine an experiment for the framework's behaviour rather than its own.
SCOREABLE_VERDICTS = frozenset({"held", "falsified"})


@dataclass
class Run:
    """One historical execution, as flakiness measurement sees it."""
    execution_id: int
    git_sha: str | None
    verdict: str
    score: float | None
    hypothesis_version: int


@dataclass
class FlakinessVerdict:
    experiment: str
    status: str                       # uncharacterised | stable | flaky
    gating: bool
    window_runs: int = 0
    sigma: float | None = None
    flip_rate: float | None = None
    reason: str = ""
    runs_needed: int = 0
    evidence: dict = field(default_factory=dict)

    def to_row(self) -> tuple:
        return (self.window_runs, self.sigma, self.flip_rate, self.gating)


def flip_rate(verdicts: list[str]) -> float:
    """Fraction of ADJACENT PAIRS whose verdict differs, on unchanged code.

    Deliberately pairwise rather than "fraction that differ from the mode".
    An experiment that returns held x10 then falsified x10 has a mode-based
    rate of 50% but flipped exactly once — that is a regression with a clean
    before and after, not flakiness. Alternating every run is the pathology,
    and only the pairwise measure separates them.
    """
    if len(verdicts) < 2:
        return 0.0
    flips = sum(1 for a, b in zip(verdicts, verdicts[1:]) if a != b)
    return flips / (len(verdicts) - 1)


def evaluate(experiment: str, runs: list[Run]) -> FlakinessVerdict:
    """Pure. Takes the window, returns the verdict — no database, no clock.

    `runs` must already be filtered to one experiment on an UNCHANGED git_sha
    and ordered oldest-first (flip rate is order-sensitive).
    """
    scoreable = [r for r in runs if r.verdict in SCOREABLE_VERDICTS]

    if len(scoreable) < FLAKINESS_WINDOW:
        needed = FLAKINESS_WINDOW - len(scoreable)
        return FlakinessVerdict(
            experiment, "uncharacterised", gating=False,
            window_runs=len(scoreable), runs_needed=needed,
            reason=f"{len(scoreable)}/{FLAKINESS_WINDOW} scoreable runs on unchanged "
                   f"code — advisory until characterised ({needed} more needed)",
            evidence={"total_runs": len(runs), "scoreable_runs": len(scoreable)})

    window = scoreable[-FLAKINESS_WINDOW:]
    scores = [r.score for r in window if r.score is not None]
    verdicts = [r.verdict for r in window]

    # A full window of scoreable verdicts with no scores at all is not
    # stability — it is an experiment whose scores never persisted. Refusing to
    # promote on absent evidence is the same instinct as the empty-series rule.
    if len(scores) < 2:
        return FlakinessVerdict(
            experiment, "uncharacterised", gating=False, window_runs=len(window),
            reason=f"{len(scores)} scored run(s) in a window of {len(window)} — "
                   "cannot compute sigma; advisory until scores exist",
            evidence={"scored_runs": len(scores)})

    sigma = statistics.stdev(scores)
    flips = flip_rate(verdicts)
    ev = {"scores": scores, "verdicts": verdicts,
          "max_sigma": MAX_SCORE_STDDEV, "max_flip_rate": MAX_VERDICT_FLIP_RATE}

    breaches = []
    if sigma > MAX_SCORE_STDDEV:
        breaches.append(f"sigma={sigma:.4f} > {MAX_SCORE_STDDEV}")
    if flips > MAX_VERDICT_FLIP_RATE:
        breaches.append(f"flip_rate={flips:.1%} > {MAX_VERDICT_FLIP_RATE:.1%}")

    if breaches:
        return FlakinessVerdict(
            experiment, "flaky", gating=False, window_runs=len(window),
            sigma=sigma, flip_rate=flips,
            reason=(", ".join(breaches) +
                    " — cannot block a merge on this. Quarantined to advisory; "
                    "it keeps running and reporting."),
            evidence=ev)

    return FlakinessVerdict(
        experiment, "stable", gating=True, window_runs=len(window),
        sigma=sigma, flip_rate=flips,
        reason=f"sigma={sigma:.4f}, flip_rate={flips:.1%} over {len(window)} runs "
               f"on unchanged code",
        evidence=ev)


# --------------------------------------------------------------------------- #
# Persistence and the quarantine transition.
# --------------------------------------------------------------------------- #

def window_for(cur, experiment: str, limit: int = FLAKINESS_WINDOW) -> list[Run]:
    """The most recent runs for one experiment on its LATEST git_sha.

    'Unchanged code' is what makes variance attributable to the experiment
    rather than to the system under test. Runs spanning a code change measure
    the change, and quarantining an experiment for correctly detecting a
    regression would be exactly backwards.

    Runs are also scoped to the CURRENT hypothesis version: bumping the version
    changes what the experiment asserts, so earlier runs measured a different
    claim and must not count toward characterising this one.
    """
    cur.execute(
        """SELECT e.git_sha, h.version
             FROM experiment_executions e
             JOIN hypotheses h ON h.id = e.hypothesis_id
            WHERE e.experiment = %s AND e.git_sha IS NOT NULL
            ORDER BY e.id DESC LIMIT 1""", (experiment,))
    row = cur.fetchone()
    if row is None:
        return []
    git_sha, hyp_version = row

    cur.execute(
        """SELECT e.id, e.git_sha, e.verdict, e.score, h.version
             FROM experiment_executions e
             JOIN hypotheses h ON h.id = e.hypothesis_id
            WHERE e.experiment = %s AND e.git_sha = %s AND h.version = %s
              AND e.verdict IS NOT NULL
            ORDER BY e.id DESC LIMIT %s""",
        (experiment, git_sha, hyp_version, limit))
    rows = cur.fetchall()
    # Oldest-first: flip rate is order-sensitive.
    return [Run(r[0], r[1], r[2], float(r[3]) if r[3] is not None else None, r[4])
            for r in reversed(rows)]


def stored_status(cur, experiment: str) -> dict | None:
    cur.execute(
        """SELECT f.window_runs, f.score_stddev, f.verdict_flip_rate, f.gating,
                  f.quarantined_at, f.quarantine_reason
             FROM experiment_flakiness f
             JOIN experiment_types t ON t.id = f.experiment_type_id
            WHERE t.name = %s""", (experiment,))
    row = cur.fetchone()
    if row is None:
        return None
    return {"window_runs": row[0], "sigma": float(row[1]) if row[1] is not None else None,
            "flip_rate": float(row[2]) if row[2] is not None else None,
            "gating": row[3], "quarantined_at": row[4], "quarantine_reason": row[5]}


def record(cur, experiment: str, verdict: FlakinessVerdict) -> bool:
    """Write the measured status. Returns True if the GATING BIT MOVED, which
    is what tells the caller an epoch must be opened."""
    cur.execute("SELECT id FROM experiment_types WHERE name = %s", (experiment,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown experiment {experiment!r}")
    type_id = row[0]

    before = stored_status(cur, experiment)
    was_gating = bool(before and before["gating"])

    quarantined = verdict.status == "flaky"
    cur.execute(
        """INSERT INTO experiment_flakiness
               (experiment_type_id, window_runs, score_stddev, verdict_flip_rate,
                gating, quarantined_at, quarantine_reason)
           VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s)
           ON CONFLICT (experiment_type_id) DO UPDATE SET
               window_runs = EXCLUDED.window_runs,
               score_stddev = EXCLUDED.score_stddev,
               verdict_flip_rate = EXCLUDED.verdict_flip_rate,
               gating = EXCLUDED.gating,
               quarantined_at = EXCLUDED.quarantined_at,
               quarantine_reason = EXCLUDED.quarantine_reason""",
        (type_id, verdict.window_runs, verdict.sigma, verdict.flip_rate,
         verdict.gating, quarantined, verdict.reason if quarantined else None))

    return was_gating != verdict.gating
