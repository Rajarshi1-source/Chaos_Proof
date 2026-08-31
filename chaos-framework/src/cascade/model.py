"""Cascade scenario model (§18.2) — a DAG of faults, not a list.

Every experiment elsewhere in this project injects ONE fault. Real outages are
cascades: a cache eviction raises database load, which raises latency, which
exhausts a connection pool, which times out a caller, which retries, which
amplifies the load. A flat experiment cannot express that. A DAG with temporal
AND CONDITIONAL triggers can.

Two design decisions carry the feature:

  CONDITIONAL TRIGGERS, NOT ONLY DELAYS. `after: X, trigger: <promql crosses
  threshold>` means the second fault fires when the cascade ACTUALLY
  PROPAGATES, not on a stopwatch. And when the trigger never trips within
  `timeout_s`, that is not a failed run — it is the single most valuable output
  this feature produces: "the cascade did not propagate; the stale-cache
  fallback absorbed the outage completely." Provable rather than assumed, and a
  delay-only DAG cannot produce it.

  A STAGE WITH `fault: none`. Retry storms and queue growth appear AFTER
  injection stops. A fault-then-immediately-validate loop misses them entirely,
  so an observation-only stage is a first-class stage type rather than a gap
  between two real ones.
"""

import pathlib
from dataclasses import dataclass, field

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO_ROOT / "scenarios"

NO_FAULT = "none"


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Trigger:
    """A conditional gate on a stage. PromQL, a comparator, and a deadline."""
    promql: str
    comparator: str
    threshold: float
    timeout_s: float

    def satisfied(self, value: float | None) -> bool:
        """A missing series is NEVER a satisfied trigger.

        The same rule as the hypothesis engine's empty-series check, and it
        matters more here: firing a second fault because a query returned
        nothing would inject into a cascade nobody has evidence is happening.
        """
        if value is None:
            return False
        match self.comparator:
            case ">":  return value > self.threshold
            case ">=": return value >= self.threshold
            case "<":  return value < self.threshold
            case "<=": return value <= self.threshold
        raise ScenarioError(f"unknown comparator {self.comparator!r}")


@dataclass(frozen=True)
class Stage:
    id: str
    fault: str
    after: str | None = None
    at_s: float = 0.0
    duration_s: float | None = None
    trigger: Trigger | None = None
    target: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    @property
    def observation_only(self) -> bool:
        return self.fault == NO_FAULT


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    stages: list[Stage]
    hypothesis: dict
    abort_conditions: list[dict]
    source_path: str | None = None

    def stage(self, stage_id: str) -> Stage:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise ScenarioError(f"no stage {stage_id!r}")

    def order(self) -> list[Stage]:
        """Dependency order. The DAG is small and linear-ish by construction, so
        this is a topological sort with a cycle check rather than anything
        clever — but the cycle check is not optional: a scenario whose stages
        wait on each other would hang holding an injected fault."""
        resolved: list[Stage] = []
        seen: set[str] = set()
        remaining = list(self.stages)

        while remaining:
            progressed = False
            for stage in list(remaining):
                if stage.after is None or stage.after in seen:
                    resolved.append(stage)
                    seen.add(stage.id)
                    remaining.remove(stage)
                    progressed = True
            if not progressed:
                stuck = ", ".join(s.id for s in remaining)
                raise ScenarioError(
                    f"cycle or missing dependency among stages: {stuck}. A "
                    "scenario that cannot be ordered would hang while holding "
                    "an injected fault.")
        return resolved

    def teardown_order(self) -> list[Stage]:
        """REVERSE dependency order.

        Cleanup runs backwards for the same reason a cascade builds forwards:
        stage 2's fault was injected on top of stage 1's conditions, so undoing
        stage 1 first leaves stage 2's fault applied to a system that is no
        longer in the state it was injected into.
        """
        return list(reversed(self.order()))


def parse(raw: dict, source_path: str | None = None) -> Scenario:
    body = raw.get("scenario") or raw
    for required in ("name", "stages", "hypothesis"):
        if required not in body:
            raise ScenarioError(f"scenario is missing {required!r}")

    aborts = body.get("abort_conditions") or []
    if not aborts:
        # A DAG multiplies blast radius, so this is stricter than it looks: a
        # cascade simulator without an abort mechanism is the one feature in
        # this plan that could genuinely take down a cluster.
        raise ScenarioError(
            f"{body['name']}: no abort_conditions. A cascade multiplies blast "
            "radius and its abort path is the non-trivial one — never ship a "
            "scenario without them.")

    stages = []
    for entry in body["stages"]:
        trigger = None
        if entry.get("trigger"):
            t = entry["trigger"]
            trigger = Trigger(promql=t["promql"], comparator=t["comparator"],
                              threshold=float(t["threshold"]),
                              timeout_s=float(t["timeout_s"]))
        stages.append(Stage(
            id=entry["id"], fault=entry.get("fault") or NO_FAULT,
            after=entry.get("after"),
            at_s=_seconds(entry.get("at", 0)),
            duration_s=(_seconds(entry["duration"]) if entry.get("duration") else None),
            trigger=trigger, target=entry.get("target") or {},
            params=entry.get("params") or {}))

    scenario = Scenario(body["name"], body.get("description", ""), stages,
                        body["hypothesis"], aborts, source_path)
    scenario.order()          # fail at parse time, not mid-injection
    return scenario


def _seconds(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("ms"):
        return float(text[:-2]) / 1000.0
    if text.endswith("s"):
        return float(text[:-1])
    if text.endswith("m"):
        return float(text[:-1]) * 60.0
    return float(text)


def load(path: pathlib.Path) -> Scenario:
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))


def load_all(root: pathlib.Path | None = None) -> list[Scenario]:
    root = root or SCENARIO_DIR
    return [load(p) for p in sorted(root.glob("*.yaml"))]
