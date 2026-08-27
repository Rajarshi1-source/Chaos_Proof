"""The out-of-band cleanup sweeper, run every 5 minutes by the chaos-cleanup
CronJob.

Finds executions still marked `running` past their deadline — the signature of a
runner that died mid-fault — force-cleans whatever they left behind, and counts
the orphan. `chaosproof_orphaned_cleanups_total > 0` is an ALERT, not a metric to
admire: it means the in-process saga did not get to run, and the only reason the
cluster is clean is that this job noticed.

Run:  python -m src.cleanup_sweeper
"""

import subprocess
import sys

import psycopg

from .db import DSN

# Prometheus metric name for the textfile/pushgateway path; also logged so the
# number is visible even where no exporter is wired.
ORPHAN_METRIC = "chaosproof_orphaned_cleanups_total"


def _kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", *args], capture_output=True,
                          text=True, timeout=60).stdout.strip()


def sweep() -> int:
    orphans = 0
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, experiment, chaos_engine, deadline_at
                 FROM experiment_executions
                WHERE state <> 'finished'
                  AND deadline_at IS NOT NULL
                  AND deadline_at < now()
                ORDER BY id""")
        rows = cur.fetchall()

        for execution_id, experiment, engine, deadline in rows:
            orphans += 1
            steps = []
            print(f"ORPHAN execution #{execution_id} ({experiment}) past deadline "
                  f"{deadline:%Y-%m-%d %H:%M:%S} — force-cleaning", file=sys.stderr)

            if engine:
                _kubectl("patch", "chaosengine", engine, "-n", "target-app",
                         "--type", "merge", "-p", '{"spec":{"engineState":"stop"}}')
                _kubectl("delete", "chaosengine", engine, "-n", "target-app",
                         "--ignore-not-found")
                steps.append({"name": "force_delete_chaosengine", "ok": True,
                              "detail": engine})

            # A run nobody watched cannot be scored: nothing observed the window,
            # so there is no evidence to evaluate. Record it as such rather than
            # inventing an outcome.
            cur.execute(
                """UPDATE experiment_executions
                      SET state = 'finished',
                          verdict = COALESCE(verdict, 'error'),
                          verdict_reason = COALESCE(verdict_reason,
                              'orphaned: runner died mid-experiment; cleaned '
                              'out-of-band by the chaos-cleanup CronJob'),
                          finished_at = COALESCE(finished_at, now())
                    WHERE id = %s""",
                (execution_id,))
            cur.execute(
                """INSERT INTO cleanup_logs
                       (execution_id, steps, escalated, escalation_reason)
                   VALUES (%s, %s, TRUE, %s)""",
                (execution_id, psycopg.types.json.Json(steps),
                 "cleaned out-of-band: the in-process saga never ran"))

    print(f"{ORPHAN_METRIC} {orphans}")
    if orphans:
        print(f"ALERT: {orphans} orphaned execution(s) force-cleaned — a runner "
              "died mid-fault. Investigate why, do not just clear it.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(sweep())
