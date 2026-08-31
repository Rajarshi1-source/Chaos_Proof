"""The arithmetic that makes bisection converge on a commit rather than on noise.

This is the part the original feature sketch skipped, and it is the difference
between a demo and a tool.

    Required separation:   |score_good - score_bad|  >  2 x (sigma_good + sigma_bad)

    With sigma = 0.06 on unchanged code and a regression of 0.16:
        one repetition:   0.16 > 2 x 0.12 = 0.24   ->  FALSE, insufficient
        mean of r reps:   sigma_mean = sigma / sqrt(r)
        r = 3:            sigma_mean = 0.035  ->  0.16 > 2 x 0.07 = 0.14  ->  TRUE

Why 2x and not 1x: the separation has to exceed the combined spread of BOTH
distributions, not just one of them. Two populations whose means differ by
exactly one sigma overlap heavily; at 2 x (sigma_a + sigma_b) they are cleanly
apart at the scale this project can actually measure. It is a deliberately
conservative rule of thumb, not a t-test, and it is stated that way rather than
dressed up as one: with r=3 the sample sigma would itself be so poorly
determined that a formal test would carry false precision. The sigmas fed in
come from the flakiness window (n=20) rather than from the three reps, which is
what makes the estimate stable enough to act on.

Where sigma comes from: `quality/flakiness.py`, measured on UNCHANGED code over
a 20-run window. Bisection therefore cannot run against an uncharacterised
experiment - there is no sigma, so there is no way to know how many reps are
enough, and running "some" reps and hoping is the failure this module exists to
prevent.
"""

import math
from dataclasses import dataclass

# A candidate classification. INCONCLUSIVE is not an error state and not a retry
# signal: it is the honest answer when the evidence cannot place a commit on one
# side of the regression, and it terminates the search.
GOOD = "good"
BAD = "bad"
INCONCLUSIVE = "inconclusive"

# Separation multiplier. Raising it demands more reps; lowering it admits
# misclassifications. It is NOT in constants.py deliberately: it does not enter
# the epoch material (weights, gating experiment set, SLO version, scorer
# version) and changing it cannot alter a stored score. It changes how cautious
# a diagnostic tool is, which is a different kind of decision from one that
# changes what a score means.
SEPARATION_MULTIPLIER = 2.0

# Repetitions are bounded. If the arithmetic asks for more than this, the honest
# answer is that the regression is too small to bisect at this experiment's
# variance - fix the variance first. An unbounded reps count turns a 90-minute
# job into an overnight one without anyone choosing that.
MAX_REPS = 9


@dataclass(frozen=True)
class CandidateClass:
    """Where a candidate landed, and the arithmetic that put it there."""
    verdict: str                      # good | bad | inconclusive
    mean: float
    sigma_mean: float
    reps: int
    distance_to_good: float
    distance_to_bad: float
    band_good: float                  # 2 x (sigma_mean_candidate + sigma_mean_good)
    band_bad: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "mean": self.mean,
            "sigma_mean": self.sigma_mean, "reps": self.reps,
            "distance_to_good": self.distance_to_good,
            "distance_to_bad": self.distance_to_bad,
            "band_good": self.band_good, "band_bad": self.band_bad,
            "reason": self.reason,
        }


def sigma_of_mean(sigma: float, reps: int) -> float:
    """sigma_mean = sigma / sqrt(r). The standard error of the mean.

    This is the entire reason repetitions help: averaging r runs shrinks the
    spread by sqrt(r), so the flakiness band narrows while the regression being
    hunted stays exactly where it was.
    """
    if reps < 1:
        raise ValueError(f"reps must be >= 1, got {reps}")
    if sigma < 0:
        raise ValueError(f"sigma must be >= 0, got {sigma}")
    return sigma / math.sqrt(reps)


def required_separation(sigma_good: float, sigma_bad: float, reps: int = 1) -> float:
    """The score gap a bisection needs before it can classify anything.

    At reps=1 this is the raw 2 x (sigma_good + sigma_bad) from the plan.
    """
    return SEPARATION_MULTIPLIER * (sigma_of_mean(sigma_good, reps)
                                    + sigma_of_mean(sigma_bad, reps))


def reps_needed(delta: float, sigma_good: float, sigma_bad: float) -> int | None:
    """Smallest r with  delta > 2 x (sigma_good + sigma_bad) / sqrt(r).

    Solving:  sqrt(r) > 2 x (sg + sb) / delta  ->  r > (2 x (sg + sb) / delta)^2

    Returns None when the answer exceeds MAX_REPS - meaning this regression is
    not separable from this experiment's noise at any repetition count worth
    paying for. That is a finding about the experiment, not a failure of the
    caller: quarantine it and fix the variance, then bisect.

    Worked example from the plan, pinned by a test of the same name:
        delta=0.16, sg=sb=0.06  ->  (2 x 0.12 / 0.16)^2 = 1.5^2 = 2.25  ->  r=3
    """
    if delta <= 0:
        return None
    combined = SEPARATION_MULTIPLIER * (sigma_good + sigma_bad)
    if combined == 0:
        return 1                      # a noiseless experiment needs one run
    exact = (combined / delta) ** 2
    # Strict inequality: at exactly r = exact the separation EQUALS the band and
    # the candidate would sit on the boundary. floor + 1 steps past it in both
    # the integral and the non-integral case.
    r = math.floor(exact) + 1
    return r if r <= MAX_REPS else None


def anchors_separable(score_good: float, score_bad: float,
                      sigma_good: float, sigma_bad: float,
                      reps: int) -> tuple[bool, str]:
    """Pre-flight: are the two ENDPOINTS distinguishable at this rep count?

    If they are not, no candidate between them can be classified either, and the
    search is a 90-minute coin flip. Refusing here - before any cluster time is
    spent - is the cheapest place to say so.
    """
    delta = abs(score_good - score_bad)
    band = required_separation(sigma_good, sigma_bad, reps)
    if delta > band:
        return True, (f"delta={delta:.4f} > band={band:.4f} at reps={reps} "
                      f"(sigma_mean good={sigma_of_mean(sigma_good, reps):.4f}, "
                      f"bad={sigma_of_mean(sigma_bad, reps):.4f})")
    want = reps_needed(delta, sigma_good, sigma_bad)
    suffix = (f"; {want} repetitions would separate them"
              if want is not None
              else f"; no repetition count <= {MAX_REPS} separates them - the "
                   f"regression is inside this experiment's noise")
    return False, (f"delta={delta:.4f} <= band={band:.4f} at reps={reps}{suffix}")


def classify_candidate(mean: float, reps: int,
                       score_good: float, sigma_good: float,
                       score_bad: float, sigma_bad: float) -> CandidateClass:
    """Place one candidate on the good side, the bad side, or neither.

    THE BAND HERE IS THE SEPARATION RULE, NOT A SECOND RULE ON TOP OF IT. The
    plan's criterion

        |score_good - score_bad|  >  2 x (sigma_mean_good + sigma_mean_bad)

    is algebraically identical to "the two intervals score +/- 2 x sigma_mean do
    not overlap". So the acceptance band around each anchor is exactly
    `2 x sigma_mean` for that anchor, and passing the pre-flight separation
    check is precisely what guarantees the two bands are disjoint.

    Stacking a wider band on top - say `2 x (sigma_candidate + sigma_anchor)` -
    looks more conservative and is in fact broken: at the boundary rep count the
    plan calls sufficient, the two widened bands overlap across the whole
    interval, every candidate lands inside both, and the search abandons every
    time. A rule that never returns an answer is not a cautious rule.

    A candidate is `good` when its mean is consistent with having been drawn
    from the good distribution and not the bad one, and `bad` in the mirror
    case. Every other outcome is `inconclusive`:

      - inside BOTH bands: only reachable if the anchors were not separable,
        which pre-flight rejects. Kept as a defended case rather than an
        assertion, because a caller may pass a rep count of its own.
      - inside NEITHER: the candidate sits in the gap between the bands, which
        is what a PARTIAL regression looks like - two commits each moving the
        score a little, so neither half is clean. Guessing here is precisely the
        misclassification that cannot be recovered from, because the other half
        is never revisited.

    Note the asymmetry with `git bisect`, which assumes a single boolean
    transition. A score is continuous, so "neither half" is a real state, and
    reporting it beats inventing a transition that does not exist.
    """
    band_good = SEPARATION_MULTIPLIER * sigma_of_mean(sigma_good, reps)
    band_bad = SEPARATION_MULTIPLIER * sigma_of_mean(sigma_bad, reps)
    sm_cand = max(band_good, band_bad) / SEPARATION_MULTIPLIER
    d_good = abs(mean - score_good)
    d_bad = abs(mean - score_bad)

    matches_good = d_good <= band_good
    matches_bad = d_bad <= band_bad

    if matches_good and not matches_bad:
        verdict, reason = GOOD, (
            f"mean={mean:.4f} is within {band_good:.4f} of good ({score_good:.4f}) "
            f"and {d_bad:.4f} from bad - clean of the regression")
    elif matches_bad and not matches_good:
        verdict, reason = BAD, (
            f"mean={mean:.4f} is within {band_bad:.4f} of bad ({score_bad:.4f}) "
            f"and {d_good:.4f} from good - carries the regression")
    elif matches_good and matches_bad:
        verdict, reason = INCONCLUSIVE, (
            f"mean={mean:.4f} is inside BOTH bands (good +/-{band_good:.4f}, "
            f"bad +/-{band_bad:.4f}) - the anchors are not separated at "
            f"reps={reps}, so this candidate cannot discriminate")
    else:
        verdict, reason = INCONCLUSIVE, (
            f"mean={mean:.4f} falls in the gap between the bands: {d_good:.4f} "
            f"from good (band {band_good:.4f}) and {d_bad:.4f} from bad "
            f"(band {band_bad:.4f}) - it straddles the flakiness band, which is "
            f"what a partial regression spread across several commits looks like")

    return CandidateClass(verdict=verdict, mean=mean, sigma_mean=sm_cand, reps=reps,
                          distance_to_good=d_good, distance_to_bad=d_bad,
                          band_good=band_good, band_bad=band_bad, reason=reason)
