"""The scorer is graded at --cov-fail-under=80 by its own CI step, and it is the
only component that gets its own coverage floor.

The reason is not that it is the most complex thing here — it is the least
complex. It is that its bugs are invisible. A wrong score looks exactly like a
right score: the dashboard renders it, Slack posts it, the trend line absorbs
it, and nobody downstream can tell. Every other component fails loudly.
"""

import pytest

from src.constants import FAIL_THRESHOLD, PASS_THRESHOLD, WEIGHTS
from src.scoring import scorer
from src.scoring.scorer import Check


def check(check_type: str, *, applicable: bool = True, outcome: str = "pass",
          score: float | None = 1.0) -> Check:
    return Check(check_type, f"{check_type} check", applicable, outcome, score)


# --------------------------------------------------------------------------- #
# The correction that defines Rev 2: exclude, never partial-credit.
# --------------------------------------------------------------------------- #

def test_all_applicable_and_passing_scores_one():
    result = scorer.calculate([check(t) for t in WEIGHTS])
    assert result.score == 1.0
    assert result.status == "pass"
    assert result.weights_denominator == pytest.approx(sum(WEIGHTS.values()))
    assert result.excluded == []


def test_non_applicable_check_is_excluded_and_weights_renormalise():
    """Disk fill is the canonical case: it exercises no resilience pattern.

    Rev 1 scored the missing check as 0.5 partial credit, which fixed 12.5% of
    the score by a constant rather than by evidence. Rev 2 removes the check
    from the denominator entirely — the score then describes only what was
    actually observed.
    """
    # The GATE 4 disk-fill profile exactly: SLO recovered, the expected alert
    # never fired, no resilience pattern to validate, recovery complete.
    checks = [
        check("slo_recovery", outcome="pass", score=1.0),
        check("alert_validation", outcome="fail", score=0.0),
        check("resilience_pattern", applicable=False, score=None),
        check("recovery_completeness", outcome="pass", score=1.0),
    ]
    result = scorer.calculate(checks)

    denominator = WEIGHTS["slo_recovery"] + WEIGHTS["alert_validation"] \
        + WEIGHTS["recovery_completeness"]
    expected = (WEIGHTS["slo_recovery"] + WEIGHTS["recovery_completeness"]) / denominator

    assert result.weights_denominator == pytest.approx(round(denominator, 3))
    assert result.score == pytest.approx(round(expected, 4))
    assert result.excluded == ["resilience_pattern"]
    # 0.6667, the GATE 4 figure — and specifically NOT 0.625, which is what the
    # half-credit formula produced from the same evidence.
    assert result.score == pytest.approx(0.6667, abs=1e-4)
    assert result.score != pytest.approx(0.625, abs=1e-4)


def test_excluded_check_never_contributes_score_even_if_one_is_present():
    """Defence against the obvious regression: someone sets `score` on a
    non-applicable check and the renormalisation silently starts counting it."""
    with_stray = scorer.calculate([
        check("slo_recovery"), check("alert_validation"),
        Check("resilience_pattern", "stray", False, "pass", 1.0),
        check("recovery_completeness", outcome="fail", score=0.0),
    ])
    without_stray = scorer.calculate([
        check("slo_recovery"), check("alert_validation"),
        Check("resilience_pattern", "clean", False, "pass", None),
        check("recovery_completeness", outcome="fail", score=0.0),
    ])
    assert with_stray.score == without_stray.score


# --------------------------------------------------------------------------- #
# INVALID: no score at all, never a zero.
# --------------------------------------------------------------------------- #

def test_invalid_check_produces_no_score_not_a_zero():
    """A zero would drag the aggregate as if the system had failed, when in fact
    nothing was measured. Those are different findings and must not average
    together."""
    result = scorer.calculate([
        check("slo_recovery"),
        check("alert_validation", outcome="invalid", score=None),
        check("resilience_pattern"),
        check("recovery_completeness"),
    ])
    assert result.score is None
    assert result.status == "invalid"
    assert result.score != 0.0
    assert "alert_validation" in result.reason


def test_invalid_poisons_the_run_even_when_everything_else_passed():
    result = scorer.calculate([
        check(t, outcome=("invalid" if t == "resilience_pattern" else "pass"),
              score=(None if t == "resilience_pattern" else 1.0))
        for t in WEIGHTS
    ])
    assert result.score is None
    assert result.status == "invalid"


def test_no_applicable_checks_is_invalid_not_a_perfect_score():
    """An empty applicable set has a zero denominator. Returning 1.0 (nothing
    failed) or 0.0 (nothing passed) would both be fabrications."""
    result = scorer.calculate([check(t, applicable=False, score=None) for t in WEIGHTS])
    assert result.score is None
    assert result.status == "invalid"
    assert "no applicable checks" in result.reason


# --------------------------------------------------------------------------- #
# Classification boundaries. These ARE the SLO, so they are asserted exactly.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,expected", [
    (1.0, "pass"),
    (PASS_THRESHOLD, "pass"),               # inclusive lower bound
    (PASS_THRESHOLD - 1e-9, "partial"),
    (FAIL_THRESHOLD, "partial"),            # inclusive: fail is strictly below
    (FAIL_THRESHOLD - 1e-9, "fail"),
    (0.0, "fail"),
])
def test_classify_boundaries(value, expected):
    assert scorer.classify(value) == expected


def test_partial_credit_flows_through_proportionally():
    result = scorer.calculate([
        check("slo_recovery", outcome="partial", score=0.5),
        check("alert_validation", outcome="partial", score=0.5),
        check("resilience_pattern", outcome="partial", score=0.5),
        check("recovery_completeness", outcome="partial", score=0.5),
    ])
    assert result.score == pytest.approx(0.5)
    assert result.status == "partial"


def test_score_of_none_on_an_applicable_passing_check_reads_as_zero():
    """Documents a real edge: `c.score or 0.0`. An applicable check that forgot
    to set a score contributes nothing rather than raising — which is the safe
    direction, because the alternative is inventing credit."""
    result = scorer.calculate([
        Check("slo_recovery", "no score set", True, "pass", None),
        check("alert_validation"), check("resilience_pattern"),
        check("recovery_completeness"),
    ])
    expected = 1.0 - WEIGHTS["slo_recovery"] / sum(WEIGHTS.values())
    assert result.score == pytest.approx(round(expected, 4))


# --------------------------------------------------------------------------- #
# Unweighted check types must raise, not silently vanish.
# --------------------------------------------------------------------------- #

def test_unknown_check_type_raises_and_says_it_opens_an_epoch():
    with pytest.raises(ValueError, match="new scoring epoch"):
        scorer.calculate([check("slo_recovery"), check("some_new_signal")])


def test_unknown_check_type_is_ignored_when_not_applicable():
    """A non-applicable check is excluded before the weight lookup, so adding a
    check type that never applies to any experiment cannot break scoring."""
    result = scorer.calculate([
        check("slo_recovery"), check("alert_validation"),
        check("resilience_pattern"), check("recovery_completeness"),
        check("future_signal", applicable=False, score=None),
    ])
    assert result.score == 1.0
    assert "future_signal" in result.excluded


# --------------------------------------------------------------------------- #
# Scoring epochs: the hash must move for everything that changes MEANING, and
# must not move for anything that does not.
# --------------------------------------------------------------------------- #

def test_epoch_material_contains_the_four_things_that_change_meaning():
    material = scorer.epoch_material(["a", "b"])
    assert set(material) == {"weights", "experiment_set", "slo_version", "scorer_version"}


def test_epoch_is_stable_under_gating_set_ordering():
    """Set membership is what matters; the order two experiments happen to be
    listed in is not a scoring change and must not open an epoch."""
    assert scorer.epoch_sha256(["b", "a"]) == scorer.epoch_sha256(["a", "b"])


def test_epoch_changes_when_the_gating_experiment_set_changes():
    """Adding a gating experiment changes what the composite score is an average
    OF. A trend line across that boundary compares different things."""
    assert scorer.epoch_sha256(["a"]) != scorer.epoch_sha256(["a", "b"])


def test_epoch_changes_when_a_weight_changes(monkeypatch):
    before = scorer.epoch_sha256(["a"])
    monkeypatch.setitem(scorer.WEIGHTS, "slo_recovery", 0.40)
    assert scorer.epoch_sha256(["a"]) != before


def test_epoch_changes_when_the_scorer_version_changes(monkeypatch):
    before = scorer.epoch_sha256(["a"])
    monkeypatch.setattr(scorer, "SCORER_VERSION", "9.9.9")
    assert scorer.epoch_sha256(["a"]) != before


def test_epoch_changes_when_the_slo_version_changes(monkeypatch):
    before = scorer.epoch_sha256(["a"])
    monkeypatch.setattr(scorer, "SLO_VERSION", 99)
    assert scorer.epoch_sha256(["a"]) != before


def test_epoch_is_a_sha256_hex_digest():
    sha = scorer.epoch_sha256(["a"])
    assert len(sha) == 64 and int(sha, 16) >= 0


def test_weights_sum_to_one():
    """Not required by the arithmetic — renormalisation handles any denominator —
    but a set that does not sum to 1.0 means a full-marks run scores below 1.0
    and every reported number is quietly off."""
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)
