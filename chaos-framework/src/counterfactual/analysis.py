"""Counterfactual analysis (§17.2) — what a resilience pattern is actually worth.

Every chaos tool proves a system survived. None prove WHAT MADE IT SURVIVE.
Running the same fault with a pattern enabled and disabled converts "we have a
circuit breaker" into "this breaker prevents ~N failed requests per incident of
this class".

STATISTICAL HONESTY IS THE WHOLE FEATURE, and it is the part the original
sketch got wrong: it showed a single with/without pair producing a currency
figure. One run per arm cannot support that. Pod scheduling, endpoint
propagation and JIT warmup all vary between runs, so a single-pair delta is an
anecdote wearing a number's clothes.

So: n >= 5 per arm, INTERLEAVED, and a distribution rather than a point. And
when the interquartile ranges overlap, the verdict is `inconclusive` — NOT
"pattern_effective with a smaller number". Reporting a median delta from
overlapping distributions is how dashboards become fiction.

That restraint is the interview signal. Anyone can produce a big number; being
able to say "my n was too small to claim that" is what a senior engineer
listens for.
"""

import statistics
from dataclasses import dataclass, field

MIN_REPETITIONS = 5

# Verdicts, and the distinction that matters most is between the last two:
#   pattern_effective     the arms separate, and the pattern is the better one
#   pattern_harmful       the arms separate, and the pattern is the WORSE one.
#                         Rare, real, and worth its own verdict rather than
#                         being folded into "effective" with a sign flip that
#                         a reader might miss.
#   no_measurable_effect  the arms are statistically indistinguishable AND the
#                         medians are effectively equal
#   inconclusive          the IQRs overlap. We cannot tell. Say so.
PATTERN_EFFECTIVE = "pattern_effective"
PATTERN_HARMFUL = "pattern_harmful"
NO_MEASURABLE_EFFECT = "no_measurable_effect"
INCONCLUSIVE = "inconclusive"
INSUFFICIENT_DATA = "insufficient_data"


def quartiles(values: list[float]) -> tuple[float, float, float]:
    """(q1, median, q3) using the inclusive method.

    Inclusive rather than exclusive because n is small by design (5-10 per arm)
    and the exclusive method discards the extremes, which at n=5 means throwing
    away 40% of the evidence. The choice is stated because it changes the IQR
    and therefore changes verdicts.
    """
    if not values:
        raise ValueError("no values")
    if len(values) == 1:
        v = float(values[0])
        return v, v, v
    ordered = sorted(float(v) for v in values)
    q1, _median, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    return q1, statistics.median(ordered), q3


def iqr_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Do two interquartile ranges overlap?

    Touching counts as overlapping. If one arm's Q3 exactly equals the other's
    Q1 the distributions are not separated by the evidence, and rounding a
    boundary case toward a confident answer is precisely the failure this check
    exists to prevent.
    """
    a_lo, a_hi = min(a), max(a)
    b_lo, b_hi = min(b), max(b)
    return a_lo <= b_hi and b_lo <= a_hi


@dataclass
class Arm:
    label: str                       # with_pattern | without_pattern
    values: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def median(self) -> float:
        return quartiles(self.values)[1]

    @property
    def iqr(self) -> tuple[float, float]:
        q1, _m, q3 = quartiles(self.values)
        return (q1, q3)


@dataclass
class CounterfactualResult:
    pattern: str
    metric: str
    n: int
    with_median: float | None
    with_iqr: tuple[float, float] | None
    without_median: float | None
    without_iqr: tuple[float, float] | None
    overlap: bool
    verdict: str
    reason: str = ""
    lower_is_better: bool = True

    @property
    def delta_median(self) -> float | None:
        """The headline number — and it is NONE whenever the verdict does not
        support one.

        This is deliberately a property rather than a stored field, so there is
        no way to persist a delta alongside an `inconclusive` verdict and have
        a renderer find it later. The dashboard rule "never show a
        counterfactual delta when the IQRs overlap" is enforced here, at the
        source, rather than trusted to every consumer.
        """
        if self.verdict in (INCONCLUSIVE, INSUFFICIENT_DATA):
            return None
        if self.with_median is None or self.without_median is None:
            return None
        return self.without_median - self.with_median

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern, "metric": self.metric, "n": self.n,
            "with_median": self.with_median, "with_iqr": list(self.with_iqr or ()),
            "without_median": self.without_median,
            "without_iqr": list(self.without_iqr or ()),
            "overlap": self.overlap, "verdict": self.verdict,
            "reason": self.reason, "delta_median": self.delta_median,
            "lower_is_better": self.lower_is_better,
        }


def analyse(pattern: str, metric: str, with_values: list[float],
            without_values: list[float], *, lower_is_better: bool = True,
            min_repetitions: int = MIN_REPETITIONS) -> CounterfactualResult:
    """Compare two arms and refuse to overclaim."""
    n = min(len(with_values), len(without_values))

    if n < min_repetitions:
        return CounterfactualResult(
            pattern, metric, n, None, None, None, None, overlap=True,
            verdict=INSUFFICIENT_DATA,
            reason=(f"{len(with_values)} with / {len(without_values)} without — "
                    f"need >= {min_repetitions} per arm. A single pair of chaos "
                    "runs is noise, not evidence."),
            lower_is_better=lower_is_better)

    a = Arm("with_pattern", with_values)
    b = Arm("without_pattern", without_values)
    overlap = iqr_overlap(a.iqr, b.iqr)

    if overlap:
        return CounterfactualResult(
            pattern, metric, n, a.median, a.iqr, b.median, b.iqr, overlap=True,
            verdict=INCONCLUSIVE,
            reason=(f"IQRs overlap (with {a.iqr[0]:.4g}-{a.iqr[1]:.4g}, without "
                    f"{b.iqr[0]:.4g}-{b.iqr[1]:.4g}) — the arms are not separated "
                    f"by this evidence. No delta is reported, because a median "
                    f"delta drawn from overlapping distributions is fiction with "
                    f"a decimal point."),
            lower_is_better=lower_is_better)

    # Separated. Which arm is better depends on the metric's direction — failed
    # requests and latency are lower-is-better; availability is not.
    with_is_better = (a.median < b.median) if lower_is_better else (a.median > b.median)

    if a.median == b.median:
        verdict, reason = NO_MEASURABLE_EFFECT, "medians are identical"
    elif with_is_better:
        verdict = PATTERN_EFFECTIVE
        reason = (f"IQRs are disjoint and the pattern arm is better "
                  f"({a.median:.4g} vs {b.median:.4g})")
    else:
        verdict = PATTERN_HARMFUL
        reason = (f"IQRs are disjoint and the pattern arm is WORSE "
                  f"({a.median:.4g} vs {b.median:.4g}). This is a real result, "
                  f"not a sign error — the pattern costs more than it saves on "
                  f"this metric.")

    return CounterfactualResult(
        pattern, metric, n, a.median, a.iqr, b.median, b.iqr, overlap=False,
        verdict=verdict, reason=reason, lower_is_better=lower_is_better)


def interleave(n: int) -> list[str]:
    """The run order: with, without, with, without...

    Interleaved rather than all-with-then-all-without so DRIFT IN CLUSTER
    CONDITIONS AFFECTS BOTH ARMS EQUALLY. A block design confounds the pattern
    with time: if the node gets busier over the hour, the second block looks
    worse for a reason that has nothing to do with the pattern, and the
    resulting number would be confidently wrong.
    """
    order = []
    for _ in range(n):
        order.extend(["with_pattern", "without_pattern"])
    return order
