"""The contract generator (§19.2) — one experiment per clause.

This is the feature's real power: **the experiment suite is derived from
declared architecture, not from a human remembering to write a test.** Add a
dependency to a contract and its experiments appear; raise a tolerance and the
experiment that checks it moves with it.

Every generated experiment carries `provenance` back to the clause that
produced it, so a falsification points at the ARCHITECTURAL CLAIM it broke
rather than at a threshold in a YAML file nobody recognises.

Two rules inherited from the experiment-suite mandate, and neither is optional
just because a machine wrote the YAML:

  * every experiment carries `min_rps_floor` and `abort_conditions`;
  * every experiment starts ADVISORY. A generated experiment has no flakiness
    history at all, so it is the last thing that should be allowed to block a
    merge. Generation is not characterisation.
"""

from dataclasses import dataclass, field

from ..constants import FLAKINESS_WINDOW
from .model import Contract, Tolerates

# The clause dimension -> Litmus fault that exercises it.
FAULT_FOR = {
    "latency": "pod-network-latency",
    "error_rate": "pod-network-loss",
    "outage": "pod-network-loss",
}

# Dependencies that are not injectable targets in this system. Named rather than
# silently skipped: a clause about redis is still a promise, it is just one this
# framework cannot falsify, and that is exactly what UNTESTED means.
NOT_INJECTABLE = {
    "redis": "no redis deployment in the target-app namespace to target",
    "postgres": "stateful target; policy denies injecting into it "
                "(deny-stateful-targets)",
}

TARGET_NAMESPACE = "target-app"
DEFAULT_FLOOR = 90.0
FAULT_DURATION_S = 60
RECOVERY_WINDOW_S = 120


@dataclass
class Provenance:
    service: str
    contract_version: str
    clause: str
    declared: float
    absorbed_by: str | None = None

    def to_dict(self) -> dict:
        return {"service": self.service, "contract_version": self.contract_version,
                "clause": self.clause, "declared": self.declared,
                "absorbed_by": self.absorbed_by}


@dataclass
class Generated:
    name: str
    spec: dict                       # the experiment YAML body
    provenance: Provenance
    untested_reason: str | None = None

    @property
    def testable(self) -> bool:
        return self.untested_reason is None

    def to_yaml_dict(self) -> dict:
        body = dict(self.spec)
        body["provenance"] = self.provenance.to_dict()
        return {"experiment": body}


def _abort_conditions(consumer: str) -> list[dict]:
    """Never ship an experiment without abort conditions — generated ones
    included. A machine-written experiment is not a safer experiment."""
    return [
        {"name": "availability_collapse", "source": "k6",
         "metric": "client_availability", "comparator": "<", "threshold": 0.80},
        {"name": "unrelated_namespace_impact", "source": "prometheus",
         "metric": "other_namespace_5xx_rate", "comparator": ">", "threshold": 0.5},
    ]


def _base(name: str, fault: str, dependency: str, params: dict,
          description: str, invariants: list[dict], consumer: str) -> dict:
    return {
        "name": name,
        "litmus_fault": fault,
        "target": {"namespace": TARGET_NAMESPACE, "app": dependency},
        "fault_duration_s": FAULT_DURATION_S,
        "recovery_window_s": RECOVERY_WINDOW_S,
        "params": params,
        "load_profile": "profiles/steady_120rps.js",
        "min_rps_floor": DEFAULT_FLOOR,
        "generated": True,
        "gating": False,          # advisory until characterised, always
        "hypothesis": {
            "version": 1,
            "description": description,
            "invariants": invariants,
            "abort_conditions": _abort_conditions(consumer),
        },
    }


def _latency_experiment(c: Contract, tol: Tolerates) -> Generated:
    """"I tolerate 800ms from payment-service" -> inject exactly 800ms, and
    assert MY OWN published SLO still holds. The injected value comes from the
    clause, so the experiment cannot drift from the claim it validates."""
    name = f"contract_{c.service}_tolerates_{tol.dependency}_latency".replace("-", "_")
    spec = _base(
        name, FAULT_FOR["latency"], tol.dependency,
        {"NETWORK_LATENCY": str(int(tol.max_latency_ms))},
        (f"{c.service} claims it tolerates {tol.max_latency_ms}ms from "
         f"{tol.dependency}; with exactly that latency injected, its own "
         f"published P99 of {c.provides.latency_p99_ms}ms must still hold."),
        [{"name": "consumer_p99_holds", "source": "k6", "metric": "client_p99_ms",
          "comparator": "<=", "threshold": c.provides.latency_p99_ms,
          "tolerance_s": 15},
         {"name": "consumer_availability_holds", "source": "k6",
          "metric": "client_availability", "comparator": ">=",
          "threshold": c.provides.availability_slo / 100.0, "tolerance_s": 15}],
        c.service)
    return Generated(name, spec, Provenance(
        c.service, c.version, f"tolerates.{tol.dependency}.max_latency_ms",
        float(tol.max_latency_ms), tol.absorbed_by))


def _error_rate_experiment(c: Contract, tol: Tolerates) -> Generated:
    """Inject exactly the declared error rate and assert the consumer's own
    published availability holds.

    This is the clause that produces the retry-amplification finding. When the
    consumer retries, the declared upstream rate is NOT the rate its own users
    experience, and the invariant is written against the consumer's OWN SLO
    precisely so the amplification has somewhere to show up.
    """
    name = f"contract_{c.service}_tolerates_{tol.dependency}_errors".replace("-", "_")
    description = (
        f"{c.service} claims it tolerates a {tol.max_error_rate_pct}% error rate "
        f"from {tol.dependency}; with exactly that rate injected, its own "
        f"published availability of {c.provides.availability_slo}% must still hold.")

    if tol.retry_amplification:
        amp = tol.retry_amplification
        effective = amp.effective_error_rate(tol.max_error_rate_pct)
        description += (
            f" DECLARED AMPLIFICATION: {amp.attempts} retry attempts mean "
            f"{effective:.1f}% of logical calls see at least one failed attempt, "
            f"each costing up to a {amp.wait_ms}ms wait plus another timeout "
            f"budget. The clause is a claim about the DEPENDENCY's rate; this "
            f"experiment measures the CONSUMER's.")

    spec = _base(
        name, FAULT_FOR["error_rate"], tol.dependency,
        {"NETWORK_PACKET_LOSS_PERCENTAGE": str(int(tol.max_error_rate_pct))},
        description,
        [{"name": "consumer_availability_holds", "source": "k6",
          "metric": "client_availability", "comparator": ">=",
          "threshold": c.provides.availability_slo / 100.0, "tolerance_s": 15},
         {"name": "consumer_p99_holds", "source": "k6", "metric": "client_p99_ms",
          "comparator": "<=", "threshold": c.provides.latency_p99_ms,
          "tolerance_s": 15}],
        c.service)
    return Generated(name, spec, Provenance(
        c.service, c.version, f"tolerates.{tol.dependency}.max_error_rate_pct",
        float(tol.max_error_rate_pct), tol.absorbed_by))


def _outage_experiment(c: Contract, tol: Tolerates) -> Generated:
    """A full outage for the declared duration. 100% loss for max_outage_seconds
    — capped at the fault duration the platform will actually run, and the cap
    is REPORTED rather than silently applied, because a 600s claim validated by
    a 60s outage is not validated."""
    name = f"contract_{c.service}_tolerates_{tol.dependency}_outage".replace("-", "_")
    declared = int(tol.max_outage_seconds)
    duration = min(declared, FAULT_DURATION_S)
    description = (
        f"{c.service} claims it tolerates a {declared}s outage of "
        f"{tol.dependency}; its own availability of {c.provides.availability_slo}% "
        f"must hold throughout and recover after.")
    if duration < declared:
        description += (
            f" NOTE: injected for {duration}s, not {declared}s — the platform's "
            f"per-experiment fault duration. The clause is validated only to "
            f"{duration}s and the report says so rather than implying full coverage.")

    spec = _base(
        name, FAULT_FOR["outage"], tol.dependency,
        {"NETWORK_PACKET_LOSS_PERCENTAGE": "100"},
        description,
        [{"name": "consumer_availability_holds", "source": "k6",
          "metric": "client_availability", "comparator": ">=",
          "threshold": c.provides.availability_slo / 100.0, "tolerance_s": 30}],
        c.service)
    spec["fault_duration_s"] = duration
    g = Generated(name, spec, Provenance(
        c.service, c.version, f"tolerates.{tol.dependency}.max_outage_seconds",
        float(declared), tol.absorbed_by))
    if duration < declared:
        g.spec["partial_coverage_s"] = duration
    return g


BUILDERS = {"latency": _latency_experiment,
            "error_rate": _error_rate_experiment,
            "outage": _outage_experiment}


def generate(contract: Contract) -> list[Generated]:
    """One experiment per testable clause; an UNTESTED marker for the rest."""
    out: list[Generated] = []
    for tol in contract.tolerates:
        blocked = tol.untestable_reason or NOT_INJECTABLE.get(tol.dependency)
        for clause_id, dimension, declared in tol.clauses():
            prov = Provenance(contract.service, contract.version, clause_id,
                              declared, tol.absorbed_by)
            if blocked:
                out.append(Generated(
                    name=f"contract_{contract.service}_{clause_id}".replace("-", "_"),
                    spec={}, provenance=prov, untested_reason=blocked))
                continue
            out.append(BUILDERS[dimension](contract, tol))

    # does_not_inflict clauses are consumer-side promises. They are not faults —
    # nothing is injected to check them — but they ARE measurable during any
    # experiment, so they are emitted as observation-only clauses rather than
    # dropped. Retry amplification is caught here.
    for d in contract.does_not_inflict:
        prov = Provenance(contract.service, contract.version, d.clause_id,
                          d.threshold if d.threshold is not None else 0.0)
        out.append(Generated(
            name=f"inflict_{contract.service}_{d.index}".replace("-", "_"),
            spec={"observation_only": True, "clause": d.clause,
                  "metric": d.metric, "comparator": d.comparator,
                  "threshold": d.threshold},
            provenance=prov,
            untested_reason=None if d.testable else
            "prose-only clause with no metric — a real promise, but not a "
            "measurable one"))
    return out


def coverage(generated: list[Generated]) -> tuple[int, int, float]:
    """(tested, total, ratio) — the `chaosproof_contract_clause_coverage_ratio`
    numerator and denominator. UNTESTED is REPORTED, so a gap is visible rather
    than invisible; a contract with half its clauses unfalsifiable should look
    half-covered, not fully green."""
    total = len(generated)
    tested = sum(1 for g in generated if g.testable)
    return tested, total, (tested / total if total else 0.0)


def gating_note() -> str:
    return (f"generated experiments are ADVISORY: they have no flakiness history, "
            f"and gating status is earned over {FLAKINESS_WINDOW} clean runs, "
            f"never granted at generation time")
