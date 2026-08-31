"""Running a counterfactual pair (§17.3).

Deliberately disabling a resilience pattern is THE MOST DANGEROUS THING
CHAOSPROOF DOES. The "without" arm is designed to fail harder, so every safety
property in this module exists because the alternative is a cluster left with
its circuit breakers off.

Five constraints, all enforced here rather than remembered:

  1. STAGING ONLY. Enforced by policy (`deny-counterfactual-outside-staging`),
     and asserted again before the first arm runs. The policy engine is the
     guardrail; this is the check that fails loudly if someone ever runs this
     module against a spec the policy engine never saw.
  2. THE WITHOUT ARM GETS A TIGHTER ABORT THRESHOLD. Harder failure is
     expected; unbounded failure is not.
  3. INTERLEAVED, n >= 5. Drift affects both arms equally.
  4. FLAG SNAPSHOT RESTORED BY THE CLEANUP SAGA, with the CronJob as the
     out-of-band path. The runner's saga already does this per-run; the pair
     runner additionally verifies the flags are back after EVERY arm, because
     a pair that half-restores leaves the next arm measuring a mixture.
  5. EXCLUDED FROM THE RESILIENCE SCORE. These are deliberately degraded
     configurations and would poison the trend.
"""

import time
import uuid
from dataclasses import dataclass, field

from ..safety import flags as flags_mod
from . import analysis

STAGING_NAMESPACE = "chaos-staging"

# The "without" arm is expected to fail harder, so its abort threshold is
# TIGHTER, not looser. Loosening it would be the intuitive move and exactly
# wrong: the arm most likely to run away is the one that most needs a bound.
WITHOUT_ARM_ABORT_TIGHTENING = 0.05


@dataclass
class ArmRun:
    arm: str
    repetition: int
    execution_id: int | None
    verdict: str
    failed_requests: float | None = None
    p99_ms: float | None = None
    recovery_s: float | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        """Only a measured run contributes to a distribution.

        An INVALID or ABORTED arm has no number worth having: the measurement
        plane failed, or the safety plane cut it short. Including it would let
        the analysis draw a median from runs that measured nothing — the same
        error as scoring an empty series as a pass.
        """
        return self.verdict in ("held", "falsified") and self.failed_requests is not None


@dataclass
class PairResult:
    pair_id: str
    pattern: str
    experiment: str
    runs: list[ArmRun] = field(default_factory=list)
    aborted_early: str | None = None

    def values(self, arm: str, metric: str = "failed_requests") -> list[float]:
        return [getattr(r, metric) for r in self.runs
                if r.arm == arm and r.usable and getattr(r, metric) is not None]

    def analyse(self, metric: str = "failed_requests",
                lower_is_better: bool = True) -> analysis.CounterfactualResult:
        return analysis.analyse(
            self.pattern, metric,
            self.values("with_pattern", metric),
            self.values("without_pattern", metric),
            lower_is_better=lower_is_better)

    def discarded(self) -> list[ArmRun]:
        return [r for r in self.runs if not r.usable]


class CounterfactualSafetyError(RuntimeError):
    """Raised BEFORE any pattern is disabled."""


def assert_safe(spec: dict) -> None:
    """Fail before the first arm, not between arms.

    Ordering matters: a check that runs after the flags are flipped can leave a
    cluster degraded while it raises.
    """
    namespace = (spec.get("target") or {}).get("namespace")
    if namespace != STAGING_NAMESPACE:
        raise CounterfactualSafetyError(
            f"counterfactual pairs run in {STAGING_NAMESPACE!r} only; this spec "
            f"targets {namespace!r}. The 'without' arm is designed to fail "
            f"harder, and the correct response to this refusal is to move the "
            f"experiment, never to widen the policy rule.")
    if spec.get("mode") != "counterfactual":
        raise CounterfactualSafetyError(
            "spec is not marked `mode: counterfactual`, so the policy engine "
            "would not apply the staging restriction to it")
    if not spec.get("disable_patterns"):
        raise CounterfactualSafetyError(
            "no `disable_patterns` — a counterfactual pair with nothing to "
            "disable is two identical arms and a misleading chart")
    if not (spec.get("abort_conditions") or spec.get("hypothesis", {}).get("abort_conditions")):
        raise CounterfactualSafetyError(
            "no abort_conditions; the arm most likely to run away is the one "
            "that most needs a bound")


def tighten_aborts(spec: dict, tightening: float = WITHOUT_ARM_ABORT_TIGHTENING) -> dict:
    """Return a copy of the spec with the 'without' arm's abort thresholds
    tightened.

    'Tighter' is direction-aware: for a `<` condition (availability collapses
    BELOW a floor) tighter means a HIGHER threshold, so the guard fires sooner.
    For a `>` condition (error rate climbs above a ceiling) tighter means a
    LOWER one. Getting this backwards would loosen exactly the arm that needs
    bounding, so it is computed from the comparator rather than assumed.
    """
    out = {k: v for k, v in spec.items()}
    hypothesis = dict(out.get("hypothesis") or {})
    source = (out.get("abort_conditions")
              or hypothesis.get("abort_conditions") or [])

    tightened = []
    for condition in source:
        c = dict(condition)
        comparator, threshold = c.get("comparator"), c.get("threshold")
        if threshold is not None and comparator in ("<", "<="):
            c["threshold"] = float(threshold) + tightening
        elif threshold is not None and comparator in (">", ">="):
            c["threshold"] = max(0.0, float(threshold) - tightening)
        c["tightened_for_arm"] = "without_pattern"
        tightened.append(c)

    if "abort_conditions" in out:
        out["abort_conditions"] = tightened
    if hypothesis.get("abort_conditions"):
        hypothesis["abort_conditions"] = tightened
        out["hypothesis"] = hypothesis
    return out


def verify_flags_restored(namespace: str, app: str, snapshot: dict) -> None:
    """Between arms, not just at the end.

    A pair that half-restores leaves the next arm measuring a mixture of two
    configurations, which is unattributable — and unattributable data is worse
    than missing data because it still plots.
    """
    current = flags_mod.snapshot(namespace, app)
    disabled = [name for name, off in (current or {}).items()
                if off and not (snapshot or {}).get(name)]
    if disabled:
        raise CounterfactualSafetyError(
            f"patterns still disabled after an arm: {disabled}. Refusing to "
            f"start the next arm — the cleanup saga or the chaos-cleanup "
            f"CronJob must restore them first. A cluster left with its "
            f"resilience patterns off is the worst outcome this project has.")


def plan_pair(spec: dict, repetitions: int = analysis.MIN_REPETITIONS) -> list[tuple[str, int]]:
    """The interleaved run order, as (arm, repetition) pairs."""
    assert_safe(spec)
    order = []
    for i in range(1, repetitions + 1):
        order.append(("with_pattern", i))
        order.append(("without_pattern", i))
    return order


def spec_for_arm(spec: dict, arm: str) -> dict:
    """The spec each arm actually runs.

    The `with_pattern` arm has NOTHING disabled — it is the baseline, and it
    must be the identical experiment or the comparison measures two different
    faults. Only the flags differ between arms.
    """
    if arm == "with_pattern":
        out = {k: v for k, v in spec.items() if k != "disable_patterns"}
        out["counterfactual_arm"] = "with_pattern"
        return out
    out = tighten_aborts(spec)
    out["counterfactual_arm"] = "without_pattern"
    return out


def new_pair_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def record(cur, pair_id: str, pattern: str, run: ArmRun) -> None:
    """Store EVERY run, including the discarded ones.

    The distribution is the finding, so the raw runs have to survive: a stored
    median with no way to see its spread is exactly the single seductive number
    this feature exists to avoid.
    """
    if run.execution_id is None:
        return
    cur.execute(
        """INSERT INTO counterfactual_runs
               (pair_id, execution_id, arm, pattern_name, repetition,
                failed_requests, p99_ms, recovery_s)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (pair_id, run.execution_id, run.arm, pattern, run.repetition,
         None if run.failed_requests is None else int(run.failed_requests),
         run.p99_ms, run.recovery_s))


def load_pair(cur, pattern: str) -> PairResult | None:
    """Most recent pair for a pattern."""
    cur.execute(
        """SELECT pair_id FROM counterfactual_runs
            WHERE pattern_name = %s
            ORDER BY id DESC LIMIT 1""", (pattern,))
    row = cur.fetchone()
    if row is None:
        return None
    pair_id = row[0]

    cur.execute(
        """SELECT c.arm, c.repetition, c.execution_id, e.verdict,
                  c.failed_requests, c.p99_ms, c.recovery_s
             FROM counterfactual_runs c
             JOIN experiment_executions e ON e.id = c.execution_id
            WHERE c.pair_id = %s
            ORDER BY c.repetition, c.arm""", (pair_id,))
    runs = [ArmRun(arm=r[0], repetition=r[1], execution_id=r[2], verdict=r[3],
                   failed_requests=None if r[4] is None else float(r[4]),
                   p99_ms=None if r[5] is None else float(r[5]),
                   recovery_s=None if r[6] is None else float(r[6]))
            for r in cur.fetchall()]
    if not runs:
        return None

    cur.execute("""SELECT e.experiment FROM counterfactual_runs c
                     JOIN experiment_executions e ON e.id = c.execution_id
                    WHERE c.pair_id = %s LIMIT 1""", (pair_id,))
    experiment = (cur.fetchone() or ["unknown"])[0]
    return PairResult(pair_id, pattern, experiment, runs)


def counterfactual_execution_ids(cur) -> list[int]:
    """Every execution that was part of a counterfactual pair.

    The score aggregate and the trend chart must exclude these: they are
    deliberately degraded configurations, and averaging them into the
    resilience score would make the system look worse the more carefully it is
    measured.
    """
    cur.execute("SELECT DISTINCT execution_id FROM counterfactual_runs")
    return [r[0] for r in cur.fetchall()]


def metrics_from_samples(samples) -> tuple[float | None, float | None]:
    """(failed_requests, p99_ms) for one arm, from its sampled window.

    Failed requests are derived CLIENT-side — the count of requests the load
    generator saw fail. Server-side counting would miss the failures that never
    reached a server, which under a partition is most of them.
    """
    if samples is None or not samples.samples:
        return None, None
    failed = 0.0
    for s in samples.samples:
        rps = s.client.get("client_rps")
        availability = s.client.get("client_availability")
        if rps is None or availability is None:
            continue
        # Each tick covers SAMPLE_INTERVAL_S seconds of traffic.
        failed += rps * (1.0 - availability) * 5.0
    p99 = [s.client.get("client_p99_ms") for s in samples.samples]
    present = [v for v in p99 if v is not None]
    return failed, (max(present) if present else None)


def wait_between_arms(seconds: float = 30.0) -> None:
    """Let the system settle between arms.

    Without this the next arm starts while the previous fault's recovery is
    still in progress, and the two arms are no longer measuring the same
    starting conditions — which is the confound interleaving exists to remove.
    """
    time.sleep(seconds)
