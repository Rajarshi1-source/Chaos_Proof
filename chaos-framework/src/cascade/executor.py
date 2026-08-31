"""Cascade execution (§18.3) — and its abort path, which is the hard part.

A DAG multiplies blast radius. Aborting stage 2 while stage 1 is still active
must halt BOTH, in reverse dependency order, and the cleanup saga runs stage
cleanups in reverse too. §15's safety plane exists before this module for that
reason: a cascade you cannot stop is not an experiment.

THE MOST VALUABLE OUTCOME THIS PRODUCES IS A TRIGGER THAT NEVER FIRES.
`db_pressure` waits for database P99 to actually cross 200ms. If it does not
within `timeout_s`, the scenario records `did_not_propagate` — the stale-cache
fallback absorbed the cache outage completely, proved rather than assumed. A
delay-only DAG cannot produce that result, and this module treats it as a
first-class outcome rather than a timeout error.
"""

import time
from dataclasses import dataclass, field

from .model import NO_FAULT, Scenario, Stage

# Stage outcomes.
FIRED = "fired"                     # the fault was injected
DID_NOT_PROPAGATE = "did_not_propagate"   # trigger never tripped — the FINDING
OBSERVED = "observed"               # fault: none, watched only
SKIPPED = "skipped"                 # an upstream stage did not fire
ABORTED = "aborted"


@dataclass
class StageResult:
    stage_id: str
    outcome: str
    fired_at_s: float | None = None
    trigger_value: float | None = None
    waited_s: float | None = None
    engine: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {"stage": self.stage_id, "outcome": self.outcome,
                "fired_at_s": self.fired_at_s, "trigger_value": self.trigger_value,
                "waited_s": self.waited_s, "engine": self.engine, "note": self.note}


@dataclass
class CascadeResult:
    scenario: str
    stages: list[StageResult] = field(default_factory=list)
    aborted_by: str | None = None
    teardown: list[str] = field(default_factory=list)

    @property
    def propagated(self) -> bool:
        """Did every conditional stage actually fire?"""
        return all(s.outcome != DID_NOT_PROPAGATE for s in self.stages)

    def headline(self) -> str:
        absorbed = [s for s in self.stages if s.outcome == DID_NOT_PROPAGATE]
        if absorbed:
            names = ", ".join(s.stage_id for s in absorbed)
            return (f"CASCADE DID NOT PROPAGATE — {names} never triggered. The "
                    f"upstream failure was absorbed rather than amplified, and "
                    f"this is the most valuable result this scenario can produce: "
                    f"it is proof rather than assumption.")
        if self.aborted_by:
            return f"CASCADE ABORTED by {self.aborted_by}; every stage torn down."
        return "cascade propagated through every stage"

    def to_dict(self) -> dict:
        return {"scenario": self.scenario,
                "stages": [s.to_dict() for s in self.stages],
                "aborted_by": self.aborted_by, "teardown": self.teardown,
                "propagated": self.propagated, "headline": self.headline()}


def wait_for_trigger(stage: Stage, sample_value, *, poll_s: float = 5.0,
                     now=time.monotonic, sleep=time.sleep) -> tuple[bool, float, float | None]:
    """Poll a stage's trigger until it trips or times out.

    Returns (fired, waited_s, last_value). A timeout is NOT an error — it is
    `did_not_propagate`, and the caller records it as a finding.

    `sample_value` is injected rather than imported so this is testable without
    Prometheus; `now`/`sleep` likewise, so the timeout path can be tested
    without actually waiting.
    """
    if stage.trigger is None:
        return True, 0.0, None

    started = now()
    last = None
    while True:
        last = sample_value(stage.trigger.promql)
        if stage.trigger.satisfied(last):
            return True, now() - started, last
        waited = now() - started
        if waited >= stage.trigger.timeout_s:
            return False, waited, last
        sleep(poll_s)


def plan(scenario: Scenario) -> list[Stage]:
    return scenario.order()


def teardown(scenario: Scenario, fired: list[str], stop_stage) -> list[str]:
    """Tear down every stage that fired, in REVERSE dependency order.

    Every stage is attempted even if one fails: a teardown that stops at the
    first error leaves the remaining faults injected, which is the outcome the
    whole safety plane exists to prevent. Failures are collected and reported.
    """
    done: list[str] = []
    for stage in scenario.teardown_order():
        if stage.id not in fired:
            continue
        try:
            stop_stage(stage)
            done.append(f"{stage.id}=ok")
        except Exception as exc:                                   # noqa: BLE001
            done.append(f"{stage.id}=FAILED({type(exc).__name__})")
    return done


def run(scenario: Scenario, *, inject, stop_stage, sample_value, check_aborts,
        observe, poll_s: float = 5.0, now=time.monotonic, sleep=time.sleep
        ) -> CascadeResult:
    """Execute the DAG.

    Every side effect is injected as a callable, which is what makes the abort
    and teardown ordering testable without a cluster — the part most likely to
    be wrong is the part hardest to exercise for real.
    """
    result = CascadeResult(scenario.name)
    fired: list[str] = []
    started = now()

    try:
        for stage in plan(scenario):
            # An abort between stages halts the whole DAG, not just this stage.
            abort = check_aborts()
            if abort:
                result.aborted_by = abort
                result.stages.append(StageResult(stage.id, ABORTED,
                                                 note="aborted before this stage"))
                break

            # A stage whose predecessor never fired must not fire either. The
            # cascade is a chain of consequences; injecting stage 3 after stage
            # 2 was absorbed would measure something the scenario never claimed.
            if stage.after and stage.after not in fired:
                result.stages.append(StageResult(
                    stage.id, SKIPPED,
                    note=f"upstream stage {stage.after} did not fire"))
                continue

            if stage.trigger is not None:
                ok, waited, value = wait_for_trigger(
                    stage, sample_value, poll_s=poll_s, now=now, sleep=sleep)
                if not ok:
                    result.stages.append(StageResult(
                        stage.id, DID_NOT_PROPAGATE, waited_s=waited,
                        trigger_value=value,
                        note=(f"trigger never crossed {stage.trigger.comparator} "
                              f"{stage.trigger.threshold} within "
                              f"{stage.trigger.timeout_s:.0f}s (last "
                              f"{'no data' if value is None else format(value, '.4g')})"
                              " — the upstream failure was absorbed, not amplified")))
                    continue
            else:
                waited, value = 0.0, None

            if stage.observation_only:
                # Watch amplification without adding to it. Retry storms and
                # queue growth appear here, after injection stops.
                observe(stage)
                result.stages.append(StageResult(
                    stage.id, OBSERVED, fired_at_s=now() - started,
                    waited_s=waited,
                    note="observation-only stage; no fault injected"))
                continue

            engine = inject(stage)
            fired.append(stage.id)
            result.stages.append(StageResult(
                stage.id, FIRED, fired_at_s=now() - started,
                waited_s=waited, trigger_value=value, engine=engine))

            abort = check_aborts()
            if abort:
                result.aborted_by = abort
                break
    finally:
        # Runs on EVERY exit path, including an exception mid-cascade.
        result.teardown = teardown(scenario, fired, stop_stage)

    return result
