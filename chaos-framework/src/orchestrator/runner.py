"""The experiment runner — the six-stage skeleton from §14.4. Implemented so far:
stage 0 (load presence — the plane must already be up), 3 (inject), 4 (sample,
dual-source, fixed deadline), 5 (hypothesis evaluation), 6 (persist). Pre-flight
(stage 1) and the watchdog around stage 4 arrive with the safety plane (Phase 5).

What is structural rather than remembered: the validity gate runs inside
evaluate() on every path, and engine cleanup runs on every exit path."""

import hashlib
import pathlib
import subprocess
import time

import yaml

from .. import queries
from ..db import persist_run
from ..hypothesis.engine import HypothesisVerdict, evaluate, parse_invariants
from ..integrations.litmus import LitmusClient
from ..integrations.prometheus import PrometheusClient
from ..integrations.alertmanager import AlertmanagerClient
from ..measurement.sampler import DualSourceSampler
from ..measurement.validity import LoadFacts
from ..scoring import scorer
from ..validators import checks as V

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


class PreflightSkip(Exception):
    """Pre-flight refused to run. SKIPPED is a recorded outcome, not a silent
    no-op — 'the framework correctly refused' proves the guardrails are real."""


def _git_sha() -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else None
    except OSError:
        return None


def hpa_exists(namespace: str, name: str) -> bool:
    """Pre-flight: does an HPA own this workload? The CPU-spike hypothesis only
    makes sense when one does — asserting an autoscaler reaction with no
    autoscaler installed would falsify a healthy system every run."""
    proc = subprocess.run(
        ["kubectl", "get", "hpa", name, "-n", namespace, "--ignore-not-found",
         "-o", "name"], capture_output=True, text=True, timeout=30)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def run(spec_path: pathlib.Path, prom_url: str, testrun: str, alertmanager_url: str,
        on_tick=None):
    """Returns (engine, HypothesisVerdict, execution_id, facts, chaos, samples,
    check_list, ScoreResult)."""
    spec = yaml.safe_load(spec_path.read_text())["experiment"]
    prom = PrometheusClient(prom_url)
    litmus = LitmusClient()
    alerts = AlertmanagerClient(alertmanager_url)

    ns = spec["target"]["namespace"]
    app = spec["target"]["app"]
    fault_s = int(spec["fault_duration_s"])
    recovery_s = int(spec["recovery_window_s"])
    floor = float(spec["min_rps_floor"])
    invariants = parse_invariants(spec["hypothesis"]["invariants"])

    script = REPO_ROOT / spec["load_profile"]
    script_sha = hashlib.sha256(script.read_bytes()).hexdigest()

    sampler = DualSourceSampler(prom, queries.client_queries(testrun),
                                queries.server_queries(target=app))

    # STAGE 1 (partial) — pre-flight. The full gate lands in Phase 5; the HPA
    # precondition is here because experiment 5's hypothesis depends on it.
    if spec.get("requires_hpa") and not hpa_exists(ns, app):
        raise PreflightSkip(
            f"{spec['name']} assumes an HPA owns {app}; none found — the "
            "hypothesis asserts an autoscaler reaction that cannot happen")

    # STAGE 3 — inject (pre-flight slots in front of this in Phase 5)
    engine = litmus.apply_fault(spec["litmus_fault"], ns, app, fault_s,
                                spec.get("params"))
    fault_injected_at = time.time()

    # STAGE 4 — dual-source sampling at fixed cadence (watchdog wraps this in Phase 5)
    try:
        samples = sampler.stream(duration_s=fault_s + recovery_s, on_tick=on_tick)
    finally:
        litmus.delete_engine(ns, engine)          # cleanup on EVERY exit path

    finished_at = time.time()
    chaos = litmus.chaos_result(ns, engine, spec["litmus_fault"])

    # STAGE 5 — hypothesis evaluation (validity gate runs first, inside)
    facts = LoadFacts(
        achieved_rps=samples.mean_client("client_rps"),
        dropped_iterations=samples.windowed_increase("dropped_iterations"),
        coverage=samples.coverage(),
    )
    verdict = evaluate(invariants, samples, facts, floor)

    # STAGE 5b — the four validators, as sub-evidence feeding the score.
    expected_alerts = spec.get("expected_alerts") or []
    fired = {name: alerts.first_fired_at(name, fault_injected_at)
             for name in expected_alerts}
    check_list = [
        V.slo_recovery(samples),
        V.alert_validation(expected_alerts, fired, fault_injected_at),
        V.resilience_pattern(samples, spec.get("pattern_metric"),
                             spec.get("pattern_name")),
        V.recovery_completeness(samples),
    ]
    # An unmeasurable window poisons the checks too: INVALID never scores.
    if verdict.verdict == "invalid":
        for c in check_list:
            c.outcome, c.score = "invalid", None
    score = scorer.calculate(check_list)

    # STAGE 6 — persist everything in one transaction
    execution_id = persist_run(
        spec=spec,
        verdict=verdict.verdict,
        reason=verdict.reason,
        fault_injected_at=fault_injected_at,
        finished_at=finished_at,
        load_facts=facts,
        samples=samples,
        script_sha256=script_sha,
        git_sha=_git_sha(),
        invariant_outcomes=verdict.outcomes,
        check_list=check_list,
        score=score,
    )

    return engine, verdict, execution_id, facts, chaos, samples, check_list, score
