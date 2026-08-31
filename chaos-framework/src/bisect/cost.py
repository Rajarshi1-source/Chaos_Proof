"""What a bisection costs, computed BEFORE it is incurred.

The third documented scaling decision (ADR-007). Each candidate costs
`reps x (load warm-up + fault + recovery + cleanup)`; a 40-commit range needs
log2(40) = 6 candidates. That is a job which monopolises the one cluster for an
hour and a half.

    Option A - bisect automatically on every score regression. Rejected: a
    90-minute cluster-monopolising job triggered by NOISE is a denial of service
    against the daily chaos schedule, and score dips are exactly the thing this
    project has already proved are sometimes noise.

    Option B - on demand, with the estimate shown before starting, a hard
    candidate cap, and a nightly window. Chosen. The Slack message reporting a
    regression carries a button reading "Bisect (est. 6 candidates, ~90 min)".
    A human decides.

Two things here are deliberately not round numbers:

  1. **The cap is derived from the window, not invented.** The nightly window is
     four hours, so a bisection that cannot finish inside four hours is refused
     rather than started and killed at 05:00 half-way through - a partial
     binary search has consumed the cluster and learned nothing.

  2. **The per-run cost is derived from the experiment spec.** The plan's round
     figure is 5 minutes per run; for `pod_kill_payment_svc` the actual figure
     is 6.0 - a 120s steady-state window, a 60s fault, a 120s recovery window
     and a 60s cleanup allowance. The round number omits cleanup. An estimate
     shown to a human so they can consent to the cost must not be the
     optimistic one, so `from_spec()` is what the CLI uses and the 5-minute
     figure survives only as the explicit argument in the plan's worked example.
"""

import datetime as _dt
import math
from dataclasses import dataclass

from ..constants import STEADY_STATE_WINDOW_S

# The nightly window, local time. Chosen to sit after the 03:00 scheduled chaos
# run rather than across it: two things injecting faults into one cluster at
# once is not two experiments, it is one confounded experiment.
NIGHTLY_WINDOW_START_HOUR = 1
NIGHTLY_WINDOW_END_HOUR = 5

# The hard candidate cap. 8 candidates covers a 256-commit range, which is more
# history than this project's daily cadence produces between two known-good
# scores. Beyond it the honest instruction is to narrow the range by hand.
MAX_CANDIDATES = 8

# Cleanup is not free and is not optional (the saga plus its out-of-band
# CronJob). Omitting it is how a cost estimate becomes an underestimate.
CLEANUP_ALLOWANCE_S = 60

# The plan's round figure, kept only so the worked example stays reproducible.
PLAN_MINUTES_PER_RUN = 5.0


def window_minutes() -> float:
    """Length of the nightly window. This IS the total-cost cap."""
    span = (NIGHTLY_WINDOW_END_HOUR - NIGHTLY_WINDOW_START_HOUR) % 24
    return span * 60.0


@dataclass(frozen=True)
class CostEstimate:
    """The number a human is shown before they authorise the cluster time."""
    commits_in_range: int
    candidates: int
    reps: int
    minutes_per_run: float
    total_minutes: float
    affordable: bool
    reason: str

    @property
    def button_label(self) -> str:
        """Exactly what §20.3 puts on the Slack button.

        Rounded UP to the next five minutes, never to the nearest. A cost
        estimate a human is about to consent to must not be optimistic: nearest-5
        rounding turned 72 minutes into "~70 min", which is a smaller number than
        the truth on the one control whose entire purpose is to make the cost
        look as large as it really is.
        """
        rounded = math.ceil(self.total_minutes / 5) * 5
        return f"Bisect (est. {self.candidates} candidates, ~{rounded:.0f} min)"

    def to_dict(self) -> dict:
        return {
            "commitsInRange": self.commits_in_range, "candidates": self.candidates,
            "reps": self.reps, "minutesPerRun": self.minutes_per_run,
            "totalMinutes": self.total_minutes, "affordable": self.affordable,
            "reason": self.reason, "buttonLabel": self.button_label,
        }


def minutes_per_run_from_spec(spec: dict) -> float:
    """Derive the per-run cost from what the experiment actually declares.

    steady-state window + fault duration + recovery window + cleanup allowance.
    The steady-state window is the pre-flight check that must pass before
    injection, and it is real wall-clock time on the cluster even though it is
    not part of the fault.
    """
    exp = spec.get("experiment", spec)
    seconds = (STEADY_STATE_WINDOW_S
               + float(exp.get("fault_duration_s", 60))
               + float(exp.get("recovery_window_s", 120))
               + CLEANUP_ALLOWANCE_S)
    return seconds / 60.0


def candidates_for(commits_in_range: int) -> int:
    """Iterations of `while hi - lo > 1` over a range of this width.

    The search interval halves each step, so the count is ceil(log2(width)).
    A width of 40 gives 6, which is the plan's figure.
    """
    if commits_in_range <= 1:
        return 0
    return math.ceil(math.log2(commits_in_range))


def estimate(commits_in_range: int, reps: int,
             minutes_per_run: float = PLAN_MINUTES_PER_RUN) -> CostEstimate:
    """Cost, and whether it fits inside the nightly window.

    `affordable=False` is a refusal to start, not a warning to click through.
    A bisection killed at the end of the window has spent the whole cluster
    night and produced no commit.
    """
    candidates = candidates_for(commits_in_range)
    total = candidates * reps * minutes_per_run
    cap_minutes = window_minutes()

    if candidates == 0:
        return CostEstimate(commits_in_range, 0, reps, minutes_per_run, 0.0, False,
                            f"range of {commits_in_range} commit(s) has nothing to "
                            f"search - good and bad are adjacent or identical")
    if candidates > MAX_CANDIDATES:
        return CostEstimate(commits_in_range, candidates, reps, minutes_per_run,
                            total, False,
                            f"{candidates} candidates exceeds the cap of "
                            f"{MAX_CANDIDATES} - narrow the range by hand and "
                            f"bisect the half you suspect")
    if total > cap_minutes:
        return CostEstimate(commits_in_range, candidates, reps, minutes_per_run,
                            total, False,
                            f"~{total:.0f} min does not fit the "
                            f"{cap_minutes:.0f}-minute nightly window "
                            f"({NIGHTLY_WINDOW_START_HOUR:02d}:00-"
                            f"{NIGHTLY_WINDOW_END_HOUR:02d}:00); reduce reps or "
                            f"narrow the range")

    return CostEstimate(commits_in_range, candidates, reps, minutes_per_run, total,
                        True,
                        f"{candidates} candidates x {reps} reps x "
                        f"{minutes_per_run:.1f} min = ~{total:.0f} min, inside the "
                        f"{cap_minutes:.0f}-minute nightly window")


@dataclass(frozen=True)
class WindowCheck:
    inside: bool
    now_hour: int
    reason: str


def in_nightly_window(now: _dt.datetime | None = None) -> WindowCheck:
    """Is it currently the nightly window?

    Outside it, bisection needs an explicit override, and the override is
    recorded. The window is not a safety control - the safety plane is - it is a
    scheduling courtesy to the daily run, and treating it as unbreakable would
    make the tool useless in the one situation where someone actually needs it
    (a regression found at 10:00 that is blocking a release).
    """
    now = now or _dt.datetime.now()
    h = now.hour
    start, end = NIGHTLY_WINDOW_START_HOUR, NIGHTLY_WINDOW_END_HOUR
    inside = start <= h < end if start < end else (h >= start or h < end)
    if inside:
        return WindowCheck(True, h, f"{h:02d}:00 is inside the nightly window "
                                    f"{start:02d}:00-{end:02d}:00")
    return WindowCheck(False, h,
                       f"{h:02d}:00 is outside the nightly window "
                       f"{start:02d}:00-{end:02d}:00 - a bisection now competes "
                       f"with the daily schedule. Pass --now to override; the "
                       f"override is recorded in the result.")
