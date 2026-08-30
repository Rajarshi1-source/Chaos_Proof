"""The MLOps wrapper: epochs, flakiness, bundles.

These are the rules that keep a resilience score comparable and a CI gate
trusted. All hermetic — no cluster, no database.
"""

import pytest

from src.constants import FLAKINESS_WINDOW, MAX_SCORE_STDDEV, MAX_VERDICT_FLIP_RATE
from src.quality import bundles, epochs
from src.quality import flakiness as F
from src.quality.epochs import EpochMismatch
from src.scoring import scorer
from src.scoring.scorer import Check


# --------------------------------------------------------------------------- #
# Epochs — the versioned model artefact.
# --------------------------------------------------------------------------- #

def test_epoch_is_pure_and_reproducible():
    """No clock, no environment, no database. The determinism digest anchors on
    this; an epoch that varied with wall time could not anchor anything."""
    assert epochs.compute(["a"]).sha256 == epochs.compute(["a"]).sha256


def test_quarantining_an_experiment_changes_the_epoch():
    """The consequence people miss. Only GATING experiments are in the
    experiment_set, so a demotion changes what the composite score is an
    average OF — and a trend line across that boundary compares two different
    things."""
    before = epochs.compute(["pod_kill", "partition"]).sha256
    after = epochs.compute(["pod_kill"]).sha256
    assert before != after


def test_epoch_mismatch_is_a_refusal_not_a_coercion():
    with pytest.raises(EpochMismatch) as exc:
        epochs.assert_same("a" * 64, "b" * 64)
    assert "refusing to compare" in str(exc.value)
    assert "retro-scoring" in str(exc.value)


def test_epoch_diff_names_what_actually_changed():
    """A boundary labelled 'scorer changed' teaches nobody anything."""
    a = epochs.compute(["pod_kill"])
    b = epochs.compute(["pod_kill", "partition"])
    assert any("gating set" in d and "+partition" in d for d in epochs.diff(a, b))


def test_epoch_diff_reports_a_weight_move(monkeypatch):
    a = epochs.compute([])
    monkeypatch.setitem(scorer.WEIGHTS, "slo_recovery", 0.40)
    b = epochs.compute([])
    assert any("weights" in d and "slo_recovery" in d for d in epochs.diff(a, b))


def test_there_is_no_way_to_edit_a_recorded_epoch():
    """Immutability is enforced by the absence of the function, not by a
    comment asking people not to."""
    assert not hasattr(epochs, "update_epoch")
    assert not any(n.startswith("update") for n in dir(epochs))


# --------------------------------------------------------------------------- #
# Flakiness — why anyone would trust the CI gate.
# --------------------------------------------------------------------------- #

def runs(scores, verdicts=None, *, sha="abc123", version=1):
    verdicts = verdicts or ["held"] * len(scores)
    return [F.Run(i, sha, v, s, version)
            for i, (s, v) in enumerate(zip(scores, verdicts))]


def test_a_new_experiment_is_advisory_until_characterised():
    """Progressive delivery applied to our own tooling: an experiment reports
    and comments before it may block."""
    v = F.evaluate("new_one", runs([0.9] * 5))
    assert v.status == "uncharacterised"
    assert v.gating is False
    assert v.runs_needed == FLAKINESS_WINDOW - 5


def test_a_stable_experiment_earns_gating_after_a_full_window():
    v = F.evaluate("steady", runs([0.90, 0.91] * (FLAKINESS_WINDOW // 2)))
    assert v.status == "stable"
    assert v.gating is True
    assert v.sigma < MAX_SCORE_STDDEV


def test_high_sigma_quarantines_to_advisory():
    """The whole mechanism: an experiment that cannot hold sigma under the
    threshold loses the authority to block a merge."""
    scores = [0.2 if i % 2 else 0.95 for i in range(FLAKINESS_WINDOW)]
    v = F.evaluate("swingy", runs(scores))
    assert v.status == "flaky"
    assert v.gating is False
    assert v.sigma > MAX_SCORE_STDDEV
    assert "cannot block a merge" in v.reason


def test_verdict_flips_quarantine_even_when_scores_are_tight():
    """Two independent signals. A run that alternates held/falsified on
    unchanged code is flaky even if every score is nearly identical."""
    verdicts = ["held" if i % 2 else "falsified" for i in range(FLAKINESS_WINDOW)]
    v = F.evaluate("flippy", runs([0.90] * FLAKINESS_WINDOW, verdicts))
    assert v.status == "flaky"
    assert v.flip_rate > MAX_VERDICT_FLIP_RATE


def test_flip_rate_is_pairwise_not_mode_based():
    """held x10 then falsified x10 flipped ONCE — a regression with a clean
    before and after, not flakiness. A mode-based rate would call it 50% and
    quarantine an experiment for correctly detecting a change."""
    clean_break = ["held"] * 10 + ["falsified"] * 10
    assert F.flip_rate(clean_break) == pytest.approx(1 / 19)
    alternating = ["held", "falsified"] * 10
    assert F.flip_rate(alternating) == pytest.approx(1.0)


def test_flip_rate_of_a_single_run_is_zero_not_an_error():
    assert F.flip_rate(["held"]) == 0.0
    assert F.flip_rate([]) == 0.0


def test_unscoreable_verdicts_do_not_count_toward_the_window():
    """INVALID means the measurement plane failed; ABORTED means the safety
    plane cut the run short. Counting either as a flip would quarantine an
    experiment for the FRAMEWORK's behaviour rather than its own."""
    mixed = runs([0.9] * 10 + [None] * 10,
                 ["held"] * 10 + ["invalid"] * 5 + ["aborted"] * 5)
    v = F.evaluate("mixed", mixed)
    assert v.status == "uncharacterised"
    assert v.window_runs == 10


def test_a_full_window_with_no_scores_is_uncharacterised_not_stable():
    """Refusing to promote on absent evidence is the same instinct as the
    empty-series rule: a window whose scores never persisted is not proof of
    stability."""
    v = F.evaluate("scoreless", runs([None] * FLAKINESS_WINDOW))
    assert v.status == "uncharacterised"
    assert v.gating is False
    assert "cannot compute sigma" in v.reason


def test_sigma_exactly_at_the_threshold_is_still_stable():
    """The comparison is strict (>), so a value sitting exactly on the limit
    passes. Asserted because a later refactor to >= would silently quarantine
    borderline experiments."""
    assert F.evaluate("edge", runs([0.9] * FLAKINESS_WINDOW)).status == "stable"


def test_quarantine_is_not_deletion():
    """A quarantined experiment keeps its measurements and keeps reporting.
    There is no code path here that removes an experiment."""
    v = F.evaluate("swingy", runs([0.2 if i % 2 else 0.95
                                   for i in range(FLAKINESS_WINDOW)]))
    assert v.evidence["scores"]
    assert v.window_runs == FLAKINESS_WINDOW


# --------------------------------------------------------------------------- #
# Evidence bundles — content addressing.
# --------------------------------------------------------------------------- #

def sample_bundle(**over):
    base = dict(
        experiment="x", hypothesis={"version": 1, "invariants": []},
        load={"achieved_rps": 119.4, "dropped_iterations": 0, "coverage": 1.0,
              "min_rps_floor": 90.0},
        samples=[{"t": 1.0, "client": {"client_availability": 1.0}, "server": {}}],
        checks=[], verdict="held", verdict_reason=None, score={"score": 1.0},
        epoch={"epoch_sha256": "a" * 64}, git_sha="deadbeef")
    base.update(over)
    return bundles.build(**base)


def test_the_digest_is_the_runs_identity():
    assert bundles.verify(sample_bundle())


def test_the_digest_does_not_depend_on_itself():
    b = sample_bundle()
    assert bundles.digest(b) == bundles.digest({k: v for k, v in b.items()
                                                if k != "bundle_sha256"})


def test_sealing_is_idempotent():
    b = sample_bundle()
    assert bundles.seal(b) == b


def test_any_edit_breaks_the_digest():
    """Write-once, immutable. A bundle edited after the fact must not still
    claim to be the run it describes."""
    b = dict(sample_bundle())
    b["verdict"] = "falsified"
    assert not bundles.verify(b)


def test_key_order_does_not_change_the_digest():
    a = sample_bundle()
    reordered = {k: a[k] for k in reversed(list(a))}
    assert bundles.digest(a) == bundles.digest(reordered)


def test_float_noise_below_the_precision_floor_does_not_move_the_digest():
    """A determinism gate that fires on the 17th decimal place of a float is a
    gate people disable. Two runs that differ only below the rounding floor
    must hash identically."""
    a = sample_bundle(load={"achieved_rps": 119.4, "dropped_iterations": 0,
                            "coverage": 1.0, "min_rps_floor": 90.0})
    b = sample_bundle(load={"achieved_rps": 119.4 + 1e-12, "dropped_iterations": 0,
                            "coverage": 1.0, "min_rps_floor": 90.0})
    assert bundles.digest(a) == bundles.digest(b)


def test_a_real_difference_above_the_floor_does_move_the_digest():
    a = sample_bundle()
    b = sample_bundle(load={"achieved_rps": 118.0, "dropped_iterations": 0,
                            "coverage": 1.0, "min_rps_floor": 90.0})
    assert bundles.digest(a) != bundles.digest(b)


def test_a_bundle_reconstructs_its_sample_set():
    b = sample_bundle()
    ss = bundles.sample_set(b)
    assert len(ss.samples) == 1
    assert ss.samples[0].client["client_availability"] == 1.0


# --------------------------------------------------------------------------- #
# The verdict/check coupling, shared by the runner and the replay eval.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("verdict", ["invalid", "aborted"])
def test_an_unscoreable_verdict_poisons_its_checks(verdict):
    """Otherwise the evidence bundle carries four green rows describing a run
    that measured nothing."""
    checks = [Check(t, t, True, "pass", 1.0) for t in scorer.WEIGHTS]
    poisoned = scorer.checks_for_verdict(checks, verdict)
    assert all(c.outcome == "invalid" and c.score is None for c in poisoned)
    assert scorer.calculate(poisoned).score is None


@pytest.mark.parametrize("verdict", ["held", "falsified"])
def test_a_scoreable_verdict_leaves_its_checks_alone(verdict):
    checks = [Check(t, t, True, "pass", 1.0) for t in scorer.WEIGHTS]
    assert scorer.checks_for_verdict(checks, verdict) is checks


def test_poisoning_does_not_mutate_the_original_checks():
    """The caller's list must survive — the runner persists it afterwards."""
    checks = [Check(t, t, True, "pass", 1.0) for t in scorer.WEIGHTS]
    scorer.checks_for_verdict(checks, "invalid")
    assert all(c.outcome == "pass" for c in checks)
