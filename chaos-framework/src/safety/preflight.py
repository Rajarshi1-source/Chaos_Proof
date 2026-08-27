"""Pre-flight — runs BEFORE the Litmus CRD is applied. Never inject before this
returns `proceed`.

Order matters: cheapest and most decisive checks first, so a run that was never
going to be allowed spends the least time discovering it.

SKIPPED and DENIED are RECORDED OUTCOMES with reasons, not silent no-ops. A
framework that quietly does nothing teaches you nothing; one that refuses out
loud proves its guardrails are load-bearing.
"""

from dataclasses import dataclass, field

from ..constants import MAX_BUDGET_BURN_PCT, STEADY_STATE_WINDOW_S
from . import blast_radius, budget_gate, policy


@dataclass
class PreflightVerdict:
    ok: bool
    verdict: str | None = None          # skipped | denied
    reason: str | None = None
    radius: blast_radius.BlastRadius | None = None
    evidence: dict = field(default_factory=dict)

    @classmethod
    def skip(cls, reason: str, **ev) -> "PreflightVerdict":
        return cls(False, "skipped", reason, evidence=ev)

    @classmethod
    def deny(cls, reason: str, **ev) -> "PreflightVerdict":
        return cls(False, "denied", reason, evidence=ev)

    @classmethod
    def proceed(cls, radius, **ev) -> "PreflightVerdict":
        return cls(True, None, None, radius, evidence=ev)


def run(spec: dict, sampler, require_override: bool = False) -> PreflightVerdict:
    ns = spec["target"]["namespace"]
    floor = float(spec["min_rps_floor"])

    # 1. Is the system already unhealthy? Injecting into a sick cluster is not
    #    an experiment — you cannot attribute the damage.
    client, server = sampler.snapshot()
    availability = client.get("client_availability")
    if availability is None:
        return PreflightVerdict.skip(
            "steady state cannot be established: no client availability samples "
            f"in the last {STEADY_STATE_WINDOW_S}s — the measurement plane is not ready")
    if availability < 0.99:
        return PreflightVerdict.skip(
            f"steady state not established before injection "
            f"(client availability {availability:.4f} < 0.99)",
            client_availability=availability)

    # 2. Is there enough traffic to measure anything? Below the floor the verdict
    #    would be INVALID, and spending blast radius for no evidence is a bad trade.
    current_rps = client.get("client_rps")
    if current_rps is None or current_rps < floor:
        return PreflightVerdict.skip(
            f"below SLI validity floor — achieved "
            f"{0.0 if current_rps is None else current_rps:.0f} rps against a floor of "
            f"{floor:.0f}; start the load plane first",
            current_rps=current_rps, floor=floor)

    # 3. Would the blast radius exceed budget?
    radius = blast_radius.compute(spec, current_rps)
    if radius.error_budget_burn_pct > MAX_BUDGET_BURN_PCT:
        return PreflightVerdict.deny(
            f"projected to burn {radius.error_budget_burn_pct:.2f}% of the error "
            f"budget, above the {MAX_BUDGET_BURN_PCT}% ceiling",
            blast_radius=radius.to_dict())

    # 4. Is the budget already spent, or is chaos frozen?
    gate = budget_gate.gate(ns)
    if gate.state != "open":
        return PreflightVerdict.deny(f"budget gate {gate.state}: {gate.reason}",
                                     blast_radius=radius.to_dict())

    # 5. Declarative policy — first DENY wins, then REQUIRE_OVERRIDE, else ALLOW.
    decision = policy.evaluate({
        "experiment": {
            "target_namespace": ns,
            "target_kind": "Deployment",
            "target_name": spec["target"]["app"],
            "mode": spec.get("mode", "standard"),
            "min_rps_floor": floor,
        },
        "target": {"replicas": radius.affected_pods / max(radius.affected_replica_fraction, 1e-9)},
        "load": {"current_rps": current_rps},
        "blast": {"score": radius.score(),
                  "error_budget_burn_pct": radius.error_budget_burn_pct},
        "budget": {"gate_state": gate.state},
    })
    if decision.effect == "deny":
        return PreflightVerdict.deny(
            f"policy rule {decision.rule_id}: {decision.message}",
            blast_radius=radius.to_dict(), policy_rule=decision.rule_id)
    if decision.effect == "require_override" and not require_override:
        return PreflightVerdict.deny(
            f"policy rule {decision.rule_id} requires an explicit override: "
            f"{decision.message}",
            blast_radius=radius.to_dict(), policy_rule=decision.rule_id)

    return PreflightVerdict.proceed(
        radius,
        client_availability=availability,
        current_rps=current_rps,
        budget_gate=gate.state,
        policy=decision.effect)
