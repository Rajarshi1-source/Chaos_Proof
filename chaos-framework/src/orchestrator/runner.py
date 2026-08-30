"""The experiment runner — the six-stage skeleton from §14.4, now with the
safety plane in place.

What is STRUCTURAL here rather than remembered, and why each ordering matters:

  STAGE 0  load plane up          — no traffic means no evidence
  STAGE 1  pre-flight             — NEVER inject before this returns proceed
  STAGE 2  baseline
  STAGE 3  inject, probes armed   — the in-band abort travels with the fault
  STAGE 4  sample, watchdog armed — the out-of-band abort survives a runner crash
  STAGE 5  evaluate
  STAGE 6  score, persist, CLEANUP ON EVERY EXIT PATH

A runner that omits one of these cannot be made safe by discipline in the
individual experiments — which is exactly why safety lives here and not in each
experiment class.
"""

import hashlib
import pathlib
import subprocess
import time

import yaml

from .. import queries
from ..db import persist_run
from ..hypothesis.engine import evaluate, parse_invariants
from ..integrations.alertmanager import AlertmanagerClient
from ..integrations.litmus import LitmusClient
from ..integrations.prometheus import PrometheusClient
from ..measurement.sampler import DualSourceSampler
from ..measurement.validity import LoadFacts
from ..safety import blast_radius as blast_mod
from ..safety import cleanup as cleanup_mod
from ..safety import flags as flags_mod
from ..safety import preflight as preflight_mod
from ..safety import watchdog as watchdog_mod
from ..safety.lock import FencedLock, LockLost
from ..scoring import scorer
from ..validators import checks as V

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
IN_CLUSTER_PROM = "http://prometheus-operated.monitoring:9090"


class PreflightSkip(Exception):
    """Pre-flight refused. SKIPPED/DENIED are recorded outcomes, not silent
    no-ops — 'the framework correctly refused' proves the guardrails are real."""

    def __init__(self, verdict: str, reason: str, evidence: dict | None = None):
        super().__init__(reason)
        self.verdict = verdict
        self.reason = reason
        self.evidence = evidence or {}


def _git_sha() -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else None
    except OSError:
        return None


def hpa_exists(namespace: str, name: str) -> bool:
    proc = subprocess.run(
        ["kubectl", "get", "hpa", name, "-n", namespace, "--ignore-not-found",
         "-o", "name"], capture_output=True, text=True, timeout=30)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def run(spec_path: pathlib.Path, prom_url: str, testrun: str, alertmanager_url: str,
        on_tick=None, require_override: bool = False):
    """Returns a dict of everything the CLI needs to render, so adding a field
    never changes a tuple's shape at three call sites."""
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
                                queries.server_queries(target=app, target_ns=ns))

    # Every experiment ships abort_conditions — build the watchdog FIRST so an
    # experiment without them fails here, before anything is injected.
    watchdog = watchdog_mod.from_spec(spec)

    # Serial execution, fenced. Two overlapping experiments make every result
    # unattributable — that invalidates the dataset, not just one run.
    fence = f"{spec['name']}-{int(time.time())}"
    lock = FencedLock(namespace=ns, fence=fence)
    if not lock.acquire():
        raise PreflightSkip("skipped",
                            f"another experiment holds chaos:lock:{ns} — experiments "
                            "run serially so their effects stay attributable")

    saga = cleanup_mod.CleanupSaga()
    engine: str | None = None
    aborted_by: str | None = None

    try:
        # STAGE 1 — pre-flight. NEVER inject before this returns proceed.
        if spec.get("requires_hpa") and not hpa_exists(ns, app):
            raise PreflightSkip("skipped",
                                f"{spec['name']} assumes an HPA owns {app}; none found "
                                "— the hypothesis asserts an autoscaler reaction that "
                                "cannot happen")

        pre = preflight_mod.run(spec, sampler, require_override=require_override)
        if not pre.ok:
            raise PreflightSkip(pre.verdict, pre.reason, pre.evidence)

        # STAGE 2/3 — inject, with the in-band abort probes armed on the engine.
        lock.verify()                       # the fence, before a mutating call
        probes = litmus.probes_for(spec, testrun, IN_CLUSTER_PROM, target_ns=ns)
        engine = litmus.apply_fault(spec["litmus_fault"], ns, app, fault_s,
                                    spec.get("params"), probes=probes)
        fault_injected_at = time.time()

        # Engine cleanup is registered on the VERY NEXT LINE after injection.
        # An earlier draft registered it after the flag-plane block, and when a
        # flag snapshot threw, the fault was left running with NOTHING registered
        # to stop it — an orphaned ChaosEngine found in the cluster afterwards.
        # Nothing that can raise may sit between injecting a fault and being able
        # to undo it. That is the whole reason the rule is "register before the
        # fault can do anything" rather than "register somewhere near the top".
        saga.register("stop_chaosengine", lambda: (litmus.stop_engine(ns, engine), "stopped")[1])
        saga.register("delete_chaosengine", lambda: (litmus.delete_engine(ns, engine), "deleted")[1])

        # Counterfactual arm: the flag plane lives on the service that OWNS the
        # pattern (the consumer), which is NOT the fault target. Disabling
        # `paymentService.fallback` means changing order-api while partitioning
        # payment-service.
        disable = spec.get("disable_patterns") or []
        flag_app = spec.get("flag_target", "order-api")
        if disable:
            flag_snapshot = flags_mod.snapshot(ns, flag_app)
            saga.register("restore_feature_flags",
                          lambda: flags_mod.restore(ns, flag_app, flag_snapshot))
            flags_mod.apply(ns, flag_app, disable)

        saga.register("verify_steady_state",
                      # 420s: a partition restarts JVMs, and a Boot 4 cold start
                      # plus endpoint reconvergence outlasts a 240s deadline. Sized
                      # from an observed escalation, not from a round number.
                      lambda: cleanup_mod.verify_steady_state(sampler, timeout_s=420))

        # STAGE 4 — sample both sources; the watchdog wraps the whole window.
        def tick(sample):
            if on_tick:
                on_tick(sample)
            if not watchdog.tripped and watchdog.check(sample):
                # Halt the fault NOW rather than at its own schedule. This is the
                # difference between a guard and a report.
                lock.verify()
                litmus.stop_engine(ns, engine)

        samples = sampler.stream(duration_s=fault_s + recovery_s,
                                 on_tick=tick, stop_when=lambda: watchdog.tripped)
        if watchdog.tripped:
            aborted_by = "python_watchdog"

        finished_at = time.time()
        chaos = litmus.chaos_result(ns, engine, spec["litmus_fault"])

        # STAGE 5 — hypothesis evaluation (validity gate runs first, inside).
        facts = LoadFacts(
            achieved_rps=samples.mean_client("client_rps"),
            dropped_iterations=samples.windowed_increase("dropped_iterations"),
            coverage=samples.coverage(),
        )
        verdict = evaluate(invariants, samples, facts, floor)

        # An ABORTED run is not scoreable: the fault was cut short, so the
        # hypothesis was never given its stated conditions to hold under.
        if aborted_by:
            verdict.verdict = "aborted"
            verdict.reason = watchdog.reason

        # Nor is a run whose IN-BAND GUARD NEVER RAN (defect D-C). A promProbe
        # that fails to execute still counts as a probe failure, and every one of
        # them carries stopOnFailure: true — so Litmus halts the fault seconds
        # after injection and the ChaosEngine reaches `Stopped`, which is exactly
        # what a successful abort looks like from the outside. The window then
        # gets evaluated as though the fault had run for its declared duration.
        #
        # It falsifies rather than passes, which sounds like the safe direction
        # until you read the verdict: the hypothesis was falsified by the
        # INJECTOR failing, and the report names the system. That is worse than a
        # false pass, because it is a confident wrong answer with evidence
        # attached. INVALID is the honest verdict — nothing was measured — and it
        # is checked after the abort branch so a genuine abort keeps its own name.
        elif chaos.get("probe_errors"):
            verdict.verdict = "invalid"
            verdict.reason = ("in-band abort probes did not execute, so the fault ran "
                              "without its guard and was halted early: "
                              + "; ".join(chaos["probe_errors"]))

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
        # The same coupling the replay eval applies, from one implementation:
        # an unscoreable verdict poisons its checks too, so the bundle never
        # carries four green rows describing a run that measured nothing.
        check_list = scorer.checks_for_verdict(check_list, verdict.verdict)
        score = scorer.calculate(check_list)

        # STAGE 6 — cleanup on the success path too, THEN persist.
        cleanup_log = saga.run()

        execution_id = persist_run(
            spec=spec, verdict=verdict.verdict, reason=verdict.reason,
            fault_injected_at=fault_injected_at, finished_at=finished_at,
            load_facts=facts, samples=samples, script_sha256=script_sha,
            git_sha=_git_sha(), invariant_outcomes=verdict.outcomes,
            check_list=check_list, score=score,
            blast_radius=pre.radius.to_dict() if pre.radius else None,
            chaos_engine=engine,
            preflight={"decision": "proceed", "evidence": pre.evidence},
            cleanup_log=cleanup_log.to_dict(),
            abort=({"path": aborted_by, "condition": watchdog.evidence.get("condition"),
                    "observed": watchdog.evidence.get("observed"),
                    "threshold": watchdog.evidence.get("threshold"),
                    "comparator": watchdog.evidence.get("comparator"),
                    "evidence": watchdog.evidence} if aborted_by else None),
        )

        return {"engine": engine, "verdict": verdict, "execution_id": execution_id,
                "facts": facts, "chaos": chaos, "samples": samples,
                "checks": check_list, "score": score, "preflight": pre,
                "cleanup": cleanup_log, "aborted_by": aborted_by,
                "watchdog": watchdog}

    finally:
        # Cleanup runs on EVERY exit path, including a crash mid-fault. Running
        # the saga twice is safe — its steps are idempotent — and running it zero
        # times leaves a fault injected with nobody watching.
        if engine is not None:
            try:
                saga.run()
            except Exception:
                pass
        lock.release()


def record_refusal(spec: dict, verdict: str, reason: str, evidence: dict) -> int | None:
    """A refusal is evidence. Persist it so the dashboard can show that the
    framework declined, and why."""
    from ..db import persist_refusal
    return persist_refusal(spec, verdict, reason, evidence)
