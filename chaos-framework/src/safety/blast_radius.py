"""Blast radius, computed BEFORE injection — from the current load rate, never
a guess. If you cannot say how many requests are at risk, you are not entitled
to put them at risk.

The score() arithmetic is deliberately simple and explainable. A reviewer must
be able to evaluate it mentally; a clever weighting nobody trusts is worse than
a crude one everybody can check.
"""

import json
import subprocess
from dataclasses import asdict, dataclass

# Availability SLO 99.5% over 30 days. Derivable on a whiteboard:
#   total budget = 0.005 x 30d x 86400 s/d = 12,960 error-seconds
AVAILABILITY_SLO = 0.995
BUDGET_WINDOW_DAYS = 30
TOTAL_BUDGET_ERROR_SECONDS = (1 - AVAILABILITY_SLO) * BUDGET_WINDOW_DAYS * 86400

# Historical p95 impact per experiment type, until enough runs exist to derive it.
# degraded_error_rate: fraction of requests failing while the fault bites.
# impact_seconds: how long that lasts — NOT the fault duration, because impact
# starts after endpoint removal and often ends before the fault does.
IMPACT_MODEL: dict[str, tuple[float, float]] = {
    "pod-delete":           (0.04, 45.0),
    "pod-network-latency":  (0.00, 60.0),   # latency degrades, does not fail
    "pod-network-loss":     (0.05, 60.0),
    "disk-fill":            (0.02, 60.0),
    "pod-cpu-hog":          (0.01, 60.0),
    "container-kill":       (0.03, 30.0),
}


@dataclass(frozen=True)
class BlastRadius:
    affected_pods: int
    affected_replica_fraction: float      # affected / total for the target workload
    affected_namespaces: list[str]
    requests_at_risk_per_min: float       # from the CURRENT load rate, not a guess
    max_recovery_s: float                 # historical p95 for this experiment type
    error_budget_burn_pct: float          # share of the 30-day budget this could consume
    user_facing: bool

    def score(self) -> int:
        """Deliberately simple and explainable — a reviewer can evaluate it mentally."""
        return (self.affected_pods
                + 10 * len(self.affected_namespaces)
                + (25 if self.user_facing else 0)
                + int(50 * self.affected_replica_fraction))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = self.score()
        return d


def _replica_count(namespace: str, deployment: str) -> int:
    proc = subprocess.run(
        ["kubectl", "get", "deployment", deployment, "-n", namespace,
         "-o", "jsonpath={.status.replicas}"],
        capture_output=True, text=True, timeout=30)
    try:
        return int(proc.stdout.strip() or 0)
    except ValueError:
        return 0


def compute(spec: dict, current_rps: float) -> BlastRadius:
    ns = spec["target"]["namespace"]
    app = spec["target"]["app"]
    fault = spec["litmus_fault"]

    total_replicas = _replica_count(ns, app)
    affected_pct = float((spec.get("params") or {}).get("PODS_AFFECTED_PERC", 100))
    affected_pods = max(1, int(round(total_replicas * affected_pct / 100.0))) if total_replicas else 1
    fraction = (affected_pods / total_replicas) if total_replicas else 1.0

    error_rate, impact_s = IMPACT_MODEL.get(fault, (0.05, 60.0))

    # The arithmetic that makes chaos an easy sell instead of a scary one:
    #   budget consumed = error_rate x impact_seconds
    #   share of monthly = consumed / 12,960 error-seconds
    consumed_error_seconds = error_rate * impact_s
    burn_pct = consumed_error_seconds / TOTAL_BUDGET_ERROR_SECONDS * 100.0

    return BlastRadius(
        affected_pods=affected_pods,
        affected_replica_fraction=round(fraction, 3),
        affected_namespaces=[ns],
        requests_at_risk_per_min=round(current_rps * 60.0 * fraction, 1),
        max_recovery_s=impact_s,
        error_budget_burn_pct=round(burn_pct, 4),
        user_facing=True,                 # every target here sits behind order-api
    )


def explain(radius: BlastRadius) -> str:
    return json.dumps(radius.to_dict(), sort_keys=True)
