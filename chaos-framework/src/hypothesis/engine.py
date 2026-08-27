"""The hypothesis engine: evaluates a steady-state hypothesis against a sampled
window and returns per-invariant outcomes — never one blob.

Rules that are load-bearing (hypothesis-engine skill):
- The validity gate runs FIRST; an unmeasurable window is INVALID before any
  invariant is looked at.
- A missing or empty series is NEVER a passing series — outcome `invalid`.
- Hold-throughout (`tolerance_s`) and recovery (`recover_within_s`) are distinct
  kinds: one describes degradation budget, the other recovery. Rev 1 conflated
  them, which is why a service that never degraded and one that degraded and
  recovered scored identically.

Recovery semantics: the deadline counts from the FIRST BREACH, not from engine
apply time — Litmus engine bootstrap (image pull, helper scheduling) sits between
apply and the actual fault, and a verdict must speak about the system, not about
the injector's startup latency.
"""

from dataclasses import dataclass, field

from ..measurement.sampler import SampleSet
from ..measurement.validity import LoadFacts


@dataclass
class Invariant:
    name: str
    source: str                    # k6 | prometheus
    metric: str                    # key into the sampler's per-tick dicts
    comparator: str                # >= <= > <
    threshold: float
    tolerance_s: float | None = None
    recover_within_s: float | None = None

    def satisfied(self, value: float) -> bool:
        match self.comparator:
            case ">=": return value >= self.threshold
            case "<=": return value <= self.threshold
            case ">":  return value > self.threshold
            case "<":  return value < self.threshold
        raise ValueError(f"unknown comparator {self.comparator!r}")

    def worse(self, a: float, b: float) -> float:
        """The value further from satisfying the comparator."""
        return min(a, b) if self.comparator in (">=", ">") else max(a, b)


@dataclass
class InvariantOutcome:
    name: str
    outcome: str                   # held | falsified | invalid
    worst_value: float | None
    threshold: float
    breached_for_s: float
    evidence: dict = field(default_factory=dict)


@dataclass
class HypothesisVerdict:
    verdict: str                   # held | falsified | invalid
    outcomes: list[InvariantOutcome]
    reason: str | None = None


def evaluate(invariants: list[Invariant], samples: SampleSet,
             load_facts: LoadFacts, min_rps_floor: float) -> HypothesisVerdict:
    # The validity gate first: no traffic means no evidence, and no evidence
    # must never score as success.
    if not load_facts.is_valid(min_rps_floor):
        return HypothesisVerdict("invalid", [], reason=load_facts.invalidity_reason)

    outcomes: list[InvariantOutcome] = []
    for inv in invariants:
        series = _timed_series(samples, inv)
        if not series:
            # A missing series is NEVER a passing series. This is the single
            # most common way a home-grown chaos framework lies to you.
            outcomes.append(InvariantOutcome(
                inv.name, "invalid", None, inv.threshold, 0.0,
                {"reason": f"no samples for {inv.source}/{inv.metric}"}))
            continue
        outcomes.append(_check_recovery(inv, series) if inv.recover_within_s is not None
                        else _check_hold(inv, series))

    if any(o.outcome == "invalid" for o in outcomes):
        return HypothesisVerdict("invalid", outcomes,
                                 reason="one or more invariants could not be measured")
    if any(o.outcome == "falsified" for o in outcomes):
        falsified = [o.name for o in outcomes if o.outcome == "falsified"]
        return HypothesisVerdict("falsified", outcomes,
                                 reason="falsified: " + ", ".join(falsified))
    return HypothesisVerdict("held", outcomes)


def _timed_series(samples: SampleSet, inv: Invariant) -> list[tuple[float, float]]:
    """(timestamp, value) pairs for this invariant's source/metric, gaps skipped."""
    out = []
    for s in samples.samples:
        values = s.client if inv.source == "k6" else s.server
        v = values.get(inv.metric)
        if v is not None:
            out.append((s.sampled_at, v))
    return out


def _check_hold(inv: Invariant, series: list[tuple[float, float]]) -> InvariantOutcome:
    """Hold-throughout: may be briefly breached (<= tolerance_s consecutively),
    then must return. The metric is the LONGEST consecutive breach run."""
    worst = None
    longest_breach = 0.0
    run_start: float | None = None
    breaches = 0

    for ts, value in series:
        worst = value if worst is None else inv.worse(worst, value)
        if not inv.satisfied(value):
            breaches += 1
            if run_start is None:
                run_start = ts
            longest_breach = max(longest_breach, ts - run_start)
        else:
            run_start = None

    # An unhealed breach extends to the window's end.
    if run_start is not None and series:
        longest_breach = max(longest_breach, series[-1][0] - run_start)

    tolerance = inv.tolerance_s or 0.0
    outcome = "held" if longest_breach <= tolerance else "falsified"
    return InvariantOutcome(inv.name, outcome, worst, inv.threshold, round(longest_breach, 2),
                            {"kind": "hold_throughout", "tolerance_s": tolerance,
                             "breach_samples": breaches, "samples": len(series)})


def _check_recovery(inv: Invariant, series: list[tuple[float, float]]) -> InvariantOutcome:
    """Recovery: expected to breach; must return to satisfying the comparator
    within recover_within_s of the FIRST breach, and stay satisfied through the
    window's end. Never breaching is a hold — the fault didn't dent this signal."""
    worst = None
    first_breach: float | None = None
    recovered_at: float | None = None

    for ts, value in series:
        worst = value if worst is None else inv.worse(worst, value)
        if not inv.satisfied(value):
            if first_breach is None:
                first_breach = ts
            recovered_at = None                 # any later breach voids a recovery
        elif first_breach is not None and recovered_at is None:
            recovered_at = ts

    deadline = inv.recover_within_s or 0.0
    if first_breach is None:
        return InvariantOutcome(inv.name, "held", worst, inv.threshold, 0.0,
                                {"kind": "recovery", "note": "never breached"})
    if recovered_at is None:
        breached_for = series[-1][0] - first_breach
        return InvariantOutcome(inv.name, "falsified", worst, inv.threshold,
                                round(breached_for, 2),
                                {"kind": "recovery", "note": "never recovered",
                                 "deadline_s": deadline})
    breached_for = recovered_at - first_breach
    outcome = "held" if breached_for <= deadline else "falsified"
    return InvariantOutcome(inv.name, outcome, worst, inv.threshold, round(breached_for, 2),
                            {"kind": "recovery", "deadline_s": deadline,
                             "recovered": True})


def parse_abort_conditions(raw: list[dict]) -> list[Invariant]:
    """Abort conditions reuse the Invariant shape but carry NEITHER tolerance_s
    nor recover_within_s: an abort is instantaneous by definition. The watchdog
    and the Litmus promProbes both read this one declaration, so the in-band and
    out-of-band paths cannot drift apart."""
    out = []
    for r in raw:
        out.append(Invariant(
            name=r["name"], source=r["source"], metric=r["metric"],
            comparator=r["comparator"], threshold=float(r["threshold"])))
    return out


def parse_invariants(raw: list[dict]) -> list[Invariant]:
    out = []
    for r in raw:
        if (r.get("tolerance_s") is None) == (r.get("recover_within_s") is None):
            raise ValueError(
                f"invariant {r.get('name')!r} must declare EXACTLY ONE of "
                "tolerance_s (hold-throughout) or recover_within_s (recovery) — "
                "the two kinds must never be conflated")
        out.append(Invariant(
            name=r["name"], source=r["source"], metric=r["metric"],
            comparator=r["comparator"], threshold=float(r["threshold"]),
            tolerance_s=r.get("tolerance_s"),
            recover_within_s=r.get("recover_within_s")))
    return out
