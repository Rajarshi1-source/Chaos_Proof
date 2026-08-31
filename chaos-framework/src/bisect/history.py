"""Where the anchors and the sigmas come from.

Bisection needs four numbers before it can start: a score at the good commit, a
score at the bad commit, and a sigma for each. Three rules govern where they may
come from, and all three are refusals rather than fallbacks.

  1. **Sigma comes from the flakiness window, never from the anchors.** Two runs
     give no usable spread. `quality/flakiness.py` measures sigma over 20 runs on
     unchanged code, which is the only sigma in this system with enough
     observations behind it to divide by. An experiment with no characterised
     sigma cannot be bisected - there is no way to know how many repetitions are
     enough, and running "some" and hoping is the failure the whole module
     exists to prevent.

  2. **Both anchors must be scored under the SAME epoch.** Comparing a score
     from epoch A with one from epoch B is the exact thing scoring epochs exist
     to prevent, and a bisection is nothing but that comparison repeated.
     Retro-scores are consulted first for this reason: a run re-scored under the
     current epoch is comparable; the original score on the row may not be.

  3. **An unscoreable anchor is not an anchor.** INVALID, ABORTED, SKIPPED and
     DENIED carry no score, and a NULL score is not a zero.
"""

from dataclasses import dataclass

from ..constants import FLAKINESS_WINDOW


class AnchorUnavailable(RuntimeError):
    """No usable anchor. Raised rather than defaulted: a bisection started from
    a guessed score searches for a regression that may not exist."""


@dataclass(frozen=True)
class Anchor:
    sha: str
    score: float
    epoch_id: int
    epoch_sha: str
    execution_id: int
    source: str                  # "execution" | "retro_score"
    runs: int                    # how many scoreable runs at this sha

    def describe(self) -> str:
        return (f"{self.sha[:12]} score={self.score:.4f} "
                f"epoch #{self.epoch_id} ({self.epoch_sha[:12]}) "
                f"from {self.source}, {self.runs} scoreable run(s)")


def sigma_for(cur, experiment: str) -> tuple[float, str]:
    """The experiment's measured sigma, or a refusal explaining what is missing.

    Reads the STORED flakiness row rather than recomputing, so that the number
    the bisection uses is the same one the dashboard shows and the same one that
    decided whether this experiment gates a merge. Recomputing here could give a
    different answer from the same data at a different moment, and then two
    parts of the system would disagree about how noisy an experiment is.
    """
    cur.execute(
        """SELECT f.score_stddev, f.window_runs, f.gating
             FROM experiment_flakiness f
             JOIN experiment_types t ON t.id = f.experiment_type_id
            WHERE t.name = %s""", (experiment,))
    row = cur.fetchone()
    if row is None or row[0] is None:
        raise AnchorUnavailable(
            f"{experiment} has no characterised sigma. Run "
            f"`chaosctl flakiness --experiment {experiment} --apply` once it has "
            f"{FLAKINESS_WINDOW} scoreable runs on unchanged code. Without sigma "
            f"there is no way to derive a repetition count, and a bisection at a "
            f"guessed rep count converges on noise.")
    sigma, window_runs, gating = float(row[0]), row[1], row[2]
    note = (f"sigma={sigma:.4f} over {window_runs} runs on unchanged code"
            + ("" if gating else "; this experiment is currently ADVISORY, so the "
                                 "regression it reports has not been blocking merges"))
    return sigma, note


def anchor_at(cur, experiment: str, sha: str, epoch_id: int | None = None) -> Anchor:
    """The score for `experiment` at commit `sha`, under one epoch.

    Prefers a retro-score under `epoch_id` when one exists, because that is the
    score computed by TODAY's scorer from the stored samples - which is what
    makes it comparable to the other anchor. Falls back to the score recorded on
    the execution itself only when its epoch already matches.

    Averages when several scoreable runs exist at the same commit: more
    observations of the same code is more evidence, and discarding all but the
    latest would throw it away.
    """
    cur.execute(
        """SELECT r.score, e.id, ep.id, ep.epoch_sha256
             FROM retro_scores r
             JOIN experiment_executions e ON e.id = r.execution_id
             JOIN scoring_epochs ep ON ep.id = r.scoring_epoch_id
            WHERE e.experiment = %s AND e.git_sha = %s AND r.score IS NOT NULL
              AND (%s::int IS NULL OR ep.id = %s)
            ORDER BY ep.id DESC, e.id DESC""",
        (experiment, sha, epoch_id, epoch_id))
    rows = cur.fetchall()
    source = "retro_score"

    if not rows:
        cur.execute(
            """SELECT e.score, e.id, ep.id, ep.epoch_sha256
                 FROM experiment_executions e
                 JOIN scoring_epochs ep ON ep.id = e.scoring_epoch_id
                WHERE e.experiment = %s AND e.git_sha = %s AND e.score IS NOT NULL
                  AND (%s::int IS NULL OR ep.id = %s)
                ORDER BY e.id DESC""",
            (experiment, sha, epoch_id, epoch_id))
        rows = cur.fetchall()
        source = "execution"

    if not rows:
        raise AnchorUnavailable(
            f"no scoreable run of {experiment} at {sha[:12]}"
            + (f" under epoch #{epoch_id}" if epoch_id else "")
            + ". An INVALID or ABORTED run carries no score, and a NULL score is "
              "not a zero - pass --score-good/--score-bad explicitly if you are "
              "bisecting against numbers from outside the evidence store.")

    # One epoch only. Mixing them is the comparison epochs exist to prevent.
    top_epoch = rows[0][2]
    usable = [r for r in rows if r[2] == top_epoch]
    scores = [float(r[0]) for r in usable]
    return Anchor(sha=sha, score=sum(scores) / len(scores), epoch_id=top_epoch,
                  epoch_sha=usable[0][3], execution_id=usable[0][1],
                  source=source, runs=len(usable))


def check_same_epoch(good: Anchor, bad: Anchor) -> None:
    if good.epoch_id != bad.epoch_id:
        raise AnchorUnavailable(
            f"the anchors were scored under different epochs "
            f"(#{good.epoch_id} vs #{bad.epoch_id}). The difference between them "
            f"is at least partly a change to the scorer, not to the system. "
            f"Retro-score both under one epoch first: "
            f"`chaosctl retro-score --last 50 --reason 'bisection anchors' --apply`.")
