"""Seed a flakiness window so the quarantine path can be exercised end to end.

WHY THIS EXISTS, stated plainly: characterisation needs FLAKINESS_WINDOW (20)
runs on unchanged code, and one real run of `pod_kill_payment_svc` costs about
five minutes of cluster time. Demonstrating promotion AND demotion honestly
would take roughly three and a half hours of continuous injection. The
mechanism under test is deterministic given its window, so the window is
seeded and the mechanism is exercised for real.

WHAT IS REAL AND WHAT IS NOT:

  real        every line of measurement, quarantine, epoch and issue-filing
              logic. Nothing is stubbed. The rows go through the same
              `flakiness.window_for` query and the same `quarantine.apply`
              transition that a live nightly run uses.
  seeded      the 20+ execution rows themselves, and their scores.

Every seeded row carries `trigger_source = 'gate8-synthetic'` so it is
distinguishable from real evidence forever, by anyone, with one WHERE clause.
The dashboard and the resilience trend can exclude it; this script's `--purge`
removes it. A demonstration that cannot be told apart from evidence is not a
demonstration, it is contamination.

Usage:
    python scripts/seed_flakiness_window.py --experiment pod_kill_payment_svc --stable
    python scripts/seed_flakiness_window.py --experiment pod_kill_payment_svc --flaky
    python scripts/seed_flakiness_window.py --purge
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "chaos-framework"))

import psycopg                                                    # noqa: E402

from src.constants import FLAKINESS_WINDOW                        # noqa: E402
from src.db import DSN                                            # noqa: E402

MARKER = "gate8-synthetic"
SEED_SHA = "0" * 39 + "8"          # obviously not a real commit


def _ids(cur, experiment: str) -> tuple[int, str]:
    cur.execute("SELECT id FROM experiment_types WHERE name = %s", (experiment,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"{experiment!r} has never run — seed needs its type row")
    type_id = row[0]
    cur.execute("SELECT id FROM hypotheses WHERE experiment_type_id = %s "
                "ORDER BY version DESC LIMIT 1", (type_id,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"{experiment!r} has no recorded hypothesis")
    return type_id, row[0]


def seed(cur, experiment: str, scores: list[float], verdicts: list[str]) -> int:
    type_id, hypothesis_id = _ids(cur, experiment)
    cur.execute("SELECT id FROM scoring_epochs ORDER BY id DESC LIMIT 1")
    epoch_row = cur.fetchone()
    epoch_id = epoch_row[0] if epoch_row else None

    for score, verdict in zip(scores, verdicts):
        cur.execute(
            """INSERT INTO experiment_executions
                   (experiment, experiment_type_id, hypothesis_id, verdict,
                    verdict_reason, git_sha, scoring_epoch_id, score, state,
                    trigger_source, fault_injected_at, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'finished', %s,
                       now(), now())""",
            (experiment, type_id, hypothesis_id, verdict,
             f"seeded window row ({MARKER})", SEED_SHA, epoch_id, score, MARKER))
    return len(scores)


def purge(cur) -> int:
    cur.execute("DELETE FROM experiment_executions WHERE trigger_source = %s",
                (MARKER,))
    return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="pod_kill_payment_svc")
    ap.add_argument("--stable", action="store_true",
                    help="a clean window: tight scores, no verdict flips")
    ap.add_argument("--flaky", action="store_true",
                    help="destabilise: wide score spread and alternating verdicts")
    ap.add_argument("--purge", action="store_true",
                    help="remove every seeded row")
    args = ap.parse_args()

    with psycopg.connect(DSN, connect_timeout=10) as conn, conn.cursor() as cur:
        if args.purge:
            n = purge(cur)
            conn.commit()
            print(f"purged {n} seeded row(s) [{MARKER}]")
            return 0

        if args.stable:
            # Tight and boring: sigma well under 0.05, no flips. This is what
            # 20 clean runs on unchanged code looks like.
            scores = [0.90 + (i % 3) * 0.01 for i in range(FLAKINESS_WINDOW)]
            verdicts = ["held"] * FLAKINESS_WINDOW
            n = seed(cur, args.experiment, scores, verdicts)
            conn.commit()
            print(f"seeded {n} STABLE row(s) for {args.experiment} [{MARKER}]")
        elif args.flaky:
            # The destabilisation. Alternating pass/fail on IDENTICAL code —
            # which is precisely the thing that gets a CI gate switched off.
            scores = [0.95 if i % 2 else 0.35 for i in range(FLAKINESS_WINDOW)]
            verdicts = ["held" if i % 2 else "falsified"
                        for i in range(FLAKINESS_WINDOW)]
            n = seed(cur, args.experiment, scores, verdicts)
            conn.commit()
            print(f"seeded {n} FLAKY row(s) for {args.experiment} [{MARKER}]")
        else:
            ap.error("choose --stable, --flaky, or --purge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
