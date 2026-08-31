"""Persisting a bisection - including the ones that did not find anything.

`refused` and `abandoned` rows are the reason this table exists. A tool that
records only its successes reads, six months later, as a tool that always
succeeds; the interesting history is the night it refused because the anchors
were not separable, and the night it abandoned two candidates in.
"""

import json


def record(cur, result, *, requested_by: str | None = None) -> int:
    """Insert one bisection. Returns the row id.

    Takes a `BisectResult` rather than loose arguments so a new field on the
    result cannot silently stop being stored.
    """
    cur.execute("SELECT id FROM experiment_types WHERE name = %s",
                (result.experiment,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown experiment {result.experiment!r} - it has "
                         f"never run, so there are no anchors to bisect between")
    type_id = row[0]

    epoch_id = None
    if result.epoch:
        cur.execute("SELECT id FROM scoring_epochs WHERE epoch_sha256 = %s",
                    (result.epoch,))
        r = cur.fetchone()
        epoch_id = r[0] if r else None

    cost = result.cost
    cur.execute(
        """INSERT INTO bisections
             (experiment_type_id, outcome, good_sha, bad_sha, commit_sha,
              score_good, score_bad, sigma_good, sigma_bad,
              reps_derived, reps_used, est_candidates, est_minutes,
              candidates_run, scoring_epoch_id, outside_window, requested_by,
              reason, trail, finished_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
           RETURNING id""",
        (type_id, result.outcome, result.good, result.bad, result.commit,
         result.anchor_score_good, result.anchor_score_bad,
         result.anchor_sigma_good, result.anchor_sigma_bad,
         result.reps_derived, result.reps,
         cost.candidates if cost else 0,
         round(cost.total_minutes, 1) if cost else 0.0,
         result.candidates_evaluated, epoch_id, result.overrode_window,
         requested_by, result.reason,
         json.dumps([c.to_dict() for c in result.trail])))
    return cur.fetchone()[0]


def recent(cur, experiment: str | None = None, limit: int = 20) -> list[dict]:
    sql = """SELECT b.id, t.name, b.outcome, b.good_sha, b.bad_sha, b.commit_sha,
                    b.reps_used, b.est_candidates, b.est_minutes, b.candidates_run,
                    b.outside_window, b.reason, b.started_at
               FROM bisections b
               JOIN experiment_types t ON t.id = b.experiment_type_id"""
    params: list = []
    if experiment:
        sql += " WHERE t.name = %s"
        params.append(experiment)
    sql += " ORDER BY b.started_at DESC LIMIT %s"
    params.append(limit)
    cur.execute(sql, params)
    return [{"id": r[0], "experiment": r[1], "outcome": r[2], "good": r[3],
             "bad": r[4], "commit": r[5], "reps": r[6], "estCandidates": r[7],
             "estMinutes": float(r[8]), "candidatesRun": r[9],
             "outsideWindow": r[10], "reason": r[11],
             "startedAt": r[12].isoformat() if r[12] else None}
            for r in cur.fetchall()]
