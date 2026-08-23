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
from ..measurement.sampler import DualSourceSampler
from ..measurement.validity import LoadFacts

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _git_sha() -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else None
    except OSError:
        return None


def run(spec_path: pathlib.Path, prom_url: str, testrun: str,
        on_tick=None) -> tuple[str, HypothesisVerdict, int]:
    """Returns (verdict_string, HypothesisVerdict, execution_id)."""
    spec = yaml.safe_load(spec_path.read_text())["experiment"]
    prom = PrometheusClient(prom_url)
    litmus = LitmusClient()

    ns = spec["target"]["namespace"]
    app = spec["target"]["app"]
    fault_s = int(spec["fault_duration_s"])
    recovery_s = int(spec["recovery_window_s"])
    floor = float(spec["min_rps_floor"])
    invariants = parse_invariants(spec["hypothesis"]["invariants"])

    script = REPO_ROOT / spec["load_profile"]
    script_sha = hashlib.sha256(script.read_bytes()).hexdigest()

    sampler = DualSourceSampler(prom, queries.client_queries(testrun),
                                queries.server_queries())

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
    )

    return engine, verdict, execution_id, facts, chaos, samples
