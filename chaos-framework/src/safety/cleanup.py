"""Cleanup as a saga: registered BEFORE injection, executed on EVERY exit path —
success, falsification, abort, and crash.

Two rules carry the weight:

  - `restore_feature_flags` is the highest-stakes step. A cluster left with its
    circuit breakers disabled after a counterfactual run is the worst possible
    outcome of this project — worse than any fault, because it is silent and
    permanent until someone notices.

  - `verify_steady_state` runs LAST, and cleanup is not done until steady state
    is re-established. If it cannot be, that ESCALATES rather than closing
    quietly. "I tidied up" is not the same claim as "the system is healthy".

Every step records its own outcome. A saga that reports success because it never
reached step 4 is worse than one that admits it stopped there.
"""

import time
from dataclasses import dataclass, field

from ..constants import STEADY_STATE_WINDOW_S


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class CleanupLog:
    steps: list[StepResult] = field(default_factory=list)
    escalated: bool = False
    escalation_reason: str | None = None

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps) and not self.escalated

    def to_dict(self) -> dict:
        return {
            "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail,
                       "duration_s": round(s.duration_s, 2)} for s in self.steps],
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
        }


class CleanupSaga:
    """Steps are registered before injection so a crash mid-fault still has a
    complete list to run — the registration, not the happy path, is what makes
    cleanup reliable."""

    def __init__(self):
        self._steps: list[tuple[str, callable]] = []

    def register(self, name: str, fn) -> "CleanupSaga":
        self._steps.append((name, fn))
        return self

    def run(self) -> CleanupLog:
        log = CleanupLog()
        for name, fn in self._steps:
            t0 = time.monotonic()
            try:
                detail = fn() or ""
                log.steps.append(StepResult(name, True, str(detail), time.monotonic() - t0))
            except Exception as e:
                # Keep going: a failed step must not strand the steps after it.
                # Leaving a ChaosEngine behind because flag restore threw would
                # turn one problem into two.
                log.steps.append(
                    StepResult(name, False, f"{type(e).__name__}: {e}",
                               time.monotonic() - t0))
        return log


def verify_steady_state(sampler, threshold: float = 0.99,
                        timeout_s: float = 240.0, poll_s: float = 10.0) -> str:
    """The last step, and the one that matters.

    This WAITS for steady state rather than sampling once. An instantaneous read
    taken the moment a fault is halted still contains the damage: the client SLI
    is a rolling average, so it reports the outage for as long as its own window
    is wide. Checking once therefore fails every abort by construction, which
    would make the most important cleanup step permanently red and teach everyone
    to ignore it.

    Cleanup is not done until steady state is BACK. If it does not come back
    inside the deadline, this raises — which marks the saga escalated rather than
    quietly successful. A cleanup that gives up silently is worse than one that
    never ran, because it also reports that everything is fine.
    """
    deadline = time.monotonic() + timeout_s
    last: float | None = None
    while time.monotonic() < deadline:
        client, _ = sampler.snapshot()
        last = client.get("client_availability")
        if last is not None and last >= threshold:
            waited = timeout_s - (deadline - time.monotonic())
            return (f"client availability {last:.4f} >= {threshold} "
                    f"(recovered {waited:.0f}s after cleanup)")
        time.sleep(poll_s)

    if last is None:
        raise RuntimeError(
            f"steady state UNVERIFIABLE after cleanup: no client availability "
            f"samples within {timeout_s:.0f}s")
    raise RuntimeError(
        f"steady state NOT re-established within {timeout_s:.0f}s of cleanup: "
        f"client availability {last:.4f} < {threshold}")
