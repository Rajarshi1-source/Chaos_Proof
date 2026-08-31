"""Regression bisection (§20): the sigma arithmetic, the refusals, and the
abandonment that stops a binary search converging on noise.

All hermetic. The search takes a callable, so every case below runs with no
cluster, no git and no database — which is the whole reason the callable exists.
"""

import datetime as dt

import pytest

from src.bisect import cost as C
from src.bisect import runner as B
from src.bisect import sigma as S

# 02:00 — inside the nightly window, so window refusals do not fire in tests
# that are about something else.
NIGHT = dt.datetime(2026, 9, 1, 2, 0)
DAY = dt.datetime(2026, 9, 1, 14, 0)


def ramp(shas: list[str], breaks: dict[int, float], default: float):
    """An evaluator whose score changes at declared indices."""
    def evaluate(sha: str, reps: int) -> list[float | None]:
        idx = shas.index(sha)
        value = default
        for at in sorted(breaks):
            if idx >= at:
                value = breaks[at]
        return [value] * reps
    return evaluate


def range_of(n: int) -> list[str]:
    return [f"{i:040x}" for i in range(n + 1)]


# --------------------------------------------------------------------------- #
# The arithmetic. This is the plan's worked example, digit for digit.
# --------------------------------------------------------------------------- #

def test_the_plan_worked_example_one_rep_is_insufficient():
    """|0.87 - 0.71| = 0.16 against 2 x (0.06 + 0.06) = 0.24.

    0.16 > 0.24 is FALSE. A single run at each candidate cannot tell the two
    ends apart, and a bisection that ran anyway would be picking halves by coin
    flip while reporting a commit with full confidence.
    """
    assert S.required_separation(0.06, 0.06, reps=1) == pytest.approx(0.24)
    separable, why = S.anchors_separable(0.87, 0.71, 0.06, 0.06, reps=1)
    assert separable is False
    assert "3 repetitions would separate them" in why


def test_the_plan_worked_example_three_reps_suffice():
    """sigma_mean = 0.06 / sqrt(3) = 0.0346, band = 2 x (0.0346 + 0.0346) = 0.1386.

    0.16 > 0.1386 is TRUE. The plan quotes these as 0.035 and 0.14.
    """
    assert S.sigma_of_mean(0.06, 3) == pytest.approx(0.0346, abs=5e-4)
    assert S.required_separation(0.06, 0.06, reps=3) == pytest.approx(0.1386, abs=5e-4)
    separable, _ = S.anchors_separable(0.87, 0.71, 0.06, 0.06, reps=3)
    assert separable is True


def test_reps_needed_derives_three_from_the_regression_and_sigma():
    """(2 x (0.06 + 0.06) / 0.16)^2 = 1.5^2 = 2.25, so r = 3."""
    assert S.reps_needed(0.16, 0.06, 0.06) == 3


def test_reps_needed_is_strict_at_the_boundary():
    """The criterion is `>`, not `>=`. At exactly r = 4 the separation EQUALS
    the band, and a candidate sitting on the boundary is not separated from
    anything — so the answer must step past it."""
    delta = S.required_separation(0.05, 0.05, reps=4)      # exact equality at r=4
    assert S.reps_needed(delta, 0.05, 0.05) == 5


def test_a_regression_inside_the_noise_is_not_bisectable_at_any_price():
    """Not a failure to compute — a finding about the experiment. The fix is to
    reduce sigma, and no repetition count substitutes for that."""
    assert S.reps_needed(0.02, 0.06, 0.06) is None


def test_a_noiseless_experiment_needs_one_repetition():
    assert S.reps_needed(0.16, 0.0, 0.0) == 1


# --------------------------------------------------------------------------- #
# Classification. The band IS the separation rule, not a second rule on top.
# --------------------------------------------------------------------------- #

def test_the_acceptance_band_is_the_separation_criterion_itself():
    """`delta > 2 x (sigma_g + sigma_b)` is algebraically "the two intervals
    score +/- 2 x sigma_mean do not overlap". So passing the pre-flight check is
    exactly what guarantees the two classification bands are disjoint.

    This test exists because the first implementation used
    `2 x (sigma_candidate + sigma_anchor)` for the band — which looks more
    cautious and is in fact broken: at the boundary rep count the plan calls
    sufficient, the widened bands overlap everywhere, every candidate lands
    inside both, and the search abandons every single time. A rule that never
    returns an answer is not a cautious rule.
    """
    band_good = S.SEPARATION_MULTIPLIER * S.sigma_of_mean(0.06, 3)
    band_bad = S.SEPARATION_MULTIPLIER * S.sigma_of_mean(0.06, 3)
    assert 0.16 > band_good + band_bad          # the pre-flight criterion
    # ... and therefore the bands around 0.87 and 0.71 do not touch.
    assert 0.87 - band_good > 0.71 + band_bad


def test_a_candidate_at_the_good_anchor_classifies_good():
    c = S.classify_candidate(0.87, 3, 0.87, 0.06, 0.71, 0.06)
    assert c.verdict == S.GOOD


def test_a_candidate_at_the_bad_anchor_classifies_bad():
    c = S.classify_candidate(0.71, 3, 0.87, 0.06, 0.71, 0.06)
    assert c.verdict == S.BAD


def test_a_candidate_one_sigma_mean_off_the_anchor_still_classifies():
    """The band has to tolerate ordinary run-to-run variation, or a genuinely
    good candidate would be abandoned half the time and the search would almost
    never finish."""
    off = 0.87 - S.sigma_of_mean(0.06, 3)
    assert S.classify_candidate(off, 3, 0.87, 0.06, 0.71, 0.06).verdict == S.GOOD


def test_a_candidate_midway_between_the_anchors_is_inconclusive():
    """What a PARTIAL regression looks like: several commits each moving the
    score a little, so neither half is clean."""
    c = S.classify_candidate(0.79, 3, 0.87, 0.06, 0.71, 0.06)
    assert c.verdict == S.INCONCLUSIVE
    assert "straddles the flakiness band" in c.reason


def test_measuring_fewer_repetitions_widens_the_band():
    """If two of three reps came back INVALID, sigma_mean is sigma/sqrt(1), not
    sigma/sqrt(3). The candidate becomes harder to classify, which is the
    correct consequence of having measured less."""
    wide = S.classify_candidate(0.87, 1, 0.87, 0.06, 0.71, 0.06)
    narrow = S.classify_candidate(0.87, 3, 0.87, 0.06, 0.71, 0.06)
    assert wide.band_good > narrow.band_good


# --------------------------------------------------------------------------- #
# Cost, the cap, and the window.
# --------------------------------------------------------------------------- #

def test_forty_commits_cost_six_candidates_and_ninety_minutes():
    """§20.3's number, and the label that goes on the Slack button."""
    e = C.estimate(40, reps=3, minutes_per_run=C.PLAN_MINUTES_PER_RUN)
    assert e.candidates == 6
    assert e.total_minutes == pytest.approx(90.0)
    assert e.button_label == "Bisect (est. 6 candidates, ~90 min)"
    assert e.affordable


def test_the_button_label_rounds_the_cost_up_never_to_the_nearest():
    """A 72-minute estimate rounded to the nearest five reads "~70 min" — a
    smaller number than the truth, on the one control whose entire purpose is
    to make the cost visible before it is incurred."""
    assert "~75 min" in C.estimate(12, reps=3, minutes_per_run=6.0).button_label


def test_the_cost_cap_is_the_nightly_window_not_an_invented_number():
    """A bisection killed at 05:00 half-way through has spent the whole cluster
    night and produced no commit, so the refusal is the useful behaviour."""
    assert C.window_minutes() == 240.0
    e = C.estimate(64, reps=9, minutes_per_run=6.0)     # 6 x 9 x 6 = 324 min
    assert not e.affordable
    assert "nightly window" in e.reason


def test_too_wide_a_range_is_refused_rather_than_truncated():
    e = C.estimate(5000, reps=3, minutes_per_run=1.0)
    assert not e.affordable
    assert "narrow the range" in e.reason


def test_an_adjacent_pair_has_nothing_to_search():
    assert C.estimate(1, reps=3).candidates == 0
    assert not C.estimate(1, reps=3).affordable


def test_per_run_cost_is_derived_from_the_spec_including_cleanup():
    """The plan's round 5 minutes omits cleanup. An estimate a human consents to
    must not be the optimistic one."""
    spec = {"experiment": {"fault_duration_s": 60, "recovery_window_s": 120}}
    assert C.minutes_per_run_from_spec(spec) == pytest.approx(6.0)


def test_the_nightly_window_is_a_courtesy_not_a_safety_control():
    assert C.in_nightly_window(NIGHT).inside is True
    outside = C.in_nightly_window(DAY)
    assert outside.inside is False
    assert "--now to override" in outside.reason


# --------------------------------------------------------------------------- #
# The search.
# --------------------------------------------------------------------------- #

def _bisect(shas, evaluate, **kw):
    kw.setdefault("score_good", 0.87)
    kw.setdefault("sigma_good", 0.06)
    kw.setdefault("score_bad", 0.71)
    kw.setdefault("sigma_bad", 0.06)
    kw.setdefault("now", NIGHT)
    return B.bisect(experiment="pod_kill_payment_svc", shas=shas,
                    evaluate=evaluate, **kw)


def test_a_clean_single_commit_regression_is_found():
    shas = range_of(40)
    r = _bisect(shas, ramp(shas, {27: 0.71}, 0.87))
    assert r.outcome == B.FOUND
    assert r.commit == shas[27]
    assert r.reps == 3


def test_the_search_costs_no_more_candidates_than_the_estimate_promised():
    shas = range_of(40)
    r = _bisect(shas, ramp(shas, {27: 0.71}, 0.87))
    assert r.candidates_evaluated <= r.cost.candidates


def test_the_repetition_count_is_derived_not_defaulted():
    """Passing reps=None is the intended call. A hardcoded 3 that happened to be
    right for one sigma would be silently wrong for every other experiment."""
    shas = range_of(40)
    r = _bisect(shas, ramp(shas, {27: 0.71}, 0.87), reps=None)
    assert r.reps == 3 and r.reps_derived == 3


def test_a_candidate_inside_the_flakiness_band_abandons_and_never_guesses():
    """THE point of the module. One misclassification sends the binary search
    down the wrong half permanently — the correct half is never revisited, so
    no later evidence contradicts it, and the search still terminates
    confidently on a commit. Abandoning is the only honest option."""
    shas = range_of(40)
    r = _bisect(shas, ramp(shas, {20: 0.79, 30: 0.71}, 0.87))
    assert r.outcome == B.ABANDONED
    assert "straddles the flakiness band" in r.reason
    assert r.commit == shas[20]         # the AMBIGUOUS candidate, not a culprit


def test_an_unscoreable_candidate_is_not_a_zero():
    """INVALID means the measurement plane failed, not that the code is bad.
    Averaging a zero in would drag the mean to the bad anchor and choose a half
    on the strength of a run that measured nothing."""
    shas = range_of(40)

    def evaluate(sha, reps):
        if shas.index(sha) == 20:
            return [None] * reps
        return [0.87 if shas.index(sha) < 27 else 0.71] * reps

    r = _bisect(shas, evaluate)
    assert r.outcome == B.ABANDONED
    assert "no scoreable repetition" in r.reason
    assert r.trail[-1].mean is None


def test_partial_measurement_is_used_but_the_band_widens_to_match():
    """Two of three reps INVALID is still evidence; it is just weaker evidence.
    The band is computed at n=1, not n=3."""
    shas = range_of(40)

    def evaluate(sha, reps):
        good = shas.index(sha) < 27
        scores = [0.87 if good else 0.71] + [None] * (reps - 1)
        return scores

    r = _bisect(shas, evaluate)
    assert r.outcome == B.FOUND
    assert all(c.scoreable == 1 for c in r.trail)
    assert all("band was widened" in c.note for c in r.trail)


# --------------------------------------------------------------------------- #
# The refusals. All three happen BEFORE any cluster time is spent.
# --------------------------------------------------------------------------- #

def test_inseparable_anchors_are_refused_before_anything_runs():
    shas = range_of(40)
    calls = []
    r = _bisect(shas, lambda s, n: calls.append(s) or [0.87] * n,
                score_bad=0.85)
    assert r.outcome == B.REFUSED
    assert calls == []


def test_a_range_touching_an_epoch_defining_path_is_refused():
    """Bisection compares scores. If the range edits the scorer, the weights,
    an SLO or a hypothesis, the endpoints were never comparable and the search
    would converge — confidently — on the commit that edited the scorer."""
    shas = range_of(40)
    r = _bisect(shas, ramp(shas, {27: 0.71}, 0.87),
                epoch_conflicts=["chaos-framework/src/constants.py"])
    assert r.outcome == B.REFUSED
    assert "never comparable" in r.reason
    assert "constants.py" in r.reason


def test_running_outside_the_nightly_window_needs_an_explicit_override():
    shas = range_of(40)
    evaluate = ramp(shas, {27: 0.71}, 0.87)
    assert _bisect(shas, evaluate, now=DAY).outcome == B.REFUSED
    allowed = _bisect(shas, evaluate, now=DAY, allow_outside_window=True)
    assert allowed.outcome == B.FOUND
    assert allowed.overrode_window is True      # recorded, not silent


def test_an_unaffordable_estimate_is_refused_before_the_first_candidate():
    shas = range_of(200)
    calls = []
    r = _bisect(shas, lambda s, n: calls.append(s) or [0.87] * n,
                minutes_per_run=30.0)
    assert r.outcome == B.REFUSED
    assert calls == []


def test_every_result_carries_the_numbers_it_started_from():
    """So a stored bisection can be re-derived rather than merely trusted.
    `reps_used != reps_derived` is exactly where a human overrode the
    arithmetic, and that is what someone will go looking for."""
    shas = range_of(40)
    r = _bisect(shas, ramp(shas, {27: 0.71}, 0.87), reps=5)
    assert (r.anchor_score_good, r.anchor_score_bad) == (0.87, 0.71)
    assert r.anchor_sigma_good == 0.06
    assert r.reps_derived == 3 and r.reps == 5


# --------------------------------------------------------------------------- #
# Rendered output. Stable, like `chaosctl replay` — it is read in a terminal
# during an incident and quoted into an issue afterwards.
# --------------------------------------------------------------------------- #

def test_render_names_the_outcome_in_the_first_word():
    shas = range_of(40)
    found = _bisect(shas, ramp(shas, {27: 0.71}, 0.87)).render()
    abandoned = _bisect(shas, ramp(shas, {20: 0.79, 30: 0.71}, 0.87)).render()
    assert found.startswith("BISECT_FOUND")
    assert abandoned.startswith("BISECT_ABANDONED")


def test_render_shows_the_cost_that_was_authorised():
    shas = range_of(40)
    out = _bisect(shas, ramp(shas, {27: 0.71}, 0.87),
                  minutes_per_run=C.PLAN_MINUTES_PER_RUN).render()
    assert "Bisect (est. 6 candidates, ~90 min)" in out


def test_render_marks_an_overridden_window():
    shas = range_of(40)
    out = _bisect(shas, ramp(shas, {27: 0.71}, 0.87), now=DAY,
                  allow_outside_window=True).render()
    assert "[OVERRIDDEN]" in out
