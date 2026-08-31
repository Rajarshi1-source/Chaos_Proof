# ADR-007 — Bounded, on-demand bisection with a visible cost estimate

**Status:** accepted · **Date:** 2026-08-31 · **The third documented scaling decision**

## Context

The dashboard shows the score fell from 0.87 to 0.71 over a week. *Which commit
did that?* Bisection replays the identical experiment — same epoch, same load
profile, same hypothesis version — across the commit range and binary-searches
for the change that moved the number. `git bisect` for reliability.

Two things have to be decided: **when it runs**, and **how many times it runs
each candidate**. The second one is what separates a tool from a demo.

## Part 1 — the repetition count, and why naive bisection converges on noise

Chaos experiments have run-to-run variance. §21.3 measures it: sigma of the score
on *unchanged* code over a 20-run window.

```
Required separation:   |score_good − score_bad|  >  2 × (σ_good + σ_bad)

With σ = 0.06 and a regression of 0.16:
    r = 1:   0.16 > 2 × 0.12 = 0.24        FALSE — insufficient
    σ_mean = σ / √r
    r = 3:   σ_mean = 0.035 → 0.16 > 0.14  TRUE
```

A single run at each candidate has a meaningful chance of misclassifying it, and
**one misclassification sends the binary search down the wrong half
permanently.** The correct half is never revisited, so no later evidence
contradicts the mistake — and the search still terminates, still names a commit,
and is still wrong. A tool that reports the wrong commit with the same confidence
as the right one is worse than no tool.

So repetitions are **derived** from the measured sigma, never defaulted. An
experiment with no characterised sigma cannot be bisected at all: there is no way
to know how many repetitions are enough, and running "some" and hoping is the
failure this whole mechanism exists to prevent.

### `abandoned` is a first-class outcome

If a candidate's mean lands inside the flakiness band, the search **stops and
says so**. It does not guess a half.

The classification band *is* the separation rule rather than a second rule
layered on it: `delta > 2 × (σ_g + σ_b)` is algebraically "the two intervals
`score ± 2σ_mean` do not overlap", so passing pre-flight is exactly what
guarantees the two acceptance bands are disjoint. An earlier implementation used
`2 × (σ_candidate + σ_anchor)` for the band, reasoning that a wider band is more
cautious. It is not: at the boundary repetition count the arithmetic calls
sufficient, the widened bands overlap across the whole interval, every candidate
lands inside both, and the search abandons every single time. A rule that never
returns an answer is not a cautious rule, and
`test_the_acceptance_band_is_the_separation_criterion_itself` pins the correction.

A repetition that produces no score — `INVALID`, `ABORTED`, `SKIPPED`, `DENIED`
— returns `None` and is never coerced to zero. A zero would drag the mean toward
the bad anchor and pick a half on the strength of a run that measured nothing:
the empty-series rule arriving through a side door.

### The range must not contain the scorer

The scorer, the weights, the SLO definitions and the hypotheses live in the same
repository as the target application. Rolling the whole tree back at each
candidate would score every candidate with whatever scorer existed at that
commit, and a range containing a weight change would make the search converge —
confidently — on the commit that edited the scorer.

Two mechanisms cover it. `git worktree` materialises only the target-app tree at
the candidate while the framework executing the search stays pinned. And if the
range touches any epoch-defining path at all, the bisection is **refused**:
pinning does not rescue that case, because the endpoints were never comparable
to begin with. That refusal is the same finding the epoch boundary on the trend
chart reports, arriving through a different door.

## Part 2 — when it runs

**Problem:** each candidate costs `reps × (load warm-up + fault + recovery +
cleanup)`. For `pod_kill_payment_svc` that is 6.0 minutes per run — 120s
steady-state window, 60s fault, 120s recovery, 60s cleanup — so 18 minutes per
candidate at 3 reps. A 40-commit range needs `log₂(40) ≈ 6` candidates: **~90
minutes of exclusive cluster time.**

**Option A — bisect on every score regression, automatically.** Rejected. A
90-minute cluster-monopolising job triggered by *noise* is a denial of service
against the daily chaos schedule — and this project has already demonstrated
that score dips are sometimes noise, which is the entire reason flakiness
quarantine exists.

**Option B — on demand, with the cost shown before starting, a hard candidate
cap, and a nightly window.** Chosen. The Slack message reporting a regression
carries a button reading **"Bisect (est. 6 candidates, ~90 min)"**. A human
decides.

Three details are deliberate:

- **The cap is derived from the window, not invented.** The nightly window is
  four hours, so a bisection that cannot finish inside four hours is refused
  rather than started and killed at 05:00 half-way through. A partial binary
  search has consumed the cluster night and learned nothing.
- **The per-run cost comes from the experiment spec, including cleanup.** The
  round figure of 5 minutes omits cleanup; the derived figure for pod-kill is
  6.0. An estimate a human is about to consent to must not be the optimistic
  one — which is also why the button label rounds *up* to the next five minutes
  rather than to the nearest (72 minutes must not read as "~70 min").
- **The button is a URL button, not an interactive Slack action.** An
  interactive button posts to a request URL, which would make ChaosProof an
  inbound HTTP endpoint needing signature verification, a replay window and a
  rotating secret — a security surface added so a click could skip one page
  load. ChaosProof is otherwise a net *consumer* of Prometheus and Alertmanager
  and receives no inbound webhooks at all. The link lands on GitHub's own
  dispatch form, which already authenticates the person, records who pressed it,
  and shows the inputs before anything starts. The authorisation this decision
  asks for is *a person deciding* — not a particular widget.

The window is a scheduling courtesy, not a safety control, so `--now` overrides
it and the override is recorded on the row. A regression blocking a release at
10:00 is a legitimate reason to take the cluster; the record is what keeps it
exceptional.

## Consequences

**Regressions are not diagnosed instantly.** In exchange the daily schedule is
never starved, and the cost is visible before it is incurred rather than
discovered afterwards.

Every outcome is persisted, including the refusals — a tool that records only
its successes reads, six months later, as a tool that always succeeds.

## Status of the implementation

The search, the sigma arithmetic, the cost model and every refusal path are
covered by 33 hermetic tests that need no cluster. **The live candidate driver
in `bisect/driver.py` has not been executed against a real cluster**: bisection
needs one cluster exclusively for ninety minutes with a rebuild and redeploy
between candidates, which the single kind node this project runs on cannot
provide while the daily schedule also uses it. That boundary is stated in the
module docstring and in `EXPERIMENTS.md` rather than left to be discovered.

## The reversal condition

**A second cluster the daily schedule does not use.** Automatic bisection on
every regression stops being a denial of service the moment it is not competing
for the only cluster — Option A becomes correct, and the cost estimate becomes
informational rather than a gate.
