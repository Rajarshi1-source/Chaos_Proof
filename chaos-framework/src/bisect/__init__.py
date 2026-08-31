"""Resilience regression bisection (§20).

The dashboard shows the score fell from 0.87 to 0.71 over a week. *Which
commit did that?* Bisection replays the identical experiment — same epoch,
same load profile, same hypothesis version — across the commit range and
binary-searches for the change that moved the number.

Two things separate this from `git bisect run ./score.sh`:

  1. **The sigma arithmetic** (`sigma.py`). Chaos experiments have run-to-run
     variance. A single run at each candidate has a meaningful chance of
     misclassifying it, and ONE MISCLASSIFICATION SENDS THE BINARY SEARCH DOWN
     THE WRONG HALF PERMANENTLY — there is no later evidence that contradicts
     it, because the correct half is never visited again. So the number of
     repetitions is derived from the measured flakiness, and a candidate that
     lands inside the flakiness band returns `abandoned`, never a guess.

  2. **The cost is visible before it is incurred** (`cost.py`). A candidate
     costs reps x ~5 minutes; a 40-commit range needs six of them, so ~90
     minutes of exclusive cluster time. Bisecting automatically on every score
     dip is a denial of service against the daily chaos schedule, so this is
     on-demand, capped, and confined to a nightly window — with the estimate
     printed first and a human deciding.

The search itself (`runner.py`) is pure: it takes a callable that evaluates a
candidate and knows nothing about clusters, git, or databases. That is what
makes the arithmetic testable without a cluster.
"""

from .cost import CostEstimate, WindowCheck, estimate, in_nightly_window
from .runner import BisectResult, bisect
from .sigma import (
    CandidateClass,
    anchors_separable,
    classify_candidate,
    reps_needed,
    required_separation,
    sigma_of_mean,
)

__all__ = [
    "BisectResult", "CandidateClass", "CostEstimate", "WindowCheck",
    "anchors_separable", "bisect", "classify_candidate", "estimate",
    "in_nightly_window", "reps_needed", "required_separation", "sigma_of_mean",
]
