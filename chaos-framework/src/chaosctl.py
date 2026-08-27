"""chaosctl — the CLI surface (mlops-quality reference §5). Phase 3 ships `run`
and `list`; plan/replay/freeze/flakiness/epoch/bisect land with their phases.

Usage:
    python -m src.chaosctl run pod_kill_payment_svc
    python -m src.chaosctl list
Env:
    PROM_URL (default http://localhost:19090), TESTRUN (default steady-120rps)
"""

import argparse
import os
import pathlib
import sys

import yaml

from .orchestrator import runner
from .orchestrator.runner import PreflightSkip

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPERIMENTS_DIR = REPO_ROOT / "experiments"

VERDICT_BANNER = {
    "held": "HYPOTHESIS_HELD",
    "falsified": "HYPOTHESIS_FALSIFIED",
    "invalid": "INVALID",
    "aborted": "ABORTED",
    "skipped": "SKIPPED",
    "denied": "DENIED",
}
EXIT_CODES = {"held": 0, "falsified": 1, "invalid": 2,
              "aborted": 3, "skipped": 4, "denied": 5}


def _resolve(name: str) -> pathlib.Path:
    for path in sorted(EXPERIMENTS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())["experiment"]
        if spec["name"] == name:
            return path
    raise SystemExit(f"no experiment named {name!r} in {EXPERIMENTS_DIR}. "
                     f"Try: python -m src.chaosctl list")


def cmd_list(_args) -> int:
    print(f"{'NAME':32} {'FAULT':22} {'FLOOR':>6}  HYPOTHESIS")
    for path in sorted(EXPERIMENTS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())["experiment"]
        print(f"{spec['name']:32} {spec['litmus_fault']:22} "
              f"{spec['min_rps_floor']:>6.0f}  v{spec['hypothesis']['version']} "
              f"({len(spec['hypothesis']['invariants'])} invariants)")
    return 0


def cmd_run(args) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    spec_path = _resolve(args.experiment)
    spec = yaml.safe_load(spec_path.read_text())["experiment"]
    prom_url = os.environ.get("PROM_URL", "http://localhost:19090")
    testrun = os.environ.get("TESTRUN", "steady-120rps")
    am_url = os.environ.get("ALERTMANAGER_URL", "http://localhost:19093")

    print(f"experiment : {spec['name']}   fault {spec['litmus_fault']} "
          f"{spec['fault_duration_s']}s + recovery {spec['recovery_window_s']}s")
    print(f"hypothesis : v{spec['hypothesis']['version']}   floor "
          f"{spec['min_rps_floor']:.0f} rps   testrun {testrun}")
    print("injecting  : ", end="")

    t0 = None

    def on_tick(sample):
        nonlocal t0
        if t0 is None:
            t0 = sample.sampled_at
            print("armed")
        rps = sample.client.get("client_rps")
        avail = sample.client.get("client_availability")
        replicas = sample.server.get("payment_replicas_available")
        print(f"  t+{sample.sampled_at - t0:5.1f}s  rps={_f(rps, '.0f')}  "
              f"avail={_f(avail, '.4f')}  payment_replicas={_f(replicas, '.0f')}")

    try:
        r = runner.run(spec_path, prom_url, testrun, am_url, on_tick=on_tick,
                       require_override=getattr(args, "override", False))
    except PreflightSkip as e:
        # SKIPPED/DENIED are first-class recorded outcomes, never silent no-ops.
        print("REFUSED")
        execution_id = runner.record_refusal(spec, e.verdict, e.reason, e.evidence)
        print()
        print(f"execution  : #{execution_id}  (refusal recorded, nothing injected)")
        print(f"VERDICT    : {VERDICT_BANNER.get(e.verdict, e.verdict.upper())} — {e.reason}")
        return EXIT_CODES.get(e.verdict, 5)

    engine, verdict, execution_id = r["engine"], r["verdict"], r["execution_id"]
    facts, chaos, samples = r["facts"], r["chaos"], r["samples"]
    check_list, score = r["checks"], r["score"]

    print()
    radius = r["preflight"].radius
    if radius:
        print(f"blast      : score {radius.score()}  {radius.affected_pods} pod(s)  "
              f"{radius.requests_at_risk_per_min:.0f} req/min at risk  "
              f"budget burn {radius.error_budget_burn_pct:.3f}%")
    print(f"engine     : {engine}   litmus verdict={chaos.get('verdict')}")
    print(f"load       : achieved {facts.achieved_rps:.1f} rps   "
          f"dropped {facts.dropped_iterations:.0f}   coverage {facts.coverage:.0%}")
    print(f"execution  : #{execution_id}   samples {len(samples.samples)}")
    print()

    if verdict.outcomes:
        print(f"{'INVARIANT':32} {'KIND':16} {'OUTCOME':10} "
              f"{'WORST':>10} {'THRESHOLD':>10} {'BREACHED':>9}")
        for o in verdict.outcomes:
            kind = o.evidence.get("kind", "-")
            worst = "-" if o.worst_value is None else f"{o.worst_value:.4g}"
            print(f"{o.name:32} {kind:16} {o.outcome.upper():10} "
                  f"{worst:>10} {o.threshold:>10.4g} {o.breached_for_s:>8.1f}s")
        print()

    print(f"{'CHECK':24} {'APPLICABLE':11} {'OUTCOME':9} {'SCORE':>6}  DETAIL")
    for c in check_list:
        s = " n/a" if c.score is None else f"{c.score:.2f}"
        print(f"{c.check_type:24} {str(c.applicable):11} {c.outcome.upper():9} "
              f"{s:>6}  {c.actual_value or c.message}")
    print()
    if score.score is None:
        print(f"SCORE      : none — {score.reason}")
    else:
        excl = f", excluded {score.excluded}" if score.excluded else ""
        print(f"SCORE      : {score.score:.4f}  ({score.status}, "
              f"weights denominator {score.weights_denominator}{excl})")
    print()

    if r["aborted_by"]:
        w = r["watchdog"]
        print(f"ABORT      : {r['aborted_by']} — {w.reason}")
    cl = r["cleanup"]
    steps = "  ".join(f"{s.name}={'ok' if s.ok else 'FAILED'}" for s in cl.steps)
    print(f"cleanup    : {steps}")
    if cl.escalated:
        print(f"             ESCALATED — {cl.escalation_reason}")
    print()

    banner = VERDICT_BANNER.get(verdict.verdict, verdict.verdict.upper())
    print(f"VERDICT    : {banner}" + (f" — {verdict.reason}" if verdict.reason else ""))
    return EXIT_CODES.get(verdict.verdict, 6)


def _f(v, spec) -> str:
    return "-" if v is None else format(v, spec)


def main() -> int:
    parser = argparse.ArgumentParser(prog="chaosctl")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run one experiment, print per-invariant verdicts")
    p_run.add_argument("experiment")
    p_run.add_argument("--override", action="store_true",
                       help="satisfy a require_override policy rule (recorded)")
    p_run.set_defaults(func=cmd_run)
    p_list = sub.add_parser("list", help="list registered experiments")
    p_list.set_defaults(func=cmd_list)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
