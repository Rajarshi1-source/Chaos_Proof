"""The out-of-band abort path.

Two independent abort paths is deliberate. Litmus `promProbe` with
`stopOnFailure: true` is in-band and fast — but it dies with the chaos runner.
This watchdog is out-of-band and survives a runner crash.

**A safety mechanism owned by the process that can crash is not a safety
mechanism.**

Both paths read the SAME `abort_conditions` from the hypothesis YAML, so they
cannot drift apart: one declaration, two enforcers.
"""

from dataclasses import dataclass, field

from ..hypothesis.engine import Invariant, parse_abort_conditions


@dataclass
class Watchdog:
    conditions: list[Invariant]
    tripped: bool = False
    reason: str | None = None
    evidence: dict = field(default_factory=dict)

    def check(self, sample) -> str | None:
        """Evaluate every abort condition against one sample. Returns a reason
        when the run must stop, None otherwise.

        A MISSING signal does not trip the watchdog — it is reported and the run
        continues, because an abort on absent data would make every scrape gap a
        false halt. The validity gate is what catches missing data at verdict
        time; conflating the two would make the safety plane flaky, and a flaky
        guard gets disabled.
        """
        for cond in self.conditions:
            values = sample.client if cond.source == "k6" else sample.server
            value = values.get(cond.metric)
            if value is None:
                continue
            # An abort condition describes the TRIGGER, not the healthy state:
            # `client_availability < 0.80` means "abort when availability drops
            # below 0.80". So it trips when SATISFIED. The Litmus probe uses the
            # opposite convention (it states the healthy criteria and aborts on
            # failure), which is why probes_for() inverts on the way out — one
            # declaration, two enforcers, neither guessing.
            if cond.satisfied(value):
                self.tripped = True
                self.reason = (
                    f"abort condition '{cond.name}' tripped: {cond.metric} "
                    f"= {value:.4g} {cond.comparator} {cond.threshold:g}")
                self.evidence = {
                    "condition": cond.name, "metric": cond.metric,
                    "observed": value, "comparator": cond.comparator,
                    "threshold": cond.threshold,
                    "sampled_at": sample.sampled_at,
                }
                return self.reason
        return None


def from_spec(spec: dict) -> Watchdog:
    """Every experiment ships abort_conditions — the framework refuses to run one
    that does not, rather than injecting a fault it has no way to stop."""
    hyp = spec.get("hypothesis", {})
    raw = spec.get("abort_conditions") or hyp.get("abort_conditions") or []
    if not raw:
        raise ValueError(
            f"experiment {spec.get('name')!r} declares no abort_conditions. "
            "Never ship an experiment without them: a fault you cannot stop is "
            "not an experiment, it is an outage you scheduled.")
    return Watchdog(conditions=parse_abort_conditions(raw))
