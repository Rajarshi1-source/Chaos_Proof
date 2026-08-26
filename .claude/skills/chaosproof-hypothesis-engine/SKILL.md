---
name: chaosproof-hypothesis-engine
description: "Build ChaosProof's verdict layer: falsifiable steady-state hypotheses instead of a bare pass/fail. Use for ANY work on the hypothesis YAML schema, hold-throughout versus recovery invariants, per-invariant outcomes, the verdict vocabulary (HELD, FALSIFIED, INVALID, SKIPPED, DENIED, ABORTED), the empty-series rule, the six-stage experiment runner, the four validators as sub-evidence, and the score calculator with applicable-check renormalisation. Trigger on hypothesis, steady state, invariant, tolerance_s, recover_within_s, falsified, verdict, HYPOTHESIS_HELD, INVALID, experiment runner, orchestrator, score calculator, weights, renormalise, or validator. MANDATE: a missing or empty series is never a passing series. Non-applicable checks are excluded and weights renormalised, never scored as partial credit. INVALID produces no score at all, not a zero. Write the hypothesis before seeing the result."
---

# ChaosProof Hypothesis Engine

Rev 1 asked "did the system recover?" — a binary with no stated expectation, which means the answer is whatever the validator happens to check. *Principles of Chaos Engineering* (Rosenthal, Basiri et al.) starts somewhere else: with a **steady-state hypothesis**, a formal falsifiable claim about healthy behaviour. The experiment exists to try to falsify it.

Two structural consequences, and both are the point:

- The pass criterion moves out of Python into **reviewable YAML** a colleague can argue with.
- You write down what you expect **before** seeing the result, which is the only way an experiment can surprise you.

## The verdict vocabulary

```
Rev 1:  passed | failed | partial
Rev 2:  HYPOTHESIS_HELD | HYPOTHESIS_FALSIFIED | INVALID | SKIPPED | DENIED | ABORTED
```

| Verdict | Meaning | Scoreable? |
|---|---|---|
| `HYPOTHESIS_HELD` | Every invariant held within tolerance | ✅ |
| `HYPOTHESIS_FALSIFIED` | At least one invariant breached beyond tolerance | ✅ |
| **`INVALID`** | **The experiment could not measure what it claimed to** | ❌ **no score at all** |
| `SKIPPED` | Pre-flight refused: steady state absent, or below the SLI floor | ❌ |
| `DENIED` | Policy refused: blast radius, budget gate, protected target | ❌ |
| `ABORTED` | Halted mid-fault by a probe or the watchdog | ❌ |

`INVALID` is the one that matters most. It means the measurement was incapable of detecting failure, and it must **never** be reported as a pass — not as zero, not as partial credit. See `chaosproof-measurement-integrity` for what triggers it.

`SKIPPED` and `DENIED` are **recorded outcomes, not silent no-ops.** They get Slack messages and dashboard treatment, because "the framework correctly refused to run" proves the guardrails are load-bearing.

## The hypothesis object

```yaml
# experiments/pod_kill_payment.yaml
experiment:
  name: pod_kill_payment_svc
  litmus_fault: pod-delete
  target: { namespace: target-app, app: payment-service }
  fault_duration_s: 60
  recovery_window_s: 120

  load_profile: profiles/steady_120rps.js
  min_rps_floor: 90              # below this: INVALID, not PASS

  hypothesis:
    version: 3                   # bumping this resets gating status (see mlops skill)
    description: >
      When one replica of payment-service is killed under 120 rps of steady traffic,
      the system maintains >=99.0% client-observed availability and client P99 latency
      stays under 800ms, because surviving replicas absorb traffic via the Service
      endpoints and order-api's circuit breaker never needs to open.

    invariants:
      - name: client_availability_holds
        source: k6                          # CLIENT-side, not server-side
        expr: 1 - (http_req_failed / http_reqs)
        comparator: ">="
        threshold: 0.990
        tolerance_s: 10                     # may breach <=10s during endpoint convergence

      - name: client_p99_holds
        source: k6
        expr: http_req_duration_p99_ms
        comparator: "<="
        threshold: 800
        tolerance_s: 15

      - name: replicas_restored
        source: prometheus
        expr: sum(kube_deployment_status_replicas_available{deployment="payment-service"})
        comparator: ">="
        threshold: 3
        recover_within_s: 30                # RECOVERY invariant, not hold-throughout

    abort_conditions:                       # see chaosproof-safety-plane
      - { name: availability_collapse, source: k6,
          expr: "1 - (http_req_failed / http_reqs)", comparator: "<", threshold: 0.80 }
```

### Two invariant kinds — do not conflate them

| Kind | Field | Semantics |
|---|---|---|
| **Hold-throughout** | `tolerance_s` | May be briefly breached, then must return. Describes **degradation budget** |
| **Recovery** | `recover_within_s` | Expected to breach; must come back inside a deadline. Describes **recovery** |

Rev 1 scored both with one "did it recover" check, which is why a service that never degraded and a service that degraded and recovered scored identically. Every new invariant must declare which kind it is.

## Evaluation

```python
# chaos-framework/src/hypothesis/engine.py
@dataclass
class InvariantOutcome:
    name: str
    outcome: str            # held | falsified | invalid
    worst_value: float | None
    threshold: float
    breached_for_s: float
    evidence: dict          # the sample window, so the verdict is auditable

def evaluate(hypothesis, samples: SampleSet) -> HypothesisVerdict:
    if not samples.is_valid(hypothesis.min_rps_floor):
        return HypothesisVerdict("invalid", [], reason=samples.invalidity_reason)

    outcomes = []
    for inv in hypothesis.invariants:
        series = samples.series(inv.source, inv.expr)
        if series.is_empty():
            # A missing series is NEVER a passing series. This is the single most
            # common way a home-grown chaos framework lies to you.
            outcomes.append(InvariantOutcome(inv.name, "invalid", None, inv.threshold, 0,
                                             {"reason": "no samples for expression"}))
            continue
        outcomes.append(_check_recovery(inv, series) if inv.recover_within_s is not None
                        else _check_hold(inv, series))

    if any(o.outcome == "invalid" for o in outcomes):
        return HypothesisVerdict("invalid", outcomes)
    if any(o.outcome == "falsified" for o in outcomes):
        return HypothesisVerdict("falsified", outcomes)
    return HypothesisVerdict("held", outcomes)
```

**The empty-series rule is load-bearing.** If `resilience4j_circuitbreaker_state` returns nothing because `resilience4j-micrometer` was never added to the classpath, a naive validator sees "no failures found" and passes. Return `invalid` and refuse to score.

**Verdicts are per-invariant, not one blob.** Persist each to `hypothesis_results` with `worst_value`, `threshold`, `breached_for_s`, and the evidence window. The dashboard names *which* invariant was falsified; "the experiment failed" is not an actionable result.

## The scorer — renormalise, never partial-credit

Rev 1 silently scored non-applicable checks as 0.5. The proof is arithmetic: its worked example put disk-fill at 0.625 with `pattern=N/A`, and 0.625 is only reachable as `0.35 + 0 + (0.5 × 0.25) + 0.15`. The 0.5 is **unrelated to any evidence**, so it skews an experiment in whichever direction its other checks happen to sit — worse than a consistent bias, because no offset corrects it. It also makes scores non-comparable across experiment types, which is what an interviewer will catch.

```python
# chaos-framework/src/scoring/score_calculator.py
WEIGHTS = {                       # part of the scoring epoch
    "slo_recovery":          0.35,
    "alert_validation":      0.25,
    "resilience_pattern":    0.25,
    "recovery_completeness": 0.15,
}

def calculate(checks: list[Check], epoch: Epoch) -> ScoreResult:
    if any(c.outcome == "invalid" for c in checks):
        return ScoreResult(score=None, status="invalid",
                           reason="SLI validity floor not met — experiment not scoreable")

    applicable = [c for c in checks if c.applicable]
    if not applicable:
        return ScoreResult(score=None, status="invalid", reason="no applicable checks")

    total_weight = sum(WEIGHTS[c.check_type] for c in applicable)
    score = sum(WEIGHTS[c.check_type] * c.score for c in applicable) / total_weight
    return ScoreResult(score=score, status=classify(score),
                       weights_denominator=total_weight,     # PERSIST for audit
                       excluded=[c.check_type for c in checks if not c.applicable],
                       epoch=epoch)
```

Rules:

- **Every check declares `applicable: bool`.** Non-applicable checks are excluded and the remaining weights renormalise.
- **Persist `weights_denominator`** on the execution row so a stored score can be re-derived rather than merely trusted.
- **`INVALID` poisons the whole experiment** — no score, not a zero. A zero would drag the aggregate as if the system failed, when in fact nothing was measured.
- **Never introduce a fifth weight without opening a new scoring epoch** (see `chaosproof-mlops-quality`).

Re-scoring the original example (`evals/scoring_worked_example.py`, verified 26 Aug 2026): disk-fill 0.625 → **0.6667** (`0.50 / 0.75`), daily aggregate 87.5% → **88.2%**.

**Three arithmetic corrections to the source material, found by running it.** The published 0.588 is `0.50 / 0.85` — the 0.15 completeness weight excluded instead of the 0.25 pattern weight. §4's Rev 1 column also mis-states Network Partition as 0.725 when the weights give 0.750, which moves the Rev 1 daily from 87.1% to 87.5%. And the direction **flips**: here the applicable checks average 0.667, above the arbitrary 0.5, so the partial credit was deflating this experiment. Derive these numbers, never copy them — a corpus case that asserts a published figure pins the typo instead of the behaviour.

## The runner — six stages, gates structurally in front

```python
# chaos-framework/src/orchestrator/experiment_runner.py
async def run(self, experiment) -> ExperimentResult:
    epoch = await self.epochs.current()
    bundle = EvidenceBundle(experiment=experiment, epoch=epoch, git_sha=self.git_sha)

    # STAGE 0 — load plane up, steady state established
    load = await self.load.start(experiment.load_profile)
    bundle.load_run = load
    await self.load.await_steady(experiment.min_rps_floor, timeout_s=120)

    # STAGE 1 — pre-flight: health, validity, blast radius, budget gate
    pre = await self.preflight(experiment)
    if not pre.ok:
        await self.load.stop(load)
        return self._record(bundle, verdict=pre.verdict, reason=pre.reason)

    # STAGE 2 — baseline
    bundle.baseline = await self.sampler.window(experiment, seconds=120)

    # STAGE 3 — inject, probes armed as in-experiment aborts
    crd = experiment.to_chaos_engine_crd(probes=self.safety.probes_for(experiment))
    injection = await self.litmus.apply(crd)
    bundle.injected_at = injection.started_at

    # STAGE 4 — sample from BOTH sources while watching abort conditions
    async with self.watchdog.armed(experiment.hypothesis.abort_conditions) as wd:
        bundle.samples = await self.sampler.stream(
            experiment, until=injection.ends_at + experiment.recovery_window_s,
            interval_s=5, on_abort=wd.trip)
    if wd.tripped:
        await self.safety.abort(injection, reason=wd.reason)
        return self._record(bundle, verdict="aborted", reason=wd.reason)

    # STAGE 5 — hypothesis first, then the four legacy checks as sub-evidence
    bundle.hypothesis_verdict = evaluate(experiment.hypothesis, bundle.samples)
    bundle.checks = await self.validators.run_all(experiment, bundle)

    # STAGE 6 — score (only if valid), persist, sign, notify
    bundle.score = self.scorer.calculate(bundle.checks, epoch=epoch)
    await self.load.stop(load)
    await self.cleanup.run(experiment)                # saga, on EVERY path
    return await self._finalise(bundle)
```

**What must stay structural rather than remembered:** load starts before pre-flight; pre-flight runs before injection; the watchdog wraps the sampling window; cleanup runs on every exit path. A runner that omits one of these cannot be made safe by discipline in the individual experiments — which is why safety lives here and not in each experiment class.

## The four validators, as sub-evidence

The hypothesis is the verdict; the four Rev 1 checks remain as structured evidence feeding the score:

| Check | Asserts | `applicable = false` when |
|---|---|---|
| `slo_recovery` | Client SLI restored and held for 30s | never |
| `alert_validation` | Expected alert fired; **excess** latency within SLO | the experiment expects no alert |
| `resilience_pattern` | The named pattern transitioned as designed | the experiment exercises no pattern (e.g. disk fill) |
| `recovery_completeness` | No lingering restarts, throttling, pressure, or new alerts | never |

Use `max_over_time` for pattern transitions rather than an instant read — a circuit breaker that opened and closed inside the sampling interval is a **pass**, and an instant query at the wrong moment records it as a failure.

## Anti-patterns

1. Adding a validator that returns `passed=True` on an empty result set.
2. Scoring `INVALID` as zero to "keep the aggregate simple."
3. Introducing a hypothesis without `min_rps_floor`.
4. Using one invariant to cover both degradation and recovery.
5. Editing `WEIGHTS` without opening a new epoch.
6. Writing a hypothesis description that isn't falsifiable ("the system should behave well").

> Interview framing: *"Every experiment starts with a falsifiable hypothesis with quantified invariants, and the framework reports which invariant held or was falsified. It also has a third verdict — `INVALID` — for runs where it couldn't measure. That distinction between 'no evidence of failure' and 'evidence of no failure' is the hardest thing in observability, and every tool I compared conflates them."*
