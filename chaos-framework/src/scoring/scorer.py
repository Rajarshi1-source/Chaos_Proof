"""The composite resilience score — corrected (defect D3).

Rev 1 silently scored non-applicable checks as 0.5 partial credit. That is
arbitrary: 0.5 is unrelated to how the system actually behaved, and it makes
scores non-comparable across experiment types, because an experiment with no
resilience pattern to validate has a fixed 0.125 of its score determined by a
constant rather than by evidence.

Rev 2: a check declares `applicable`. Non-applicable checks are EXCLUDED and the
remaining weights RENORMALISED over the applicable set — never partial-credited.
`weights_denominator` is persisted so any stored score can be re-derived rather
than merely trusted.

INVALID poisons the whole experiment: no score at all, not a zero. A zero would
drag the aggregate as if the system failed, when in fact nothing was measured.
"""

import hashlib
import json
from dataclasses import dataclass, field

from ..constants import FAIL_THRESHOLD, PASS_THRESHOLD, WEIGHTS

SCORER_VERSION = "1.0.0"
# Bumped 1 -> 2 on 30 Aug 2026. The SCORER did not change; the definition of two
# SLIs it consumes did (defects D-A and D-B in src/queries.py — client
# availability was an unweighted average over per-status label series, and
# client p99 was a run-cumulative gauge). Scores computed before and after are
# not comparable, so this opens a new epoch rather than quietly rewriting
# history. The old scores stay exactly as recorded; the trend line breaks at the
# boundary and says why.
SLO_VERSION = 2


@dataclass
class Check:
    check_type: str                 # must be a key in WEIGHTS
    check_name: str
    applicable: bool
    outcome: str                    # pass | fail | partial | invalid
    score: float | None             # 0.0..1.0; None when not applicable or invalid
    expected_value: str | None = None
    actual_value: str | None = None
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ScoreResult:
    score: float | None
    status: str                     # pass | partial | fail | invalid
    weights_denominator: float | None = None
    excluded: list[str] = field(default_factory=list)
    reason: str | None = None


def classify(score: float) -> str:
    if score >= PASS_THRESHOLD:
        return "pass"
    if score < FAIL_THRESHOLD:
        return "fail"
    return "partial"


def checks_for_verdict(checks: list[Check], verdict: str) -> list[Check]:
    """Apply the verdict's consequence to the check list.

    An INVALID or ABORTED run is not scoreable, and that has to be true of the
    CHECKS too, not only of the final number: on an invalid run the four
    validators were reading the same broken measurement plane the hypothesis
    was, and on an aborted run the fault was cut short before they saw what
    they are grading. Leaving them as passes and merely suppressing the total
    would leave four green rows in the evidence bundle describing a run that
    measured nothing.

    Lives here rather than in the runner because the replay eval must apply
    exactly the same coupling. Two implementations of this rule would drift,
    and the corpus would then certify behaviour the runner no longer has.
    """
    if verdict not in ("invalid", "aborted"):
        return checks
    out = []
    for c in checks:
        poisoned = Check(c.check_type, c.check_name, c.applicable, "invalid", None,
                         c.expected_value, c.actual_value, c.message, dict(c.details))
        out.append(poisoned)
    return out


def calculate(checks: list[Check]) -> ScoreResult:
    # INVALID poisons everything — not a zero, no score at all.
    if any(c.outcome == "invalid" for c in checks):
        invalid = [c.check_type for c in checks if c.outcome == "invalid"]
        return ScoreResult(None, "invalid",
                           reason=f"not scoreable — invalid checks: {', '.join(invalid)}")

    applicable = [c for c in checks if c.applicable]
    if not applicable:
        return ScoreResult(None, "invalid", reason="no applicable checks")

    unknown = [c.check_type for c in applicable if c.check_type not in WEIGHTS]
    if unknown:
        raise ValueError(f"unweighted check types {unknown} — adding a weight opens "
                         "a new scoring epoch (mlops-quality skill)")

    denominator = sum(WEIGHTS[c.check_type] for c in applicable)
    numerator = sum(WEIGHTS[c.check_type] * (c.score or 0.0) for c in applicable)
    score = numerator / denominator

    return ScoreResult(
        score=round(score, 4),
        status=classify(score),
        weights_denominator=round(denominator, 3),
        excluded=[c.check_type for c in checks if not c.applicable],
    )


def epoch_material(gating_experiments: list[str]) -> dict:
    """Everything that changes the MEANING of a score. Any change here must open
    a new epoch, or the trend chart silently compares incomparable numbers."""
    return {
        # A COPY, not the live dict. An Epoch is meant to be an immutable record
        # of the weights a score was computed under; handing out a reference to
        # the module-level WEIGHTS makes every Epoch object share one mutable
        # dict, so two epochs taken either side of a weight change compare equal
        # on weights and `epochs.diff` can never report the very change the
        # mechanism exists to surface.
        "weights": dict(WEIGHTS),
        "experiment_set": sorted(gating_experiments),   # GATING experiments only
        "slo_version": SLO_VERSION,
        "scorer_version": SCORER_VERSION,
    }


def epoch_sha256(gating_experiments: list[str]) -> str:
    material = json.dumps(epoch_material(gating_experiments),
                          sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()
