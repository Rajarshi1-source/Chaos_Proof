---
name: chaosproof-mlops-quality
description: "Build ChaosProof's MLOps and quality wrapper: how a resilience score stays comparable and how a CI chaos gate stays trusted. Use for ANY work on scoring epochs and retro-scoring, the hermetic replay eval that gates CI on verdicts and a determinism digest, the regression corpus, flakiness measurement with automatic quarantine and advisory-to-gating promotion, bisection and the sigma arithmetic that stops it converging on noise, content-addressed evidence bundles, cosign signing, and the LLM narrator boundary. Trigger on scoring epoch, retro-score, replay eval, determinism digest, corpus, flakiness, sigma, quarantine, advisory, gating, bisect, evidence bundle, chaosctl replay, cosign, or narrator. MANDATE: never change a weight, threshold, SLO, or the scorer without opening a new epoch; never UPDATE a stored score; a new or reflagged experiment cannot gate a merge before 20 clean runs; the LLM never touches a verdict."
---

# ChaosProof MLOps and Quality Wrapper

The framing that makes this coherent: **ChaosProof's scorer is a deterministic model that maps evidence to a number, so it gets the treatment a model gets** — versioned artefact, labelled dataset, offline eval gating CI, data validation, shadow deployment, progressive rollout, drift monitoring, rollback.

Deliberately **no machine learning**: the decisions must be unit-testable, replayable, and hashable. The one LLM in the system writes postmortem prose and never touches a verdict.

Read `references/data-model-and-cli.md` for the full schema, the `chaosctl` surface, and the exported metric names.

| MLOps practice | ChaosProof equivalent |
|---|---|
| Versioned model artefact | **Scoring epoch** — `sha256(weights ‖ experiment set ‖ SLO version ‖ scorer version)` |
| Labelled dataset | Committed **evidence bundles** with expected verdicts |
| Offline eval gating CI | `evals/replay_eval.py` — verdicts, scores, determinism digest |
| Data validation | The **SLI-validity gate** — refuse to score unmeasurable input |
| Shadow deployment | New experiments run **advisory-only** until characterised |
| Progressive rollout | Advisory → gating promotion, per experiment |
| Drift monitoring | Score σ on unchanged `git_sha`, verdict-flip rate |
| Rollback | Epoch rollback + `chaosctl freeze` |
| Model card | `EXPERIMENTS.md` — hypothesis, fault, blast radius, aborts, σ, gating status |

## Scoring epochs

A resilience score is only comparable to another score computed the same way. Rev 1's 45%→92% story spanned four weeks in which alert rules were added (changing what `alert_validation` could pass) and experiments were tuned — so part of the "improvement" was edits to the scorer.

```python
def current_epoch() -> Epoch:
    material = canonical_json({
        "weights":        WEIGHTS,
        "experiment_set": sorted(registry.gating_experiment_names()),
        "slo_version":    slos.version,
        "scorer_version": SCORER_VERSION,
    })
    return Epoch(sha256=hashlib.sha256(material.encode()).hexdigest(), ...)
```

Three consequences, all mandatory:

1. **A trend line may only connect points within one epoch.** Epoch boundaries render as a labelled vertical line with the change reason. Two honestly separated segments are far more persuasive than one smooth line a reviewer can dismantle.
2. **Retro-scoring.** Because raw observations live in `sli_samples` and the evidence bundle, changing the scorer triggers a re-score of the last N stored runs under the new epoch, producing a *comparable* history. **This is the entire reason raw samples are persisted rather than just scores.**
3. **Epochs are immutable.** A closed epoch is never edited; a corrected score is a **new row** under a new epoch referencing the same bundle, never an `UPDATE`. That is what makes retro-scoring auditable rather than revisionist.

Anything that changes the meaning of a score opens an epoch: the weights, the gating experiment set, an SLO threshold, the scorer code, or any constant in the threshold module.

> *"Four weeks, one scoring epoch after I retro-scored the early runs. The weights changed once in week two when I added alert validation — that boundary is on the chart, and the earlier runs were re-scored under the new weights so the comparison is real. Without that, some of my improvement would have been me editing the scorer, and I'd rather show you the seam than have you find it."*

## The replay eval — a CI gate, not a unit test

```python
# evals/replay_eval.py
"""
Replays committed evidence bundles through the scorer and hypothesis engine.
Hermetic: no cluster, no Prometheus, no network. Runs on every PR in seconds.
"""
TARGET_ACCURACY = 1.00      # verdicts are deterministic: any mismatch is a bug

for case in corpus:                                   # >= 20 bundles
    verdict = engine.evaluate(case.hypothesis, SampleSet.from_bundle(case.bundle))
    score   = scorer.calculate(case.checks, epoch=case.epoch)
    assert verdict.outcome == case.expected_verdict, case.name
    assert abs(score.value - case.expected_score) < 1e-9, case.name
    digests.append(sha256_canonical({"verdict": verdict, "score": score}))

assert digests == frozen_digests[SCORER_VERSION]      # determinism anchor
```

**The corpus must include the cases that catch the original defects** — that is how a fix stays fixed:

| Case | Asserts |
|---|---|
| `no_load_zero_traffic` | Verdict is **`invalid`**, not `held` — the load-plane regression test |
| `pattern_not_applicable` | Weights **renormalise** to 0.75; score is 0.588, not 0.625 |
| `empty_promql_series` | Missing series → `invalid`, never a pass |
| `alert_for_exceeds_slo` | Config assertion fails and names the rule |
| `dropped_iterations_high` | Load generator saturated → `invalid` |
| `abort_mid_experiment` | Verdict `aborted`, score `None`, cleanup recorded |
| `epoch_mismatch` | Scoring a bundle under a different epoch is **refused**, not silently coerced |

**The determinism digest is a frozen artefact keyed by `SCORER_VERSION`.** When a deliberate logic change moves the digests, the reviewer updates the frozen set *in the same PR* — which makes every scoring-affecting change visible in review. That is the point, not an inconvenience.

Two sibling gates in the same CI stage: `evals/policy_eval.py` (every safety rule has a must-allow and must-deny case) and `evals/alert_rule_lint.py` (every alert rule's irreducible latency must be under its own SLO).

## Flakiness quarantine — why anyone would trust the CI gate

This is the most practically important thing here, and the honest answer to the strongest objection to chaos-in-CI: **a flaky gate gets disabled.** If a chaos check fails randomly on innocent PRs, developers route around it within a week — `--no-verify`, an admin merge, or deleting the workflow. Every abandoned CI gate was abandoned for this reason.

```python
# chaos-framework/src/quality/flakiness.py
FLAKINESS_WINDOW      = 20      # runs on unchanged git_sha
MAX_SCORE_STDDEV      = 0.05
MAX_VERDICT_FLIP_RATE = 0.05

def evaluate(experiment_type: str) -> FlakinessVerdict:
    runs = db.runs_on_unchanged_sha(experiment_type, limit=FLAKINESS_WINDOW)
    if len(runs) < FLAKINESS_WINDOW:
        return FlakinessVerdict("uncharacterised", gating=False)   # advisory until proven

    sigma = stdev(r.score for r in runs if r.score is not None)
    flips = flip_rate(r.verdict for r in runs)

    if sigma > MAX_SCORE_STDDEV or flips > MAX_VERDICT_FLIP_RATE:
        return FlakinessVerdict("flaky", gating=False,
            reason=f"sigma={sigma:.3f} flips={flips:.1%} — cannot block a merge on this")
    return FlakinessVerdict("stable", gating=True)
```

Three load-bearing rules:

1. **A new experiment is advisory-only until characterised.** It reports and comments on PRs; it cannot block. It earns gating status after 20 clean runs — progressive delivery applied to your own tooling.
2. **A flaky experiment is auto-quarantined** to advisory, with a Slack notice naming σ and a GitHub issue opened to fix **the experiment**. Quarantine is not deletion; it keeps running and reporting.
3. **Bumping an experiment's `hypothesis.version` resets it to advisory.** Changing what an experiment asserts invalidates its flakiness history.

Only gating experiments count in the epoch's `experiment_set` — so quarantining one **opens a new epoch**, which is correct: the score now means something different.

> *"The hardest problem with chaos in CI isn't building the gate — it's that a flaky gate gets disabled. So ChaosProof measures its own variance: it runs each experiment against unchanged code, computes the standard deviation of the score and the verdict flip rate, and any experiment that can't hold sigma under 0.05 is automatically demoted to advisory and files an issue against itself. New experiments start advisory and earn the right to block a merge after twenty clean runs."*

## Regression bisection

Score dropped 87% → 71% over a week. Which commit? Binary-search the range, replaying the identical experiment (same epoch, same load profile, same hypothesis version) at each candidate.

```python
async def bisect(experiment_type: str, good_sha: str, bad_sha: str,
                 reps: int = 3) -> BisectResult:
    lo, hi = commit_index(good_sha), commit_index(bad_sha)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        verdict = await self._evaluate_candidate(experiment_type, commit_at(mid), reps)
        if verdict == "inconclusive":
            return BisectResult.abandoned(
                commit_at(mid), reason="candidate straddles the flakiness band")
        lo, hi = (mid, hi) if verdict == "good" else (lo, mid)
    return BisectResult.found(commit_at(hi))
```

**Why naive bisection converges on noise** — the arithmetic that makes this a tool rather than a demo:

```
Required separation:  |score_good − score_bad|  >  2 × (σ_good + σ_bad)
With σ = 0.06 and a 0.16 regression:  0.16 > 2 × 0.12 = 0.24  →  FALSE
  ⇒ 1 repetition is insufficient. Use the mean of r reps: σ_mean = σ / √r
  With r = 3:  σ_mean = 0.035  →  0.16 > 2 × 0.07 = 0.14  →  TRUE
```

**One misclassification sends the binary search down the wrong half permanently.** If a candidate's mean lands inside the flakiness band, `abandoned` is the correct outcome — never guess.

**Bisection is on-demand with a visible cost estimate**, a hard candidate cap, and a nightly window. Each candidate costs `reps × ~5 min`; a 40-commit range needs `log₂(40) ≈ 6` candidates ≈ 90 minutes of cluster time. The Slack message reporting a regression carries a button — *"Bisect (est. 6 candidates, ~90 min)"* — and a human decides. Automatic bisection on every score dip is a denial of service against the daily schedule.

## Evidence bundles

Every execution produces one **content-addressed** bundle: hypothesis version, load-run summary and script hash, every SLI sample from both sources, the Litmus `ChaosResult`, probe outcomes, blast radius, the safety-gate decision, all check results, the verdict, the epoch, the cleanup log, and `git_sha`. The `sha256` of its canonical JSON **is** the run's identity.

Bundles make three otherwise-impossible things possible: retro-scoring under a new epoch, the hermetic replay eval, and offline reproduction via `chaosctl replay <sha>` with no cluster, network, or database. Keep the replay output format stable — it doubles as a golden-file test and the dashboard renders it for the public read-only demo.

**Bundles are immutable and write-once.** Sign them in CI with cosign keyless OIDC — one step, no key material to lose, the CI identity is the signer.

## The LLM boundary

```python
class Narrator:                       # provider-agnostic; model id from config
    """Drafts prose from the bundle. NEVER produces or alters a verdict, score, or number."""

class TemplateNarrator(Narrator):     # DEFAULT: deterministic, offline, no API key
    ...

class LLMNarrator(Narrator):
    """Output is DISCARDED if it references any metric, timestamp, or entity not in the bundle."""
```

Four rules in priority order: `TemplateNarrator` is the default so the system works with no API key and no network (a portfolio project that breaks without a paid API is a liability in a live demo); provider-agnostic adapter with the model id from config, never hardcoded at the call site; the grounding check is not optional and discards are logged and counted; **the LLM never touches a verdict**.

> *"The decision layer is deterministic rules — I need to unit-test it, replay it, and hash it. The LLM writes prose a human then edits, and if it mentions a number that isn't in the evidence bundle, the draft is thrown away. In 2026 everyone claims an AI SRE agent; 'my scoring is deterministic and my writing is AI-assisted' is the more defensible position."*

## Non-negotiables

1. **Never change a weight, threshold, SLO, or the scorer without opening a new epoch.**
2. **Never `UPDATE` a stored score** — new row, new epoch, same bundle.
3. **Never let a new or reflagged experiment gate a merge** before 20 clean runs.
4. **Never delete a flaky experiment** — quarantine it and fix it.
5. **Never bisect without repetitions derived from σ.**
6. **Never add a scoring change without a corpus case** proving the new behaviour.
7. **Never let the narrator emit a number that isn't in the bundle.**
