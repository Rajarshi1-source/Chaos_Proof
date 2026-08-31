"""The binary search itself. Pure: no cluster, no git, no database.

`bisect()` takes a callable that evaluates one candidate and returns its
per-repetition scores. Everything that touches the world - checking out a
commit, deploying it, running the experiment - lives behind that callable, which
is what makes the arithmetic in `sigma.py` testable on a laptop with no cluster.

Three outcomes, and all three are results:

    found      - a single commit, with the trail that reached it.
    abandoned  - a candidate landed inside the flakiness band. The search stops
                 and says which commit and why. It does NOT guess a half.
    refused    - pre-flight said no: the anchors are not separable at this rep
                 count, the cost does not fit the nightly window, or the range
                 contains an epoch-defining change.

`abandoned` is the one that matters. One misclassification sends a binary search
down the wrong half permanently - the correct half is never visited again, so no
later evidence can contradict the mistake, and the search still terminates
confidently on a commit. A tool that reports the wrong commit with the same
certainty as the right one is worse than no tool. So an ambiguous candidate ends
the search.
"""

from dataclasses import dataclass, field

from .cost import (
    CostEstimate,
    PLAN_MINUTES_PER_RUN,
    WindowCheck,
    estimate,
    in_nightly_window,
)
from .sigma import (
    GOOD,
    INCONCLUSIVE,
    CandidateClass,
    anchors_separable,
    classify_candidate,
    reps_needed,
)

FOUND = "found"
ABANDONED = "abandoned"
REFUSED = "refused"


@dataclass
class CandidateEvaluation:
    """One visited candidate, with everything needed to re-derive its class."""
    index: int
    sha: str
    scores: list[float | None]
    scoreable: int
    mean: float | None
    classification: CandidateClass | None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index, "sha": self.sha, "scores": self.scores,
            "scoreable": self.scoreable, "mean": self.mean, "note": self.note,
            "classification": (self.classification.to_dict()
                               if self.classification else None),
        }


@dataclass
class BisectResult:
    outcome: str                                  # found | abandoned | refused
    experiment: str
    reason: str
    commit: str | None = None
    good: str | None = None
    bad: str | None = None
    reps: int = 0
    cost: CostEstimate | None = None
    trail: list[CandidateEvaluation] = field(default_factory=list)
    epoch: str | None = None
    overrode_window: bool = False
    window: WindowCheck | None = None

    # The four numbers the search started from, plus what the arithmetic asked
    # for. Carried on the result so the stored row can be re-derived rather than
    # trusted: `reps_derived != reps` is exactly the case where a human
    # overrode the sigma arithmetic, and that is what someone will look for.
    anchor_score_good: float = 0.0
    anchor_score_bad: float = 0.0
    anchor_sigma_good: float = 0.0
    anchor_sigma_bad: float = 0.0
    reps_derived: int | None = None

    @property
    def candidates_evaluated(self) -> int:
        return len(self.trail)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "experiment": self.experiment,
            "reason": self.reason, "commit": self.commit,
            "good": self.good, "bad": self.bad, "reps": self.reps,
            "epoch": self.epoch, "overrodeWindow": self.overrode_window,
            "candidatesEvaluated": self.candidates_evaluated,
            "cost": self.cost.to_dict() if self.cost else None,
            "trail": [c.to_dict() for c in self.trail],
        }

    def render(self) -> str:
        """Stable, greppable output. Like `chaosctl replay`, this doubles as a
        golden-file test, so the shape is part of the contract."""
        head = {FOUND: "BISECT_FOUND", ABANDONED: "BISECT_ABANDONED",
                REFUSED: "BISECT_REFUSED"}[self.outcome]
        lines = [f"{head}  {self.experiment}", f"  reason:  {self.reason}"]
        if self.commit:
            lines.append(f"  commit:  {self.commit}")
        if self.good and self.bad:
            lines.append(f"  range:   {self.good[:12]}..{self.bad[:12]}  "
                         f"reps={self.reps}")
        if self.cost:
            lines.append(f"  cost:    {self.cost.button_label}  "
                         f"({self.cost.reason})")
        if self.window and not self.window.inside:
            lines.append(f"  window:  {self.window.reason}"
                         + ("  [OVERRIDDEN]" if self.overrode_window else ""))
        if self.epoch:
            lines.append(f"  epoch:   {self.epoch[:12]}")
        for c in self.trail:
            verdict = c.classification.verdict if c.classification else "unscoreable"
            mean = "—" if c.mean is None else f"{c.mean:.4f}"
            lines.append(f"  [{c.index:>4}] {c.sha[:12]}  mean={mean}  "
                         f"n={c.scoreable}/{self.reps}  -> {verdict}")
            detail = (c.classification.reason if c.classification else c.note)
            if detail:
                lines.append(f"         {detail}")
        return "\n".join(lines)

    # ---- constructors, so every exit path is named ------------------------- #

    @classmethod
    def found(cls, **kw) -> "BisectResult":
        return cls(outcome=FOUND, **kw)

    @classmethod
    def abandoned(cls, **kw) -> "BisectResult":
        return cls(outcome=ABANDONED, **kw)

    @classmethod
    def refused(cls, **kw) -> "BisectResult":
        return cls(outcome=REFUSED, **kw)


def bisect(*, experiment: str, shas: list[str],
           score_good: float, sigma_good: float,
           score_bad: float, sigma_bad: float,
           evaluate,
           reps: int | None = None,
           minutes_per_run: float = PLAN_MINUTES_PER_RUN,
           epoch: str | None = None,
           epoch_conflicts: list[str] | None = None,
           now=None, allow_outside_window: bool = False) -> BisectResult:
    """Binary-search `shas` (oldest first, good at index 0, bad at index -1).

    `evaluate(sha, reps) -> list[float | None]` runs the SAME experiment at that
    commit `reps` times and returns one score per repetition. `None` is the
    correct return for a repetition that produced no score - INVALID, ABORTED,
    SKIPPED or DENIED. It is never coerced to zero: a zero would drag the mean
    toward the bad anchor and misclassify the candidate on the strength of a run
    that measured nothing.

    `reps=None` derives the repetition count from the observed regression and
    the two sigmas, which is the intended way to call this.
    """
    epoch_conflicts = list(epoch_conflicts or [])
    delta = abs(score_good - score_bad)
    anchors = dict(anchor_score_good=score_good, anchor_score_bad=score_bad,
                   anchor_sigma_good=sigma_good, anchor_sigma_bad=sigma_bad)

    # ---- pre-flight 1: is the score difference even about resilience? ------ #
    if epoch_conflicts:
        return BisectResult.refused(
            experiment=experiment, good=shas[0], bad=shas[-1], epoch=epoch,
            **anchors,
            reason=("the range changes what a score MEANS, so its endpoints were "
                    "never comparable: " + ", ".join(epoch_conflicts[:6])
                    + (f" (+{len(epoch_conflicts) - 6} more)"
                       if len(epoch_conflicts) > 6 else "")
                    + ". Bisecting would converge on the commit that edited the "
                      "scorer. Retro-score both anchors under one epoch first."))

    # ---- pre-flight 2: how many repetitions does the arithmetic demand? ---- #
    derived = reps_needed(delta, sigma_good, sigma_bad)
    anchors["reps_derived"] = derived
    if reps is None:
        if derived is None:
            return BisectResult.refused(
                experiment=experiment, good=shas[0], bad=shas[-1], epoch=epoch,
                **anchors,
                reason=(f"a regression of {delta:.4f} is not separable from this "
                        f"experiment's noise (sigma_good={sigma_good:.4f}, "
                        f"sigma_bad={sigma_bad:.4f}) at any affordable repetition "
                        f"count. Reduce the variance before bisecting - that is a "
                        f"finding about the experiment, not about the code."))
        reps = derived

    separable, sep_reason = anchors_separable(score_good, score_bad,
                                              sigma_good, sigma_bad, reps)
    if not separable:
        return BisectResult.refused(
            experiment=experiment, good=shas[0], bad=shas[-1], reps=reps,
            epoch=epoch, **anchors,
            reason=(f"the anchors themselves are not separable, so no candidate "
                    f"between them could be classified either: {sep_reason}"))

    # ---- pre-flight 3: does the cost fit the night? ------------------------ #
    width = len(shas) - 1
    cost = estimate(width, reps, minutes_per_run)
    if not cost.affordable:
        return BisectResult.refused(
            experiment=experiment, good=shas[0], bad=shas[-1], reps=reps,
            cost=cost, epoch=epoch, **anchors, reason=cost.reason)

    window = in_nightly_window(now)
    if not window.inside and not allow_outside_window:
        return BisectResult.refused(
            experiment=experiment, good=shas[0], bad=shas[-1], reps=reps,
            cost=cost, epoch=epoch, window=window, **anchors,
            reason=window.reason)

    common = dict(experiment=experiment, good=shas[0], bad=shas[-1], reps=reps,
                  cost=cost, epoch=epoch, window=window,
                  overrode_window=not window.inside, **anchors)

    # ---- the search -------------------------------------------------------- #
    lo, hi = 0, len(shas) - 1
    trail: list[CandidateEvaluation] = []

    while hi - lo > 1:
        mid = (lo + hi) // 2
        sha = shas[mid]
        raw = list(evaluate(sha, reps))
        scoreable = [s for s in raw if s is not None]

        if not scoreable:
            ev = CandidateEvaluation(
                index=mid, sha=sha, scores=raw, scoreable=0, mean=None,
                classification=None,
                note=(f"all {len(raw)} repetitions were unscoreable (INVALID, "
                      f"ABORTED, SKIPPED or DENIED). No evidence is not a score "
                      f"of zero, so this candidate cannot be placed."))
            trail.append(ev)
            return BisectResult.abandoned(
                **common, commit=sha, trail=trail,
                reason=(f"candidate {sha[:12]} produced no scoreable repetition - "
                        f"the measurement plane failed here, and treating that as "
                        f"a bad score would send the search down a half chosen by "
                        f"a broken run."))

        mean = sum(scoreable) / len(scoreable)
        # The band is computed at the number of repetitions ACTUALLY measured,
        # not the number requested. If two of three came back INVALID,
        # sigma_mean is sigma/sqrt(1), not sigma/sqrt(3), the bands widen, and
        # the candidate is far more likely to be ambiguous - the correct
        # consequence of having measured less, not a problem to paper over.
        cls = classify_candidate(mean, len(scoreable),
                                 score_good, sigma_good, score_bad, sigma_bad)
        trail.append(CandidateEvaluation(
            index=mid, sha=sha, scores=raw, scoreable=len(scoreable),
            mean=mean, classification=cls,
            note=("" if len(scoreable) == reps else
                  f"{reps - len(scoreable)} of {reps} repetitions were "
                  f"unscoreable; the band was widened to sqrt({len(scoreable)})")))

        if cls.verdict == INCONCLUSIVE:
            return BisectResult.abandoned(
                **common, commit=sha, trail=trail,
                reason=(f"candidate {sha[:12]} straddles the flakiness band: "
                        f"{cls.reason}. Reporting the range is honest; picking a "
                        f"half here would be a coin flip the search can never "
                        f"revisit."))

        lo, hi = (mid, hi) if cls.verdict == GOOD else (lo, mid)

    return BisectResult.found(
        **common, commit=shas[hi], trail=trail,
        reason=(f"{shas[hi][:12]} is the first commit on the bad side; "
                f"{shas[lo][:12]} immediately before it classified good. "
                f"{len(trail)} candidate(s) at {reps} repetitions each."))
