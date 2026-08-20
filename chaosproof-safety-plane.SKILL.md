---
name: chaosproof-safety-plane
description: "Build ChaosProof's safety layer, the mechanism that makes chaos beyond CI responsible. Use for ANY work on pre-flight gating (steady-state check, SLI floor, blast radius, budget gate), blast-radius computation and error-budget arithmetic, Litmus promProbes as in-experiment aborts with Continuous mode and stopOnFailure, the independent Python watchdog, CEL safety policy in git, the error-budget gate and chaos breaker, and the cleanup saga. Trigger on blast radius, pre-flight, abort, stopOnFailure, promProbe, watchdog, error budget, budget gate, chaos breaker, freeze, kill switch, CEL policy, cleanup saga, SKIPPED, DENIED, or ABORTED. MANDATE: never inject before pre-flight returns proceed; never ship an experiment without abort conditions; cleanup runs on every exit path with a CronJob as the out-of-band path; ChaosProof never targets its own namespace. Build this before the cascade simulator."
---

# ChaosProof Safety Plane

Chaos in CI runs against an ephemeral `kind` cluster where safety is free — nothing real is at stake. That is also its limitation, and a sharp interviewer finds it: *"so you never ran chaos against anything that mattered."* The safety plane is what lets you answer *"I did, and here is the mechanism that made it responsible."*

Rev 1 **claimed** four safeguards in its interview answers and implemented two. The pre-flight health check and any in-experiment abort did not exist. Do not describe a safeguard that isn't built.

## Three layers at three timescales

| Layer | When | Mechanism | Outcome on failure |
|---|---|---|---|
| **Pre-flight** | Before injection | Steady-state check, SLI validity floor, blast radius, budget gate, CEL policy | `SKIPPED` or `DENIED` |
| **In-experiment** | During the fault | Litmus `promProbe` with `stopOnFailure` **plus** an independent Python watchdog | `ABORTED` |
| **Post-experiment** | After | Cleanup saga, verify steady state re-established | Escalate, trip the chaos breaker |

**Two independent abort paths is deliberate.** Litmus probes are in-band and fast but die with the chaos runner; the Python watchdog is out-of-band and survives a runner crash. A safety mechanism owned by the process that can crash is not a safety mechanism.

## Pre-flight

Runs **before** the Litmus CRD is applied. Order matters — cheapest and most decisive checks first.

```python
async def preflight(self, experiment) -> PreflightVerdict:
    # 1. Already unhealthy? Injecting into a sick cluster is not an experiment.
    if not await self.steady_state.holds(experiment.hypothesis, window_s=120):
        return PreflightVerdict.skip("steady state not established before injection")

    # 2. Enough traffic to measure anything?
    if await self.load.current_rps() < experiment.min_rps_floor:
        return PreflightVerdict.skip("below SLI validity floor — start the load plane first")

    # 3. Would the blast radius exceed budget?
    radius = self.blast.compute(experiment)
    if radius.error_budget_burn_pct > MAX_BUDGET_BURN_PCT:
        return PreflightVerdict.deny(f"would burn {radius.error_budget_burn_pct:.1f}% of budget")

    # 4. Budget already spent, or chaos frozen?
    gate = await self.budget.gate(experiment.target_namespace)
    if gate.state != "open":
        return PreflightVerdict.deny(f"budget gate {gate.state}: {gate.reason}")

    # 5. Declarative policy (§ below)
    decision = self.policy.evaluate(experiment, radius, gate)
    if decision.effect == "deny":
        return PreflightVerdict.deny(decision.message)

    return PreflightVerdict.proceed(radius)
```

`SKIPPED` and `DENIED` are recorded with their reasons, posted to Slack, and shown distinctly on the dashboard. A silent no-op teaches you nothing; a recorded refusal proves the guardrail fired.

## Blast radius, computed before injection

```python
@dataclass(frozen=True)
class BlastRadius:
    affected_pods: int
    affected_replica_fraction: float      # affected / total for the target workload
    affected_namespaces: list[str]
    requests_at_risk_per_min: float       # from the CURRENT load rate, not a guess
    max_recovery_s: float                 # historical p95 for this experiment type
    error_budget_burn_pct: float
    user_facing: bool

    def score(self) -> int:
        """Deliberately simple and explainable — a reviewer can evaluate it mentally."""
        return (self.affected_pods
                + 10 * len(self.affected_namespaces)
                + (25 if self.user_facing else 0)
                + int(50 * self.affected_replica_fraction))
```

Keep the score arithmetic. A policy rule a reviewer can evaluate in their head is worth more than a clever weighting nobody trusts.

**The budget arithmetic — be able to derive this on a whiteboard:**

```
Availability SLO   = 99.5% over 30 days
Total budget       = 0.005 × 30d × 86400 s/d              = 12,960 error-seconds
Experiment p95:  degraded availability 96% (error rate 4%), impact duration 45 s
Budget consumed    = 0.04 × 45                            = 1.8 error-seconds
Share of monthly   = 1.8 / 12,960                         = 0.014%
Daily × 30         = 0.42% of the monthly budget
```

> *"My entire daily chaos programme — six experiments, every day, for a month — consumes under half a percent of my availability error budget. That calculation is what makes chaos an easy sell instead of a scary one, and the gate is derived from it: any single experiment projected to burn more than 2% of the remaining budget needs an explicit override."*

## In-experiment aborts via Litmus probes

Probes here are **abort conditions**, not health checks. That is a different job from Rev 1's single `httpProbe`.

```yaml
probe:
  - name: client-availability-guard
    type: promProbe
    mode: Continuous                 # NOT EOT — EOT only tells you afterwards
    runProperties:
      probeTimeout: 5s
      interval: 5s
      retry: 1
      stopOnFailure: true            # <-- this is what makes it an abort
    promProbe/inputs:
      endpoint: http://prometheus-operated.monitoring:9090
      query: |
        1 - (sum(rate(k6_http_reqs_failed_total{testrun="{{TESTRUN}}"}[30s]))
             / sum(rate(k6_http_reqs_total{testrun="{{TESTRUN}}"}[30s])))
      comparator: { type: float, criteria: ">=", value: "0.80" }

  - name: blast-radius-containment
    type: promProbe
    mode: Continuous
    runProperties: { probeTimeout: 5s, interval: 10s, retry: 0, stopOnFailure: true }
    promProbe/inputs:
      endpoint: http://prometheus-operated.monitoring:9090
      query: |
        sum(rate(http_server_requests_seconds_count{status=~"5..",namespace!="target-app"}[30s]))
      comparator: { type: float, criteria: "<=", value: "0.5" }
```

Three implementation notes that cost hours if missed:

1. **`mode: Continuous` + `stopOnFailure: true`** turns a probe into an abort. `mode: EOT` is a validator.
2. **Pin LitmusChaos ≥ 3.28.0.** A stale-config leak across *multiple probes of the same type* was fixed there — and both probes above are `promProbe`, so this is load-bearing, not hypothetical.
3. **Probe queries read k6's exported series**, which is why the load plane must remote-write to Prometheus. A guard reading server-side metrics cannot see failures that never reached a server.

The Python watchdog evaluates the same `abort_conditions` from the hypothesis YAML out-of-band, so the two paths share one declaration and cannot drift apart.

## Safety rules as declarative policy

`policy/chaos_safety.yaml`, evaluated with `cel-python`. **First DENY wins, then REQUIRE_OVERRIDE, else ALLOW.** Adding a guardrail is a pull request, not a code change.

```yaml
rules:
  - id: deny-outside-allowed-namespaces
    effect: deny
    expr: '!(experiment.target_namespace in ["target-app","chaos-staging"])'
  - id: deny-stateful-targets
    effect: deny
    expr: 'experiment.target_kind == "StatefulSet" || experiment.target_name.startsWith("postgres")'
  - id: deny-chaosproof-self
    effect: deny
    expr: 'experiment.target_namespace == "chaosproof"'
  - id: deny-single-replica
    effect: deny
    expr: 'target.replicas < 2'
  - id: deny-counterfactual-outside-staging
    effect: deny
    expr: 'experiment.mode == "counterfactual" && experiment.target_namespace != "chaos-staging"'
  - id: deny-budget-gate-closed
    effect: deny
    expr: 'budget.gate_state != "open"'
  - id: deny-below-validity-floor
    effect: deny
    expr: 'load.current_rps < experiment.min_rps_floor'
  - id: require-override-large-radius
    effect: require_override
    expr: 'blast.score() > 60'
  - id: require-override-budget-heavy
    effect: require_override
    expr: 'blast.error_budget_burn_pct > 2.0'
```

Every rule carries a `message` used verbatim in Slack and the evidence bundle. **`evals/policy_eval.py` requires every rule to have a must-allow and a must-deny case, and it gates CI** — the policy file is code, so it gets tested.

**ChaosProof refuses to inject chaos into ChaosProof.** A framework that can kill the pod holding the experiment's state loses the experiment it was running, and the resulting half-written execution row is worse than no data. The rule lives in the policy file, not in code.

## The error-budget gate — the SRE position, enforced

Rev 1 recorded budget consumption and never read it. The budget is **admission control**, not decoration.

```python
BUDGET_WARN_PCT   = 50.0
BUDGET_FREEZE_PCT = 80.0

def gate(namespace: str) -> Gate:
    spent = budget.spent_pct(namespace, window_days=30)     # ALL burn, not just chaos
    if breaker.is_open(namespace):
        return Gate("closed", f"chaos breaker open: {breaker.reason(namespace)}")
    if spent >= BUDGET_FREEZE_PCT:
        return Gate("closed",
                    f"{spent:.0f}% of the 30-day error budget is spent — "
                    "reliability work takes priority over injecting more failure")
    if spent >= BUDGET_WARN_PCT:
        return Gate("restricted", f"{spent:.0f}% spent — low-radius experiments only")
    return Gate("open", f"{spent:.0f}% spent")
```

**The gate counts all budget burn — real incidents included — not just what chaos consumed.** That subtlety is the whole point: the question is "how much reliability allowance is left," not "how much have I spent on experiments."

Requires **≥30 days of Prometheus retention**. Check it at startup and refuse to gate on an incomputable budget.

### The chaos breaker

Trips on any of:

- 3 aborts in 24 hours — the system keeps behaving worse than predicted
- 2 consecutive `INVALID` verdicts for the same experiment — **the measurement plane is broken and results are meaningless**
- Cluster-wide Kubernetes API error rate above 20%
- Manual `chaosctl freeze --reason "release in progress"`

Recovery is `open → half_open` after 6 hours, where **exactly one low-radius experiment** is allowed through and its outcome decides `closed` or `open` again. The freeze is itself a recorded event with a reason and an owner.

## Cleanup as a saga

Registered **before** injection, executed on **every** exit path including abort and crash:

```python
CLEANUP_STEPS = [
    ("delete_chaosengine",    lambda e: litmus.delete(e.crd_name)),
    ("restore_feature_flags", lambda e: flags.restore(e.flag_snapshot)),
    ("restore_replicas",      lambda e: k8s.scale_to(e.target, e.replica_snapshot)),
    ("resume_gitops",         lambda e: gitops.resume(e.target)),
    ("stop_load",             lambda e: load.stop(e.load_run_id)),
    ("verify_steady_state",   lambda e: steady_state.assert_holds(e.hypothesis, window_s=120)),
]
```

- **The last step is the important one.** Cleanup is not done until steady state is re-established; if it cannot be, escalate rather than closing quietly.
- **`restore_feature_flags` is the highest-stakes step.** A cluster left with its circuit breakers disabled after a counterfactual run is the worst possible outcome of this project.
- **Out-of-band path:** a `chaos-cleanup` CronJob every 5 minutes force-cleans any `running` execution past its deadline. Alert on `chaosproof_orphaned_cleanups_total > 0`.
- **Cascades clean up in reverse dependency order**, and aborting stage 2 while stage 1 is active must halt both.

## Non-negotiables

1. **Never inject before pre-flight returns `proceed`.**
2. **Never ship an experiment without abort conditions** in its hypothesis YAML.
3. **Never let cleanup depend only on the happy path** — the CronJob is not optional.
4. **Never widen the policy file to make a run succeed.** A denial is a result.
5. **Never target the `chaosproof` namespace.**
6. **Never run counterfactual (pattern-disabled) experiments outside `chaos-staging`.**
7. **Build this skill before the cascade simulator.** A fault DAG without a working abort path is the one feature here that could genuinely take down a cluster.

> Interview framing: *"Three layers at three timescales. Before injection: blast radius from current load, a steady-state precondition, an SLI validity floor, and an error-budget gate — and I can show that my whole daily programme costs under half a percent of my monthly budget. During: continuous Litmus probes with `stopOnFailure` watching client availability and containment outside the target namespace, plus an independent watchdog, because a safety mechanism owned by the process that can crash isn't one. After: a cleanup saga registered before injection, with a CronJob as the out-of-band path — and cleanup isn't finished until steady state is back."*
