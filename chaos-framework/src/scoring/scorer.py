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
SLO_VERSION = 1


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
        "weights": WEIGHTS,
        "experiment_set": sorted(gating_experiments),   # GATING experiments only
        "slo_version": SLO_VERSION,
        "scorer_version": SCORER_VERSION,
    }


def epoch_sha256(gating_experiments: list[str]) -> str:
    material = json.dumps(epoch_material(gating_experiments),
                          sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode()).hexdigest()
