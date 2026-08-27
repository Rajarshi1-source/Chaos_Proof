"""The error-budget gate and the chaos breaker — admission control, not decoration.

Rev 1 recorded budget consumption and never read it. The SRE position is that a
spent budget STOPS you injecting more failure, because at that point the right
engineering decision is to fix reliability rather than keep proving it is broken.

The subtlety that is the whole point: the gate counts ALL budget burn, real
incidents included — not just what chaos consumed. The question is "how much
reliability allowance is left", not "how much have I spent on experiments".
"""

from dataclasses import dataclass

import psycopg

from ..constants import BUDGET_FREEZE_PCT, BUDGET_WARN_PCT
from ..db import DSN
from .blast_radius import (
    AVAILABILITY_SLO,
    BUDGET_WINDOW_DAYS,
    TOTAL_BUDGET_ERROR_SECONDS,
)

# Chaos-breaker trip conditions (safety-plane skill).
BREAKER_ABORTS_24H = 3
BREAKER_CONSECUTIVE_INVALID = 2
BREAKER_HALF_OPEN_AFTER_HOURS = 6


@dataclass(frozen=True)
class Gate:
    state: str          # open | restricted | closed
    reason: str


@dataclass(frozen=True)
class BreakerState:
    state: str          # closed | half_open | open
    reason: str


def _spent_pct(cur, namespace: str) -> float:
    """Budget spend over the rolling window, from ALL recorded burn."""
    cur.execute(
        """SELECT COALESCE(SUM(budget_consumed), 0)
             FROM error_budget_events
            WHERE recorded_at >= now() - make_interval(days => %s)""",
        (BUDGET_WINDOW_DAYS,))
    consumed_error_seconds = float(cur.fetchone()[0] or 0.0)
    return consumed_error_seconds / TOTAL_BUDGET_ERROR_SECONDS * 100.0


def breaker(cur, namespace: str) -> BreakerState:
    """Trips on repeated aborts, repeated INVALID, or a manual freeze.

    Two consecutive INVALID verdicts for one experiment is the subtle one: it
    means the MEASUREMENT PLANE is broken, so every further result is
    meaningless. Continuing to inject faults you cannot measure spends real
    blast radius to learn nothing.
    """
    # Manual freeze wins over everything and is itself a recorded event.
    cur.execute(
        """SELECT reason, created_at FROM chaos_freezes
            WHERE lifted_at IS NULL ORDER BY created_at DESC LIMIT 1""")
    row = cur.fetchone()
    if row:
        return BreakerState("open", f"manual freeze: {row[0]}")

    cur.execute(
        """SELECT count(*) FROM experiment_executions
            WHERE verdict = 'aborted' AND started_at >= now() - interval '24 hours'""")
    aborts = int(cur.fetchone()[0] or 0)
    if aborts >= BREAKER_ABORTS_24H:
        return BreakerState(
            "open", f"{aborts} aborts in 24h — the system keeps behaving worse "
                    "than predicted")

    # Two consecutive INVALID for the SAME experiment.
    cur.execute(
        """SELECT experiment, verdict,
                  row_number() OVER (PARTITION BY experiment ORDER BY id DESC) AS rn
             FROM experiment_executions
            WHERE verdict IS NOT NULL""")
    recent: dict[str, list[str]] = {}
    for experiment, verdict, rn in cur.fetchall():
        if rn <= BREAKER_CONSECUTIVE_INVALID:
            recent.setdefault(experiment, []).append(verdict)
    for experiment, verdicts in recent.items():
        if (len(verdicts) >= BREAKER_CONSECUTIVE_INVALID
                and all(v == "invalid" for v in verdicts)):
            return BreakerState(
                "open", f"{BREAKER_CONSECUTIVE_INVALID} consecutive INVALID for "
                        f"{experiment} — the measurement plane is broken and further "
                        "results would be meaningless")

    return BreakerState("closed", "no trip conditions met")


def gate(namespace: str) -> Gate:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        b = breaker(cur, namespace)
        if b.state == "open":
            return Gate("closed", f"chaos breaker open: {b.reason}")

        spent = _spent_pct(cur, namespace)

    if spent >= BUDGET_FREEZE_PCT:
        return Gate("closed",
                    f"{spent:.0f}% of the {BUDGET_WINDOW_DAYS}-day error budget is "
                    "spent — reliability work takes priority over injecting more failure")
    if spent >= BUDGET_WARN_PCT:
        return Gate("restricted",
                    f"{spent:.0f}% spent — low-radius experiments only")
    return Gate("open", f"{spent:.1f}% spent")


def record_burn(cur, execution_id: int, error_rate: float, impact_s: float) -> float:
    """Record what this execution actually cost, in error-seconds."""
    consumed = error_rate * impact_s
    cur.execute(
        """INSERT INTO error_budget_events
               (execution_id, budget_consumed, budget_remaining, sli_value)
           VALUES (%s, %s, %s, %s)""",
        (execution_id, consumed,
         TOTAL_BUDGET_ERROR_SECONDS - consumed, 1.0 - error_rate))
    return consumed


def arithmetic_note() -> str:
    """The whiteboard derivation, so the number on the dashboard can be checked."""
    return (
        f"Availability SLO {AVAILABILITY_SLO:.3%} over {BUDGET_WINDOW_DAYS}d "
        f"=> {TOTAL_BUDGET_ERROR_SECONDS:,.0f} error-seconds of budget. "
        "A p95 pod-kill (4% error for 45s) costs 1.8 error-seconds = 0.014%; "
        "six experiments daily for a month costs ~0.42% of the monthly budget.")
