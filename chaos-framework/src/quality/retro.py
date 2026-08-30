"""Retro-scoring (§21.1) — the reason raw samples are persisted at all.

When the scorer changes, every stored score becomes incomparable to every new
one. The options are: pretend otherwise (the trend chart becomes fiction),
throw the history away (the 45%->92% story dies), or RE-SCORE the old runs
under the new epoch from the raw observations that were kept for exactly this.

That third option is the entire justification for `sli_samples` existing as a
partitioned table of 5-second ticks rather than a single score column. It is
also what makes the story survivable under questioning:

    "Four weeks, one scoring epoch after I retro-scored the early runs. The
     weights changed once in week two when I added alert validation — that
     boundary is on the chart, and the earlier runs were re-scored under the
     new weights so the comparison is real. Without that, some of my
     improvement would have been me editing the scorer, and I'd rather show you
     the seam than have you find it."

WHAT CAN AND CANNOT BE RE-DERIVED — stated because the honest answer is "most
of it, not all of it":

  re-derived from sli_samples   slo_recovery, resilience_pattern,
                                recovery_completeness — these read sample
                                series and nothing else.
  carried from stored evidence  alert_validation — its inputs are Alertmanager
                                fire times, which are not sample data and were
                                never in `sli_samples`. Re-deriving it would
                                mean inventing them.

Every retro-score records which checks came from which source in `method`, so a
retro-scored point on the trend chart can be interrogated rather than trusted.
Pretending the whole score was recomputed would be a small lie that a reviewer
could find, and the entire value of this mechanism is that it survives being
looked at.
"""

from dataclasses import dataclass, field

from ..measurement.sampler import Sample, SampleSet
from ..scoring import scorer
from ..scoring.scorer import Check
from ..validators import checks as V
from . import epochs

# Checks that read sample series only, and are therefore re-derivable.
DERIVABLE = ("slo_recovery", "resilience_pattern", "recovery_completeness")


@dataclass
class RetroResult:
    execution_id: int
    experiment: str
    original_score: float | None
    original_epoch: str | None
    new_score: float | None
    new_status: str
    epoch_sha: str
    method: dict = field(default_factory=dict)
    skipped_reason: str | None = None

    @property
    def changed(self) -> bool:
        if self.original_score is None or self.new_score is None:
            return self.original_score is not self.new_score
        return abs(self.original_score - self.new_score) > 1e-9

    def delta(self) -> str:
        if self.original_score is None or self.new_score is None:
            return f"{_fmt(self.original_score)} -> {_fmt(self.new_score)}"
        return (f"{self.original_score:.4f} -> {self.new_score:.4f} "
                f"({self.new_score - self.original_score:+.4f})")


def _fmt(v) -> str:
    return "none" if v is None else f"{v:.4f}"


def samples_for(cur, execution_id: int) -> SampleSet:
    """Rebuild the sampled window from `sli_samples`. Rows are (t, source,
    metric, value); ticks are grouped by timestamp so the reconstruction has
    the same shape the runner saw."""
    cur.execute(
        """SELECT extract(epoch FROM sampled_at)::float8, source, metric, value
             FROM sli_samples WHERE execution_id = %s
            ORDER BY sampled_at, source, metric""", (execution_id,))
    ticks: dict[float, dict[str, dict]] = {}
    for ts, source, metric, value in cur.fetchall():
        tick = ticks.setdefault(ts, {"client": {}, "server": {}})
        bucket = "client" if source == "k6" else "server"
        tick[bucket][metric] = None if value is None else float(value)

    ss = SampleSet()
    for ts in sorted(ticks):
        ss.add(Sample(sampled_at=ts, client=ticks[ts]["client"],
                      server=ticks[ts]["server"]))
    return ss


def stored_checks(cur, execution_id: int) -> dict[str, Check]:
    cur.execute(
        """SELECT check_type, check_name, applicable, outcome, score,
                  expected_value, actual_value, message
             FROM validation_checks WHERE execution_id = %s""", (execution_id,))
    return {r[0]: Check(r[0], r[1], r[2], r[3],
                        None if r[4] is None else float(r[4]), r[5], r[6], r[7] or "")
            for r in cur.fetchall()}


def rescore_one(cur, execution_id: int) -> RetroResult:
    """Re-derive one run's score from its stored observations."""
    cur.execute(
        """SELECT e.experiment, e.verdict, e.score, ep.epoch_sha256,
                  t.name, e.experiment_type_id
             FROM experiment_executions e
             LEFT JOIN scoring_epochs ep ON ep.id = e.scoring_epoch_id
             LEFT JOIN experiment_types t ON t.id = e.experiment_type_id
            WHERE e.id = %s""", (execution_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"no execution #{execution_id}")
    experiment, verdict, original, original_epoch, _tname, _tid = row
    original = None if original is None else float(original)

    epoch = epochs.current(cur)

    # A run that was never scoreable stays unscoreable. Re-scoring an INVALID
    # run under a new epoch cannot make it measurable — the measurement plane
    # failed, and no change to the weights repairs that after the fact.
    if verdict in ("invalid", "aborted", "skipped", "denied", "error", None):
        return RetroResult(execution_id, experiment, original, original_epoch,
                           None, "invalid", epoch.sha256,
                           skipped_reason=f"verdict {verdict!r} was never scoreable")

    samples = samples_for(cur, execution_id)
    stored = stored_checks(cur, execution_id)

    if not samples.samples:
        return RetroResult(execution_id, experiment, original, original_epoch,
                           None, "invalid", epoch.sha256,
                           skipped_reason="no sli_samples retained for this run — "
                                          "nothing to re-derive from")

    pattern_metric, pattern_name = _pattern_from(stored)
    derived = {
        "slo_recovery": V.slo_recovery(samples),
        "resilience_pattern": V.resilience_pattern(samples, pattern_metric, pattern_name),
        "recovery_completeness": V.recovery_completeness(samples),
    }

    check_list, method = [], {"re_derived": [], "carried_over": [], "missing": []}
    for name in ("slo_recovery", "alert_validation", "resilience_pattern",
                 "recovery_completeness"):
        if name in derived:
            check_list.append(derived[name])
            method["re_derived"].append(name)
        elif name in stored:
            check_list.append(stored[name])
            method["carried_over"].append(name)
        else:
            method["missing"].append(name)

    result = scorer.calculate(check_list)
    return RetroResult(execution_id, experiment, original, original_epoch,
                       result.score, result.status, epoch.sha256, method)


def _pattern_from(stored: dict[str, Check]) -> tuple[str | None, str | None]:
    """Recover the pattern metric from the stored check's own evidence.

    The experiment spec is not in the database — only its effects are — so the
    stored `resilience_pattern` check is the record of whether a pattern was
    asserted at all. An experiment that declared none must keep declaring none
    on re-score, or retro-scoring would silently change which checks apply and
    the two scores would differ for a reason that has nothing to do with the
    epoch.
    """
    check = stored.get("resilience_pattern")
    if check is None or not check.applicable:
        return None, None
    expected = check.expected_value or ""
    if "max_over_time(" in expected:
        metric = expected.split("max_over_time(", 1)[1].split(")", 1)[0]
        return metric, (check.check_name or "").replace(" activated", "") or metric
    return None, None


def persist(cur, result: RetroResult, epoch_id: int) -> bool:
    """Insert the retro-score. Returns False when one already exists for this
    (run, epoch) — idempotent, never an overwrite."""
    cur.execute(
        """INSERT INTO retro_scores
               (execution_id, scoring_epoch_id, score, status,
                weights_denominator, method)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (execution_id, scoring_epoch_id) DO NOTHING
           RETURNING id""",
        (result.execution_id, epoch_id, result.new_score, result.new_status,
         None, _json(result.method)))
    return cur.fetchone() is not None


def rescore_last(cur, n: int, reason: str,
                 experiment: str | None = None) -> tuple[list[RetroResult], object]:
    """Re-score the last N runs under the CURRENT epoch, writing new rows."""
    epoch = epochs.open_epoch(cur, reason)

    params: list = []
    where = "WHERE verdict IS NOT NULL"
    if experiment:
        where += " AND experiment = %s"
        params.append(experiment)
    params.append(n)
    cur.execute(f"SELECT id FROM experiment_executions {where} "
                f"ORDER BY id DESC LIMIT %s", params)
    ids = [r[0] for r in reversed(cur.fetchall())]

    results = []
    for execution_id in ids:
        result = rescore_one(cur, execution_id)
        if result.skipped_reason is None:
            persist(cur, result, epoch.id)
        results.append(result)
    return results, epoch


def _json(obj) -> str:
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
